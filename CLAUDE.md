# CLAUDE.md – AutoExperten Photo

Read this file before working on the repository.

@AGENTS.md

## Project

**AutoExperten Photo** – internal vehicle photography workflow for
**AutoExperten Schwetzingen** (www.autoexperten-rn.de, +49 6202 9262357).

Employees photograph vehicles for online listings. The app guides them
through a fixed sequence of 15 shots (guided camera with framing overlays),
lets them review/retake photos, mark the set complete, and later process the
photos into the AutoExperten showroom style.

Workflow: Login → Fahrzeuge → Neues Fahrzeug → Geführte Kamera (15 Fotos) →
Fotos überprüfen → Foto wiederholen → Aufnahmen abschließen → Fotos bearbeiten.

## Development principles

- **Mobile first.** Phones (iPhone/Android) first, then tablet, then desktop.
  The camera is designed for portrait use and adapts to landscape.
- **German UI.** Every user-facing text is German. Code identifiers stay English.
  Never show raw technical errors – use `toUserMessage()` (`src/lib/errors.ts`).
- **Clean, professional design.** Dark graphite background, dark-gray cards,
  AutoExperten blue (`#0A7BFF`, Tailwind `ae-blue`) for primary actions.
  Theme tokens live in `src/app/globals.css` (`@theme`).
- **Originals are never overwritten.** Every capture gets its own photo id and
  storage path. A retake inserts a new photo and archives the old one
  (`archived_at`). DB trigger + storage policies enforce this. The newest
  capture (`taken_at`) stays active: an older capture that arrives late (a
  queued upload after a retake, a second device) is stored archived. One rule
  for `add_vehicle_photo`, `MockDataProvider.savePhoto` and the UI
  (`src/lib/photos/capture-order.ts`: a saved photo counts as taken at most
  5 minutes after it was saved; ties → the new capture wins).
- **The vehicle must stay visually truthful.** Never regenerate or recreate the
  vehicle with generative AI (paint colour, wheels, badges, headlights, body,
  equipment, scratches, damage, wear, interior). Processing may only replace
  the background, add shadow/branding and harmonise light around the
  original vehicle pixels.
- **Processing results are separate files** (`vehicle-processed` bucket,
  `processed_storage_path`). Never write into originals.
- **Guided photography is the core feature.** Protect its UX: current shot,
  progress and remaining photos must always be visible; one tap to capture.
- **Avoid unnecessary complexity.** No new frameworks/state libraries without a
  strong reason. Do not build payments, CRM, pricing, public registration etc.
- **Keep components reusable** and screens thin (`src/features/*` compose
  `src/components/*`; business rules live in `src/lib/*` as pure functions).
- **The processing API must stay easy to connect**: the UI only talks to
  `/api/processing-status`, `/api/process-photo` (Supabase), `/api/process-upload`
  (demo) and `/api/process-job/:jobId[/result]`; implementations sit behind the
  `ImageProcessor` interface (`RealImageProcessor` → `processor/`, the only code
  that talks HTTP to the processor).
- **No fake processing.** "Bearbeitet" only shows real processor results.
  Without a connected processor nothing is processed or saved ("Echte
  Showroom-Bearbeitung ist noch nicht verbunden."); never fall back to the
  original or add bars/overlays to stand in for a result.
- **No generative image AI in processing** (no DALL·E, Stable Diffusion, Flux,
  Midjourney, Generative Fill, image-to-image). The processor composites the
  ORIGINAL vehicle pixels; vehicle corrections stay within `HARD_LIMITS` and
  the colour guard in `processor/app/pipeline/light.py`. Never loosen them.
  Lighting harmonisation is only that guarded exposure/WB correction plus the
  light wrap; see-through windows and outdoor reflections in the paint stay
  (vehicle pixels – window/relight treatments were evaluated and removed).
  Matting (`matting.py`) may tighten the model's outline, never grow it, and
  never thins thin parts (antenna masts, mirror arms) below the model alpha.
  BiRefNet stays the matting model. The vehicle detector (RF-DETR-Seg,
  `processor/app/pipeline/detection.py`, Apache-2.0, pinned revision +
  SHA-256) is a **semantic check only**: it picks the segmentation window of
  portrait/square photos and tells which mask regions are the vehicle, another
  object or a lost vehicle part – it never draws the cut line and never
  generates or changes a pixel. A lost part may only be taken from the
  whole-frame BiRefNet pass, never drawn in.
- **One fixed 3D showroom, eight angle-specific plates (processor/showroom3d).**
  One physical Blender room (`processor/showroom3d/render_plates.py`, CC0
  assets, modelled after `public/presets/autoexperten-standard-reference.jpg`)
  is rendered from one camera per exterior shot into
  `public/presets/autoexperten-standard/` (`<shot>.jpg` empty plate without
  branding, `<shot>-shadow.png` proxy-car floor shadow, `<shot>-reflection.png`
  the floor's own reflection, `plates.json` camera + floor/wall homographies +
  proxy car). Each photo goes onto the plate of its
  angle (a mirrored 3/4 photo onto the partner plate); plates are never
  generated per photo. Without a complete, valid plate set exterior jobs fail
  with "AutoExperten Showroom-Master fehlt." – there is no fallback background.
  The official logo/texts are composited in wall space through the plate's
  wall homography (`processor/app/showroom/wall_branding.py`, layout in the
  JSON `branding`, fractions of the brand wall).
- **Quality gate instead of bad results.** Photos that would give a bad
  listing image (too small, cropped, wrong perspective, unclear mask/tyres,
  a foreign object glued to the outline, a lost vehicle part such as a
  mirror) are rejected with exact German retake messages – never repaired
  (`processor/app/pipeline/quality.py`, thresholds in the JSON `quality`; app:
  `src/lib/processing/quality-gate.ts`). A smaller, partly lost piece (e.g. a
  mirror base) is never drawn in either: the result gets the "check" hint
  `vehicle_part_uncertain`. Keep the thresholds generous for normal phone
  photos. Portrait/square exterior photos are segmented in a window around
  the detected vehicle; if their car would need too much upscaling they get
  "Für Außenaufnahmen das Smartphone bitte quer halten."
- **Official logo only** (`public/brand/official/`); never redraw or
  approximate it.

## Architecture map

| Area | Location |
| --- | --- |
| Shot standard (15 shots, overlays, instructions) | `src/lib/shots/shot-template.ts` |
| Shot completion / ordering / navigation (pure) | `src/lib/shots/shot-progress.ts` |
| Vehicle validation & status rules (pure) | `src/lib/vehicles/` |
| File & storage naming (`AE_{ref}_{nn}_{shot}.jpg`) | `src/lib/naming/file-naming.ts` |
| Backend contracts (`DataProvider`, `AuthService`) | `src/lib/data/types.ts` |
| Demo mode (IndexedDB) | `src/lib/data/mock/` |
| Supabase implementation | `src/lib/data/supabase/` |
| Status-syncing workflow operations | `src/lib/workflow/vehicle-workflow.ts` |
| Offline-tolerant upload queue (refused files, "Verwerfen") | `src/lib/offline/upload-queue.ts` |
| Photo slots, capture-order rule (shared with `add_vehicle_photo`) | `src/lib/photos/photo-slots.ts`, `src/lib/photos/capture-order.ts` |
| "Fahrzeugdaten" save without overwriting changes made elsewhere | `saveVehicleData` in `src/lib/workflow/vehicle-workflow.ts` |
| Camera (stream, capture, picked-file check, quality-check interface) | `src/lib/camera/` (`photo-file.ts`: JPG/PNG/WebP/HEIC ≤ 50 MB), `src/components/camera/` |
| Processing contract, config, presets, real processor, client, adapters | `src/lib/processing/` |
| Processing runs (per vehicle, outside the screen), start blockers, processor hints | `src/lib/processing/processing-runs.ts`, `run-blockers.ts`, `processing-warnings.ts`, `src/components/processing/ProcessingWarningList.tsx` |
| Data backend decision (`NEXT_PUBLIC_DATA_BACKEND`) | `src/lib/data/backend-mode.ts` |
| Demo data migrations (IndexedDB) | `src/lib/data/mock/migrations.ts` |
| API routes | `src/app/api/processing-status`, `process-photo`, `process-upload`, `process-job/[jobId]` (+ `/result`) |
| Processing access (login / demo access code), error mapping | `src/lib/api/processing-access.ts`, `src/lib/api/processing-errors.ts` |
| Client service container | `src/lib/app-services.ts` |
| DB schema, RLS, storage policies (apply the files in file-name order) | `supabase/migrations/`, static guard `src/lib/data/supabase/migrations.test.ts` |
| Brand config / official logo | `src/config/brand.ts`, `src/components/brand/BrandLogo.tsx`, `public/brand/` |
| Showroom preset, 3D plates, branding layer | `public/presets/` (README = plates + rendering), `processor/showroom3d/`, `processor/app/showroom/` (`plates.py`, `wall_branding.py`, `branding.py`) |
| Image processor (Python/FastAPI) | `processor/` (see `processor/README.md`) |
| Processor pipeline steps | `processor/app/pipeline/` (decode, detection, segmentation, matting, mask, vehicle_geometry, placement, quality, light, grounding/shadow, reflection, composite, export; debug allow-list `debug.py`) |
| Processor scripts | `processor/scripts/` (`download_models.py` – both models, SHA-256; `make_portrait_cases.py` – derived portrait test frames; `visual_regression.py`; `render_showroom_preview.py`) |
| Dev test page (dev only) | `src/app/dev/processing-test`, `src/app/api/dev/`, `src/lib/dev-tools.ts` |

Routes: `/login`, `/fahrzeuge`, `/fahrzeuge/neu`, `/fahrzeuge/[id]`,
`/fahrzeuge/[id]/kamera`, `/fahrzeuge/[id]/fotos`, `/fahrzeuge/[id]/bearbeiten`,
`/fahrzeuge/[id]/daten`, `/kamera`, `/bearbeiten`, `/mehr`;
dev only (404 in production unless `ENABLE_DEV_TOOLS=true`): `/dev/processing-test`.

## Conventions & gotchas

- Next.js 16 (App Router) with **Cache Components** enabled. Dynamic params
  must be read inside `<Suspense>`; routes stay mounted (React Activity) when
  navigating away, so effects re-run when a page becomes visible again.
  `middleware` is now `src/proxy.ts`.
- Client data access happens only in effects/handlers via `getAppServices()`
  (never during render – it is browser-only). Render-safe helpers:
  `getBackendMode()`, `getShotTemplate()`.
- React Compiler lint rules are on (`react-hooks/set-state-in-effect`,
  `refs`, `purity`): set state in async callbacks, not synchronously in effects.
- Always order photos by the shot template (`sortPhotosByShotOrder`), never by
  time.
- Demo mode is automatic when `NEXT_PUBLIC_SUPABASE_URL`/key are missing or
  empty (or forced with `NEXT_PUBLIC_DATA_BACKEND=demo`). The data backend and the
  image processor (`IMAGE_PROCESSOR=mock` = none connected, `real`) are decided
  independently – demo mode can use the real processor.
- Processing adapters (`src/lib/processing/photo-processing.ts`): Supabase sends
  ids (processor stores the result, `kind: "stored"`); demo uploads a downscaled
  copy (≤ 3200 px, < 4 MB for Vercel) via `/api/process-upload`, downloads
  `/api/process-job/:id/result` (`kind: "file"`) and saves it in IndexedDB.
  Public demo deployments must set `PROCESSING_ACCESS_CODE`.
- Logo: official assets in `/public/brand/` (`LOGO_ASSETS`); see `public/brand/README.md`.
- Processor: settings come from env / `processor/.env` (`app/config.py`);
  `PROCESSOR_DEBUG=true` writes debug images (and serves `/docs`) – never on
  public instances. The processor does not run on Vercel (BiRefNet model
  224 MB, ≈ 7 GB peak RAM per job; detector + 0.1–0.3 GB, ≈ 4–12 s). The
  Docker image sets `PROCESSOR_REQUIRE_API_KEY=true` – no start without
  `PROCESSOR_API_KEY`; plain `uvicorn` works without a key. Publish it on
  `127.0.0.1` locally.
- Processor decoding (`decode.py`, Pillow ≥ 13): headers are checked before
  Pillow parses a file (size, chunk/segment floods); animated PNG/WebP are
  refused; single-scan sequential JPEGs of 80–320 MP are decoded at 1/2, other
  photos above 80 MP fail with `image_too_large` (a retake code in the app).
  New preset fields are validated in `app/presets.py` (never defaulted); a new
  debug image must be listed in `DEBUG_FILE_NAMES` (`app/pipeline/debug.py`).
- Database: apply new migrations before deploying the app version that needs
  them; a new migration must pass `migrations.test.ts` (no SECURITY DEFINER
  beyond the membership helpers, policies only `to authenticated`, no grants to
  `anon`/`public`, original-protection trigger kept, no app column dropped).
  The app tolerates a missing `processing_warnings` column.

## Commands

```bash
npm run dev        # dev server (http://localhost:3000)
npm run dev:https  # dev server with HTTPS (camera on other devices in the LAN)
npm run lint
npm run typecheck  # next typegen + tsc
npm test           # vitest
npm run build
npm run check      # all of the above
```

Processor (from `processor/`):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m app.cli photo.jpg -o /tmp/out.jpg --debug-dir /tmp/dbg
docker build -f processor/Dockerfile -t autoexperten-processor .   # from repo root
docker run --rm -p 127.0.0.1:8000:8000 --env-file processor/.env autoexperten-processor   # key in the file
```

Node.js 22.13+ or 24 (`engines.node`). Run `npm run check` (and the
processor tests when touching `processor/`) before committing.
