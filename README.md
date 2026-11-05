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
never regenerated. There is no simulated processing: without a connected
processor nothing is processed, and "Bearbeitet" only ever shows real results.

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
9. [Image processing](#image-processing) (configuration, Supabase & demo adapters, processor, showroom, dev test page)
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
| Choose style & process photos with the real showroom processor | Fotos bearbeiten | `/fahrzeuge/[id]/bearbeiten` |
| Tabs: open captures / ready for processing / account & settings | Kamera · Bearbeiten · Mehr | `/kamera`, `/bearbeiten`, `/mehr` |

Vehicle statuses: **Neu** → **Aufnahmen offen** → **Vollständig** (explicit
"Aufnahmen abschließen", only with all 15 required shots) → **Bearbeitet**.
A set with a shot to retake is never shown as "Vollständig": "Fotos
überprüfen" shows "15 / 15 Fotos · 1 neu aufnehmen" with a red progress bar,
"Aufnahmen abschließen" asks "1 Foto muss neu aufgenommen werden. Trotzdem
abschließen?" first, the vehicle page and the list cards show a "Neu
aufnehmen" badge instead of the green "Vollständig" chip, and the "Kamera"
tab keeps the vehicle and opens the camera at its first red shot
(`src/lib/processing/retake-summary.ts`). Which shot is red follows one rule
on every screen and in the export (see [Instant check in the
camera](#instant-check-in-the-camera)).
The "Bearbeiten" tab also lists vehicles with all required photos that are
not completed yet ("Bereit zum Abschließen", button "Aufnahmen abschließen"
→ "Fotos überprüfen"). "Ergebnisse ansehen" opens the vehicle page in the
"Bearbeitet" view (`?ansicht=bearbeitet`); a manual Original/Bearbeitet choice
holds only while the page is shown. A processed cover photo on the vehicle
page is shown whole (the 4:3 listing image is never cropped). The large view
("Großansicht") shows its actions – or an open question – right below the
version switch, before the processor's hints and the capture details, in every
layout, so they never need scrolling; on phones in landscape (viewport height
≤ 520 px) the photo is on the left and a scrolling side panel holds them.

**Fahrzeugdaten** (`/fahrzeuge/[id]/daten`, "Ändern" on the vehicle page):
the form shows the newest saved data – including changes made on another
device – until the employee changes a field; from then on the input is kept
while photos upload in the background and after the browser's Back. While
the page's own data is still loading, the form is read-only ("Wird
aktualisiert…"). Saving never overwrites a change made elsewhere: it is
conditional on the vehicle's `updated_at` (`saveVehicleData` in
`src/lib/workflow/vehicle-workflow.ts`; Supabase: conditional `PATCH`, demo:
compared in `MockDataProvider`). If the data changed meanwhile, the form
shows the new data and "Die Fahrzeugdaten wurden inzwischen geändert. Bitte
prüfen und erneut speichern."; a change of the status alone (e.g. a photo
saved in the background) is no conflict.

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
the 15 required shots and exported as `_16_extra_01`, `_17_extra_02`, …
(`exportOrderOf`), so plain name order is the listing order. The "Interne
Fahrzeugnummer" is unique (`src/lib/vehicles/reference.ts`, compared as the
file names would, ignoring case): "Diese Fahrzeugnummer ist schon vergeben."
Only a new or changed number is checked – vehicles that already share a
number (data from before the rule) stay editable. The untouched demo sample
vehicles never block a number (they go with the first real vehicle). The
check runs in the app (no database constraint yet: the seed and older data
contain duplicates).

### Export ("Alle Fotos speichern")

On the vehicle page (primary in the "Bearbeitet" view) – and per photo with
"Speichern" in the large view, for the version shown:

1. A summary first (`src/lib/export/listing-export.ts`, pure): "15 Fotos
   bereit" or e.g. "13 bearbeitet · 1 noch nicht bearbeitet · 1 muss neu
   aufgenommen werden", with the shots to look at. Per template shot the
   processed result, otherwise the original (marked); extra photos as
   originals after them. Rejected shots are left out unless ticked – on
   every device: a processor rejection is kept with the photo (a
   `rejected:<errorCode>` entry in `processing_warnings` of a photo without
   a processed result, written by `processPhoto` via
   `DataProvider.recordPhotoRejection`), so the desktop that downloads the
   ZIP knows it as well as the phone that took the photo. A file that cannot
   be loaded never blocks the others: a missing one can be left out
   ("Ohne dieses Foto speichern") or – for a missing showroom image –
   replaced by the original ("Original verwenden", marked in the summary).
2. Phones: the share sheet (`navigator.share({ files })` when `canShare` is
   true), in parts of at most 10 files / 48 MB (Chrome on Android's limit),
   one tap each. The sheet knows before the tap whether this browser can
   share the files (`canShareFileTypes`) and otherwise says "ZIP" from the
   start. The hint names "Bilder sichern" on iOS only (Android: a target such
   as Google Fotos or "Dateien"); after the share it says neutrally that the
   files were handed to the share menu. Only the files that will be saved
   are loaded while the summary is read (rejected shots once ticked), so the
   share starts from a fresh tap. Cancelling is no error; any other refusal
   offers the ZIP.
3. Desktop and fallback: `AE_{ref}_Fotos.zip`, built in the browser by a
   dependency-free store-only ZIP writer (`src/lib/export/zip.ts`, refuses
   archives that would need ZIP64).

The files come from `DataProvider.getPhotoFile` – IndexedDB in demo mode, a
Storage download with the signed-in session in Supabase mode (no signed URL,
no service key, no extra API route).

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

Requirements: **Node.js 22 (≥ 22.13) or 24**, npm. `package.json` declares
`engines.node` `^22.13.0 || >=24.0.0`: the Supabase client needs Node 22 or
later, and the test and lint tooling (jsdom, typescript-eslint) 22.13+ on the
22 line; npm warns outside the range, and Vercel takes the Node.js version
from it. (Vitest does not support the odd release 25.)

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

If `NEXT_PUBLIC_SUPABASE_URL` or the publishable/anon key is missing (an
empty value counts as missing) – or `NEXT_PUBLIC_DATA_BACKEND=demo` is set –
the app uses:

- `DemoAuthService` – any e-mail + password is accepted (prefilled:
  `demo@autoexperten-rn.de` / `demo`), session kept in `localStorage`.
- `MockDataProvider` – vehicles, photo records and **image blobs are stored
  in the browser (IndexedDB)**, seeded with three sample vehicles:
  Mercedes-Benz S 63 AMG (15 photos, Vollständig), BMW X5 (9 photos),
  Audi A6 (4 photos). Sample photos are local, clearly labelled placeholder
  images in `public/demo/shots/` (no remote URLs).
- A small **"Demo-Modus"** badge in the header; the login page says
  "Demo-Modus: Beliebige Zugangsdaten funktionieren, Daten bleiben nur auf
  diesem Gerät." and "Mehr" shows "Demo-Modus · Daten nur auf diesem Gerät"
  (online: "Angemeldet · Daten werden online gespeichert") – no backend names.

Demo data only lives in that browser. The sample vehicles only illustrate
an empty app: they are removed as soon as a real vehicle exists (a sample
someone photographed or processed is kept as real data), and their internal
numbers never block a real vehicle's number. "Mehr → Alle Daten auf diesem
Gerät löschen" asks for "LÖSCHEN" to be typed and then deletes every vehicle,
photo and result on the device together with photos not saved yet (the local
upload queue), the runs, checks and jobs kept on the device; the samples do
not come back. "Fahrzeug löschen" (vehicle page, after a question; demo mode
only – the Supabase schema has no delete policy) stops the vehicle's run and
checks, forgets its kept jobs and stored run, drops its waiting uploads and
deletes the vehicle with all photo records and files in one IndexedDB
transaction. The app asks the browser once per page load to keep its data
(`navigator.storage.persist()`); "Mehr" shows the storage used and, if the
browser may clear it, the tip to add the app to the home screen. A full
storage shows "Der Speicher auf diesem Gerät ist voll. Bitte bereits
gespeicherte Fahrzeuge löschen." Both providers implement the same
`DataProvider` interface (`src/lib/data/types.ts`), so the UI is identical.
The sample photos are placeholder drawings, not listing photos: a vehicle
with one of them cannot be processed ("Beispielfahrzeug – bitte ein eigenes
Fahrzeug fotografieren." on its processing page).

Demo mode does **not** disable real image processing: with
`IMAGE_PROCESSOR=real` the photos are uploaded to the processor and the
results are stored in the browser (see [Image processing](#image-processing)).
Processed versions created by older app versions in demo mode were simulated
(original photo + branding bar); a one-time data migration
(`src/lib/data/mock/migrations.ts`) deletes them and resets "Bearbeitet"
vehicles to "Vollständig".

---

## Configure Supabase

### 1. Environment variables

Copy `.env.example` to `.env.local`, fill in the project URL and uncomment
**one** of the two key lines:

| Variable | Where | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | browser + server | Project URL (the project's API settings in the Supabase dashboard) |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` *(or the legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY`)* | browser + server | Public client key: the publishable key (`sb_publishable_…`, the default of new projects) or the legacy anon key of older projects. Both are public; requests still run with the employee's session and RLS. If both are set, the anon key is used; an empty value counts as not set (`src/lib/supabase/config.ts`). |
| `NEXT_PUBLIC_DATA_BACKEND` | browser + server | `demo` or `supabase`; unset = Supabase when configured, else demo (see [Image processing → Configuration](#configuration)) |
| `IMAGE_PROCESSOR` | server | `mock` (default) = no processor connected, or `real` |
| `IMAGE_PROCESSING_API_URL` | server | Base URL of the processor (`real` only) |
| `IMAGE_PROCESSING_API_KEY` | server | Bearer secret for the processor (`real` only) |
| `NEXT_PUBLIC_IMAGE_PROCESSOR` | browser | UI hint for the first render of the processing page only |
| `PROCESSING_ACCESS_CODE` | server | Demo mode only: access code for the processing routes |

**Never** put the service role key into the app's environment (no
`NEXT_PUBLIC_*` variable, no Vercel variable). The app does not need it; all
requests run with the employee's session and RLS. Only the image processor
reads `SUPABASE_SERVICE_ROLE_KEY`, in Supabase mode, from `processor/.env`
(see [Image processing](#image-processing)).

### 2. Database setup

The schema lives in **`supabase/migrations/`** (tables, indexes, triggers,
RLS policies, storage buckets and storage policies) – no manual table
creation in the dashboard is needed. The files build on each other; apply
them **in this order**:

| # | File | Content |
| --- | --- | --- |
| 1 | `20261007000000_initial_schema.sql` | The complete schema: tables, indexes, triggers, RLS, buckets, storage policies, `add_vehicle_photo()`. |
| 2 | `20261028000000_storage_access_index_and_late_photo_uploads.sql` | `can_access_vehicle_object()` finds the vehicle by primary key (fast storage checks with many vehicles); `add_vehicle_photo()` stores a late, older capture as archived (below). |
| 3 | `20261028000100_photo_processing_warnings.sql` | Optional column `vehicle_photos.processing_warnings` – the processor's hints on a processed photo. |

- **Supabase CLI:** `supabase link --project-ref <ref>` then `supabase db push`
  (applies every file the database does not have yet, in this order), or
- **SQL editor:** paste and run the files one after another, in this order.

Every file is safe to run twice. Running the **initial** file again on its
own restores the old versions of `can_access_vehicle_object()` and
`add_vehicle_photo()` – then run files 2 and 3 again as well.

**Apply new migrations before you deploy a new app version.** The app
tolerates a missing `processing_warnings` column (file 3): photos are read
and saved without the processor's hints, and the browser console warns once
("[AutoExperten Photo] vehicle_photos.processing_warnings is missing – apply
supabase/migrations/20261028000100_photo_processing_warnings.sql. …").
`src/lib/data/supabase/migrations.test.ts` checks the migrations statically
(e.g. SECURITY DEFINER only for the membership helpers, every policy
`to authenticated` and never `(true)`, no grants to `anon`/`public`, the
original-protection trigger, no column the app reads dropped) – a new
migration must keep to these rules.

What they create:

| Object | Notes |
| --- | --- |
| `organizations`, `organization_members` | Seeds "AutoExperten Schwetzingen". New auth users join it automatically (trigger on `auth.users`); existing users are back-filled. |
| `vehicles` | `id, user_id, manufacturer, model, color, license_plate, vin, mileage, first_registration, internal_reference, notes, status (new/capturing/complete/processed), created_at, updated_at` + `organization_id`. `updated_at` is set by a trigger on every change; "Fahrzeugdaten" saves only while it is unchanged (see [What the app does](#what-the-app-does)). |
| `vehicle_photos` | `id, vehicle_id, shot_key, shot_order, title, original_storage_path, processed_storage_path, processed_preset, processing_warnings (file 3), thumbnail_storage_path, width, height, taken_at, archived_at, created_at, updated_at`. One *active* photo per shot (partial unique index). A trigger makes `original_storage_path` (and `vehicle_id`, `shot_key`) immutable. `processing_warnings`: jsonb array of `{ code, message }`, at most 20 entries / 16 KB, written with the processed version, replaced by a new processing, `null` for a new photo. A photo without a processed version may carry one entry `{ "code": "rejected:<errorCode>", … }`: the processor's rejection (written only while `processed_storage_path` is null; a later result replaces it). |
| `add_vehicle_photo()` | Inserts a new photo and archives the active photo of the same shot in one transaction – **unless the new capture is older** (`taken_at`) than the active one, e.g. a queued upload that arrives after a retake, or a photo from a second device: then the new photo is stored archived and the newer one stays active. Equal or newer captures replace it. The active photo counts as taken at most 5 minutes after it was saved (`created_at`), so a device clock running ahead blocks later retakes for 5 minutes at most. Calls for the same shot run one after another (advisory lock); idempotent for upload retries (same photo id). |
| `can_access_vehicle_object()` | Used by the storage policies: true if the first path segment is a vehicle the user may access (RLS applies). Since file 2 it looks the vehicle up by primary key – signing 2,000 cover URLs took ≈ 0.13–0.19 s instead of 11–12 s. |
| `processing_jobs` | Prepared for a persistent job log (not used yet – job state lives in the processor). |

The demo backend (`MockDataProvider.savePhoto`) and the app's photo tiles use
the same capture-order rule (`src/lib/photos/capture-order.ts`). A capture
with the camera, "Foto hochladen" or "Foto wiederholen" on a Zusatzfoto is
dated now and keeps that real capture time ("Aufgenommen am"); "Weitere Fotos
hinzufügen" uses the file's date, but never one in the future. Known limits: within 5
minutes after a photo dated in the future (device clock ahead) was saved, a
retake is stored archived; a retake from a second device whose clock is
behind by more than the time since the current photo was taken is stored
archived as well.

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
`upsert: false`); processed files may be regenerated. The originals bucket
takes JPG, PNG, WebP and HEIC/HEIF up to 50 MB – the app checks picked files
against the same limits. Images are displayed via signed URLs that are valid
for 2 hours; a cached URL is reused only during its first 30 minutes, so
every URL a page gets stays valid for at least 90 more minutes.

The vehicle list is read in pages of 500 (keyset paging, newest first, with
the total count) until every vehicle is loaded – it works with any Data API
max-rows setting, is never cut off silently, and vehicles created or deleted
meanwhile neither shift a page nor cause an error.

### 4. Users

This is an internal tool – **disable public sign-ups** (Authentication →
Providers → Email → "Allow new users to sign up" off).

**Onboarding an employee:** Supabase dashboard → Authentication → Users →
**Add user → Create new user**: e-mail, an initial password, and tick
**Auto Confirm User**. Hand the password over in person; the employee signs
in with e-mail and password. New users join the AutoExperten organisation
automatically. On shared devices the login form is cleared after a
successful sign-in and whenever the login page is hidden, so the next person
never finds the previous credentials (demo mode: the demo credentials
again); while signing in the button shows "Anmeldung läuft…" and a second
submit is ignored.

Invitations ("Send invitation"), magic links and password-recovery mails do
**not** work with this app: its browser client uses the PKCE flow
(`@supabase/ssr`) and rejects the links' tokens ("Not a valid PKCE flow
url."), and the app has no "set password" or "forgot password" page. A
forgotten password is therefore reset by an admin, who sets a new one with
the Supabase Auth admin API – e.g.
`supabase.auth.admin.updateUserById(userId, { password })` with the service
role key, outside the app – and hands it over.

---

## Deployment (Vercel)

The project deploys to Vercel without extra configuration (framework preset
"Next.js", default build command `next build`; the Node.js version comes from
`engines.node` in `package.json` – 22.13+ or 24).

1. Vercel → **Add New → Project** → import `mbesli542-cyber/autofotos`.
2. Every push to the configured production branch creates a production
   deployment; other branches get preview URLs.
3. **Without environment variables the deployment runs in demo mode** – each
   browser keeps its own local demo data, nothing is shared or stored on a server.
4. To go live with Supabase, first apply the database migrations
   ([2. Database setup](#2-database-setup)), then add `NEXT_PUBLIC_SUPABASE_URL`
   and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (or the legacy
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`) under *Project Settings → Environment
   Variables* and **redeploy** – `NEXT_PUBLIC_*` values are baked in at build
   time. Later app versions: apply new migrations first, then deploy.
5. To connect real image processing, run the processor on a VM/container
   (it never runs on Vercel – ≥ 12 GB RAM; Docker Compose + automatic HTTPS:
   `processor/deploy/`, steps in [`processor/README.md` → Deploy](processor/README.md);
   its image does not start without `PROCESSOR_API_KEY`) and set
   `IMAGE_PROCESSOR=real`, `IMAGE_PROCESSING_API_URL`,
   `IMAGE_PROCESSING_API_KEY` (= the processor's `PROCESSOR_API_KEY`) and
   `NEXT_PUBLIC_IMAGE_PROCESSOR=real`. In Supabase mode the processor also
   needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in its own environment
   (never in Vercel). For a **public demo deployment** also set
   `PROCESSING_ACCESS_CODE` – otherwise anyone can use your processor.

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
  stored **unchanged** (original quality and EXIF). Accepted are JPG, PNG,
  WebP and HEIC/HEIF up to 50 MB – the limits of the originals bucket –
  checked before anything is queued, in demo mode too
  (`src/lib/camera/photo-file.ts`); a file without a type is recognised by
  its extension (`.jpg`, `.heic`, …). Anything else shows "Dieses Bildformat
  wird nicht unterstützt (JPG, PNG, WebP oder HEIC)." or "Das Foto ist zu groß
  (max. 50 MB)." and nothing is saved. The same applies to "Weitere Fotos
  hinzufügen".
- **Zusatzfotos:** "Foto wiederholen" on an additional photo ("Fotos
  überprüfen", vehicle page) opens a picker for one file, which replaces
  exactly that photo (the previous one is archived). The guided camera only
  handles the 15 required shots.
- **Camera visits and Back:** every navigation into the camera (links, "Foto
  wiederholen", "Aufnahmen fortsetzen", "Fahrzeug erstellen & Fotos
  aufnehmen") starts at the requested shot or the first open one; the
  browser's or Android's Back shows the camera exactly as it was left (same
  shot, no old preview – a photo whose preview was still shown is kept and
  the camera moves on to the next shot). Leaving the camera (after a retake
  from "Fotos überprüfen"/"Fotos bearbeiten", after the last required photo,
  or with "Kamera schließen") goes back to the page's history entry when the
  camera was opened from there – no page appears twice – and otherwise
  replaces the camera's entry, so Back never reopens the camera. Going back
  needs the browser's Navigation API (current Chrome/Android, newer Safari and
  Firefox); without it the camera's entry is replaced.
- **No usable shot:** a link naming a photo the camera does not have (e.g. an
  additional photo) continues with the first open shot ("Diese Aufnahme gibt
  es in der Kamera nicht. Es geht mit den Pflichtfotos weiter."). When all 15
  required photos are there and no shot is requested, nothing is armed: the
  shutter and "Foto hochladen" stay disabled until the employee taps the photo
  to replace ("… Tippen Sie oben auf das Foto, das Sie wiederholen möchten.").
- **Header:** step and title, the shot progress (tap a segment to jump to
  that shot – red segments are shots to retake), "N von 15 aufgenommen ·
  noch N", chips for failed uploads ("N nicht gespeichert · Erneut
  versuchen", or "· Speicher voll" when the device's storage refused it),
  refused files, shots to retake and "Prüfung aus" – with several chips each
  shows only its icon and count (and the counts read "N von 15 · noch N"),
  so counts and chips stay one row; every chip is 24 px high with a 44 px
  tap area where the status line does not lie below it – and
  ONE check status line: a check that just finished, "Noch N Fotos neu
  aufnehmen" after a retake, "Speicher voll – Foto nicht gespeichert",
  briefly "Bitte Smartphone quer halten", else the current shot's state or,
  before it is captured, its "Tipp" (tap = full text below the header, with
  "Jetzt wiederholen" for another red shot). After a retake with no shot
  missing the camera goes to the next red shot; it leaves for "Fotos
  überprüfen" only when none is red. The shot shown is kept with the
  camera's history entry (`history.state`): a reload or a tab the phone
  discarded reopens the camera at that shot, not at the shot of the link it
  was opened with.
- **Landscape (short screens):** the title line shows `n / 15 · noch N`, the
  chips show only their icon and count ("Prüfung aus" keeps its word), the
  check status line sits next to the progress (the progress keeps at least
  25rem, the line truncates), saving shows a spinner.
- **Nothing on the framing guide:** every message about photos and checks is
  in the header. "Bitte Smartphone quer halten" (an exterior shot while the
  picture is portrait) shows in the status line for 3.5 s – once while
  exterior shots follow each other in portrait; its full text names the
  iPhone and Android rotation settings – and the instruction box then says
  "Smartphone quer halten". The instruction box carries only the
  instruction and the light bulb that opens "Tipps für perfekte Fotos" (with
  the current shot's hint first) – a sheet that also opens by itself once
  per device (again after a sign-out) when the camera opens for a vehicle
  without photos. The shot's short `tip` is in the header's status line, and
  the box sits at the viewfinder's bottom edge but is as wide as the stage,
  so its text keeps few lines and stays below the framing guide – also in
  the narrow 9:16 portrait viewfinder of a small phone.
  Other short messages (toasts, e.g. a failed capture) appear in the middle
  of the viewfinder while the camera is open; elsewhere they appear at the
  top.
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
- **HEIC:** the file pickers do not list HEIC/HEIF, so iOS converts photos
  from the library to JPEG, which every browser can show. A HEIC file picked
  another way (e.g. from the Files app) is accepted and stored as-is;
  browsers other than Safari may not be able to display it.

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
   the camera immediately advances to the next shot. In demo mode a photo
   only counts as saved once IndexedDB has committed it; a full browser
   storage leaves it in the queue ("Foto konnte nicht gespeichert werden…")
   instead of losing it.
3. Failed uploads stay on the device, are shown with a warning badge
   ("nicht gespeichert"), retried automatically when the connection returns
   and on the next app start, or manually ("Erneut versuchen") – network,
   session and server errors. A full device storage (demo mode) is told
   apart (`storage_full`, `src/lib/offline/upload-status.ts`): the camera
   says "Speicher voll" and that captures the storage could not keep are
   lost when the app is closed; the vehicle page says so instead of "Sie
   bleiben auf diesem Gerät."; such a capture is saved as soon as the storage
   takes it again.
4. **Refused files** – a type the storage does not take (only JPG, PNG, WebP,
   HEIC/HEIF) or more than 50 MB – are final: "Dieses Bildformat wird nicht
   unterstützt (JPG, PNG, WebP oder HEIC)." / "Das Foto ist zu groß (max.
   50 MB).". They are not retried automatically, and "Erneut versuchen" (camera
   header, vehicle page) counts and retries only failures a retry can fix. The
   camera shows "N nicht speicherbar · bitte verwerfen", the vehicle page
   "… Bitte das Foto antippen und in der Großansicht „Verwerfen“ wählen.".
5. **"Verwerfen"** (large view, after "Foto verwerfen?") deletes a capture that
   could not be saved from the device; a previously saved photo of the shot
   stays. For a temporary failure "Erneut speichern" stays the main action,
   "Verwerfen" is offered below it. The stored record is deleted first – if
   that fails, the capture stays and the reason is shown in the question
   (likewise for "Foto wirklich löschen?"). A question closes when another
   photo is shown or the large view is closed. A delete or discard on its way
   belongs to its photo or capture: only that photo's question shows it as
   running, and only that question ends with its answer or shows its error.
   While a capture is being discarded it is neither uploaded nor retried; one
   that is being uploaded right now cannot be discarded ("Das Foto wird gerade
   gespeichert und kann deshalb nicht verworfen werden.") and is saved after
   all. A capture discarded in another tab of the same device is not uploaded
   by this one – unless this tab had already started uploading it: there is
   no lock across tabs, so that capture is saved after all and becomes the
   shot's photo although the other tab discarded it.
6. **The newest capture of a shot is its photo** (by capture time, demo and
   Supabase alike – `src/lib/photos/capture-order.ts`, `add_vehicle_photo()`):
   an older capture whose upload arrives late (retried after a retake, or from
   a second device) is stored archived and never replaces the newer one. Once
   a newer capture is saved, the queue drops older unsaved captures of the
   same shot; a refused file older than a photo saved elsewhere is removed
   from the device when the vehicle is opened.
7. Retries are idempotent (the queue id is the photo id), and ids do not
   depend on `crypto.randomUUID` (only available in secure contexts such as
   HTTPS; without it they come from `crypto.getRandomValues`,
   `src/lib/ids.ts`), so "Foto hochladen" and demo vehicles also work over
   plain HTTP in the LAN (the live camera needs HTTPS, see above).

"Aufnahmen abschließen" is only possible once all required photos are saved
on the server, and "Fotos verarbeiten" waits for captures still being saved.
No full offline sync is implemented on purpose.

---

## Image processing

### Principle

**The real vehicle is never regenerated or recreated with generative AI.**
Paint colour, wheels, badges, headlights, body shape, visible equipment,
scratches, damage, wear and the interior must stay truthful. Only the
surroundings change. Original photos are never modified – results are
separate files.

### Configuration

Two independent decisions (pure helpers in `src/lib`):

| Variable | Values | Decides |
| --- | --- | --- |
| `NEXT_PUBLIC_DATA_BACKEND` | `demo` · `supabase` · unset | where vehicles and photos live. Unset: Supabase if its env is complete, otherwise demo. `demo` forces demo mode even with Supabase env. `supabase` without Supabase env → demo + console warning. (`src/lib/data/backend-mode.ts`; used by `getBackendMode()`, `createBackend()`, `authenticateRequest()`, `src/proxy.ts`) |
| `IMAGE_PROCESSOR` | `mock` (default) · `real` | whether a processor is connected. `mock` means **no processor** – there are no simulated results. `real` needs `IMAGE_PROCESSING_API_URL` (+ `IMAGE_PROCESSING_API_KEY`). (`src/lib/processing/processor-config.ts`) |
| `IMAGE_PROCESSING_API_URL` / `_KEY` | URL, secret | the processor's base URL (path prefix allowed) and Bearer key – server only, never sent to the browser |
| `PROCESSING_ACCESS_CODE` | any text | demo mode only (see below) |
| `NEXT_PUBLIC_IMAGE_PROCESSOR` | `real` · `mock` | UI hint for the first render; the real state comes from `GET /api/processing-status` |

**"Bearbeitet" only ever shows real processor results.** Without a
processor (`IMAGE_PROCESSOR=mock`) the processing page says "Echte
Showroom-Bearbeitung ist noch nicht verbunden.", the start button is disabled
and nothing is saved. A configured processor that does not answer (dealer PC
asleep, restarting, or no answer from the status request) shows "Der
Showroom-Rechner ist gerade nicht erreichbar. Bitte später erneut
versuchen." with "Erneut prüfen", and an offline phone "Keine
Internetverbindung – die Bearbeitung ist wieder möglich, sobald Sie online
sind." (`src/lib/processing/processor-connection.ts`); "Fotos verarbeiten"
is enabled only while connected. If the
processor reports that its showroom plates are missing or invalid
(`showroomSource: "missing"`), the page shows "AutoExperten Showroom-Master
fehlt." and stays disabled; a job that was not composited onto the plates is
never stored (`src/lib/processing/processor-contract.ts`). If the processor
reports a configuration problem (`presetError` in its `/health`, e.g. a
missing official logo), the page shows "Der AutoExperten Showroom ist auf dem
Server nicht richtig eingerichtet." and stays disabled. In Supabase mode a
processor without access to the database and storage (`supabase: false` in
its authenticated `/health`) counts as not connected: "Der
Bildverarbeitungs-Service ist nicht mit der Fahrzeug-Datenbank verbunden.
Bitte den Administrator informieren." (the server log names
`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`). Photos the processor's quality
gate rejects (vehicle too small, cropped, wrong perspective, an object glued
to the outline, a lost mirror, …) fail with a German retake hint and
`errorCode`; the shot is offered for "Foto neu aufnehmen"
(`src/lib/processing/quality-gate.ts`). Photos without a processed version
show "Noch nicht bearbeitet" – never the original as a stand-in.

### API (browser ⇄ app)

```
GET  /api/processing-status            → { connected, processor, showroomSource, showroomError, connectionError, accessCodeRequired, dataBackend }
POST /api/process-photo                Supabase mode: { vehicleId, photoId, preset } → 202 { jobId, status }
POST /api/process-upload               demo mode: multipart file (image ≤ 4.4 MB), preset, shotKey → 202 { jobId, status }
GET  /api/process-job/:jobId           → { jobId, status, progress, result, error, warnings, … }
GET  /api/process-job/:jobId/result    demo mode: image/jpeg (409 while not ready)
status: queued | processing | complete | failed
preset: autoexperten_standard | autoexperten_dark | original_plus   (only the first is available)
result: { kind: "stored", processedStoragePath } | { kind: "file", width, height, bytes }
```

The UI only uses `src/lib/processing/processing-client.ts`. The route handlers
delegate to the `ImageProcessor` interface (`src/lib/processing/types.ts`);
`RealImageProcessor` is the only code that talks HTTP to the processor
(`POST {url}/jobs`, `POST {url}/jobs/upload`, `GET {url}/jobs/{id}`,
`GET {url}/jobs/{id}/result`, `GET {url}/health`, always with the Bearer key).
Job ids are validated (`^[a-f0-9]{32}$`) before they are forwarded; processor
errors become German messages, and the processor's own German job errors
(e.g. "Das Fahrzeug konnte im Foto nicht erkannt werden.") are shown per photo.
Retries in the client (`processing-client.ts`):

- a full processor queue (503 `busy`) is retried for up to 10 minutes per
  photo with exponential back-off and "equal jitter": the wait starts at
  1.5–3 s and doubles up to 30–60 s (3 s … 60 s, half of it fixed, half
  random); in demo mode each attempt sends the upload copy (at most 4 MB)
  again – every 30–60 s in a long busy phase;
- status polls tolerate a lost connection and 5xx answers (back-off 2/4/8/15 s)
  as long as the last answer is at most 5 minutes old; 4xx answers fail at once.
  The 5-minute processing timeout starts when the job leaves the processor's
  queue – a queued job may wait up to 30 minutes;
- the result download (demo mode) retries a lost connection or 5xx answers
  with the same back-off for about a minute (409 "not ready" is retried as
  well); 4xx such as 404 (expired result) fail at once.

**Two adapters, chosen by the data backend** (`src/lib/processing/photo-processing.ts`):

- **Supabase:** `POST /api/process-photo` → processor `POST /jobs` (it reads the
  original from `vehicle-originals` and writes `vehicle-processed/…`) → poll →
  `result.kind = "stored"` → `recordProcessedPhoto`. Requires the login.
- **Demo (upload):** photos only exist in the browser (IndexedDB), so the
  client takes the original Blob, makes a **copy** that stays below Vercel's
  4.5 MB request limit (long edge ≤ 3200 px, JPEG 0.9, lower quality if still
  > 4 MB, EXIF orientation applied; a photo the browser cannot decode is sent
  unchanged if ≤ 4 MB) → `POST /api/process-upload` → processor `POST /jobs/upload`
  → poll `GET /api/process-job/:id` → download `GET /api/process-job/:id/result`
  (the app streams the processor's JPEG; the browser never sees the processor's
  URL, key or result URL) → `saveProcessedPhoto` stores it as a separate file in
  IndexedDB. Originals are never changed. The processed version is recorded on
  the photo in one IndexedDB read-modify-write transaction
  (`recordProcessedPhoto`): a retake or "Foto löschen" saved meanwhile is kept
  – only the processed fields change, and an archived photo never becomes
  active again.

**Which photos:** exterior shots 01–08 get the AutoExperten showroom (4:3).
Interior/detail shots 09–15 are sent too – the processor returns them in their
original environment (it never composites non-exterior shot keys), labelled
"Innenraum/Detail – Originalumgebung" in the results. Extra photos are not
processed. A vehicle becomes "Bearbeitet" only when every required shot has a
real processed version.

**Access code (public demo deployments):** in demo mode there is no login. If
`PROCESSING_ACCESS_CODE` is set, `/api/process-upload` and `/api/process-job/*`
require the header `x-processing-access-code` (constant-time compare, else
401 `access_code_required`); the processing page asks once for the
"Zugangscode für die Bildbearbeitung" and keeps it in `localStorage` on that
device. **Without it, anyone who can open the demo deployment can use your
processor.** Supabase mode uses the normal login instead.

**Vercel never runs the model.** The Next.js app (on Vercel) only proxies
small requests; the processor runs on its own VM/container (see below).
Exterior results are 2400 × 1800 JPEGs (preset `output.longEdgeMin/Max`,
quality 92), interior/detail shots keep their aspect ratio at a long edge of
at most 2400 px – typically ≈ 0.75–1.4 MB, streamed through
`/api/process-job/:id/result`.

### The processing page ("Fotos bearbeiten")

- **Styles:** only "AutoExperten Standard" can be chosen; `autoexperten_dark`
  and `original_plus` are shown as "Demnächst verfügbar"
  (`src/lib/processing/presets.ts`, `available: false`). The app never submits
  them, and the processor rejects them with 400 `preset_unavailable`
  ("Dieser Bearbeitungsstil ist noch nicht verfügbar."), nothing queued.
- **Start blockers** (`src/lib/processing/run-blockers.ts`): "Fotos
  verarbeiten" is disabled while new photos of the vehicle are still being
  saved ("Neue Fotos werden noch gespeichert. Bitte kurz warten.") and for a
  vehicle with the demo's placeholder drawings ("Beispielfahrzeug – bitte ein
  eigenes Fahrzeug fotografieren.").
- **Runs** are kept per vehicle outside the screen
  (`src/lib/processing/processing-runs.ts`): leaving and reopening the page
  shows the running or finished run, and the same vehicle cannot be started
  twice. At most three photos are processed at a time. Right before submitting
  (start and every retry) the run reads the vehicle's current photos again,
  and each shot once more right before its photo is sent, so a retake saved a
  moment ago – even while the shot waited in the run – is processed instead
  of the photo it replaced, before the page shows it; a photo is never
  processed twice in one run (a queued row whose shot's current photo already
  has a row is dropped). A run ends when the user signs out or another user
  signs in (its uploads and polls are aborted; the next person starts with the
  style selection); "Anderen Stil wählen" also aborts work still on its way.
  A running run is kept on the device (`stored-runs.ts`, per user) and
  continues when its page opens again after a reload or a discarded tab:
  finished rows stay, open rows use the shot's current photo, jobs already
  submitted are polled instead of sent again. The page holds a screen wake
  lock while it runs (`src/lib/device/screen-wake-lock.ts`). "Abmelden" and
  "Fahrzeug löschen" forget stored runs and kept jobs.
- **Processor gone during a run:** while submitted jobs get no answer (dealer
  PC asleep or restarting, connection lost) their rows say "Wartet auf
  Rechner" and the run card "Der Showroom-Rechner ist gerade nicht
  erreichbar. Die Bearbeitung geht automatisch weiter, sobald er wieder
  antwortet – bitte die App geöffnet lassen." – the jobs are polled again
  with back-off (a job the restarted processor no longer knows is sent once
  more).
- **Per photo:** temporary failures (connection, busy processor) offer
  "Erneut versuchen". A rejected photo offers "Foto neu aufnehmen"; a photo
  retaken during or after the run offers "Neues Foto bearbeiten" (the row is
  brought into view once) and the summary "Neues Foto bereit – jetzt
  bearbeiten" / "N neue Fotos bereit – jetzt bearbeiten" (processed three at a
  time, like a run). Shots whose photo was replaced count as "N neue(s)
  Foto(s) noch nicht bearbeitet", not as processed. A new capture that is
  still uploading shows its upload state – for a failed, retryable upload its
  own German reason with "Erneut versuchen"; a capture the storage refused for
  good is left to "Fotos überprüfen" ("Verwerfen"), and a capture older than
  the shot's saved photo does not count as a new photo.
- **Known limits:** a retake saved in another tab or on another device while
  its shot waits in a run is processed, but the page still shows the old
  photo list and may offer "Neues Foto bereit – jetzt bearbeiten", which then
  does nothing until the page is reloaded. "Foto löschen" on a shot that
  still waits in a run does not stop it: in demo mode the deleted photo is
  processed all the same and its row counts as done (the photo stays
  deleted); in Supabase mode `/api/process-photo` refuses it and the row fails
  ("Foto wurde nicht gefunden.").
- **Retake messages** come from the processor in German, e.g. the new ones
  "Ein anderes Objekt (z. B. ein Fahrzeug, Schild oder Pfosten im
  Hintergrund) berührt die Fahrzeugkontur. Bitte so fotografieren, dass
  hinter dem Fahrzeug freier Hintergrund ist.", "Ein Teil des Fahrzeugs (z. B.
  ein Außenspiegel) hebt sich nicht vom Hintergrund ab und würde im Ergebnis
  fehlen. Bitte vor ruhigerem Hintergrund neu fotografieren.", "Für
  Außenaufnahmen das Smartphone bitte quer halten." and (`image_too_large`)
  "Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler
  Kamera-Auflösung neu aufnehmen."; all codes and texts:
  `processor/README.md` → Quality gate / job error codes.
- **Processor hints (warnings)** are shown under each finished row in the
  processor's German text (`src/lib/processing/processing-warnings.ts`,
  `ProcessingWarningList`): hints to check or retake in amber, information
  hints (`low_resolution`, `vehicle_reduced`, `vehicle_small`) subtly.
  `plate_mirrored` and `background_object_suspected` offer "Foto neu
  aufnehmen"; the summary asks "Bitte N Ergebnisse prüfen". Hints to check
  include `vehicle_part_uncertain` – a small part of the car (e.g. at a mirror
  or a light) may be missing from the result. Unknown codes count as "please
  check". The hints are stored with the processed version
  (demo: on the IndexedDB photo record; Supabase: `processing_warnings`,
  written by `recordProcessedPhoto`) and shown later in "Bearbeitet" – a badge
  on the tile, a line on the vehicle page, the text in the large view. A new
  processing replaces them, a new photo starts without. Texts over 300
  characters are shortened with "…"; at most 20 hints / 12 KB are kept, hints
  to check or retake first.

### Instant check in the camera

Right after a capture of a template shot is saved, the photo is processed in
the background with the standard style – the same processing as "Fotos
bearbeiten", results saved the same way (`src/lib/processing/photo-checks.ts`).
The camera's shot segments turn green ("Geprüft – passt"), amber ("Geprüft –
bitte Ergebnis ansehen") or red ("Bitte neu aufnehmen: <Grund>"); interior
and detail shots are only passed through and show a neutral "Gespeichert".
One wording on every screen (`SHOT_CHECK_LABELS` in `shot-check.ts`).

- **Off** without a processor ("noch nicht verbunden"), while the
  Showroom-Rechner is unreachable ("gerade nicht erreichbar" – asked again
  every 60 s while the camera, "Fotos überprüfen" or the vehicle page is shown
  and the tab is visible), offline, or in demo mode without the stored access
  code. Captures wait and are checked once the checks are on again.
- **Opening** the camera, "Fotos überprüfen" or the vehicle page checks the
  template photos without a check, a result or a stored rejection
  (`checkOnOpen`); a check that failed is not sent again on every visit.
- **Reloads:** every submitted job is kept on the device per user
  (`job-journal.ts`, 24 h) and polled again after a reload instead of sending
  the photo a second time; a job the processor no longer knows (404 after a
  restart) is submitted once more.
- **A rejection is kept with the photo** (`rejected:<errorCode>` in
  `processing_warnings` while it has no result, `DataProvider.recordPhotoRejection`
  – a conditional update allowed by the existing "Members update photos"
  policy), so every device – the desktop that downloads the ZIP as well as
  the phone that took the photo – knows the shot must be retaken. Rejections
  and failed checks are also kept on the device (localStorage) for the
  states of this device.
- **Which state a shot shows** – one rule (`getShotCheckState`) for the
  camera, "Fotos überprüfen", the vehicle page, the vehicle lists / Kamera tab
  and the export: a check in progress, then a result (saved by this tab's
  check, else saved with the photo), then the rejection kept with the photo,
  then the outcome kept on this device. A rejection never hides a saved
  result – except on the device where "Erneut prüfen" sent a photo with a
  result again and the processor now refuses it: that newer verdict wins
  (also after a reload) as long as the result is unchanged (the photo's
  `updatedAt`); a result saved later wins again. A check that could not run
  never hides what was saved.
- **Forgotten:** "Fahrzeug löschen" stops the vehicle's checks and forgets
  its outcomes and kept jobs; "Abmelden" and "Alle Daten auf diesem Gerät
  löschen" forget all of them (a rejection stays saved with its photo).

### Real processor (Stage 2 prototype) – `processor/`

A separate Python service (FastAPI + Pillow + OpenCV + ONNX Runtime, CPU)
implements the contract above. Full documentation: **[processor/README.md](processor/README.md)**.

```
original photo
 → decode: JPEG/PNG/WebP/HEIC, EXIF orientation, ICC → sRGB; headers checked before Pillow parses
   the file (declared size, chunk/segment floods, animated PNG/WebP refused); single-scan
   sequential JPEGs of 80–320 MP (108/200 MP phone modes) decoded at 1/2, other photos above
   80 MP → image_too_large; exterior shots: plate set and official logo checked (a missing or
   unusable one fails the job before any model runs); resolution gate (long edge ≥ 1600 px)
 → vehicle detector (RF-DETR-Seg, COCO instance segmentation, local ONNX) – semantic check only
 → vehicle segmentation (BiRefNet, local ONNX model; portrait/square photos on a window around the
   detected vehicle)
 → alpha: model alpha snapped onto colour edges (never grown beyond the model's outline, thin parts
   such as antenna masts kept), clean-up (a traffic cone / post standing behind the car is cut from
   the outline, with a warning); semantic check: a foreign object that is a separate part of the
   mask is dropped, one glued to the outline rejects the photo, and so does a vehicle part the
   cut-out lost (e.g. a dark mirror; after a window pass it is first taken from a whole-frame
   BiRefNet pass if that has it); a partly lost piece (e.g. a mirror base) only gets the hint
   vehicle_part_uncertain – nothing is drawn in
 → vehicle geometry (tyre contacts, near end of 3/4 views, contact rise)
 → plate of the shot's angle (3D showroom, 8 cameras; mirrored 3/4 plate if the photo shows the other side)
 → placement on the plate (target width 81 % for 3/4, 85 % side, 62 % front/rear, centred,
   lowest tyre contact on the plate's ground line, roof below the branding)
 → quality gate (too small / cropped / mask / perspective / tyre contacts / foreign object /
   missing part → German retake hint, nothing stored)
 → cut-out of the ORIGINAL vehicle pixels, conservative light matching (colour guard)
 → branded plate (official logo + texts in wall space, lit by the plate's light)
 → grounding: tyre contact shadows, underbody, the plate's rendered car shadow – on the floor only;
   floor reflection of the original vehicle pixels
 → edge harmonisation (decontamination, light wrap)
 → JPEG 4:3, 2400 × 1800, quality 92, sRGB, no EXIF
```

**Truthful-vehicle policy:** no generative image AI anywhere (no DALL·E,
Stable Diffusion, Flux, Midjourney, Generative Fill, image-to-image). Paint
colour, wheels, badges, lights, glass, damage and wear are the photographed
pixels. Vehicle corrections are capped by hard limits in code
(`processor/app/pipeline/light.py`) and reverted if the measured hue or
saturation of the vehicle would change. Interior shots are not composited.
Scenery seen through the windows and outdoor reflections in the paint stay –
they are photographed vehicle pixels (window and relighting treatments were
evaluated and removed because they changed stickers, livery, interior and dark
paint; see `processor/README.md` → Known limitations).

**Vehicle detector:** a second local model, RF-DETR-Seg Medium (COCO instance
segmentation, Apache-2.0, 130 MB, pinned Hugging Face revision + SHA-256),
only *checks* the BiRefNet cut-out: it finds the photographed vehicle (the
window for portrait/square photos) and tells which outline regions belong to
it, to another object or to nothing it knows, and which vehicle parts the
cut-out lacks. It never draws the cut line – BiRefNet stays the matting model
– and never generates, moves or changes a pixel. Cost: ≈ 0.1–0.3 GB extra
RAM and ≈ 4–12 s per exterior job (up to ≈ 23 s on a CPU shared with other
jobs).

**Known limitations** (details in `processor/README.md`): on crowded lots
more photos ask for a retake – an object behind the car that the cut-out
glues to the outline (a car parked behind, a hubcap, a sign, a bollard) and a
vehicle part the cut-out lost (e.g. a dark mirror in front of a dark car) are
rejected with a retake message, never repaired or drawn in (8 of the 21
normally framed real test photos, each of which would have given a visibly
wrong result). A smaller lost piece (e.g. a mirror base or the edge of a
light) is accepted with only the hint `vehicle_part_uncertain`, and the
result lacks it. Scenery seen through the windows and reflections in the paint
stay. Portrait photos are segmented in a window around the car but give it
fewer pixels (portrait frames at stream resolution are rejected with "Für
Außenaufnahmen das Smartphone bitte quer halten."), and the test sets contain
no real upright car photos – the portrait cases are derived from landscape
photos. Jobs live in the processor's memory: a restart forgets running jobs.

**Run it locally** (Python 3.12+)

```bash
cd processor
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8000      # GET http://localhost:8000/health
```

or with Docker (context = repo root; the image refuses to start without
`PROCESSOR_API_KEY` – `PROCESSOR_REQUIRE_API_KEY=true` is set in it):

```bash
docker build -f processor/Dockerfile -t autoexperten-processor .
docker run --rm -p 127.0.0.1:8000:8000 --env-file processor/.env autoexperten-processor
```

`processor/.env` (from `processor/.env.example`) holds `PROCESSOR_API_KEY`,
so the key never appears on the command line (`-e PROCESSOR_API_KEY` without
a value takes it from the calling shell instead). `127.0.0.1:` keeps the port
on this machine – `-p 8000:8000` would publish it on every interface, past
the host firewall. Only for a local test without a key add
`-e PROCESSOR_REQUIRE_API_KEY=false`. Existing virtualenvs need
`pip install -r requirements-dev.txt` again after an update (the processor
requires Pillow 13).

The segmentation model (224 MB, ≈ 7 GB peak RAM per running job on the CPU;
the lighter `isnet-general-use` needs ≈ 1.5 GB) makes the processor unsuitable
for Vercel functions – run it on a VM/container with ≥ 12 GB RAM. The vehicle
detector (130 MB) adds ≈ 0.1–0.3 GB and ≈ 4–12 s per exterior job.

**Test one real car photo**

1. Start the processor (above) and `npm run dev`.
2. In `.env.local` set `IMAGE_PROCESSING_API_URL=http://localhost:8000`
   (and `IMAGE_PROCESSING_API_KEY` = the processor's `PROCESSOR_API_KEY`).
3. Open **http://localhost:3000/dev/processing-test** – choose a photo, style
   "AutoExperten Standard", shot, **Verarbeiten** → Original / Bearbeitet side
   by side → "Ergebnis herunterladen".
   The page is not linked anywhere and is only served in development (or with
   `ENABLE_DEV_TOOLS=true`, internal deployments only).
4. Without the app: `cd processor && .venv/bin/python -m app.cli car.jpg -o /tmp/out.jpg --debug-dir /tmp/dbg`.

**Connect the app** (`.env.local`): `IMAGE_PROCESSOR=real`,
`NEXT_PUBLIC_IMAGE_PROCESSOR=real`, `IMAGE_PROCESSING_API_URL`,
`IMAGE_PROCESSING_API_KEY`. In demo mode that is all (photos are uploaded). In
Supabase mode the processor additionally needs `SUPABASE_URL` +
`SUPABASE_SERVICE_ROLE_KEY` (processor side only) to read originals and store
results in `vehicle-processed`. No UI changes needed.

**Debugging:** `PROCESSOR_DEBUG=true` keeps `original.jpg`, `mask.png`,
`geometry.jpg`, `grounding.jpg`, `vehicle-transparent.png`, `background.jpg`
(branded plate), `composite-before-shadow.jpg`, `shadow.png`, `final.jpg` and
`metadata.json` per job (`processor/data/jobs/<id>/debug/`, also shown on the
dev test page) and serves `/docs` + `/openapi.json`. Never enable it on a
public instance.

### AutoExperten showroom preset

`public/presets/autoexperten-standard.json` is the processing preset
(plate set, branding layout, output size, placement limits, shadow, floor
reflection, adjustment limits, quality gate) – used by the processor and
referenced from `src/lib/processing/presets.ts`.

**One 3D showroom, eight plates.** The AutoExperten showroom is one physical
3D room (`processor/showroom3d/render_plates.py`, Blender, CC0 assets,
modelled after the approved design
`public/presets/autoexperten-standard-reference.jpg`) rendered from one
camera per exterior shot into `public/presets/autoexperten-standard/`
(`<shot>.jpg` empty plate, `<shot>-shadow.png` floor shadow of a typical car,
`<shot>-reflection.png` the floor's own reflection, `plates.json` camera +
floor/wall homographies). Every vehicle goes onto the
plate of its photographed angle, so perspective, floor and light fit.
Rendering and checking: [`public/presets/README.md`](public/presets/README.md).

**Branding:** the official logo PNG, "SCHWETZINGEN", www.autoexperten-rn.de and
+49 6202 9262357 are composited deterministically onto the brand wall of each
plate through its wall homography (`processor/app/showroom/wall_branding.py`,
layout in the JSON `branding` section as fractions of the brand wall) – never
AI-generated, never over the vehicle; the vehicle roof stays below them.

**No silent fallback:** without a complete, valid plate set every exterior job
fails with "AutoExperten Showroom-Master fehlt." and nothing is saved; there
is no procedural stand-in background.

The look of `autoexperten_standard` (default):

- bright premium showroom, clean white / light-gray wall
- warm ceiling spotlights (~3000 K)
- blue vertical LED accent lighting (AutoExperten blue)
- premium warm wood / parquet floor
- vertical wood-slat wall panels, plants (as in the reference mockup)
- brand wall: official logo, Schwetzingen, www.autoexperten-rn.de, +49 6202 9262357

`autoexperten_dark` (dark premium showroom) and `original_plus` (original
background, only light/colour/contrast optimised) are defined in the app but
not implemented by the processor yet: the processing page shows them as
"Demnächst verfügbar", and the processor answers 400 `preset_unavailable`
("Dieser Bearbeitungsstil ist noch nicht verfügbar.") without queuing a job.

The processor validates the preset strictly (unknown keys, wrong types,
NaN/Infinity and out-of-range values are configuration errors, never replaced
by defaults – ranges in `processor/README.md` → Presets and assets); a broken
preset, a missing official logo or an output aspect that does not fit the
plates shows up as `presetError` in its `/health`, and the app then blocks
processing up front.

---

## Project structure

```
src/
  app/                         routes (App Router)
    (app)/layout.tsx           auth gate for everything below
    (app)/(tabs)/…             screens with bottom navigation
    (app)/(capture)/…/kamera   full-screen guided camera
    api/processing-status      GET  – is a real processor connected (health check)
    api/process-photo          POST – start a processing job (Supabase mode)
    api/process-upload         POST – upload a photo for processing (demo mode)
    api/process-job/[jobId]    GET  – job status (+ /result: processed JPEG, demo mode)
    dev/processing-test        developer test page (dev only, not linked)
    api/dev/processing-test/…  its proxy routes to the processor (dev only)
    login/, offline/, manifest.ts, layout.tsx, globals.css
  features/                    screen components (compose components + hooks)
  components/                  reusable UI
    brand/ layout/ ui/ vehicles/ photos/ camera/ processing/ export/ auth/ providers/
  hooks/                       auth, data loading, upload queue, camera stream
  lib/
    shots/        shot template + pure progress/ordering logic
    vehicles/     validation, status rules, summaries
    naming/       export names & storage paths
    export/       listing export: set + summary, share sheet/download, ZIP writer, file loader
    data/         DataProvider/AuthService + mock (IndexedDB) & Supabase impls
    workflow/     operations that keep vehicle status in sync (incl. saveVehicleData)
    offline/      IndexedDB helper, upload queue
    photos/       photo slots, capture-order rule (shared with add_vehicle_photo)
    camera/       getUserMedia, capture, image utils, picked-file check, quality-check interface
    processing/   API contract, config, presets, RealImageProcessor, client, upload prep, adapters,
                  run store (processing-runs.ts), start blockers, processor hints, quality gate
    api/          processing access (login / demo access code), processor error mapping
    supabase/     config, browser + server clients
    app-services.ts  client service container
  config/brand.ts  company data, colours, logo assets
  proxy.ts         Supabase session refresh / redirect (Next 16 "proxy")
public/
  overlays/  camera framing guides (SVG)    demo/shots/  demo placeholder photos
  icons/     PWA icons (official monogram)  brand/       official logo (+ official/ originals)
  presets/   showroom preset JSON + 3D showroom plates (autoexperten-standard/)  sw.js  service worker
supabase/migrations/   schema, RLS, storage (applied in file order, see Configure Supabase)
scripts/generate-demo-assets.mjs
processor/             Python image processor (FastAPI) – see processor/README.md
  app/pipeline/        decode, detection (vehicle detector), segmentation, matting, mask,
                       vehicle_geometry, placement, quality, light, grounding/shadow, reflection,
                       composite, export
  app/jobs/ storage/   job manager, Supabase photo store
  app/showroom/        plate set (plates.py), wall-space branding (official logo + texts)
  scripts/             download_models.py, render_showroom_preview.py, visual regression tools
  showroom3d/          Blender scene + cameras of the 3D showroom, plate post-processing
  deploy/              Docker Compose + Caddy (HTTPS) for a server
  tests/               pytest (synthetic plate sets: tests/plate_fixtures.py; visual/ = real-photo set)
```

---

## Tests & quality checks

`npm test` runs Vitest:

- required shot completion, missing shots, progress
- shot ordering (template order, never time) and guided navigation
- export file naming / storage paths
- vehicle validation (required fields, VIN, mileage, dates, plates)
- vehicle status rules
- data backend / image processor configuration
- processor adapter (URL joining, auth header, upload, result streaming, health, error mapping)
- processor response parsing (no `resultUrl` leak, fallback showroom never "complete")
- processing client (busy retry, access code, result download) and the two adapters
- upload preparation rules, shot treatment (showroom vs. original environment)
- processing access code, demo data migration (fake processed versions removed)
- photo slots (incl. pending uploads and extra photos), capture order (newest
  capture wins, 5-minute cap), picked-file check, capture times
- upload queue (refused files, "Verwerfen" – refused while the capture is
  being saved, respected by another tab –, older captures dropped), IndexedDB
  commit handling and one-transaction updates, demo and Supabase data
  providers (late uploads archived, conditional vehicle saves, keyset paging,
  missing `processing_warnings` column, a processed result recorded without
  undoing a retake), static migration guard (`migrations.test.ts`)
- processing runs (incl. a retake saved while its shot waits), start blockers,
  processor hints, quality-gate retakes
- component and screen tests: camera, photo viewer, review, vehicle pages,
  "Fahrzeugdaten", processing page and job list, login, "Mehr", `ShotProgress`,
  `VehicleForm`

`cd processor && .venv/bin/python -m pytest` runs the processor tests (API
validation/auth incl. the required API key of the Docker image, job lifecycle
incl. quality-gate failures, plate-set and preset validation, wall-space
branding, vehicle geometry, quality gate, placement, grounding, reflection,
mask and matting, vehicle detector and semantic check, portrait window pass,
compositing output, colour guard, decoding limits, debug output, Supabase
store). `processor/tests/visual/` describes the real-photo regression set.

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
