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
| 2 Vehicle segmentation (`VehicleSegmenter` → alpha mask) | `app/pipeline/segmentation.py` |
| 3 Alpha: the model alpha (up-sampled, mild choke) snapped onto real colour edges by a colour guided filter on the edge band – it may tighten the cut-out but never grow it beyond the model's own contour, and thin parts of the model's mask (antenna masts, mirror arms) keep at least the model alpha; edge confidence goes to the metadata only. Clean-up: the vehicle plus parts close to it (mirror heads, antenna pieces), foreign "spikes" (a traffic cone or post standing behind the car, merged into its outline below the roofline) cut from the outline (alpha only, anti-aliased cut edge, warning) – a spike whose cut line is not certain is left uncut and the job is rejected – window holes filled, bbox | `app/pipeline/matting.py`, `mask.py` |
| 4 Vehicle geometry: tyre contacts (lower hull + narrow tyre "foot" + dark neutral rubber above it in the source photo – a lower, body-coloured bumper is no tyre; width and near/far side per tyre), near end (3/4), contact rise, aspect | `app/pipeline/vehicle_geometry.py` |
| 5 Plate selection: the shot's plate; a 3/4 photo of the other side gets the mirrored plate of its front/rear group (`plateUsed` + warning) | `app/pipeline/pipeline.py` (`select_plate`) |
| 6 Placement v2: plate target width, centred, lowest tyre contact on the plate's ground line; limited by height, margins, branding headroom, floor | `app/pipeline/placement.py` |
| 7 Quality gate (cropped, too small/upscale, mask, perspective, ground contact) | `app/pipeline/quality.py` |
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
`python scripts/download_models.py`) and verified against a pinned SHA-256.

**Resources (measured, CPU, one job):**

| Model | File | Peak RAM per running job | Segmentation time (4 cores) |
| --- | --- | --- | --- |
| `birefnet-general-lite` (default) | 224 MB | **≈ 7 GB** | ≈ 10–50 s (depends on load) |
| `isnet-general-use` | 179 MB | ≈ 1.5 GB | ≈ 4 s |

Plan a server with at least 12 GB RAM for BiRefNet and keep
`PROCESSOR_CONCURRENCY=1` (each extra parallel job needs another ≈ 7 GB).
The rest of the pipeline adds a few seconds and well under 1 GB. This is why
the processor **does not run on Vercel** – deploy it on a VM/container (see
Docker below).

To add another backend implement `VehicleSegmenter` and register it in
`create_segmenter()`.

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
| `source_resolution_too_low` | long edge < `minSourceLongEdge` (1600), or the vehicle needs more than `maxUpscale` (1.5×) although it fills ≥ `minVehicleWidthRatio` (45 %) of the photo | Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen. |
| `vehicle_cropped` | solid vehicle touches the left/right/bottom border (top: over ≥ 15 % of its width) | Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren. |
| `vehicle_too_small` | needs more than `maxUpscale` and covers < 45 % of the photo width | Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren. |
| `mask_low_confidence` | soft-edge fraction, split parts, coverage or fill ratio out of range, or a background object (cone, post) detected in the outline that could not be cut with a certain cut line (reason `foreign_object`) | Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen. |
| `perspective_mismatch` | bbox aspect far from the plate's proxy car; a much steeper floor (contact rise > `maxRiseFactor` × the proxy's); or a photo from above: rise > `highViewRiseFactor` (1.7×) together with a tall bbox (aspect < `highViewAspectFactor`, 0.8×) | Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren. |
| `ground_contact_uncertain` | 3/4 and side shots with fewer than 2 plausible tyre contacts | Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein. |

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
source (`plates`/`missing`) and `showroomMasterError`; `503` if the model
cannot be loaded). The model
is warmed up in the background after start-up. Use `--host 0.0.0.0` only
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
magenta), `grounding.jpg` (the same over the composite), `vehicle-transparent.png`, `background.jpg` (branded
plate), `composite-before-shadow.jpg`, `shadow.png` (grounding + reflection darkening),
`final.jpg`, `metadata.json`

under `PROCESSOR_DATA_DIR/jobs/<jobId>/debug/`, listed at
`GET /jobs/{id}/debug`. With debug off (default) these files are neither
written nor served. **Never enable debug on an instance reachable from the
internet** (the files contain the full original photo).

`metadata` of an exterior job contains `plateUsed`, `plate` (camera summary,
ground line, target width), `plateSelection`, `geometry` (contacts, near end,
contact rise, aspect), `contacts` (source and output px), `mask` (bbox,
coverage, components, `modelFragments`, `detachedDropped`, `foreignRemoved` /
`foreignSuspected` boxes, `edges` – colour-edge diagnostics such as
`edgeConfidence`, `lowContrastFraction` and `thinProtectedPx`, never used as a
gate), `qualityGate` (every check with its measurements, e.g.
`checks.mask.reasons` and `foreignSuspected`), `placement` (scale, limiting rule,
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

The segmentation model is downloaded and checksum-verified at build time
(`--build-arg SEGMENTATION_MODEL=isnet-general-use` for the alternative),
the service runs as a non-root user and stores jobs in the `/data` volume.

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
spoilers kept, detached blobs), colour-edge matting (no alpha above the model
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
- **Background objects merged into the outline:** only slender objects
  standing behind the car below its roofline (traffic cone, bollard, post up to
  ~6 % of the vehicle width) are cut, along the chord between the points where
  the car's outline meets them – a few pixels of the object can remain at that
  chord (e.g. a light sliver of a cone stripe). When the cut line is not certain
  nothing is cut and the job is rejected (`mask_low_confidence`, reason
  `foreign_object`). NOT handled – they stay in the cut-out, and such a job is
  currently accepted WITHOUT a warning: objects glued to the side of the
  outline (another car's hubcap at the end of a side view, a neighbour car's
  wheel next to a headlight, a bollard stripe right behind a mirror – 3 of
  the 12 accepted regression photos), wide objects (a parking meter, a person)
  and poles reaching above the roofline. On such pixels the segmentation
  model is less certain than on the body (≈ 0.999 instead of ≥ 0.99998), but
  just as uncertain as on real mirrors, roof rails, fins, mudflaps, tow
  hitches and damaged bumper parts, and their shape is a bump like a mirror or
  a spare wheel – neither tells them from vehicle parts without risking real
  parts. A reliable fix needs another signal (e.g. a car instance
  segmentation as a second opinion, or a manual check of the result).
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
