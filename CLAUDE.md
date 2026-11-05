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
  ORIGINAL vehicle pixels. Vehicle light corrections: without
  `vehicleAdjustments.studioLight` a preset gets the conservative correction
  (`HARD_LIMITS` + colour guard in `processor/app/pipeline/light.py`: ±0.25 EV,
  white balance ±2 %, no contrast). **Dealer decision (2026-11-05):** the
  shipped preset (his studio) and the dealer variants enable
  `vehicleAdjustments.studioLight` (every field written out) – a Lightroom-like
  GLOBAL light match towards the studio plate: exposure
  towards the studio's key light (0 … +1 EV; the deepest blacks keep their
  level), contrast only ABOVE the car's median (≤ 0.3, back to 0 at white),
  highlight shoulder (knee ≤ 0.95, highlights keep ≥ 40 % of their detail,
  nothing new clips), shadow lift (≤ 0.5 EV), white balance = colour temperature
  only towards the studio (≤ ±8 % per channel; the car's green–magenta tint stays;
  reference = glass/tyres/trim outside the paint, never the paint). The tone part
  never darkens a pixel. It is ONE global mapping of the original pixel values
  (tone part ratio-preserving: hue and saturation of every pixel unchanged) – no
  spatial or local operation, no relighting model. The paint guard (measured on
  the paint = large uniform body areas, before/after) scales it back
  automatically: paint hue ≤ 2.5° (max 3°) and saturation ±10 % on EVERY paint
  pixel with C* ≥ 5 (chroma-weighted; near-neutral tinted paint such as grey-blue
  or sage included; "not measured" is reported as null, never 0), near-neutral
  paint colour shift in a*b* ≤ 1.5 ΔE (max 2, exposure-normalised), tone class
  (median and black point L* of black/very dark paint +4 (max 5), from mid tones
  +6 (max 10)), paint shading ≥ 90 % (bright panels ≥ 85 %), the car's darkest
  2 % ≤ +2 L*, ≤ 0.2 % newly clipped pixels (max 0.3 %), highlight detail
  ≥ 55 %. These maxima (`STUDIO_LIGHT_RANGES`, `app/presets.py`) are the dealer's
  limits – never loosen them further, never add local retouching (damage,
  scratches, dirt, badges, reflections stay; mobile.de: photos show the actual
  condition).
  Beyond that, lighting harmonisation is only the light wrap; see-through
  windows and outdoor reflections in the paint stay (vehicle pixels –
  window/relight treatments were evaluated and removed).
  Matting (`matting.py`) may tighten the model's outline, never grow it, and
  never thins thin parts (antenna masts, mirror arms) below the model alpha.
  BiRefNet stays the matting model. The vehicle detector (RF-DETR-Seg,
  `processor/app/pipeline/detection.py`, Apache-2.0, pinned revision +
  SHA-256) is a **semantic check only**: it picks the segmentation window of
  portrait/square photos and tells which mask regions are the vehicle, another
  object or a lost vehicle part – it never draws the cut line and never
  generates or changes a pixel. A lost part may only be taken from the
  whole-frame BiRefNet pass, never drawn in.
- **One fixed showroom, eight angle-specific plates: the dealer's own studio
  (processor/showroom3d/dealer_*.py).** The shipped plate set
  `public/presets/autoexperten-haendler/` is the dealer's own showroom image
  (`processor/showroom3d/dealer/haendler_hintergrund.png`, supplied by him, made
  with an image AI; his AI lettering removed by `dealer_clean.py`, upscaled ONCE
  with Lanczos – a fixed asset, never regenerated): ONE `plate.jpg` for all
  eight exterior shots, one fixed level studio camera, the car turned per shot
  (`dealer_layout.py`); per shot a `<shot>-shadow.png` (Blender shadow pass of a
  proxy car at that pose, `render_dealer_passes.py`), one `reflection.png` (the
  floor's own reflection measured on the plate), `plates.json` (camera +
  floor/wall homographies + proxy car per shot), `branding.json`. Each photo goes
  onto the entry of its angle (a mirrored 3/4 photo onto the partner entry);
  plates are never generated per photo. Without a complete, valid plate set
  exterior jobs fail with "AutoExperten Showroom-Master fehlt." – there is no
  fallback background. The official logo + "Schwetzingen" between two thin
  rules are composited in wall space where his lettering was
  (`processor/app/showroom/wall_branding.py`, layout in the JSON `branding`,
  fractions of the brand wall). The shipped preset `autoexperten-standard.json`
  is `variants/autoexperten-haendler.json` ("Schatten mittel"; `-leicht` /
  `-kraeftig` are the alternatives) with the standard name. The earlier Blender
  room (`processor/showroom3d/render_plates.py`, modelled after
  `public/presets/autoexperten-standard-reference.jpg`) is tooling only – its
  eight plates are no longer shipped (they are in the git history, 990d9f7).
- **Quality gate instead of bad results.** Photos that would give a bad
  listing image (too small, cropped, wrong perspective, unclear mask/tyres,
  a foreign object glued to the outline, a lost vehicle part such as a
  mirror) are rejected with exact German retake messages – never repaired
  (`processor/app/pipeline/quality.py`, thresholds in the JSON `quality`; app:
  `src/lib/processing/quality-gate.ts`). A smaller, partly lost piece (e.g. a
  mirror base) is never drawn in either: the result gets the "check" hint
  `vehicle_part_uncertain`; implausible tyre contacts on an accepted photo
  (e.g. an exhaust taken as a tyre) get the "check" hint
  `ground_contact_uncertain` (the same code as a failed job's `errorCode` is the
  retake). Keep the thresholds generous for normal phone photos. The
  perspective factors are relative to the plate's proxy car: a new plate
  camera needs them re-scaled to the same absolute thresholds (dealer studio:
  `highViewRiseFactor` 1.42, `highViewAspectFactor` 0.88 – with 1.7/0.8 a photo
  from a window above passed). Portrait/square exterior photos are segmented in a window around
  the detected vehicle; if their car would need too much upscaling they get
  "Für Außenaufnahmen das Smartphone bitte quer halten."
- **Official logo only** (`public/brand/official/`); never redraw or
  approximate it.
- **No dealer contact data in processed photos** (phone, e-mail, web address):
  mobile.de's dealer terms (AGB-HB, listings no. 9/14) forbid it. The brand
  wall shows the logo + "Schwetzingen" only (`website`/`phone` texts empty;
  guarded by `test_listing_photos_carry_no_dealer_contact_data`).

## Architecture map

| Area | Location |
| --- | --- |
| Shot standard (15 shots, overlays, instructions) | `src/lib/shots/shot-template.ts` |
| Shooting guide ("Tipps für perfekte Fotos" – by itself once per device and again after a sign-out, from the light bulb with the current shot's hint first; per-shot `tip` in the header's status line until the shot is captured – the instruction box holds only the instruction, as wide as the stage, above the guide for exterior shots and below it for interior/detail shots; "Bitte Smartphone quer halten" for exterior shots in portrait, briefly in the header's status line, then in the instruction box) | `src/lib/shots/shooting-guide.ts`, `src/components/camera/ShootingTips.tsx`, `InstructionPanel.tsx` |
| Shot completion / ordering / navigation (pure) | `src/lib/shots/shot-progress.ts` |
| Vehicle validation & status rules (pure) | `src/lib/vehicles/` |
| File & storage naming (`AE_{ref}_{nn}_{shot}.jpg`) | `src/lib/naming/file-naming.ts` |
| Export "Alle Fotos speichern" (listing set + summary – rejected shots by the check rule below, only with "trotzdem mitspeichern" –, share sheet in parts / ZIP, per-photo "Speichern"; unique "Interne Fahrzeugnummer", untouched demo samples never block one) | `src/lib/export/` (`listing-export.ts`, `device-save.ts`, `zip.ts` store-only ZIP writer, `zip-blob.ts`, `export-files.ts`), `src/components/export/`, `src/lib/vehicles/reference.ts`, `DataProvider.getPhotoFile` |
| Backend contracts (`DataProvider`, `AuthService`) | `src/lib/data/types.ts` |
| Demo mode (IndexedDB) | `src/lib/data/mock/` |
| Supabase implementation | `src/lib/data/supabase/` |
| Status-syncing workflow operations | `src/lib/workflow/vehicle-workflow.ts` |
| Offline-tolerant upload queue (refused files, "Verwerfen") | `src/lib/offline/upload-queue.ts` |
| Failed-upload texts (storage full, captures kept in memory only) | `src/lib/offline/upload-status.ts` |
| Demo-mode storage safety (`persist()`, storage full → `storage_full`, "Fahrzeug löschen" – also its runs, checks and kept jobs –, "Alle Daten auf diesem Gerät löschen" with typed "LÖSCHEN", sample vehicles removed with the first real one) | `src/lib/offline/persistent-storage.ts`, `src/lib/workflow/device-data.ts`, `src/lib/data/mock/seed.ts`, `src/lib/errors.ts` |
| Photo slots, capture-order rule (shared with `add_vehicle_photo`) | `src/lib/photos/photo-slots.ts`, `src/lib/photos/capture-order.ts` |
| "Fahrzeugdaten" save without overwriting changes made elsewhere | `saveVehicleData` in `src/lib/workflow/vehicle-workflow.ts` |
| Camera (stream, capture, picked-file check, quality-check interface) | `src/lib/camera/` (`photo-file.ts`: JPG/PNG/WebP/HEIC ≤ 50 MB), `src/components/camera/` |
| Processing contract, config, presets, real processor, client, adapters | `src/lib/processing/` |
| Processing runs (per vehicle, outside the screen; kept on the device and resumed after a reload by the same user only; screen wake lock while running), start blockers, processor hints | `src/lib/processing/processing-runs.ts`, `stored-runs.ts`, `run-blockers.ts`, `processing-warnings.ts`, `src/lib/device/screen-wake-lock.ts`, `src/components/processing/ProcessingWarningList.tsx` |
| Instant per-photo check in the camera (auto-processing after a capture is saved, green/red shot segments, interior/detail only "Gespeichert"; one job per photo shared with runs; page open checks only photos without a check, result or stored rejection – `checkOnOpen` –, never resends a failed one; 60 s availability poll while the Showroom-Rechner is `unreachable`, none while `no_processor`) | `src/lib/processing/photo-checks.ts`, `src/hooks/use-photo-checks.ts`, `src/components/camera/AutoCheckInfo.tsx` |
| Check/run persistence on the device (submitted jobs per user, polled again after a reload, 404 → resubmitted once; rejections and failed checks; running runs) – forgotten per vehicle by "Fahrzeug löschen", all by sign-out (`endProcessingSession`) and the device reset | `src/lib/processing/job-journal.ts`, `stored-runs.ts`, `photo-checks.ts` (`stopVehicle`, `clear`, `stopAll`) |
| Which check state a shot shows – ONE rule for camera, review, vehicle page, lists/Kamera tab and export: check in progress > a rejection on this device that came after the photo's still unchanged result ("Erneut prüfen", `afterResult` = the photo's `updatedAt`, `rejectsSavedResult`) > a result (saved by this tab's check, else with the photo) > the rejection kept with the photo (`rejected:<code>` in `processing_warnings` while it has no result, written by `processPhoto` via `DataProvider.recordPhotoRejection`, known on every device) > the outcome kept on this device; one wording (`SHOT_CHECK_LABELS`) | `src/lib/processing/shot-check.ts` (`getShotCheckState`), `processing-warnings.ts` (`storedRejection`) |
| Camera header (step, title, progress, "noch N", chips for failed/refused uploads, storage full, retakes, "Prüfung aus" – one row with the counts, icon + number when there are several, 24 px high with 44 px tap areas –, and the ONE status line for check results, "Noch N Fotos neu aufnehmen", "Speicher voll", "Bitte Smartphone quer halten", the shot's state or its "Tipp" – nothing on the framing guide); next red shot after a retake; the shot shown is kept in `history.state`, so a reload or discarded tab reopens the camera there | `src/components/camera/CameraCapture.tsx`, `CheckBanner.tsx` (`checkStatusContent`), `ShotProgress.tsx`, `src/lib/shots/shot-progress.ts` (`getRetakeContinuation`) |
| "Neu aufnehmen" summaries (review header, completion question, list badge, Kamera tab – never "Vollständig" while a shot is red) | `src/lib/processing/retake-summary.ts`, `src/hooks/use-retake-shots.ts` |
| Processing connection wording (offline / Showroom-Rechner unreachable / not set up) | `src/lib/processing/processor-connection.ts` |
| Data backend decision (`NEXT_PUBLIC_DATA_BACKEND`) | `src/lib/data/backend-mode.ts` |
| Demo data migrations (IndexedDB) | `src/lib/data/mock/migrations.ts` |
| API routes | `src/app/api/processing-status`, `process-photo`, `process-upload`, `process-job/[jobId]` (+ `/result`) |
| Processing access (login / demo access code), error mapping | `src/lib/api/processing-access.ts`, `src/lib/api/processing-errors.ts` |
| Client service container | `src/lib/app-services.ts` |
| DB schema, RLS, storage policies (apply the files in file-name order) | `supabase/migrations/`, static guard `src/lib/data/supabase/migrations.test.ts` |
| Brand config / official logo | `src/config/brand.ts`, `src/components/brand/BrandLogo.tsx`, `public/brand/` |
| Showroom preset, dealer studio plates, branding layer | `public/presets/` (README; `autoexperten-haendler/` = the shipped plate set + its README; `variants/` = the three shadow strengths), `processor/showroom3d/` (`dealer_*.py` + `render_dealer_passes.py` build the shipped set; `render_plates.py` = the earlier 3D room), `processor/app/showroom/` (`plates.py`, `wall_branding.py`, `branding.py`, `icons.py` Lucide ISC) |
| Image processor (Python/FastAPI) | `processor/` (see `processor/README.md`) |
| Processor pipeline steps | `processor/app/pipeline/` (decode, detection, segmentation, matting, mask, vehicle_geometry, placement, quality, light, grounding/shadow, reflection, composite, export; `room_depth.py` variant placement fit; `detail_backdrop.py` EXPERIMENTAL detail-shot showroom backdrop, off by default; debug allow-list `debug.py`) |
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
- Device-local stores (`localStorage` keys `ae-photo.*`: runs, job journal,
  check outcomes, shooting-tips flag): "Abmelden" clears them
  (`endProcessingSession` + `resetShootingTipsPrefs` in `MoreScreen`),
  "Fahrzeug löschen" forgets the vehicle's entries. A new store must be added
  there. A check state is decided only by `getShotCheckState` – never read the
  stored rejection or the device's checks on their own in a screen.
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
