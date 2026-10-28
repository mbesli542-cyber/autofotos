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
| 1 Decode JPEG/PNG/WebP/HEIC, EXIF orientation, ICC → sRGB; resolution gate | `app/pipeline/decode.py`, `quality.py` |
| 2 Vehicle detector (RF-DETR-Seg, COCO instance segmentation) on the full frame; vehicle segmentation (`VehicleSegmenter` → alpha mask, BiRefNet) – portrait/square photos on the ORIGINAL pixels of a window around the detected vehicle | `app/pipeline/detection.py`, `segmentation.py` |
| 3 Alpha: the model alpha (up-sampled, mild choke) snapped onto real colour edges by a colour guided filter on the edge band – it may tighten the cut-out but never grow it beyond the model's own contour, and thin parts of the model's mask (antenna masts, mirror arms) keep at least the model alpha; edge confidence goes to the metadata only. Clean-up: the vehicle plus parts close to it (mirror heads, antenna pieces), foreign "spikes" (a traffic cone or post standing behind the car, merged into its outline below the roofline) cut from the outline (alpha only, anti-aliased cut edge, warning) – a spike whose cut line is not certain is left uncut and the job is rejected; **semantic check**: outline regions the detector does not see as part of THE vehicle (another car's wheel or rear, a hubcap, a sign, a bollard) are dropped when they are a separate part of the mask, otherwise the job is rejected – window holes filled, bbox | `app/pipeline/matting.py`, `mask.py`, `detection.py` |
| 4 Vehicle geometry: tyre contacts (lower hull + narrow tyre "foot" + dark neutral rubber above it in the source photo – a lower, body-coloured bumper is no tyre; width and near/far side per tyre), near end (3/4), contact rise, aspect | `app/pipeline/vehicle_geometry.py` |
| 5 Plate selection: the shot's plate; a 3/4 photo of the other side gets the mirrored plate of its front/rear group (`plateUsed` + warning) | `app/pipeline/pipeline.py` (`select_plate`) |
| 6 Placement v2: plate target width, centred, lowest tyre contact on the plate's ground line; limited by height, margins, branding headroom, floor | `app/pipeline/placement.py` |
| 7 Quality gate (cropped, too small/upscale, mask, perspective, ground contact, foreign object in the outline) | `app/pipeline/quality.py` |
| 8 Cut-out in linear light, edge decontamination; conservative light matching with colour guard | `composite.py`, `harmonize.py`, `light.py` |
| 9 Branded plate: official logo + texts in wall space through the wall homography | `app/presets.py` (`BackgroundProvider`), `app/showroom/wall_branding.py`, `branding.py` |
| 10 Grounding v3 (linear light, floor only, before the car): ground model = car pose (proxy fitted to the tyre contacts in floor metres, scale along/across the car separately, limits reported as `poseClamped`), footprint (overhangs solved from the silhouette), **floor line per column in the photo's own perspective** (between same-side tyres and beyond them along a side: the line through their contacts; the end facing the camera in 3/4 views and front/rear views: outline + 15 cm bumper clearance at the plate's local scale; through every tyre's contact); light ground/snow remnants at the tyre bottoms / along the outline removed from the alpha (smoothed cut, never vehicle pixels); per tyre a contact patch in physical metres aligned with the pose (tread across the axle, falloff along the rolling direction from the tyre/floor gap R − √(R² − s²)), hard core kept within the tyre's x-extent + 3 cm, soft halo, crease; underbody: dark deep under the body → lighter at the floor line → 12 cm falloff, rounded off at the ends; the plate's proxy shadow as calibrated ambient; hidden-tyre pools for front/rear without visible tyres; slightly cooler deep shadow (as rendered) | `app/pipeline/grounding.py` (`build_ground_model`, `clean_ground_fringe`, `apply_grounding`), `shadow.py` |
| 10b Floor reflection: mirror axis = the floor line; inside the car's mirror image and under the car the plate's own floor reflection (reflection pass: LED streaks, wall glow, sheen) is removed and the mirrored ORIGINAL vehicle pixels are added as a colour-neutral specular term with the plate's measured reflectance (`reflectance` in plates.json × `reflection.strength`, ≤ `maxReflectance`); gloss blur grows with the distance (`blurRate`, 4× vertically), fades out between ½ and `fade` × car height; floor pixels only | `app/pipeline/reflection.py` (`apply_reflection`) |
| 11 Light wrap, premultiplied composite, JPEG export (4:3, 2400–3200 px, quality 92, sRGB, no EXIF/GPS) | `harmonize.py`, `composite.py`, `export.py` |

Orchestration: `app/pipeline/pipeline.py` (`process_photo`). Interior shots
(cockpit, seats, trunk, odometer …) are **not** composited – they are only
re-encoded (orientation, size, sRGB) because a showroom background makes no
sense there.

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
   full frame the coarse vehicle mask swallows small glued objects): per
   BiRefNet pixel the vehicle, another detected object or nothing;
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
5. **missing parts** – the other direction: what the zoomed vehicle instance
   claims beyond the BiRefNet mask (without the instance mask's coarse band, an
   opening of 1.5 mask cells), thicker than 2.5 % and larger than 0.08 % of the
   vehicle width (²), centred in the upper three quarters of the car and covered
   ≥ 60 % by the vehicle instance of a zoom tile centred on it, is a part of the
   vehicle the cut-out lost. Measured on the 57 evaluated masks: exactly the dark
   side mirrors in front of dark parked cars that BiRefNet dropped (thickness
   3.8–4.4 %, cover 0.95–0.99; BiRefNet's own alpha there ≈ 0.01), nothing else
   (the other claims at most 1.5 % thick, or ground under the bumpers in the
   lowest quarter of the car). After a window
   pass (portrait/square photos, below) the part is taken from the whole-frame
   pass when that pass has it – only inside the part's claimed region, only
   solid pixels attached to the car; otherwise the job is rejected
   (`mask_low_confidence`, check `parts`). Nothing is drawn in. Extra BiRefNet
   passes on other framings were tried and are not used: of 14 windows around
   the hard mirror of `front` (squares of 0.25–0.5 × the car width centred on
   it, half the car, the vehicle window – on `front` and two upright crops of
   it) one kept it.

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
`segmentation`). When the window pass lost a vehicle part the detector sees
(step 5 – e.g. both mirrors of a front view, which the whole frame kept), the
whole-frame pass runs as well and the part is taken from it (`merge_parts`,
metadata `segmentation.partsFromWholeFrame`; one more BiRefNet pass, ≈ 25 s,
only for such photos).

**Resources (measured, CPU, one job):**

| Model | File | Peak RAM per running job | Time per job (4 cores) |
| --- | --- | --- | --- |
| `birefnet-general-lite` (default) | 224 MB | **≈ 7 GB** | ≈ 10–50 s (depends on load) |
| `isnet-general-use` | 179 MB | ≈ 1.5 GB | ≈ 4 s |
| `rf-detr-seg-medium` (detector, always on) | 130 MB | + ≈ 0.1–0.3 GB on top of BiRefNet's peak (its session stays loaded; it never runs at the same time as BiRefNet) | typically 9–12 runs × 0.45–0.6 s on idle cores (up to 1.4 s under load) ≈ 5–13 s per job (at most 1 + 8 tiles + 10 verifications per check; measured 19 runs when a missing part is checked twice) |

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
`vehicleAdjustments`, `quality`. Unknown keys, wrong types and out-of-range
values are rejected (`PresetConfigError`), never replaced by defaults. The
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
| `mask_low_confidence` (check `parts`, reason `vehicle_part_missing`) | the vehicle detector sees a part of the vehicle that the cut-out lacks (a dark side mirror in front of a dark car: BiRefNet's alpha there is ≈ 0) – the listing image would show the car without it. Nothing is drawn in; after a window pass the part is first taken from the whole-frame pass if that pass has it. Checked last | Ein Teil des Fahrzeugs (z. B. ein Außenspiegel) hebt sich nicht vom Hintergrund ab und würde im Ergebnis fehlen. Bitte vor ruhigerem Hintergrund neu fotografieren. |

## Run locally

Requires Python 3.12+ (tested with 3.13).

```bash
cd processor
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # optional – edit as needed
.venv/bin/python scripts/download_models.py   # optional, else downloaded on first job
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET http://localhost:8000/health` → `{"status":"ok","version":"2.0.0"}`
(with `Authorization: Bearer <key>` also model, debug, Supabase, showroom
source (`plates`/`missing`) and `showroomMasterError`; `503` if the
segmentation model or the vehicle detector cannot be loaded – `modelLoaded`
covers both, `detector`/`detectorLoaded` name and report the detector). The models are warmed up in the background after start-up. Use `--host 0.0.0.0` only
together with `PROCESSOR_API_KEY`.

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
  messages for the UI. Quality-gate rejections carry `metadata.errorCode`
  (one of the codes above) and `metadata.qualityGate` (all checks).

Other endpoints: `POST /jobs/upload` (multipart), `GET /jobs/{id}/result`,
`GET /jobs/{id}/debug[/{name}]` (debug mode only),
`GET /showroom/{preset}/{shot}.jpg?width=` (branded plate without vehicle,
header `X-Showroom-Source: plates`), `GET /health`.

Security:

- When `PROCESSOR_API_KEY` is set every endpoint except `/health` requires
  `Authorization: Bearer <key>`; the check runs before any request body is
  read (`app/guard.py`). With `SUPABASE_*` configured the service refuses to
  start without a key (the service role bypasses RLS).
- Request bodies are capped (`PROCESSOR_MAX_UPLOAD_MB`, also for chunked
  uploads → `413`); only JPEG, PNG, WebP and HEIC are decoded, up to 80 MP.
- At most 4 upload jobs per worker (they hold the photo in memory) and 200
  jobs in total may wait or run; more → `503` "ausgelastet" (the app retries
  automatically for about a minute).
- `/docs` and `/openapi.json` exist only with `PROCESSOR_DEBUG=true`.

Jobs are kept in memory and on disk for `PROCESSOR_JOB_TTL_HOURS` (expired
jobs are deleted within 10 minutes); a restart
forgets running jobs (prototype), and job folders of earlier runs are deleted
once they are older than the TTL.

## Debugging

With `PROCESSOR_DEBUG=true` every job keeps

`original.jpg`, `mask.png`, `geometry.jpg` (mask, bbox, tyre contacts:
green = plausible; ground model mapped back: projected footprint cyan, floor
line yellow, tyre contact patches red, tyre x-extents green, hidden tyres
magenta; semantic check: detector vehicle box thin magenta, verified regions
red = foreign / orange = kept as vehicle, segmentation window white), `grounding.jpg` (the same over the composite), `vehicle-transparent.png`, `background.jpg` (branded
plate), `composite-before-shadow.jpg`, `shadow.png` (grounding + reflection darkening),
`final.jpg`, `metadata.json`

under `PROCESSOR_DATA_DIR/jobs/<jobId>/debug/`, listed at
`GET /jobs/{id}/debug`. With debug off (default) these files are neither
written nor served. **Never enable debug on an instance reachable from the
internet** (the files contain the full original photo).

`metadata` of an exterior job contains `plateUsed`, `plate` (camera summary,
ground line, target width), `plateSelection`, `geometry` (contacts, near end,
contact rise, aspect), `contacts` (source and output px), `detector`,
`segmentation` (passes, `window`, `windowUsed`, `boxSource`), `mask` (bbox,
coverage, components, `modelFragments`, `detachedDropped`, `foreignRemoved` /
`foreignSuspected` boxes, `vehicleDetected`, `semantic` – the detector's
vehicle (label, score, IoU, box), tiles, runs, ms and every verified region
(`evidence` `other_object`/`unclaimed`/`roof_protrusion`, labels, `verify`
with `vehicleCover`, `foreign`, `action`), `edges` – colour-edge diagnostics such as
`edgeConfidence`, `lowContrastFraction` and `thinProtectedPx`, never used as a
gate), `qualityGate` (every check with its measurements, e.g.
`checks.mask.reasons`, `checks.foreign.foreignSuspected`; a rejected job's
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
busy backgrounds and background objects cut from the vehicle outline.
`busy_background` fires when the segmentation model's OWN output is
fragmented (> 25 parts); BiRefNet returns 1–2 parts on every cached
regression photo, so with it this warning practically never fires (it guards
against a fragmented model output, e.g. with another segmenter).

## Docker

The build context is the repository root (the image bakes in
`public/presets` and `public/brand`):

```bash
docker build -f processor/Dockerfile -t autoexperten-processor .
docker run --rm -p 8000:8000 --env-file processor/.env autoexperten-processor
```

The segmentation model and the vehicle detector are downloaded and
checksum-verified at build time (`--build-arg SEGMENTATION_MODEL=isnet-general-use`
for the alternative segmentation model, `--build-arg DETECTOR_MODEL=…` for
another registered detector), the service runs as a non-root user and stores
jobs in the `/data` volume.

## Deploy (so the Vercel/iPhone app can process real photos)

The model needs a normal server – it never runs on Vercel. Any Linux VM with
Docker, **≥ 12 GB RAM** (BiRefNet ≈ 7 GB per running job; with
`--build-arg SEGMENTATION_MODEL=isnet-general-use` ≈ 4 GB is enough) and a
public host name, e.g. a Hetzner CX42/CPX41.

```bash
git clone <repository> && cd <repository>/processor/deploy
cp ../.env.example ../.env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → PROCESSOR_API_KEY in ../.env
PROCESSOR_DOMAIN=processor.example.com docker compose up -d --build
curl https://processor.example.com/health                         # {"status":"ok",…}
```

`docker-compose.yml` builds the image from the repository root (model, showroom
plates and official logo baked in) and puts Caddy with an automatic
Let's-Encrypt certificate in front of it (`Caddyfile`). The DNS record of
`PROCESSOR_DOMAIN` must point to the server, ports 80/443 open.

Then set in Vercel (Project → Settings → Environment Variables) and redeploy:

| Variable | Value |
| --- | --- |
| `IMAGE_PROCESSOR` | `real` |
| `NEXT_PUBLIC_IMAGE_PROCESSOR` | `real` |
| `IMAGE_PROCESSING_API_URL` | `https://processor.example.com` |
| `IMAGE_PROCESSING_API_KEY` | the same value as `PROCESSOR_API_KEY` |
| `PROCESSING_ACCESS_CODE` | recommended while the app runs in Demo-Modus (public URL without login) |

## Tests

```bash
.venv/bin/python -m pytest
```

Covers API validation and auth, job lifecycle (incl. quality-gate failures
with `errorCode`), plate-set loading/validation, wall-space branding (logo
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
vehicle → warning; a side mirror the mask lost → rejected, the gap under the
car and a clipped edge → not a missing part), the window pass of portrait/square
photos (a mirror the window pass lost is taken from the whole-frame pass, or
the photo is rejected) and the "quer halten" message (upscale rule only), colour-edge matting (no alpha above the model
outside its contour, thin parts such as antenna masts keep the model alpha),
colour guard and hard
limits (navy stays navy), decoding, debug output and the Supabase store (HTTP
mocked). Tests use small synthetic plate sets with exact geometry
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
  accepted unchanged. Not detected: objects the detector neither
  assigns to another class nor leaves outside its vehicle mask (e.g. a grey
  post exactly behind the car's own silhouette, or a car parked right behind
  that the detector merges into the same "car" instance), objects thinner than
  0.9 % of the vehicle width, objects that reach less than 3 mask cells of the
  verifying zoom tile (≈ 1.5–2 % of the vehicle width) beyond the detector's
  vehicle outline – there the coarse outline cannot tell the car's own edge
  from what is behind it (e.g. a thin rail running along the bonnet edge) –
  and – as "unclaimed" regions (no COCO class, e.g. a bollard) – objects in the
  lower half of the car (there the coarse masks cannot tell ground, shadow,
  sills and tyres apart). At most 6 foreign-object candidates (and 4
  missing-part candidates) are verified per photo (the regression photos have
  at most 3). Without a detected vehicle (warning `vehicle_not_detected`) only
  the shape rules apply. The detector costs ≈ 5–13 s per exterior job
  (typically 9–12 runs of 0.45–0.6 s on 4 idle cores, up to 1.4 s each under
  CPU load).
- **Vehicle parts the cut-out loses:** BiRefNet can drop a dark part in front
  of a dark background – the black mirror of `front` in front of the dark car
  parked behind it has an alpha of ≈ 0.01, and 13 of 14 other framings tried
  (zoomed on the mirror, half the car, the vehicle window) lose it as well. The
  detector check (Segmentation, step 5) rejects such a photo instead of
  showing the car without its mirror; after a window pass the part is taken
  from the whole-frame pass when that pass has it (the upright 3:4 crops of
  `front`). This costs retakes: `front` itself is now rejected. Not checked:
  parts thinner than 2.5 % of the vehicle width or centred in the lowest
  quarter of the car, where the coarse instance masks claim ground and shadow
  as well – e.g. the bright chrome side-step tube of the X-Trail against the
  snow: its lower half is missing in the landscape `side_right` and most of it
  in the derived upright frames `side_right_9x16`/`_3x4` (accepted) – the
  detector's vehicle outline runs along the top of the tube as well, so no
  check can see it – or the edge of the indicator lens that the outline clips
  in `old_hardlight` (≈ 1.5 % thick). Landscape photos get no second pass (no
  framing recovered the hard mirror reliably).
- **Portrait photos:** segmented in a window around the vehicle (see
  Segmentation) – BiRefNet then sees the car framed like a landscape photo. A
  portrait photo still gives the car only the short side of the sensor: at
  stream resolution (1080 × 1920), or when the car fills only about half of the
  width, it is rejected with "Für Außenaufnahmen das Smartphone bitte quer
  halten.". There are no real upright car photos in the test sets: the portrait
  cases are frames derived from the landscape photos
  (`scripts/make_portrait_cases.py`) and the audit's composites. Without a
  detected vehicle the window needs a second BiRefNet pass (≈ +25 s), and so
  does a vehicle part the window pass lost (the whole-frame pass, see
  Segmentation).
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
