# AutoExperten processor

Separate image-processing service for **AutoExperten Photo** (Python 3.12+,
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
| 7 Showroom: master photo (or emergency fallback) + deterministic branding layer | `app/presets.py`, `app/showroom/branding.py` |
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
(`widthRatio` 0.80 – 0.82 for 3/4 and side views, 0.60 for front/rear,
`centerX` 0.5, `groundLine` 0.84, overrides for all 8 exterior shots in
`shotPlacement`), the branding layout, shadow and the vehicle adjustment
limits. The code clamps every adjustment
to `HARD_LIMITS` in `app/pipeline/light.py`, so the JSON cannot make the
vehicle correction aggressive.

**Showroom master** – the EMPTY AutoExperten showroom, without any text or
logo:

```
public/presets/autoexperten-standard-showroom.jpg   (3200 × 2400)
```

The current master is AutoExperten's showroom design
(`public/presets/autoexperten-standard-reference.jpg`) with its painted-in
lettering removed by `scripts/prepare_showroom_master.py` (deterministic, no
AI; `background.floorHorizon` = 0.586). A sharper photo of the empty showroom
can replace it at any time (same name; set `floorHorizon`). Requirements,
photographer brief and image-generator prompt: `public/presets/README.md`.
Every exterior vehicle of every brand uses this same background.

**Branding layer** (`app/showroom/branding.py`) – the official logo PNG from
`public/brand/official/` (only scaled, never redrawn), `SCHWETZINGEN`,
`www.autoexperten-rn.de` and `+49 6202 9262357` (bundled Inter font, SIL OFL,
`app/showroom/fonts/`) are composited onto the background at the positions in
the preset's `branding` section – before the vehicle, so they are never drawn
over it. The placement keeps the vehicle roof below the branding
(`branding.clearance`). Preview without a vehicle:
`.venv/bin/python scripts/render_showroom_preview.py -o /tmp/showroom.jpg --guides`
or `GET /showroom/autoexperten_standard.jpg?width=2400`.

**Emergency fallback (not the final design)** – the procedural plate
`public/presets/fallback/autoexperten-standard-fallback.jpg`
(`app/showroom/fallback.py`, `scripts/render_fallback_showroom.py`) is only
used with `PROCESSOR_ALLOW_FALLBACK_SHOWROOM=true` (developers; jobs then carry
`showroom_fallback` / `showroomSource: "fallback"`). Otherwise, while the master
is missing or unreadable, every exterior job (app uploads, Supabase jobs, dev
page) fails with **"AutoExperten Showroom-Master fehlt."** – no fake result is
produced. Interior/detail shots never need the showroom. `/health`
(authenticated) reports `showroomSource`, `showroomMasterError` (master exists
but cannot be read) and `presetError` (the preset JSON is invalid – typos,
wrong types and out-of-range values are rejected with a clear message instead
of silently using defaults).

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

`GET http://localhost:8000/health` → `{"status":"ok","version":"1.0.0"}`
(with `Authorization: Bearer <key>` also model, debug, Supabase and
showroom source (`master`/`fallback`); `503` if the model cannot be loaded). The model
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
master and official logo baked in) and puts Caddy with an automatic
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

Covers API validation and auth, job lifecycle, mask dimensions and
clean-up, placement geometry (width ratio, centre, ground line, aspect
preservation, no cropping), compositing output size and format, colour
guard and hard limits (navy stays navy), decoding, debug output, the
branding layer (official logo colours, positions), master/fallback selection,
the fallback plate and the Supabase store (HTTP mocked). Tests use a
synthetic vehicle and a fake segmenter – no model download needed.

## Known limitations (V1)

- See-through windows keep the original background pixels seen through the
  glass (the vehicle cut-out is not altered there).
- Per-shot placement differs only in width (and the automatic headroom /
  horizon limits); per-shot ground lines can follow.
- Jobs are in-memory (single instance); no batch processing yet.
- The master is upscaled from a 1448 px design image, so the background is
  soft (reads as depth of field); a 3840 × 2880 photo would be sharper.
