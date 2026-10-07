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
  (`archived_at`). DB trigger + storage policies enforce this.
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
- **The future AI processing API must stay easy to connect**: the UI only talks
  to `/api/process-photo` + `/api/process-job/:jobId`; implementations sit
  behind the `ImageProcessor` interface.

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
| Offline-tolerant upload queue | `src/lib/offline/upload-queue.ts` |
| Camera (stream, capture, quality-check interface) | `src/lib/camera/`, `src/components/camera/` |
| Processing contract, presets, mock/real processors | `src/lib/processing/` |
| API routes | `src/app/api/process-photo`, `src/app/api/process-job/[jobId]` |
| Client service container | `src/lib/app-services.ts` |
| DB schema, RLS, storage policies | `supabase/migrations/` |
| Brand config / logo placeholder | `src/config/brand.ts`, `src/components/brand/BrandLogo.tsx` |

Routes: `/login`, `/fahrzeuge`, `/fahrzeuge/neu`, `/fahrzeuge/[id]`,
`/fahrzeuge/[id]/kamera`, `/fahrzeuge/[id]/fotos`, `/fahrzeuge/[id]/bearbeiten`,
`/fahrzeuge/[id]/daten`, `/kamera`, `/bearbeiten`, `/mehr`.

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
- Demo mode is automatic when `NEXT_PUBLIC_SUPABASE_URL`/key are missing.
- Logo: real assets go to `/public/brand/`; switch via `LOGO_ASSETS.useAssetFiles`.

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

Run `npm run check` before committing.
