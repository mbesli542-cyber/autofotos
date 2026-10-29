# AutoExperten processor

Separate image-processing service for **AutoExperten Photo** (Python 3.12+,
FastAPI, Pillow, OpenCV, ONNX Runtime – CPU only).

It turns one real vehicle photo into an AutoExperten showroom listing photo:
the **original vehicle pixels** are cut out and placed into ONE fixed 3D
showroom, rendered from the camera of the photographed angle (eight
shot-specific plates), with contact and floor shadow. The vehicle is never
regenerated. Photos that would give a bad listing image are rejected by a
quality gate with a German retake hint.

> **Truthful-vehicle policy (non-negotiable).** No generative image model
> (DALL·E, Stable Diffusion, Flux, Midjourney, Generative Fill,
> image-to-image …) is used anywhere. The vehicle layer is only resampled
> (to fit the frame) and may receive a tiny exposure / white-balance
> correction within hard limits; paint colour, wheels, badges, lights,
> glass, damage and wear stay exactly as photographed. A colour guard
> measures hue and saturation of the vehicle before/after and drops any
> correction that would shift them (navy stays navy).

## Pipeline (V2 – 3D showroom plates)

| Step | Module |
| --- | --- |
| 1 Decode JPEG/PNG/WebP/HEIC (Pillow ≥ 13), EXIF orientation, ICC → sRGB (also PNG/WebP with alpha or a palette). The raw headers are read before Pillow parses anything: declared size (decompression bombs), animated PNG/WebP (refused), chunk/segment floods (limits under Security) → `decode`. JPEGs of 80–320 MP with one sequential scan (108/200 MP phone modes) are decoded at 1/2 by DCT scaling (`metadata.source` shows the reduced size); any other image above 80 MP fails with `image_too_large`, a side under 64 px with `source_resolution_too_low`. MPO uses its first image, HEIF its primary image. Then the resolution gate | `app/pipeline/decode.py`, `quality.py` |
| 2 Vehicle detector (RF-DETR-Seg, COCO instance segmentation) on the full frame; vehicle segmentation (`VehicleSegmenter` → alpha mask, BiRefNet) – portrait/square photos on the ORIGINAL pixels of a window around the detected vehicle | `app/pipeline/detection.py`, `segmentation.py` |
| 3 Alpha: the model alpha (up-sampled, mild choke) snapped onto real colour edges by a colour guided filter on the edge band – it may tighten the cut-out but never grow it beyond the model's own contour, and thin parts of the model's mask (antenna masts, mirror arms) keep at least the model alpha; edge confidence goes to the metadata only. Clean-up: the vehicle plus parts close to it (mirror heads, antenna pieces), foreign "spikes" (a traffic cone or post standing behind the car, merged into its outline below the roofline) cut from the outline (alpha only, anti-aliased cut edge, warning) – a spike whose cut line is not certain is left uncut and the job is rejected; **semantic check**: outline regions the detector does not see as part of THE vehicle (another car's wheel or rear, a hubcap, a sign, a bollard) are dropped when they are a separate part of the mask, otherwise the job is rejected; window holes filled; then a vehicle part the filled cut-out lacks (e.g. a dark mirror) rejects the job too, unless a whole-frame pass after a window pass has it – a smaller, partly lost piece (e.g. a mirror base) only gets a hint (Segmentation, step 5); bbox | `app/pipeline/matting.py`, `mask.py`, `detection.py` |
| 4 Vehicle geometry: tyre contacts (lower hull + narrow tyre "foot" + dark neutral rubber above it in the source photo – a lower, body-coloured bumper is no tyre; width and near/far side per tyre), near end (3/4), contact rise, aspect | `app/pipeline/vehicle_geometry.py` |
| 5 Plate selection: the shot's plate; a 3/4 photo of the other side gets the mirrored plate of its front/rear group (`plateUsed` + warning) | `app/pipeline/pipeline.py` (`select_plate`) |
| 6 Placement v2: plate target width, centred, lowest tyre contact on the plate's ground line; limited by height, margins, branding headroom, floor | `app/pipeline/placement.py` |
| 7 Quality gate (cropped, too small/upscale, mask, perspective, ground contact, foreign object in the outline, vehicle part missing from the cut-out) | `app/pipeline/quality.py` |
| 8 Cut-out in linear light, edge decontamination; conservative light matching with colour guard | `composite.py`, `harmonize.py`, `light.py` |
| 9 Branded plate: official logo + texts in wall space through the wall homography | `app/presets.py` (`BackgroundProvider`), `app/showroom/wall_branding.py`, `branding.py` |
| 10 Grounding v3 (linear light, floor only, before the car): ground model = car pose (proxy fitted to the tyre contacts in floor metres, scale along/across the car separately, limits reported as `poseClamped`), footprint (overhangs solved from the silhouette), **floor line per column in the photo's own perspective** (between same-side tyres and beyond them along a side: the line through their contacts; the end facing the camera in 3/4 views and front/rear views: outline + 15 cm bumper clearance at the plate's local scale; through every tyre's contact); light ground/snow remnants at the tyre bottoms / along the outline removed from the alpha (smoothed cut, never vehicle pixels; a tyre's cut changes by at most 2/3 px per px of width and tapers out over ≈ 3 cm at the ends of its range or blends into the outline cut – no notch); per tyre a contact patch in physical metres aligned with the pose (tread across the axle, falloff along the rolling direction from the tyre/floor gap R − √(R² − s²)), hard core kept within the tyre's x-extent + 3 cm, soft halo, crease; underbody: dark deep under the body → lighter at the floor line → 12 cm falloff; beyond the silhouette's ends (no car above the floor) only a penumbra that fades in below the end column's outline row (or the wall/floor junction) over 2 px per px of distance and out sideways – no hard-topped band beside the bumpers; the plate's proxy shadow as calibrated ambient; hidden-tyre pools for front/rear without visible tyres; slightly cooler deep shadow (as rendered) | `app/pipeline/grounding.py` (`build_ground_model`, `clean_ground_fringe`, `apply_grounding`), `shadow.py` |
| 10b Floor reflection: mirror axis = the floor line; inside the car's mirror image and under the car the plate's own floor reflection (reflection pass: LED streaks, wall glow, sheen) is removed and the mirrored ORIGINAL vehicle pixels are added as a colour-neutral specular term with the plate's measured reflectance (`reflectance` in plates.json × `reflection.strength`, ≤ `maxReflectance`); gloss blur grows with the distance (`blurRate`, 4× vertically), fades out between ½ and `fade` × car height; beyond the car's ends the under-car band only fades out (the reflected rays hit the wall there) – the plate's own floor reflection beside the bumpers stays; floor pixels only | `app/pipeline/reflection.py` (`apply_reflection`) |
| 11 Light wrap, premultiplied composite, JPEG export (4:3, 2400 × 1800 – preset `output.longEdgeMin` = `longEdgeMax` = 2400, the plates' resolution –, quality 92, sRGB, no EXIF/GPS; typically ≈ 0.75–1 MB) | `harmonize.py`, `composite.py`, `export.py` |

Orchestration: `app/pipeline/pipeline.py` (`process_photo`). Interior shots
(cockpit, seats, trunk, odometer …) are **not** composited – they are only
re-encoded (orientation, sRGB, long edge scaled down to at most
`output.longEdgeMax` = 2400 px, never up) because a showroom background makes
no sense there.

### Segmentation

`VehicleSegmenter` is a small protocol (`segment(rgb) -> float32 alpha`,
same size as the photo). The default backend is **BiRefNet (general, lite)**
run locally with ONNX Runtime on the CPU:

- open source (MIT), high-resolution dichotomous segmentation – keeps fine
  structures such as mirrors, antennas, wheel spokes and window frames
  much better than classic salient-object models;
- no cloud API, no GPU needed, deterministic;
- `isnet-general-use` (Apache-2.0) is the registered low-memory alternative
  (`PROCESSOR_SEGMENTATION_MODEL=isnet-general-use`): much lighter and faster,
  but in our test photos it more often kept ground/shadow fragments under
  the car, which then end up in the showroom.

Model files are downloaded on first use (or with
`python scripts/download_models.py`, which fetches the configured segmentation
model and the vehicle detector) and verified against a pinned SHA-256.

**Vehicle detector (semantic check).** BiRefNet cuts the salient foreground
precisely but does not know what a car is: an object right behind the car –
the wheel or the rear of a car parked behind it, a hubcap, a dealer sign on the
wall behind the roof, a side table, a bollard behind a mirror – merges into the
outline, and shape rules cannot tell such objects from mirrors, spoilers or
roof boxes. A second, small model gives the semantic signal:

| | |
| --- | --- |
| Model | **RF-DETR-Seg Medium** (Roboflow, DINOv2 backbone): COCO instance segmentation – box, class and mask per object (car, truck, bus, person, chair, dining table, …); 432 px input, masks at 108 px |
| Licence | **Apache-2.0** (checked 2026-10-28): upstream [roboflow/rf-detr](https://github.com/roboflow/rf-detr) – `LICENSE` Apache-2.0, its model table lists RF-DETR-Seg-M as "Apache 2.0" (only the Atto/Femto/Pico/XL/2XL *detection* models are PML 1.0; none of them is used). ONNX export: [PierreMarieCurie/rf-detr-onnx](https://huggingface.co/PierreMarieCurie/rf-detr-onnx) – model repo `apache-2.0`, export code MIT. No AGPL, no non-commercial terms |
| File | `rf-detr-seg-medium.onnx`, 130 MB (129 980 324 bytes), Hugging Face revision `94f91976449c187be38e649863e3dadecc312437`, SHA-256 `061d65fb02de2ed6e1e6fa549e89405cf745d72855e967305f37317a19e5f572` (`DETECTOR_REGISTRY` in `app/pipeline/detection.py`, `PROCESSOR_DETECTOR_MODEL=rf-detr-seg-medium`); downloaded and verified like BiRefNet (`scripts/download_models.py`, Docker build) |
| Used for | (1) the box of the photographed vehicle → the segmentation window of portrait/square photos (below); (2) which BiRefNet pixels belong to THE vehicle, to another object or to nothing it knows → the semantic check (steps 1–4); (3) which parts of THE vehicle the BiRefNet mask lacks (step 5) |
| Not used for | the cut line – its masks are coarse, **BiRefNet stays the matting model** – and never for pixels: the detector only reads the photo; it generates, moves or changes no pixel of the vehicle (or of anything else). Not a generative model |

1. full frame → THE vehicle: the car/truck/bus instance that agrees best with
   the BiRefNet mask (IoU ≥ 0.5; 0.93–0.99 on the regression and portrait
   photos, 0.86–0.91 on the cropped and far-away ones). None
   found → only the shape-based clean-up above, plus the warning
   `vehicle_not_detected`;
2. up to 6 zoomed tiles over the vehicle (side 0.45 × the vehicle size – at
   full frame the coarse vehicle mask swallows small glued objects) and two at
   twice the zoom on its left and right ends (a neighbour's wheel or hubcap):
   per BiRefNet pixel the vehicle, another detected object or nothing;
3. candidates – BiRefNet pixels the vehicle does not claim that are thicker than
   0.9 % and larger than 0.01 % of the vehicle width (²) and claimed by another
   object; unclaimed ones only in the upper half of the car and thicker than 1 %;
   large protrusions at roof level – are verified in a zoom tile centred on
   them: foreign when that tile's vehicle instance covers < 25 % of them (the
   car's own tail light that a coarse tile had handed to a scooter behind it:
   98 %) **and** they reach more than 3 mask cells of that tile beyond it –
   closer, the coarse instance outline cannot tell the car's own edge from the
   object behind it (glued objects reach 5.7–43 cells; the X-Trail's own bonnet
   edge in front of a snowy car, which that car's instance claimed, 1.5 cells
   at 14 % cover – kept). Antenna masts, mirror arms, wipers, roof rails and the
   cowl are below the thickness limit;
4. a foreign region that is a separate part of the mask is dropped (warning
   `background_object_removed`); one glued to the outline is not cut (the cut
   line between car and object is not certain) and the job is rejected
   (`mask_low_confidence`, check `foreign`, see the gate below);
5. **missing parts** – the other direction, checked on the cut-out as it will
   be: after the window-hole fill (a see-through window or glass roof that the
   fill closes is no lost part) and together with the separate foreign parts
   dropped in 4 (the coarse vehicle instance may claim them as well). What the
   zoomed vehicle instance claims beyond that mask (without the instance mask's
   coarse band, an opening of 1.5 mask cells), centred in the upper three
   quarters of the car and covered ≥ 60 % by the vehicle instance of a zoom
   tile centred on it, is
   - **a part the cut-out lost** when it is thicker than 2.5 % and larger than
     0.08 % of the vehicle width (²) – measured on the 57 evaluated masks:
     exactly the dark side mirrors in front of dark parked cars that BiRefNet
     dropped (thickness 3.8–4.4 %, cover 0.95–0.99; BiRefNet's own alpha there
     ≈ 0.01). After a window pass (portrait/square photos, below) the part is
     taken from the whole-frame pass when that pass has it; otherwise the job
     is rejected (`mask_low_confidence`, check `parts`);
   - **a partly lost piece** when it is smaller but at least 1.2 % thick and
     0.015 % of the vehicle width (²) large – e.g. the base of a mirror whose
     head the mask kept, or a clipped lens (metadata `mask.partsPartial`,
     finding action `partial`). After a window pass it is taken from the
     whole-frame pass as well when that pass has it; otherwise the job is
     accepted with the hint `vehicle_part_uncertain`. It never rejects – a
     deliberate choice: among the 80 evaluated cut-outs only two claims fall in
     this band, both real losses (the mirror base of `front_crop3x4_flip`,
     1.6 %, and the front indicator lens of `old_hardlight`, 1.5 %, a photo the
     gate should accept), and at this size a claim of the coarse instance
     outline can also be background (a round-2 probe found a mirror's shadow on
     the ground at 1.5 %; the current code no longer reports it).

   Everything else is no finding: thinner or smaller claims, and ground under
   the bumpers in the lowest quarter of the car. A see-through hole that the
   fill leaves open (lower than 8 % of the vehicle height) is not exempt: the
   vehicle instance claims it, so it counts like a lost part of its size
   (rejection or hint). Taking a part from the whole-frame pass
   (`merge_parts`): only that pass's solid pixels within the claim plus half a
   mask cell, only in regions between colour edges (CIE ΔE ≥ 20) that hold the
   part's interior or lie mostly in its core, plus the edge band next to them
   up to 2/3 of a mask cell (the part's rim, a lens; along the window pass's
   vehicle only 2 px), only attached to the car and only when they cover at
   least half of the part's core; where that pass continues into the
   background the alpha is cut with a 1.5 px anti-aliasing ramp. So the wedge
   of the white car behind, seen between a mirror and the A-pillar of
   `front_crop3x4`, stays out. Nothing is drawn in. Extra BiRefNet passes on
   other framings were tried and are not used: of 14 windows around the hard
   mirror of `front` (squares of 0.25–0.5 × the car width centred on it, half
   the car, the vehicle window – on `front` and two upright crops of it) one
   kept it.

A missing, damaged or different detector file is a configuration error exactly
like a missing segmentation model: every model file is checked against its
pinned SHA-256 when it is loaded (≈ 1 s for both models), and the detector runs
one blank inference at load to check its output layout. Jobs then fail with
"Die Bildbearbeitung ist derzeit nicht verfügbar…" (no retake request), and the
warm-up error turns `/health` into 503 (`modelError`, `detectorLoaded: false`).

**Portrait and square photos.** In one whole-frame 1024 × 1024 pass the car is
a thin band of a tall frame, and BiRefNet merged a car parked behind it, a
traffic cone and snow into the outline (letterboxing to a square does not
help). Photos that are not landscape are therefore segmented on the ORIGINAL
pixels of a window around the detector's vehicle (box + 20 % per side, widened
to at least square, at most 4:3 – the framing of a landscape photo); the alpha
is zero outside the window. Without a detected vehicle the window comes from
the bbox of a whole-frame pass; a vehicle that runs into an edge of the window
inside the photo falls back to the whole-frame alpha. Landscape photos keep the
single whole-frame pass (`segment_vehicle` in `segmentation.py`; metadata
`segmentation`). When the window pass lost a vehicle part the detector sees,
or a piece of one (step 5 – e.g. both mirrors of a front view, which the whole
frame kept), the whole-frame pass runs as well and the part is taken from it
where that pass has it (`merge_parts`, metadata
`segmentation.partsFromWholeFrame` with `missing`, `partial`, `taken`, `ms`;
then the mask check runs again). That is one more BiRefNet pass, ≈ 18–27 s
depending on the load, only for such photos – in the generated portrait set
`front_crop3x4` (both mirrors lost) and `front_crop3x4_flip` (only the mirror
bases).

**Resources (measured, CPU, one job):**

| Model | File | Peak RAM per running job | Time per job (4 cores) |
| --- | --- | --- | --- |
| `birefnet-general-lite` (default) | 224 MB | **≈ 7 GB** | ≈ 10–50 s (depends on load) |
| `isnet-general-use` | 179 MB | ≈ 1.5 GB | ≈ 4 s |
| `rf-detr-seg-medium` (detector, always on) | 130 MB | + ≈ 0.1–0.3 GB on top of BiRefNet's peak (its session stays loaded; it never runs at the same time as BiRefNet) | ≈ 4–12 s per exterior job (typically 9 runs of 0.45–0.9 s), up to ≈ 23 s on a CPU shared with other jobs. Measured over 118 jobs on 4 vCPUs: per job median 4.5–5.6 s, p90 9–12 s; per run median 0.47–0.59 s, max 2.3 s under heavy load; 4–20 runs per job (at most 1 full frame + 8 tiles + 10 verifications; after a whole-frame merge the tiles and verifications run again) |

Per exterior job, before (no detector, one whole-frame pass) and after the
detector, the window pass and the missing-part check – measured 2026-10-28 on
4 vCPUs, one process per job, models loaded beforehand (load incl. checksums
and the detector's test inference: 6.5–7 s, before 4–4.6 s), other agents on
the machine (load average 1.6–4, so ± 25 %):

| Photo | Before | After |
| --- | --- | --- |
| landscape 3840 × 2880 side view (`side_left`) | 26.1 s, peak 6.7 GB | 37.4 s (detector 9 runs, 5.5 s), peak 6.9 GB |
| portrait 2160 × 3840 side view (`side_left_9x16`) | 32 s (round 1), peak 6.7 GB – background car and cone pasted in | 45.1 s (BiRefNet on the 2160 × 1620 window 25.7 s, detector 9 runs 13 s under load), peak 6.9 GB – clean |
| portrait 3:4 front view (`front_crop3x4`), both mirrors lost by the window pass | 23.0 s, peak 6.7 GB | 56.5 s – window pass 22.7 s, whole-frame part pass 18.2 s + second semantic check, detector 19 runs 10.2 s – peak 7.2 GB, both mirrors present |
| landscape front view (`front`), one mirror lost | not timed – accepted without the mirror | 26.3 s, rejected (`parts`, detector 10 runs 5.1 s), peak 7.0 GB |
| the side portrait without a detected vehicle (window from a first whole-frame pass; round 1) | – | 53 s (two BiRefNet passes, 25 + 22 s), peak 6.6 GB |
| `suv_fr45` (neighbour car's wheel glued to the front; round 1) | 35.8 s, accepted with the wheel | 33.1 s, rejected (`foreign`, detector 10 runs 5.5 s), peak 7.0 GB |

Plan a server with at least 12 GB RAM for BiRefNet and keep
`PROCESSOR_CONCURRENCY=1` (each extra parallel job needs another ≈ 7 GB).
The rest of the pipeline adds a few seconds and well under 1 GB. This is why
the processor **does not run on Vercel** – deploy it on a VM/container (see
Docker below).

To add another backend implement `VehicleSegmenter` and register it in
`create_segmenter()` (it also attaches the detector, `segmenter.detector`).

### Presets and assets

The processing preset is `public/presets/autoexperten-standard.json` (shared
with the Next.js app; the processor reads it from `PROCESSOR_ASSETS_DIR`,
default `../public`). Sections: `background.plates` (the plate set),
`branding`, `output`, `placement` (size limits only), `shadow` (grounding v3:
`contactOpacity`, `creaseOpacity`, `underbodyOpacity` ≥ `edgeOpacity`, distances
in floor metres, `minFloorLight` ≥ 0.02), `reflection` (`enabled`, `strength` ≤ 1.5 ×
the plate's measured reflectance, `maxReflectance`/`defaultReflectance` ≤ 0.3,
`fade` 0.2–2 × car height, `blurRate` ≤ 0.2),
`vehicleAdjustments`, `quality`. Unknown keys, wrong types, NaN/Infinity and
out-of-range values are rejected (`PresetConfigError`), never replaced by
defaults – `/health` reports them as `presetError` and jobs fail with
`configuration`. Ranges besides the ones above (`app/presets.py`):
`output.aspect` whole numbers ≥ 1 between 1:2 and 3:1 (and matching the
plates), 320 ≤ `longEdgeMin` ≤ `longEdgeMax` ≤ 8000, `jpegQuality` 1–100;
fractions and opacities 0–1, `placement.minMargin` ≤ 0.2 (also per shot);
with branding on, `branding.logo.file` must be a PNG inside `brand/official/`,
logo `maxWidth`/`maxHeight` ≥ 0.02 and `opacity` ≥ 0.5, mount
`shadowOffset`/`shadowBlur` ≤ 0.02, text `capHeight` ≤ 0.1 and `tracking`
≤ 2 em; city/website/phone are one line (no control characters or line
separators) of at most 60 characters whose width – measured with the bundled
Inter font, each glyph's real advance – is at most 2.5 brand-wall heights
(the plates' wall is ≈ 1.6 heights wide); `quality.borderPx` ≤ 50. The
code clamps every vehicle adjustment to `HARD_LIMITS` in
`app/pipeline/light.py`, so the JSON cannot make the vehicle correction
aggressive.

**Showroom plates** – ONE physical 3D showroom (Blender,
`processor/showroom3d/render_plates.py`, CC0 assets) rendered from eight
shot-specific cameras (phone-like 66° HFOV, 1.25–1.4 m height):

```
public/presets/autoexperten-standard/
  <shot>.jpg          empty plate, NO branding (4:3; any resolution, 2400 × 1800 recommended)
  <shot>-shadow.png   16-bit grey: floor shadow/occlusion of a typical car at the anchor,
                      LINEAR multiplier (65535 = 1.0 = no shadow), lower resolution
  <shot>-reflection.png  optional 16-bit RGB: the floor's own glossy reflection (LED streaks,
                      wall glow, sheen), LINEAR, signed (code / 65535 − reflectionOffset),
                      lower resolution; with `reflectance` [[v, k], …] per plate
  plates.json         camera, horizon, floor/wall homographies, anchor, proxy car
                      (bbox, tyre contacts, targetWidthRatio), wall albedo + brand area
```

`app/showroom/plates.py` validates the set on load (all 8 shots, finite 3×3
homographies that agree with the anchor/contacts and with each other at the
wall/floor junction, files present and readable, sizes as declared, one aspect
ratio). How to render: `public/presets/README.md`. **There is no fallback**:
while the plate set is missing or invalid every exterior job fails with
**"AutoExperten Showroom-Master fehlt."** (`errorCode: "showroom"`), `/health`
reports `showroomSource: "missing"` + `showroomMasterError` (the reason).
Interior/detail shots never need the plates.

**Branding** (`app/showroom/wall_branding.py` + the layout engine in
`app/showroom/branding.py`) – the official logo PNG from
`public/brand/official/` (only scaled, never redrawn), `SCHWETZINGEN`,
`www.autoexperten-rn.de` and `+49 6202 9262357` (bundled Inter font, SIL OFL,
`app/showroom/fonts/`). The `branding` fractions refer to the plate's
`wall.brandArea` (the white wall between the slat panels): x from `xMin` (0)
to `xMax` (1), `top` 0 at the top (`zMax`) and 1 at the floor (`zMin`). The
layout is rendered on a uniform wall-albedo canvas (≥ 2 canvas px per output
px, so the logo is never upscaled in the result), the ratio branded/plain in
LINEAR light is warped with the plate's `wallHomography` and multiplied onto
the plate – a matte print lit by the plate's real light. Pixels without
branding keep their exact plate values. The projected element boxes keep the
vehicle roof below the branding (`branding.clearance`, fraction of the output
height). Preview without a vehicle:
`.venv/bin/python scripts/render_showroom_preview.py -o /tmp/plates.jpg --guides`
or `GET /showroom/autoexperten_standard/<shot>.jpg?width=2400`.

**Quality gate** (`app/pipeline/quality.py`, thresholds in `quality`, all
generous for normal phone photos – chest/eye height 0.8–1.8 m, any normal car
incl. SUVs). A rejected job fails with a German message and
`metadata.errorCode`/`metadata.qualityGate`; nothing is stored. First failing
check wins:

| Code | When | Message |
| --- | --- | --- |
| `source_resolution_too_low` | long edge < `minSourceLongEdge` (1600) – in any orientation, turning the phone keeps the long edge – or the vehicle needs more than `maxUpscale` (1.5×) although it fills ≥ `minVehicleWidthRatio` (45 %) of the photo | Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen. – only for the upscale rule on portrait/square photos: **Für Außenaufnahmen das Smartphone bitte quer halten.** (held sideways the same camera gives the car the long side of the sensor) |
| `vehicle_cropped` | solid vehicle touches the left/right/bottom border (top: over ≥ 15 % of its width) | Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren. |
| `vehicle_too_small` | needs more than `maxUpscale` and covers < 45 % of the photo width | Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren. |
| `mask_low_confidence` | soft-edge fraction, split parts, coverage or fill ratio out of range | Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen. |
| `perspective_mismatch` | bbox aspect far from the plate's proxy car; a much steeper floor (contact rise > `maxRiseFactor` × the proxy's); or a photo from above: rise > `highViewRiseFactor` (1.7×) together with a tall bbox (aspect < `highViewAspectFactor`, 0.8×) | Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren. |
| `ground_contact_uncertain` | 3/4 and side shots with fewer than 2 plausible tyre contacts | Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein. |
| `mask_low_confidence` (check `foreign`, reason `foreign_object`) | an object behind the car merged into its outline and could not be cut with a certain cut line: a spike (cone, post) or a region the vehicle detector does not see as part of the vehicle (another car's wheel or rear, a hubcap, a sign, a bollard). Checked after perspective and contacts – a photo from above (its own shadow lies in the outline) gets the perspective hint | Ein anderes Objekt (z. B. ein Fahrzeug, Schild oder Pfosten im Hintergrund) berührt die Fahrzeugkontur. Bitte so fotografieren, dass hinter dem Fahrzeug freier Hintergrund ist. |
| `mask_low_confidence` (check `parts`, reason `vehicle_part_missing`) | the vehicle detector sees a part of the vehicle that the cut-out lacks (a dark side mirror in front of a dark car: BiRefNet's alpha there is ≈ 0) – the listing image would show the car without it. Nothing is drawn in; after a window pass the part is first taken from the whole-frame pass if that pass has it. Checked last; the check reports `partsMissing` and `partsPartial`. A partly lost piece (1.2–2.5 % of the vehicle width, step 5) does not fail it: the result gets the hint `vehicle_part_uncertain` – "Ein kleiner Teil des Fahrzeugs (z. B. am Außenspiegel oder an einer Leuchte) hebt sich kaum vom Hintergrund ab und fehlt im Ergebnis möglicherweise – bitte das Ergebnis prüfen." | Ein Teil des Fahrzeugs (z. B. ein Außenspiegel) hebt sich nicht vom Hintergrund ab und würde im Ergebnis fehlen. Bitte vor ruhigerem Hintergrund neu fotografieren. |

## Run locally

Requires Python 3.12+ (tested with 3.13) and Pillow 13 (`pillow~=13.0` in
`requirements.txt` – 12.x has denial-of-service advisories reachable from
uploads; `tests/test_decode.py` fails on an older venv; `certifi` has a
`>=2026.10.19` floor). After an update run
`.venv/bin/pip install -r requirements-dev.txt` again.

```bash
cd processor
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # optional – edit as needed
.venv/bin/python scripts/download_models.py   # optional, else downloaded on first job
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`scripts/download_models.py` fetches the configured segmentation model and
the vehicle detector and checks both against their pinned SHA-256 (a wrong
cached file is downloaded again).

`GET http://localhost:8000/health` → `{"status":"ok","version":"2.0.0"}`
(with `Authorization: Bearer <key>` – or without a key configured – also
model, detector, debug, `auth`, `supabase`, showroom source
(`plates`/`missing`), `showroomMasterError` and `presetError`; `503` if the
segmentation model or the vehicle detector cannot be loaded – `modelLoaded`
covers both, `detector`/`detectorLoaded` name and report the detector).
`presetError` is also set when the official logo (`brand/official/…png`) is
missing, unreadable or fully transparent, or when the preset's
`output.aspect` does not fit the plates – `showroomSource` stays `plates`
then, exterior jobs fail with `errorCode: "configuration"` right after
decoding (the pipeline checks it next to the plate set, before the detector
and segmentation run) and `GET /showroom/…` answers `503 configuration`; the
app blocks processing up front. `showroom` / `showroom_missing` only mean a missing or
unusable plate set. The models are warmed up in the background after
start-up. Without a key the API is open – fine for `uvicorn` on
`127.0.0.1`; use `--host 0.0.0.0` only together with `PROCESSOR_API_KEY`.

### Process one photo

Command line (no server needed):

```bash
.venv/bin/python -m app.cli ~/Bilder/auto.jpg -o /tmp/auto-showroom.jpg \
    --shot front_left_45 --debug-dir /tmp/auto-debug
```

HTTP (upload endpoint, used by the developer test page):

```bash
curl -F file=@auto.jpg -F preset=autoexperten_standard -F shotKey=front_left_45 \
     http://localhost:8000/jobs/upload
# → {"jobId":"…","status":"queued",…}
curl http://localhost:8000/jobs/<jobId>             # poll until "complete"
curl -o result.jpg http://localhost:8000/jobs/<jobId>/result
```

Or use the Next.js developer page **`/dev/processing-test`** (only in
`npm run dev`, or with `ENABLE_DEV_TOOLS=true`): upload a photo, choose
"AutoExperten Standard", "Verarbeiten", compare Original/Bearbeitet and
download the result.

### Connect the Next.js app

In the app's `.env.local`:

```
IMAGE_PROCESSOR=real
NEXT_PUBLIC_IMAGE_PROCESSOR=real
IMAGE_PROCESSING_API_URL=http://localhost:8000
IMAGE_PROCESSING_API_KEY=<same value as PROCESSOR_API_KEY>
```

The app keeps talking only to `/api/process-photo` and
`/api/process-job/:jobId`; `RealImageProcessor` forwards to the processor's
JSON contract:

- `POST /jobs` `{vehicleId, photoId, preset, userId?, shotKey?}` → `202` job.
  Needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in the processor: it
  downloads the original from the private `vehicle-originals` bucket, writes
  the result to `vehicle-processed/{vehicleId}/{preset}/{shotKey}/{photoId}.jpg`
  and sets `vehicle_photos.processed_storage_path`. Originals are never
  written.
- `GET /jobs/{jobId}` → `{jobId, status: queued|processing|complete|failed,
  progress, result, error, warnings, metadata, …}`; errors are short German
  messages for the UI. Failed jobs carry `metadata.errorCode`: a quality-gate
  code above (with `metadata.qualityGate`, all checks) or one of the job
  error codes below.

Other endpoints: `POST /jobs/upload` (multipart), `GET /jobs/{id}/result`,
`GET /jobs/{id}/debug[/{name}]` (debug mode only),
`GET /showroom/{preset}/{shot}.jpg?width=` (branded plate without vehicle,
header `X-Showroom-Source: plates`), `GET /health`.

Answers of `POST /jobs` and `POST /jobs/upload` that queue nothing:

| Status / code | When |
| --- | --- |
| `401 unauthorized` | missing or wrong Bearer key (checked before the body is read) – "Nicht autorisiert." |
| `400 invalid_request` | a field fails validation (ids, shot key, unknown preset id; the answer lists the `fields`) or an invalid `Content-Length` – "Ungültige Anfrage." |
| `400 preset_unavailable` | `autoexperten_dark` / `original_plus` – "Dieser Bearbeitungsstil ist noch nicht verfügbar." |
| `400 empty_file` | `POST /jobs/upload` with an empty file – "Die Datei ist leer." |
| `413 too_large` | the upload is larger than `PROCESSOR_MAX_UPLOAD_MB` (also chunked uploads) – "Die Datei ist zu groß." |
| `415 unsupported_type` | `POST /jobs/upload`: the file part's type is neither `image/*` nor `application/octet-stream` – "Bitte eine Bilddatei hochladen." |
| `503 busy` | the queue is full (below) – "Der Bildverarbeitungs-Service ist ausgelastet. Bitte gleich erneut versuchen." |
| `503 storage_full` | the disk or quota is full – also while the multipart body is spooled – "Der Bildverarbeitungs-Service hat gerade keinen Speicherplatz. Bitte später erneut versuchen." |
| `503 storage_error` | another error writing the job folder – "Das Foto konnte gerade nicht zur Bearbeitung angenommen werden. Bitte später erneut versuchen." |
| `503 storage_not_configured` | `POST /jobs` without `SUPABASE_*` (the app shows its own text without backend names) |

The app retries `busy` with back-off for up to 10 minutes and shows the
other 503 texts (as `processing_unavailable`).

Job error codes besides the quality gate (`metadata.errorCode`; the app offers
"Foto neu aufnehmen" for `decode`, `image_too_large`, `segmentation` and every
quality-gate code):

| Code | Message |
| --- | --- |
| `decode` | Das Foto konnte nicht gelesen werden. Bitte ein JPEG-, PNG- oder HEIC-Foto verwenden. (unreadable, animated PNG/WebP, a header over the budgets) |
| `image_too_large` | Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler Kamera-Auflösung neu aufnehmen. (PNG/WebP/HEIF above 80 MP, progressive or multi-scan JPEGs above 80 MP, JPEGs above 320 MP) |
| `source_resolution_too_low` | the quality-gate text – also for any photo (interior too) with a side under 64 px |
| `segmentation` | Das Fahrzeug konnte im Foto nicht erkannt werden. |
| `configuration` | Die Bildbearbeitung ist auf dem Server nicht richtig eingerichtet (Showroom/Logo). Bitte den Administrator informieren. |
| `showroom` | AutoExperten Showroom-Master fehlt. |
| `service` | Die Bildbearbeitung ist derzeit nicht verfügbar. Bitte später erneut versuchen. (a model cannot be loaded) |
| `not_found` / `storage` | Das Originalfoto wurde nicht gefunden. / Das Foto konnte nicht geladen oder gespeichert werden. Bitte später erneut versuchen. |
| `preset` | Dieser Bearbeitungsstil ist noch nicht verfügbar. (a style the processor does not implement; the API refuses it up front with `400 preset_unavailable`) |
| `unknown` | Die Bearbeitung ist fehlgeschlagen. Bitte versuchen Sie es erneut. (any other error) |

Security:

- When `PROCESSOR_API_KEY` is set every endpoint except `/health` (and, with
  `PROCESSOR_DEBUG=true`, the API docs `/docs` and `/openapi.json`) requires
  `Authorization: Bearer <key>`; the check runs before any request body is
  read (`app/guard.py`). Without a key the API is open.
- The service refuses to start without a key when `SUPABASE_*` is configured
  (the service role bypasses RLS) and whenever `PROCESSOR_REQUIRE_API_KEY=true`
  – the Docker image sets it, so a container never runs unauthenticated by
  accident (opt-out for a local test: `PROCESSOR_REQUIRE_API_KEY=false`). The
  error names the variables; `uvicorn` without Docker keeps working without a
  key.
- Request bodies are capped (`PROCESSOR_MAX_UPLOAD_MB`, also for chunked
  uploads → `413`); only JPEG, PNG, WebP and HEIC are decoded, up to 80 MP
  (single-scan sequential JPEGs up to 320 MP reduced, see step 1). Before Pillow parses a
  file, floods are refused as `decode`: JPEGs with more than 4096 segments,
  4096 fill bytes/standalone markers, 64 KiB of junk between segments or
  8 MiB of header before the first scan, or a reserved marker code; PNGs with
  more than 2048 non-IDAT or 65536 IDAT chunks; WebPs with more than 256 RIFF
  chunks. Real photos are far inside these limits.
- At most 12 upload jobs per worker (× `PROCESSOR_CONCURRENCY`) and 200 jobs
  in total may wait or run; more → `503 busy` "ausgelastet". A queued upload
  waits on disk (`<PROCESSOR_DATA_DIR>/jobs/<jobId>/upload.bin`, deleted when
  the job starts; leftovers of an earlier process are deleted at start-up) –
  worst case 12 × `PROCESSOR_MAX_UPLOAD_MB` = 480 MB of disk per worker; RAM
  holds only the running photo.
- `/docs` and `/openapi.json` exist only with `PROCESSOR_DEBUG=true` (then
  without a key).

Jobs are kept in memory and on disk for `PROCESSOR_JOB_TTL_HOURS` (expired
jobs are deleted within 10 minutes); a restart
forgets running jobs (prototype), and job folders of earlier runs are deleted
once they are older than the TTL. If the jobs folder is deleted while the
service runs, the next job creates it again.

## Debugging

With `PROCESSOR_DEBUG=true` every job keeps

`original.jpg`, `mask.png`, `geometry.jpg` (mask, bbox, tyre contacts:
green = plausible; ground model mapped back: projected footprint cyan, floor
line yellow, tyre contact patches red, tyre x-extents green, hidden tyres
magenta; semantic check: detector vehicle box thin magenta, verified regions
red = foreign / cyan = a part the mask lacks / orange = kept – a partly lost
piece is drawn orange as well –, segmentation window white), `grounding.jpg` (the same over the composite), `vehicle-transparent.png`, `background.jpg` (branded
plate), `composite-before-shadow.jpg`, `shadow.png` (grounding + reflection darkening),
`final.jpg`, `metadata.json`

under `PROCESSOR_DATA_DIR/jobs/<jobId>/debug/`, listed at
`GET /jobs/{id}/debug` and served at `GET /jobs/{id}/debug/{name}`.
`metadata.debugFiles` lists exactly these allow-listed files
(`DEBUG_FILE_NAMES` in `app/pipeline/debug.py` – a new debug image must be
added there, `tests/test_api.py` checks it). With debug off (default) these
files are neither written nor served. **Never enable debug on an instance reachable from the
internet** (the files contain the full original photo).

`metadata` of an exterior job contains `plateUsed`, `plate` (camera summary,
ground line, target width), `plateSelection`, `geometry` (contacts, near end,
contact rise, aspect), `contacts` (source and output px), `detector`,
`segmentation` (passes, `window`, `windowUsed`, `boxSource`,
`partsFromWholeFrame`), `mask` (bbox, coverage, components, `holesFilled`,
`modelFragments`, `detachedDropped`, `foreignRemoved` / `foreignSuspected` /
`partsMissing` / `partsPartial` boxes, `vehicleDetected`, `semantic` – the
detector's vehicle (label, score, IoU, box), tiles, runs, ms and every
verified region (`evidence` `other_object`/`unclaimed`/`roof_protrusion`/
`missing_part`, labels, `verify` with `vehicleCover`, the flags `foreign`,
`missing`, `partial` and the `action` `removed`/`suspected`/`kept`/`missing`/
`partial`), `edges` – colour-edge diagnostics such as
`edgeConfidence`, `lowContrastFraction` and `thinProtectedPx`, never used as a
gate), `qualityGate` (every check with its measurements, e.g.
`checks.mask.reasons`, `checks.foreign.foreignSuspected`,
`checks.parts.partsPartial`; a rejected job's
`qualityGate` also carries `mask` and `segmentation`), `placement` (scale, limiting rule,
`targetWidthRatio`, `achievedWidthRatio`, ground line), `grounding` (version 3:
pose fit incl. `clamped` limits and `poseClamped` (+ `warnings: ["poseClamped"]`),
footprint, floor line samples, per-tyre patch/radius/x-extent, hidden tyres,
`fringe` (alpha removed at tyre bottoms/outline), `farFloorDarkening` (floor
≥ 0.5 m from the car in the picture) and `farFloorDarkeningFootprint`),
`reflection` (plate pass used, reflectance range, removed/added amounts), the
vehicle correction (exposure EV, white-balance gains, measured hue/saturation
change, guard result), branding boxes and timings. Warnings (German) flag
mirrored plates, reduced vehicle size, low resolution, uncertain masks,
busy backgrounds, background objects cut from the vehicle outline, a vehicle
the detector did not find and a small vehicle part that may be missing
(`vehicle_part_uncertain`).
`busy_background` fires when the segmentation model's OWN output is
fragmented (> 25 parts); BiRefNet returns 1–2 parts on every cached
regression photo, so with it this warning practically never fires (it guards
against a fragmented model output, e.g. with another segmenter).

## Docker

The build context is the repository root (the image bakes in
`public/presets` and `public/brand`):

```bash
docker build -f processor/Dockerfile -t autoexperten-processor .
# settings from the file (PROCESSOR_API_KEY set in it) – the key never appears on the command line:
docker run --rm -p 127.0.0.1:8000:8000 --env-file processor/.env autoexperten-processor
# or: -e with only the name takes the value from the calling shell's environment
read -rs PROCESSOR_API_KEY && export PROCESSOR_API_KEY
docker run --rm -p 127.0.0.1:8000:8000 -e PROCESSOR_API_KEY autoexperten-processor
curl -H "Authorization: Bearer <key>" http://127.0.0.1:8000/health   # … "auth":true …
```

- The image sets `PROCESSOR_REQUIRE_API_KEY=true`: without `PROCESSOR_API_KEY`
  the container does not start and logs "PROCESSOR_API_KEY is not set and
  PROCESSOR_REQUIRE_API_KEY=true (the Docker image default) – refusing to
  start an unauthenticated processor. …". Only for a local test without a
  key add `-e PROCESSOR_REQUIRE_API_KEY=false`.
- Publish the port on `127.0.0.1` unless other machines must reach it:
  `-p 8000:8000` publishes on every interface, past the host firewall.
- `--env-file` values are taken literally (no quotes, no comments after a
  value), as in `processor/.env.example`.

The segmentation model and the vehicle detector are downloaded and
checksum-verified at build time (`--build-arg SEGMENTATION_MODEL=isnet-general-use`
for the alternative segmentation model, `--build-arg DETECTOR_MODEL=…` for
another registered detector), the service runs as a non-root user and stores
jobs in the `/data` volume. The image's health check calls the
unauthenticated `/health` (status only).

## Deploy (so the Vercel/iPhone app can process real photos)

The model needs a normal server – it never runs on Vercel. Any Linux VM with
Docker, **≥ 12 GB RAM** (BiRefNet ≈ 7 GB per running job; with
`--build-arg SEGMENTATION_MODEL=isnet-general-use` ≈ 4 GB is enough) and a
public host name, e.g. a Hetzner CX42/CPX41.

```bash
git clone <repository> && cd <repository>/processor/deploy
cp ../.env.example ../.env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → PROCESSOR_API_KEY in ../.env (required)
# Supabase mode: also SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in ../.env
PROCESSOR_DOMAIN=processor.example.com docker compose up -d --build
curl https://processor.example.com/health          # {"status":"ok","version":"2.0.0"} – no details
curl -H "Authorization: Bearer <PROCESSOR_API_KEY>" https://processor.example.com/health
# → {"status":"ok", …, "auth":true, "supabase":…, "showroomSource":"plates", "presetError":null, …}
```

Check the authenticated answer: `"auth":true` (the key is active),
`"modelLoaded":true` once the models are warmed up, `"showroomSource":"plates"`,
`"presetError":null` and, in Supabase mode, `"supabase":true`. If an
unauthenticated `/health` returns these details with `"auth":false`, the
processor is open. The compose file sets `PROCESSOR_REQUIRE_API_KEY: "true"`
itself, so `../.env` cannot switch it off: with an empty key the processor
container exits and restarts – `docker compose logs processor` shows the
reason.

`docker-compose.yml` builds the image from the repository root (models, showroom
plates and official logo baked in) and puts Caddy with an automatic
Let's-Encrypt certificate in front of it (`Caddyfile`). The DNS record of
`PROCESSOR_DOMAIN` must point to the server, ports 80/443 open.

Update: `git pull && PROCESSOR_DOMAIN=processor.example.com docker compose up -d --build`
(a changed `requirements.txt`, e.g. the move to Pillow 13, rebuilds the pip
layer by itself).

Then set in Vercel (Project → Settings → Environment Variables) and redeploy:

| Variable | Value |
| --- | --- |
| `IMAGE_PROCESSOR` | `real` |
| `NEXT_PUBLIC_IMAGE_PROCESSOR` | `real` |
| `IMAGE_PROCESSING_API_URL` | `https://processor.example.com` |
| `IMAGE_PROCESSING_API_KEY` | the same value as `PROCESSOR_API_KEY` |
| `PROCESSING_ACCESS_CODE` | needed while the app runs in Demo-Modus on a public URL (no login) – otherwise anyone can use the processor |

The service role key stays on the processor (`../.env`); it is never set in
Vercel.

## Tests

```bash
.venv/bin/python -m pytest
```

Covers API validation and auth (incl. `PROCESSOR_REQUIRE_API_KEY`: no start
without a key in the Docker image and the deploy template, `tests/test_config.py`),
job lifecycle (incl. quality-gate failures with `errorCode`, `preset_unavailable`,
an unusable official logo failing the job before segmentation, the upload
queue bound and `storage_full`/`storage_error`), preset validation,
plate-set loading/validation, wall-space branding (logo
lands where the wall homography says, relighting is multiplicative, wall
without branding stays bit-exact), vehicle geometry (tyre contacts, 3/4 near
end), every quality-gate code with its exact German text, placement v2,
grounding (floor only, never black), mask clean-up (foreign spikes cut with an
anti-aliased edge, an uncut one rejects the job, mirrors/antennas/rails/racks/
spoilers kept, detached blobs), the vehicle detector (ONNX outputs by shape,
pinned model, missing model, a file with another checksum or another output
layout = service problem at warm-up) and the semantic check against a fake
detector with model-like coarse masks (a neighbour car's wheel, a sign on the
roof → rejected; thin parts, a roof box, ground under the sill, a region within
the coarse outline's uncertainty → kept; a separate foreign part → dropped; no
vehicle → warning; a side mirror the mask lost → rejected, a partly lost
mirror → hint; the gap under the car, a small clipped edge, a see-through
window the fill closes and a dropped foreign part → not a missing part), the
window pass of portrait/square photos (a mirror the window pass lost is taken
from the whole-frame pass, or the photo is rejected; a lost mirror base is
taken or flagged with the hint; what is taken stops at colour edges) and the
"quer halten" message (upscale rule only), colour-edge matting (no alpha above the model
outside its contour, thin parts such as antenna masts keep the model alpha),
colour guard and hard
limits (navy stays navy), decoding (header budgets, animated images, reduced
decoding of very large JPEGs, `image_too_large`), debug output (every listed
debug file can be downloaded) and the Supabase store (HTTP mocked). Tests use small synthetic plate sets with exact geometry
(`tests/plate_fixtures.py`), synthetic vehicles and a fake segmenter – no
model download needed; one smoke test runs against the real plate set when it
exists.

## Known limitations

- **Windows:** outdoor scenery seen through the glass or reflected in it
  (trees, sky, snow, other cars) stays. It cannot be removed reliably without
  changing factual vehicle content: a tested glass treatment greyed a police
  livery stripe, a roof light-bar lens and a green emission sticker and
  darkened a dashboard, for a barely visible benefit, and was removed. A
  reliable solution would need a discriminative glass/car-parts segmentation
  model; separating the interior from the see-through scenery would remain
  unsolved even then.
- **Lighting:** harmonisation is limited to the conservative, guarded
  exposure/white-balance correction (`light.py`, `HARD_LIMITS` + colour guard)
  and the light wrap at the silhouette. Outdoor reflections in the paint stay –
  they are part of the photographed car. A tested showroom relighting
  (exposure target from the plate, contrast, white balance, floor bounce, edge
  reflection) lightened correctly exposed dark paint, needed a second colour
  guard and its combined gains exceeded the exposure limit; it was removed.
- **Background objects merged into the outline:** slender objects standing
  behind the car below its roofline (traffic cone, bollard, post up to ~6 % of
  the vehicle width) are cut along the chord between the points where the car's
  outline meets them – a few pixels of the object can remain at that chord.
  Everything else the vehicle detector does not see as part of the vehicle
  (another car's wheel, hubcap or rear, a sign behind the roof, a side table, a
  bollard behind a mirror) is only removed when it is a separate part of the
  mask; glued to the outline it is NOT cut – the detector's masks are too coarse
  for a certain cut line – and the job is rejected with the "freier
  Hintergrund" message. With cars parked close together (a dealer lot) this
  asks for a retake more often: of the 21 normally framed real photos of the
  regression and audit sets (mostly snowy dealer lots), 7 are now rejected for
  this – each has a real object glued to the outline (checked by eye) –, 1
  (`front`) for a mirror the cut-out lost (below), and the other 13 are
  accepted as before (`old_hardlight` now with a hint, below). Not detected:
  objects the detector neither assigns to another class nor leaves outside its
  vehicle mask (e.g. a grey post exactly behind the car's own silhouette, or a
  car parked right behind that the detector merges into the same "car"
  instance), objects thinner than 0.9 % of the vehicle width, objects that
  reach less than 3 mask cells of the verifying zoom tile (≈ 0.6–1.3 % of the
  vehicle width: 3 cells of a tile of 0.2–0.45 × the vehicle size) beyond the
  detector's vehicle outline – there the coarse outline cannot tell the car's
  own edge from what is behind it (e.g. a thin rail running along the bonnet
  edge) –
  and – as "unclaimed" regions (no COCO class, e.g. a bollard) – objects in the
  lower half of the car (there the coarse masks cannot tell ground, shadow,
  sills and tyres apart). At most 6 foreign-object candidates (and 4
  missing-part candidates) are verified per photo (the regression photos have
  at most 3). Without a detected vehicle (warning `vehicle_not_detected`) only
  the shape rules apply. The detector costs ≈ 4–12 s per exterior job
  (typically 9 runs of 0.45–0.9 s on 4 cores), up to ≈ 23 s on a CPU shared
  with other jobs.
- **Vehicle parts the cut-out loses:** BiRefNet can drop a dark part in front
  of a dark background – the black mirror of `front` in front of the dark car
  parked behind it has an alpha of ≈ 0.01, and 13 of 14 other framings tried
  (zoomed on the mirror, half the car, the vehicle window) lose it as well. The
  detector check (Segmentation, step 5) rejects such a photo instead of
  showing the car without its mirror; after a window pass the part is taken
  from the whole-frame pass when that pass has it (the upright 3:4 crops of
  `front`). This costs retakes: `front` itself is now rejected. Landscape
  photos get no second pass (no framing recovered the hard mirror reliably).
  - A smaller loss (1.2–2.5 % of the vehicle width, a "partly lost piece") is
    accepted with only the hint `vehicle_part_uncertain` – in probes with real
    masks that included a whole far-side mirror of a 3/4 view (1.8 %) and up
    to half of a near mirror's head. Two results of the test sets carry the
    hint, both with a real loss: `front_crop3x4_flip` (the mirrored 3:4 crop of
    `front`: its window pass keeps both mirror heads but loses their bases, its
    whole-frame pass loses both mirrors, so the bases stay missing and the
    heads float – nothing is drawn in) and `old_hardlight` (the front amber
    indicator lens, which the outline clips, is missing from the result).
  - A see-through hole in the glass that the window fill leaves open (lower
    than 8 % of the vehicle height) can be rejected with the mirror message
    although no part is lost, or get the hint (seen in probes only; no real
    photo of the sets has one).
  - Merged mirrors of the upright `front` crops: a light fragment of the car
    behind, about 5–8 photo px, remains at the right mirror's junction with
    the A-pillar.
  - Not checked: parts thinner than 1.2 % of the vehicle width (in practice
    ≈ 1.3 %: the coarse band the check removes) or centred in the lowest
    quarter of the car, where the coarse instance masks claim ground and
    shadow as well. E.g. the bright chrome side-step tube of the X-Trail
    against the snow is lost by BiRefNet itself: no pass keeps much more than
    half of it (landscape `side_right` ≈ 48–56 %, the window and whole-frame
    passes of the derived upright frames `side_right_3x4`/`_9x16` 9–31 %,
    depending on the pass and the measure; the passes lose different stretches
    and the final mask follows the window pass), and the detector's vehicle
    outline runs along the top of the tube, so no check can see or recover
    it – these results are accepted without a warning.
- **Portrait photos:** segmented in a window around the vehicle (see
  Segmentation) – BiRefNet then sees the car framed like a landscape photo. A
  portrait photo still gives the car only the short side of the sensor: at
  stream resolution (1080 × 1920), or when the car fills only about half of the
  width, it is rejected with "Für Außenaufnahmen das Smartphone bitte quer
  halten.". There are no real upright car photos in the test sets: the portrait
  cases are frames derived from the landscape photos
  (`scripts/make_portrait_cases.py`) and the audit's composites. Without a
  detected vehicle the window needs a second BiRefNet pass (≈ 18–27 s more),
  and so does a vehicle part – or a piece of one – that the window pass lost
  (the whole-frame pass, see Segmentation).
- **Tyre bottoms:** the edge is the model's outline, snapped to colour edges
  only where the photo backs it; snow or dirt stuck on the tread is part of the
  car and stays – except a light fringe within 2 cm of the floor contact (and a
  ≤ 1.5 cm light rim along sills), which grounding removes from the alpha so the
  contact shadow shows instead of a light gap. Larger snow clumps hanging under
  a bumper stay (matting).
- **Floor line:** the photo's camera is not the plate's camera; the floor right
  below the car follows the photo's own perspective (tyre contact lines, bumper
  outline + a typical 15 cm clearance at the end facing the camera). Unusually
  high or low bumpers get the typical clearance.
- The vehicle is placed with its 2D bbox/contacts; a photo whose camera
  height or angle differs from the plate camera keeps its own perspective
  (the gate only rejects strong mismatches).
- The near end of 3/4 photos (for the mirrored plate) is estimated from the
  outline; unclear cases keep the shot's plate.
- Jobs are in-memory (single instance); no batch processing yet.
