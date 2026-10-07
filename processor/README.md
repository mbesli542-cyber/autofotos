# AutoExperten processor

Separate image-processing service for **AutoExperten Photo** (Python 3.11+,
FastAPI, Pillow, OpenCV, ONNX Runtime – CPU only).

It turns one real vehicle photo into an AutoExperten showroom listing photo:
the **original vehicle pixels** are cut out and placed on the fixed
"AutoExperten Standard" showroom plate with a contact shadow. The vehicle is
never regenerated.

> **Truthful-vehicle policy (non-negotiable).** No generative image model
> (DALL·E, Stable Diffusion, Flux, Midjourney, Generative Fill,
> image-to-image …) is used anywhere. The vehicle layer is only resampled
> (to fit the frame) and may receive a tiny exposure / white-balance
> correction within hard limits; paint colour, wheels, badges, lights,
> glass, damage and wear stay exactly as photographed. A colour guard
> measures hue and saturation of the vehicle before/after and drops any
> correction that would shift them (navy stays navy).

## Pipeline (V1)

| Step | Module |
| --- | --- |
| 1 Decode JPEG/PNG/WebP/HEIC, EXIF orientation, ICC → sRGB | `app/pipeline/decode.py` |
| 2 Vehicle segmentation (`VehicleSegmenter` → alpha mask) | `app/pipeline/segmentation.py` |
| 3 Full-resolution alpha (guided filter), clean-up, hole filling, quality warnings | `app/pipeline/mask.py` |
| 4 Cut-out in linear light, edge decontamination | `app/pipeline/composite.py`, `harmonize.py` |
| 5 Conservative light matching with colour guard | `app/pipeline/light.py` |
| 6 Placement from the mask bounding box (no distortion) | `app/pipeline/placement.py` |
| 7 Showroom plate (master image or placeholder) | `app/presets.py`, `app/showroom/` |
| 8 Contact + ambient shadow derived from the mask | `app/pipeline/shadow.py` |
| 9 Light wrap, premultiplied composite | `app/pipeline/harmonize.py`, `composite.py` |
| 10 JPEG export: 4:3, 2400–3200 px, quality 92, sRGB, no EXIF/GPS | `app/pipeline/export.py` |

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
default `../public`). It defines output size/quality, placement
(`widthRatio` 0.78, `centerX` 0.5, `groundLine` 0.84, per-shot overrides),
shadow and the vehicle adjustment limits. The code clamps every adjustment
to `HARD_LIMITS` in `app/pipeline/light.py`, so the JSON cannot make the
vehicle correction aggressive.

**Showroom master image** – put the final photo of the empty AutoExperten
showroom (4:3, at least 3200×2400, no vehicle, tyres-line area free) at

```
public/presets/autoexperten-standard-showroom.jpg
```

and set `"placeholder": false` in `public/presets/autoexperten-standard.json`
(`background`). The wall/floor junction of the photo should be at about
`floorHorizon` (0.62 of the height). Nothing else needs to change. Until
then a deterministic, procedurally rendered placeholder showroom is used
(`app/showroom/placeholder.py`, regenerate the JPG with
`python scripts/render_showroom_placeholder.py`) and every job carries the
warning `showroom_placeholder`.

**Branding** – the official AutoExperten logo files live in
`public/brand/official/` (unchanged originals from autoexperten-rn.de);
see `public/brand/README.md`.

## Run locally

```bash
cd processor
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # optional – edit as needed
.venv/bin/python scripts/download_models.py   # optional, else downloaded on first job
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET http://localhost:8000/health` → `{"status":"ok","version":"1.0.0"}`
(with `Authorization: Bearer <key>` also model, debug, Supabase and
showroom-placeholder details; `503` if the model cannot be loaded). The model
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
  messages for the UI.

Other endpoints: `POST /jobs/upload` (multipart), `GET /jobs/{id}/result`,
`GET /jobs/{id}/debug[/{name}]` (debug mode only), `GET /health`.

Security:

- When `PROCESSOR_API_KEY` is set every endpoint except `/health` requires
  `Authorization: Bearer <key>`; the check runs before any request body is
  read (`app/guard.py`). With `SUPABASE_*` configured the service refuses to
  start without a key (the service role bypasses RLS).
- Request bodies are capped (`PROCESSOR_MAX_UPLOAD_MB`, also for chunked
  uploads → `413`); only JPEG, PNG, WebP and HEIC are decoded, up to 80 MP.
- At most 4 jobs per worker may wait or run; more → `503` "ausgelastet".
- `/docs` and `/openapi.json` exist only with `PROCESSOR_DEBUG=true`.

Jobs are kept in memory and on disk for `PROCESSOR_JOB_TTL_HOURS`; a restart
forgets running jobs (prototype), and job folders of earlier runs are deleted
once they are older than the TTL.

## Debugging

With `PROCESSOR_DEBUG=true` every job keeps

`original.jpg`, `mask.png`, `vehicle-transparent.png`, `background.jpg`,
`composite-before-shadow.jpg`, `shadow.png`, `final.jpg`, `metadata.json`

under `PROCESSOR_DATA_DIR/jobs/<jobId>/debug/`, listed at
`GET /jobs/{id}/debug`. With debug off (default) these files are neither
written nor served. **Never enable debug on an instance reachable from the
internet** (the files contain the full original photo).

`metadata` of a job contains the mask coverage/bbox, placement (scale,
limiting factor), the vehicle correction that was applied (exposure EV,
white-balance gains, measured hue/saturation change, guard result) and
timings. Quality warnings (German) flag cropped vehicles, tiny vehicles,
uncertain masks, busy backgrounds and low-resolution sources.

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

## Tests

```bash
.venv/bin/python -m pytest
```

Covers API validation and auth, job lifecycle, mask dimensions and
clean-up, placement geometry (width ratio, centre, ground line, aspect
preservation, no cropping), compositing output size and format, colour
guard and hard limits (navy stays navy), decoding, debug output, the
placeholder showroom and the Supabase store (HTTP mocked). Tests use a
synthetic vehicle and a fake segmenter – no model download needed.

## Known limitations (V1)

- See-through windows keep the original background pixels seen through the
  glass (the vehicle cut-out is not altered there).
- One placement standard for all exterior shots (per-shot width overrides in
  the preset); per-shot ground lines can follow.
- Jobs are in-memory (single instance); no batch processing yet.
- The showroom background is a placeholder until the real master photo is
  supplied.
