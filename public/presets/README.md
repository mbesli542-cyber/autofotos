# Showroom presets

Assets of the AutoExperten showroom processing (used by the processor in
`processor/`, see `processor/README.md`).

| File | Purpose |
| --- | --- |
| `autoexperten-standard.json` | Preset "AutoExperten Standard" – **the dealer's own studio** with "Schatten mittel" (identical to `variants/autoexperten-haendler.json` except name and description): plate set, **branding layout** (wall space), output size (2400 × 1800), placement limits, shadow, reflection, vehicle adjustments incl. the studio light match (`vehicleAdjustments.studioLight`, the dealer's decision), quality gate. Validated strictly by the processor (`processor/app/presets.py`, ranges in `processor/README.md` → Presets and assets): an invalid value is a configuration error (`presetError` in `/health`), never replaced by a default. |
| `autoexperten-haendler/` | The **showroom plates**: the dealer's own showroom image (made with an image AI, his lettering removed, upscaled once) as ONE fixed studio plate for all eight shots (`plate.jpg`, per-shot `<shot>-shadow.png`, one `reflection.png`, `plates.json`, `branding.json`). Provenance, studio camera, shadow strengths and recipe: its `README.md`. |
| `variants/` | The same look in the three shadow strengths (`autoexperten-haendler.json` = "mittel" = shipped, `-leicht`, `-kraeftig`) – complete presets. See `variants/README.md`. |
| `autoexperten-standard-reference.jpg` | Showroom design supplied by AutoExperten earlier (1448 × 1086) – the model of the earlier Blender room (`processor/showroom3d/render_plates.py`; the app's preset spec names it as look reference). Its lettering is generated, **not** the official logo, so it is never used as a background. |

The eight plates of the earlier Blender room (`autoexperten-standard/<shot>.jpg`,
`-shadow.png`, `-reflection.png`, `plates.json`, ≈ 23 MB) are no longer shipped – no preset
uses them; `render_plates.py` re-renders them, or take them from the git history (990d9f7).

## How a listing photo is built

```
the dealer's showroom image (processor/showroom3d/dealer/haendler_hintergrund.png)
   └─ AI lettering removed (dealer_clean.py), upscaled once (dealer_upscale.py, Lanczos)
                                                  → plate.jpg (empty wall, NO text/logo)
   └─ one fixed studio camera, the car turned per shot (dealer_layout.py)
                                                  → plates.json (camera, floor/wall homographies,
                                                    proxy car per shot)
   └─ Blender shadow pass of the proxy car per pose (render_dealer_passes.py)
                                                  → <shot>-shadow.png (floor shadow of a typical car)
   └─ the floor's own reflection measured on the plate (dealer_backdrop.py)
                                                  → reflection.png
 + official branding in wall space where his lettering was (logo PNG + "Schwetzingen" between
   two thin rules – no contact data, processor/app/showroom/wall_branding.py, layout in the
   JSON `branding`)
 + original vehicle pixels on the plate entry of its angle (studio light match within the paint
   guard), contact + floor shadow, the car's reflection
 = listing photo (identical room for every Mercedes, BMW, Audi, Porsche, VW …)
```

Nothing is generated per photo and nothing in the car is generated; the
branding is never drawn over the vehicle and the vehicle roof stays below it.
Without a complete, valid plate set every exterior job fails with
"AutoExperten Showroom-Master fehlt." – there is no fallback background.

## Plate set

```
autoexperten-haendler/
  plate.jpg                 (4:3, 2400 × 1800, sRGB, no branding – the image of all eight shots)
  <shot>-shadow.png         (per shot; 16-bit grey, linear multiplier, 65535 = no shadow,
                             lower resolution)
  reflection.png            (16-bit RGB, the floor's own glossy reflection, linear, signed:
                             code/65535 − reflectionOffset; shared by all shots)
  plates.json               (metadata per shot, see processor/app/showroom/plates.py)
  branding.json             (where his lettering was, fractions of the brand area)
  README.md
```

`plates.json` per shot: `image` (here always `plate.jpg`), `camera` (position,
height, distance, 66° HFOV, focal length), `horizonV`, `floorHomography` (floor
x, y in metres → normalised u, v), `wallHomography` (wall x, z → u, v), `anchor`
(where the car stands), `vehicle` (view, heading, `targetWidthRatio` – car width
/ frame width: 0.83 for 3/4 views, 0.92 side, 0.68 front/rear –, proxy car size,
projected `proxyBBox` and `proxyContacts`), `wall` (`albedo`, `brandArea` = the
white panel in metres), `shadow`, `reflection` + `reflectance` ([[v, k], …], the
floor's measured specular reflectance per image row). The processor validates
all of it on load (also per-shot plate images – the loader takes both forms);
the resolution is free (plates are scaled to the output size). The processor
removes the plate's own reflection where a car blocks it and adds the car's
mirrored pixels with that reflectance (processor/app/pipeline/reflection.py).

Building and reproducing the set (clean, upscale, Blender passes, `dealer_backdrop.py
--variant` writes the plate set and the three variant files): `autoexperten-haendler/README.md`
→ Reproducing. After a rebuild copy the "mittel" variant over the standard file and keep its
`name`/`description` (see `variants/README.md`).

### Checking the plates

```bash
processor/.venv/bin/python processor/scripts/render_showroom_preview.py -o /tmp/plates.jpg --guides     # all 8 shots
processor/.venv/bin/python processor/scripts/render_showroom_preview.py -o /tmp/p.jpg --shot front --width 3200 --guides
(cd processor && .venv/bin/python -m pytest -q tests/test_plates.py tests/test_showroom_variants.py)   # loads the shipped set
```

Cyan = wall/floor junction, orange = ground line (lowest tyre contact of the
typical car), red = projected typical car and its tyre contacts, magenta =
branding boxes and the highest point a vehicle roof may reach. The same
branded plate is served by the processor at
`GET /showroom/autoexperten_standard/<shot>.jpg?width=2400`.

### The earlier Blender room

`processor/showroom3d/render_plates.py` (Blender 4.2 LTS, CC0 Poly Haven assets via
`fetch_assets.py`) + `postprocess_plates.py` render the earlier 3D room – eight cameras, a
`<shot>.jpg` per shot – into a plate set of the same format (see `processor/showroom3d/README.md`).
It is no longer shipped; a preset that should use it again needs the set back in a folder of
`public/presets/` and its `background.plates` pointing there.

## Branding layout

All `branding` values are fractions of the **brand wall** (`wall.brandArea`;
the dealer's white panel, 7.18 m × 4.0 m): `centerX`/`maxWidth` of its width, `top`/`maxHeight`/
`capHeight` of its height (0 = top, 1 = floor). The same layout therefore
appears at the same place on the wall in every plate, in the correct
perspective. The shipped values put the official logo and "Schwetzingen" with its rules
exactly where his AI lettering was (measured by `dealer_clean.py`, `branding.json`). Tune
`logo.top`, `logo.maxWidth`, `city.top`, `website.top`,
`phone.top`, `capHeight`, `color`, `opacity`; `clearance` is the free space
(fraction of the output height) the vehicle roof keeps below the lowest
element. The logo is lit by the plate's own wall light (matte print), so
`lightMatch` stays 0.

**No dealer contact data in the photos.** `website.text` and `phone.text` stay
empty (an empty text is not drawn): mobile.de's dealer terms (AGB-HB, valid
from 1 April 2025, listings no. 9) forbid the dealer's contact data such as
phone number or e-mail address in listing photos, and no. 14 forbids web
addresses in a listing. AutoScout24.de ("Anforderungen an die Gestaltung von
Anzeigen" 2.1) does not allow photos with integrated logos or text at all –
for AutoScout24 a look without wall branding may be needed (`branding.enabled`
false). `test_listing_photos_carry_no_dealer_contact_data` guards the shipped
preset and the variants.

`autoexperten-dark` (dark showroom) is planned; no assets yet.
