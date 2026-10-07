# AutoExperten Photo

Internal vehicle photography app for **AutoExperten Schwetzingen**
([www.autoexperten-rn.de](https://www.autoexperten-rn.de) · +49 6202 9262357).

Employees photograph vehicles for online listings with a **guided camera**:
the app shows which of the 15 required angles is next, how the car should sit
in the frame (semi-transparent outline), what is still missing, and lets them
review and retake photos. Completed photo sets can then be processed into the
AutoExperten showroom style: the separate processor service (`processor/`,
Python/FastAPI) cuts out the **original** vehicle pixels and places them on
the fixed AutoExperten showroom with a contact shadow – the vehicle itself is
never regenerated. Without the processor the app uses a clearly marked mock.

> UI language: German. Mobile first. Installable as a PWA – no App Store needed.

---

## Contents

1. [What the app does](#what-the-app-does)
2. [Tech stack](#tech-stack)
3. [Install & run locally](#install--run-locally)
4. [Demo mode](#demo-mode)
5. [Configure Supabase](#configure-supabase) (env vars, database, storage, users)
   · [Deployment (Vercel)](#deployment-vercel)
6. [PWA usage](#pwa-usage)
7. [Camera: limitations, HTTPS & orientation](#camera-limitations-https--orientation)
8. [Offline / weak connection](#offline--weak-connection)
9. [Image processing](#image-processing) (mock, real processor, showroom, dev test page)
10. [Project structure](#project-structure)
11. [Tests & quality checks](#tests--quality-checks)
12. [Brand assets](#brand-assets)

---

## What the app does

| Step | Screen | Route |
| --- | --- | --- |
| Login (Supabase Auth, no public sign-up) | Anmelden | `/login` |
| Vehicle list with thumbnail, `11 / 15 Fotos`, date, status | Fahrzeuge | `/fahrzeuge` |
| Create vehicle (only Hersteller + Modell required) | Neues Fahrzeug erfassen | `/fahrzeuge/neu` |
| Guided camera: `1 / 15 · Vorne links (45°)`, framing overlay, instruction, progress with checkmarks, auto-advance | Kamera | `/fahrzeuge/[id]/kamera` |
| Review all shots in listing order, large preview, retake / delete, complete | Fotos überprüfen | `/fahrzeuge/[id]/fotos` |
| Vehicle details, progress, photo grid, Original ⇄ Bearbeitet | Fahrzeug | `/fahrzeuge/[id]` |
| Edit vehicle data | Fahrzeugdaten | `/fahrzeuge/[id]/daten` |
| Choose style & process photos (mocked) | Fotos bearbeiten | `/fahrzeuge/[id]/bearbeiten` |
| Tabs: open captures / ready for processing / account & settings | Kamera · Bearbeiten · Mehr | `/kamera`, `/bearbeiten`, `/mehr` |

Vehicle statuses: **Neu** → **Aufnahmen offen** → **Vollständig** (explicit
"Aufnahmen abschließen", only with all 15 required shots) → **Bearbeitet**.

### The 15-shot standard

Defined once in `src/lib/shots/shot-template.ts` (key, order, title,
instruction, category, overlay, required). The camera, review grid, progress
logic and export naming all read from it – change the standard there.

| # | Key | Title | Category |
|---|---|---|---|
| 01 | `front_left_45` | Vorne links (45°) | exterior |
| 02 | `front` | Vorne | exterior |
| 03 | `front_right_45` | Vorne rechts (45°) | exterior |
| 04 | `left_side` | Linke Fahrzeugseite | exterior |
| 05 | `right_side` | Rechte Fahrzeugseite | exterior |
| 06 | `rear_left_45` | Hinten links (45°) | exterior |
| 07 | `rear` | Hinten | exterior |
| 08 | `rear_right_45` | Hinten rechts (45°) | exterior |
| 09 | `cockpit` | Cockpit | interior |
| 10 | `front_interior` | Innenraum vorne | interior |
| 11 | `rear_seats` | Fond / Rücksitze | interior |
| 12 | `driver_seat` | Fahrersitz | interior |
| 13 | `door_controls` | Tür & Sitzbedienung | detail |
| 14 | `wheel_detail` | Felge / Rad | detail |
| 15 | `special_detail` | Fahrzeugdetail | detail |

Photos are **always ordered by this template** (never by upload time).
Export names are deterministic: `AE_{internalReference}_01_front_left_45.jpg`
(`buildExportFileName` in `src/lib/naming/file-naming.ts`). Additional photos
("Weitere Fotos hinzufügen") are stored as `extra_01`, `extra_02`, … after
the 15 required shots.

---

## Tech stack

- **Next.js 16** (App Router, Cache Components, Turbopack), **React 19**, **TypeScript** (strict)
- **Tailwind CSS 4** (theme tokens in `src/app/globals.css`)
- **Supabase**: Auth, PostgreSQL (RLS), Storage (private buckets) – via `@supabase/supabase-js` + `@supabase/ssr`
- **PWA**: web app manifest (`src/app/manifest.ts`) + hand-written service worker (`public/sw.js`)
- Browser APIs: `getUserMedia`, `ImageCapture` (where available), Canvas, IndexedDB
- Icons: `lucide-react`
- Tests: **Vitest** + Testing Library (jsdom)

No other runtime dependencies.

---

## Install & run locally

Requirements: **Node.js ≥ 20.9** (tested with Node 22), npm.

```bash
npm install
npm run dev          # http://localhost:3000
```

Without any configuration the app starts in **demo mode** (see below) – you
can log in with the prefilled demo credentials and test everything.

Other commands:

```bash
npm run build && npm start   # production build / server
npm run lint                 # ESLint (Next.js + React Compiler rules)
npm run typecheck            # next typegen + tsc --noEmit
npm test                     # Vitest
npm run check                # lint + typecheck + tests + build
npm run dev:https            # dev server with a self-signed HTTPS certificate
npm run generate:demo-assets # regenerate public/demo/shots/*.svg
```

---

## Demo mode

If `NEXT_PUBLIC_SUPABASE_URL` or the anon/publishable key is missing, the app
automatically uses:

- `DemoAuthService` – any e-mail + password is accepted (prefilled:
  `demo@autoexperten-rn.de` / `demo`), session kept in `localStorage`.
- `MockDataProvider` – vehicles, photo records and **image blobs are stored
  in the browser (IndexedDB)**, seeded with three sample vehicles:
  Mercedes-Benz S 63 AMG (15 photos, Vollständig), BMW X5 (9 photos),
  Audi A6 (4 photos). Sample photos are local, clearly labelled placeholder
  images in `public/demo/shots/` (no remote URLs).
- A small **"Demo-Modus"** badge in the header.

Demo data only lives in that browser. "Mehr → Demo-Daten zurücksetzen"
restores the sample vehicles. Both providers implement the same
`DataProvider` interface (`src/lib/data/types.ts`), so the UI is identical.

---

## Configure Supabase

### 1. Environment variables

Copy `.env.example` to `.env.local`:

| Variable | Where | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | browser + server | Project URL (Project Settings → API) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` *(or `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`)* | browser + server | Public anon / publishable key |
| `IMAGE_PROCESSOR` | server | `mock` (default) or `real` |
| `IMAGE_PROCESSING_API_URL` | server | Base URL of the future processing service (`real` only) |
| `IMAGE_PROCESSING_API_KEY` | server | Secret for the processing service (`real` only) |
| `NEXT_PUBLIC_IMAGE_PROCESSOR` | browser | UI hint only (`real` hides the "Vorschau-Version" notice) |

**Never** put the service role key into a `NEXT_PUBLIC_*` variable. The app
does not need it; all requests run with the employee's session and RLS.

### 2. Database setup

The complete schema is in **`supabase/migrations/20261007000000_initial_schema.sql`**
(tables, indexes, triggers, RLS policies, storage buckets and storage
policies). No manual table creation in the dashboard is needed. Either:

- **Supabase CLI:** `supabase link --project-ref <ref>` then `supabase db push`, or
- **SQL editor:** paste the file into the Supabase SQL editor and run it.

The migration is idempotent (safe to run twice).

What it creates:

| Object | Notes |
| --- | --- |
| `organizations`, `organization_members` | Seeds "AutoExperten Schwetzingen". New auth users join it automatically (trigger on `auth.users`); existing users are back-filled. |
| `vehicles` | `id, user_id, manufacturer, model, color, license_plate, vin, mileage, first_registration, internal_reference, notes, status (new/capturing/complete/processed), created_at, updated_at` + `organization_id`. |
| `vehicle_photos` | `id, vehicle_id, shot_key, shot_order, title, original_storage_path, processed_storage_path, processed_preset, thumbnail_storage_path, width, height, taken_at, archived_at, created_at, updated_at`. One *active* photo per shot (partial unique index). A trigger makes `original_storage_path` immutable. |
| `add_vehicle_photo()` | Inserts a new photo and archives the previous one of the same shot in one transaction; idempotent for upload retries. |
| `processing_jobs` | Prepared for the real processing service (not used by the mock). |

**Access model / RLS:** employees of an organisation share its vehicles;
nobody else can read or change them. Vehicles and photos have no delete
policy – photos are archived, never deleted, so originals are never lost.
(The policies were verified against a local PostgreSQL with three users:
same-org access works, other orgs and anonymous users see nothing, originals
cannot be modified.)

### 3. Storage setup

Created by the migration – three **private** buckets:

```
vehicle-originals/{vehicleId}/{shotKey}/{photoId}.jpg         original, never overwritten
vehicle-thumbnails/{vehicleId}/{shotKey}/{photoId}.jpg        480 px preview
vehicle-processed/{vehicleId}/{preset}/{shotKey}/{photoId}.jpg processed result (separate file)
```

Every capture has its own `photoId`, so a retake can never overwrite an
original. Originals have only *select* + *insert* storage policies (uploads use
`upsert: false`); processed files may be regenerated. Images are displayed via
short-lived signed URLs.

### 4. Users

This is an internal tool – **disable public sign-ups** (Authentication →
Providers → Email → "Allow new users to sign up" off) and invite/create
employees in the Supabase dashboard (Authentication → Users). They are added
to the AutoExperten organisation automatically.

---

## Deployment (Vercel)

The project deploys to Vercel without extra configuration (framework preset
"Next.js", default build command `next build`).

1. Vercel → **Add New → Project** → import `mbesli542-cyber/autofotos`.
2. Every push to the configured production branch creates a production
   deployment; other branches get preview URLs.
3. **Without environment variables the deployment runs in demo mode** – each
   browser keeps its own local demo data, nothing is shared or stored on a server.
4. To go live with Supabase, add `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` under *Project Settings → Environment
   Variables* and **redeploy** – `NEXT_PUBLIC_*` values are baked in at build time.

Vercel serves the app over HTTPS, so the camera works on phones and the app
can be installed to the home screen.

---

## PWA usage

The app is installable from the browser (served over HTTPS):

- **iPhone (Safari):** Share → "Zum Home-Bildschirm".
- **Android (Chrome):** menu → "App installieren" / "Zum Startbildschirm hinzufügen".

It then opens full-screen ("AE Photo", dark theme). Manifest:
`src/app/manifest.ts`; icons: `public/icons/` (placeholders, see
[Brand assets](#brand-assets)). The service worker (`public/sw.js`, only
registered in production builds) precaches overlays/icons and the offline
page, serves hashed static assets cache-first and pages network-first. It
never caches API calls or Supabase requests.

---

## Camera: limitations, HTTPS & orientation

- Browsers only allow camera access in a **secure context**:
  `https://…` or `http://localhost`. Opening the dev server from a phone via
  `http://192.168.x.x:3000` will **not** give camera access. Options:
  `npm run dev:https` (self-signed certificate – accept the warning on the
  phone), a tunnel (e.g. `cloudflared`, `ngrok`), or deploy (e.g. Vercel).
- The app requests the **rear camera** (`facingMode: environment`) at up to
  3840×2160. Where supported (Chrome/Android), `ImageCapture.takePhoto()` is
  used for full sensor resolution; otherwise the frame is grabbed at the
  stream's native resolution (never the small on-screen size). JPEG quality
  0.92. Settings: `src/lib/camera/config.ts`.
- **"Foto hochladen"** is always available (desktop development, denied
  permission, or to use the phone's native camera app). Uploaded files are
  stored **unchanged** (original quality and EXIF).
- **Orientation:** captured frames are already upright; uploaded photos keep
  EXIF and are displayed with the browser's EXIF orientation handling;
  dimensions/thumbnails use `createImageBitmap(..., { imageOrientation: "from-image" })`.
  If `takePhoto()` returns a different orientation than the preview, the app
  falls back to the preview frame so nothing is rotated wrongly.
- **What you see is what is saved:** the preview has the exact aspect ratio
  of the camera stream. Portrait use is the primary layout; for exterior
  shots the app suggests holding the phone in landscape (listing sites use
  landscape photos). The manifest therefore does not lock orientation.
- Permission denied / no camera / camera in use / insecure context each show
  a clear German message with "Erneut versuchen" and "Foto hochladen".
- HEIC files (iPhone photo library) are stored as-is; browsers other than
  Safari may not be able to display them.

**Quality checks (future):** `src/lib/camera/quality.ts` defines
`CaptureQualityResult` (`isCentered`, `isSharp`, `isBrightEnough`,
`angleScore`, `distanceScore`, `warnings`) and a `CaptureQualityChecker`
interface. The camera already calls the checker after every capture and
shows returned warnings ("Trotzdem verwenden" / "Foto wiederholen"); the MVP
uses a no-op checker. Plug in a real checker (on-device model or API) there.

Overlay opacity: `CAMERA_CONFIG.overlayOpacity` (default 0.4). Overlay SVGs:
`public/overlays/*.svg` (placeholder framing guides, not model-specific).

---

## Offline / weak connection

Workshop Wi-Fi can be weak, so captures are **never lost on upload failure**:

1. Each photo is written to IndexedDB first (`src/lib/offline/upload-queue.ts`).
2. It is uploaded in the background (one at a time, in capture order) while
   the camera immediately advances to the next shot.
3. Failed uploads stay on the device, are shown with a warning badge
   ("nicht gespeichert"), retried automatically when the connection returns
   and on the next app start, or manually ("Erneut versuchen").
4. Retries are idempotent (the queue id is the photo id).

"Aufnahmen abschließen" is only possible once all required photos are saved
on the server. No full offline sync is implemented on purpose.

---

## Image processing

### Principle

**The real vehicle is never regenerated or recreated with generative AI.**
Paint colour, wheels, badges, headlights, body shape, visible equipment,
scratches, damage, wear and the interior must stay truthful. Only the
surroundings change. Original photos are never modified – results are
separate files.

### API contract (already implemented)

```
POST /api/process-photo        { vehicleId, photoId, preset }  → 202 { jobId, status }
GET  /api/process-job/:jobId   → { jobId, status, progress, result, error, … }
status: queued | processing | complete | failed
preset: autoexperten_standard | autoexperten_dark | original_plus
```

The UI (`src/lib/processing/processing-client.ts`) only uses these endpoints.
Route handlers delegate to an **`ImageProcessor`** (`src/lib/processing/types.ts`):

- **`MockImageProcessor`** (default) – stateless (the job id encodes the
  request), simulates *queued → processing → complete*. The browser then
  renders a clearly marked **preview**: the original photo 1:1 plus an
  AutoExperten branding bar *below* it, stored as a separate processed file.
  This makes "Original ⇄ Bearbeitet" testable end-to-end.
- **`RealImageProcessor`** – HTTP adapter for the future service
  (`IMAGE_PROCESSOR=real`, `IMAGE_PROCESSING_API_URL`, `IMAGE_PROCESSING_API_KEY`).
  Expected service API: `POST {url}/jobs`, `GET {url}/jobs/:id`; the service
  stores the result in `vehicle-processed/…`, updates
  `vehicle_photos.processed_storage_path` and returns
  `result: { kind: "stored", processedStoragePath }`.

Connecting the real backend = implement that service + set env vars. **No UI
changes are needed.** (In demo mode only the mock processor is allowed,
because demo mode has no authentication.)

### Real processor (Stage 2 prototype) – `processor/`

A separate Python service (FastAPI + Pillow + OpenCV + ONNX Runtime, CPU)
implements the contract above. Full documentation: **[processor/README.md](processor/README.md)**.

```
original photo
 → decode (EXIF orientation, ICC → sRGB)
 → vehicle segmentation (BiRefNet, local ONNX model)
 → full-resolution alpha (guided filter), mask clean-up
 → cut-out of the ORIGINAL vehicle pixels
 → conservative light matching (tiny exposure / white balance, colour guard)
 → bbox-based placement (no distortion, ~78 % width, centred, ground line 84 %)
 → fixed AutoExperten showroom plate
 → contact + ambient shadow from the mask
 → edge harmonisation (decontamination, light wrap)
 → JPEG 4:3, 2400–3200 px, quality 92, sRGB, no EXIF
```

**Truthful-vehicle policy:** no generative image AI anywhere (no DALL·E,
Stable Diffusion, Flux, Midjourney, Generative Fill, image-to-image). Paint
colour, wheels, badges, lights, glass, damage and wear are the photographed
pixels. Vehicle corrections are capped by hard limits in code
(`processor/app/pipeline/light.py`) and reverted if the measured hue or
saturation of the vehicle would change. Interior shots are not composited.

**Run it locally** (Python 3.12+)

```bash
cd processor
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8000      # GET http://localhost:8000/health
```

or with Docker (context = repo root):
`docker build -f processor/Dockerfile -t autoexperten-processor .` and
`docker run --rm -p 8000:8000 autoexperten-processor`.

The segmentation model (224 MB, ≈ 7 GB peak RAM per running job on the CPU;
the lighter `isnet-general-use` needs ≈ 1.5 GB) makes the processor unsuitable
for Vercel functions – run it on a VM/container with ≥ 12 GB RAM.

**Test one real car photo**

1. Start the processor (above) and `npm run dev`.
2. In `.env.local` set `IMAGE_PROCESSING_API_URL=http://localhost:8000`
   (and `IMAGE_PROCESSING_API_KEY` if the processor has `PROCESSOR_API_KEY`).
3. Open **http://localhost:3000/dev/processing-test** – choose a photo, style
   "AutoExperten Standard", shot, **Verarbeiten** → Original / Bearbeitet side
   by side → "Ergebnis herunterladen".
   The page is not linked anywhere and is only served in development (or with
   `ENABLE_DEV_TOOLS=true`, internal deployments only).
4. Without the app: `cd processor && .venv/bin/python -m app.cli car.jpg -o /tmp/out.jpg --debug-dir /tmp/dbg`.

**Connect the app** (`.env.local`): `IMAGE_PROCESSOR=real`,
`NEXT_PUBLIC_IMAGE_PROCESSOR=real`, `IMAGE_PROCESSING_API_URL`,
`IMAGE_PROCESSING_API_KEY`. The processor additionally needs
`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (processor side only) to read
originals and store results in `vehicle-processed`. No UI changes needed.

**Debugging:** `PROCESSOR_DEBUG=true` keeps `original.jpg`, `mask.png`,
`vehicle-transparent.png`, `background.jpg`, `composite-before-shadow.jpg`,
`shadow.png`, `final.jpg` per job (`processor/data/jobs/<id>/debug/`, also
shown on the dev test page). Never enable it on a public instance.

### AutoExperten showroom preset

`public/presets/autoexperten-standard.json` is the processing preset (output
size, placement, shadow, adjustment limits) – used by the processor and
referenced from `src/lib/processing/presets.ts`.

**Showroom master image:** the background is one fixed image,
`public/presets/autoexperten-standard-showroom.jpg` (4:3, ≥ 3200×2400, empty
showroom, wall/floor junction at ~62 % of the height). The current file is a
deterministic **placeholder** rendered by
`processor/scripts/render_showroom_placeholder.py` (white wall, parquet,
spotlights, blue LED strips, wood slats, plants, brand wall with the official
logo, "SCHWETZINGEN", www.autoexperten-rn.de, +49 6202 9262357). To use the
real showroom photo: replace that JPG (same name) and set
`"placeholder": false` in `autoexperten-standard.json`. No code changes.

The look of `autoexperten_standard` (default):

- bright premium showroom, clean white / light-gray wall
- warm ceiling spotlights (~3000 K)
- blue vertical LED accent lighting (AutoExperten blue)
- premium warm wood / parquet floor
- vertical wood-slat wall panels, plants (as in the reference mockup)
- brand wall: official logo, Schwetzingen, www.autoexperten-rn.de, +49 6202 9262357

`autoexperten_dark` (dark premium showroom) and `original_plus` (original
background, only light/colour/contrast optimised) are defined in the app but
not implemented by the processor yet (jobs fail with
"Dieser Bearbeitungsstil ist noch nicht verfügbar.").

---

## Project structure

```
src/
  app/                         routes (App Router)
    (app)/layout.tsx           auth gate for everything below
    (app)/(tabs)/…             screens with bottom navigation
    (app)/(capture)/…/kamera   full-screen guided camera
    api/process-photo          POST – start processing job
    api/process-job/[jobId]    GET  – job status
    dev/processing-test        developer test page (dev only, not linked)
    api/dev/processing-test/…  its proxy routes to the processor (dev only)
    login/, offline/, manifest.ts, layout.tsx, globals.css
  features/                    screen components (compose components + hooks)
  components/                  reusable UI
    brand/ layout/ ui/ vehicles/ photos/ camera/ processing/ auth/ providers/
  hooks/                       auth, data loading, upload queue, camera stream
  lib/
    shots/        shot template + pure progress/ordering logic
    vehicles/     validation, status rules, summaries
    naming/       export names & storage paths
    data/         DataProvider/AuthService + mock (IndexedDB) & Supabase impls
    workflow/     operations that keep vehicle status in sync
    offline/      IndexedDB helper, upload queue
    camera/       getUserMedia, capture, image utils, quality-check interface
    processing/   API contract, presets, mock/real processors, client, mock renderer
    supabase/     config, browser + server clients
    app-services.ts  client service container
  config/brand.ts  company data, colours, logo assets
  proxy.ts         Supabase session refresh / redirect (Next 16 "proxy")
public/
  overlays/  camera framing guides (SVG)    demo/shots/  demo placeholder photos
  icons/     PWA icons (official monogram)  brand/       official logo (+ official/ originals)
  presets/   showroom preset JSON + master  sw.js        service worker
supabase/migrations/   schema, RLS, storage
scripts/generate-demo-assets.mjs
processor/             Python image processor (FastAPI) – see processor/README.md
  app/pipeline/        decode, segmentation, mask, placement, light, shadow, composite, export
  app/jobs/ storage/   job manager, Supabase photo store
  app/showroom/        placeholder showroom renderer
  tests/               pytest
```

---

## Tests & quality checks

`npm test` runs Vitest:

- required shot completion, missing shots, progress
- shot ordering (template order, never time) and guided navigation
- export file naming / storage paths
- vehicle validation (required fields, VIN, mileage, dates, plates)
- vehicle status rules
- mock processor job lifecycle and request validation
- real processor adapter (URL joining, auth header, 404 handling)
- photo slots (incl. pending uploads and extra photos)
- component tests: `ShotProgress`, `VehicleForm`

`cd processor && .venv/bin/python -m pytest` runs the processor tests (API
validation/auth, job lifecycle, mask, placement, compositing output, colour
guard, decoding, debug output, showroom placeholder, Supabase store).

The full acceptance flow (login → create vehicle → 15 guided captures →
review → retake → complete → process → Original/Bearbeitet) was verified in
headless Chromium with a fake camera on phone, landscape and desktop viewports.

---

## Brand assets

- **Logo:** the official AutoExperten logo (from autoexperten-rn.de, not
  redrawn) is in `public/brand/official/`; the UI uses trimmed copies
  `public/brand/autoexperten-logo.png` (light backgrounds) and
  `autoexperten-logo-light.png` (dark backgrounds, only the gray "Auto" turned
  white). See `public/brand/README.md`. `LOGO_ASSETS` in
  `src/config/brand.ts` points to them. If an official SVG becomes available,
  drop it into `public/brand/` and update `LOGO_ASSETS`.
- **App icons** in `public/icons/` are made from the official "AE" monogram.
- **Overlays** in `public/overlays/` are generic framing guides; they can be
  replaced with refined artwork using the same file names.
