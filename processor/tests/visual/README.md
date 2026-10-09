# Visual regression set (real photos)

`regression_set.json` lists 15 real photographs of real cars. They are used to
check the exterior pipeline (`processor/app/pipeline/`) for visual realism
(showroom plate per shot, grounding/shadows, matting, light harmonisation) and
to check that the quality gate (`app/pipeline/quality.py`) accepts and rejects
the right photos.

**The images are not in this repository.** They are third-party photos under
open licences. Only the manifest and this README are committed. The images
live in a local cache, by default `/opt/ae-regression/images/`
(`cacheDirDefault`). Never commit them, and never commit output images that
contain them.

## Manifest

`regression_set.json` has `{"version": 1, "cacheDirDefault": ..., "cases": [...]}`.
The 8 required cases come first, then the extras. Each case has:

| Field | Meaning |
| --- | --- |
| `id`, `caseId` | Case name (one image per case, so both are the same) |
| `category` | `body_style`, `lighting`, `quality_gate`, `angle` or `paint` |
| `shotKey` | Guided-camera shot the photo matches (`src/lib/shots/shot-template.ts`) |
| `expected` | `accept`, or the gate code the job should fail with: `vehicle_too_small`, `perspective_mismatch`, `vehicle_cropped` |
| `file` | File name in the cache directory |
| `url` | Exact URL the cached file was downloaded from |
| `landingUrl`, `title`, `author`, `authorUrl`, `license`, `licenseUrl` | Source and attribution |
| `width`, `height`, `sha256` | Properties of the cached file |
| `originalUrl`, `originalWidth`, `originalHeight` | Full-size original on Wikimedia Commons |
| `bodyStyle`, `source`, `notes` | Description, framing, light and known quirks |

Shot keys follow the framing overlays in `public/overlays/`:

- `front_left_45`: front on the **left** of the frame, left side of the car runs to the right.
- `front_right_45`: front on the **right** of the frame, right side runs to the left.
- `rear_left_45`: rear on the **right** of the frame, left side runs to the left.
- `rear_right_45`: rear on the **left** of the frame, right side runs to the right.
- `left_side`: profile with the nose to the left. `right_side`: profile with the nose to the right.
- `front`, `rear`: straight on, centred.

Every angle was checked by hand: which flank is visible, where the front wheels
are, and which side the steering wheel is on. Commons file titles are not
always right. For example, the Passat B8 "front view" is a front-left 3/4 shot.

## Cases

| # | id | shotKey | expected | Photo |
| --- | --- | --- | --- | --- |
| 1 | `sedan_fl45` | front_left_45 | accept | Peugeot 407 saloon, silver, street, hard sun, car ~72 % of frame width |
| 2 | `coupe_fr45` | front_right_45 | accept | Nissan 350Z, orange, industrial yard, partly in hard shadow, ~84 % |
| 3 | `suv_fr45` | front_right_45 | accept | Nissan X-Trail T31, black, snowy dealer lot, overcast, ~70 % |
| 4 | `hatch_fl` | front_left_45 | accept | Renault Clio V, red, inside a dealership (glossy floor), ~70 % |
| 5 | `estate_rr45` | rear_right_45 | accept | Audi A4 Avant allroad, black, snowy dealer lot, ~69 % |
| 6 | `old_hardlight` | right_side | accept | Mercedes W123, golden hour, very low sun, long shadow, strong colour grade, ~53 % |
| 7 | `too_far` | right_side | vehicle_too_small | MG ZS EV at a viewpoint, car only ~24 % of frame width |
| 8 | `too_high` | rear_left_45 | perspective_mismatch | Lada Samara photographed from an upper floor, roof clearly visible |
| 9 | `side_left` | left_side | accept | Mitsubishi Lancer, left profile, snowy lot, ~90 % (the side overlay uses ~89 %) |
| 10 | `side_right` | right_side | accept | Nissan X-Trail (same car as 3), right profile, ~90 % |
| 11 | `front` | front | accept | Toyota Yaris GR Sport, straight front, slushy lot, ~53 % |
| 12 | `rear` | rear | accept | Toyota Yaris GR Sport (same car), straight rear, ~60 % |
| 13 | `dark_paint_rl45` | rear_left_45 | accept | Audi A4 Avant allroad (same car as 5), black, ~65 % |
| 14 | `white_car` | front_left_45 | accept | VW Passat B8 Variant, white car on snow, ~70 % |
| 15 | `cropped` | front_left_45 | vehicle_cropped | BMW E46, damaged bumper, too close: the right frame edge cuts the car at the front door |

All files are the 3840 px wide renditions that Wikimedia serves of larger
originals (long edge 3840 px, so all of them are at least 2160 px high). Several
cars appear in more than one shot (X-Trail, Audi A4, Yaris). This is on purpose:
it lets you compare different shots of the same car, as in a real guided session.

## Fetching the images

`processor/scripts/fetch_regression_set.py` uses only the Python standard
library. It downloads the missing files into the cache one at a time, waits
and retries when Wikimedia answers HTTP 429 (at least 20 s), and checks
`sha256`. Files that are already valid are skipped. A download with another
`sha256` never replaces anything: it is kept as `<file>.mismatch` so you can
check it by eye. The script sends a generic User-Agent and no personal data.

```bash
cd processor
python3 scripts/fetch_regression_set.py                  # every case → /opt/ae-regression/images
python3 scripts/fetch_regression_set.py --cases too_far  # some cases
python3 scripts/fetch_regression_set.py --check          # verify the cache, no network
```

If a rendition URL stops working, `originalUrl` points to the full-size file.
Downscale it to the same width before you use it, and update `sha256`.

## Running the regression

`processor/scripts/visual_regression.py` runs the current pipeline on every
case, the same way the API job manager calls `process_photo`. It is a
developer tool and not part of pytest, because it needs the cached photos and
sometimes the segmentation model.

```bash
cd processor
.venv/bin/python scripts/visual_regression.py --label baseline         # all cases
.venv/bin/python scripts/visual_regression.py --label wip --cases too_far,front
.venv/bin/python scripts/visual_regression.py --compare baseline wip   # A | B per case
.venv/bin/python scripts/visual_regression.py --label wip --collage-only
```

- **Output** goes to `/opt/ae-regression/runs/<label>/` (or `--out`):
  `collage.jpg`, `run.json`, and per case `result.jpg` (accepted only),
  `result.json` (outcome, error code and German message, warnings, metrics,
  metadata) and `debug/` (mask, geometry, cut-out, plate, shadow and so on).
  `--compare` writes `runs/compare-<A>-vs-<B>/collage.jpg` and `compare.json`.
- **Outcome**: `accepted`, `rejected:<code>` (any error with a `code`, such as
  `QualityGateError`) or `crash:<type>`. A case passes when its outcome
  matches `expected`.
- **Masks** are cached in `/opt/ae-regression/masks/` as 16-bit PNG files. The
  key is the sha256 of the decoded RGB, its shape and the segmenter name. With
  every mask cached, the model is never loaded and a run takes about 20 s per
  case. A cache miss runs BiRefNet (about 7 GB of RAM, 30–60 s) in a child
  process under the shared lock `/opt/ae-regression/birefnet.lock`, so the
  machine runs only one at a time. Use `--refresh-masks` after changing
  `segment()` itself, or `--no-cache` to skip the cache.
- **Old test photos**: `--legacy-dir <dir> --label <name>` runs
  `car1.jpg` … `car8.jpg` with fixed shot keys. They are only 1024 px wide, so
  the current gate rejects them (`source_resolution_too_low`). To see the
  composite anyway, use
  `--preset-override quality.min_source_long_edge=1000 --preset-override quality.max_upscale=4`.
  Overrides are recorded in `run.json` and shown in the collage header.

The collages show third-party photos. Keep them out of the repository, and
credit the authors when you share one (the collage footer and the table below
have the credits).

## Rules for new cases

- Use only real photographs of real cars (street, parking lot, dealership, car
  show). Do not use renders, generative-AI images, press studio cut-outs, or
  toy/model cars.
- Allowed licences: CC0, public domain, CC BY, CC BY-SA. CC BY-NC, ND and
  "all rights reserved" are not allowed. Check the licence on the Commons file
  page, not only in a search API.
- Normal (`accept`) cases: long edge at least 3000 px (2400 px minimum), the
  whole car visible with some margin, the car about 45–85 % of the frame width
  (profiles up to about 90 %, like the side overlay), camera at about
  0.8–1.8 m, and nothing standing in front of the car.
- Record author, licence, licence URL and landing page, and add the photo to
  the attribution table below.

## Licences and attribution

The images are not modified. The cached files are Wikimedia's own downscaled
renditions of the originals. CC BY and CC BY-SA require attribution wherever an
image or an output made from it is shown or shared. Reports or collages built
from these photos must credit the author and licence as listed here. CC BY-SA
outputs that are shared must use the same licence.

| Case | Photo (Commons file page) | Author | Licence |
| --- | --- | --- | --- |
| `sedan_fl45` | [Peugeot 407 Sedan pre-facelift in Bandung, Jawa Barat 01](https://commons.wikimedia.org/wiki/File:Peugeot_407_Sedan_pre-facelift_in_Bandung%2C_Jawa_Barat_01.jpg) | [MoCars](https://commons.wikimedia.org/wiki/User:MoCars) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `coupe_fr45` | [Nissan 350Z Premium Pack sunset orange, 2003, view front right](https://commons.wikimedia.org/wiki/File:Nissan_350Z_Premium_Pack_sunset_orange%2C_2003%2C_view_front_right.jpg) | [TeEmKah](https://commons.wikimedia.org/wiki/User:TeEmKah) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `suv_fr45` | [Nissan X-Trail – front right side view](https://commons.wikimedia.org/wiki/File:Nissan_X-Trail_%E2%80%93_front_right_side_view.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `hatch_fl` | [2024 Renault Clio 1.6 E-Tech espirit Alpine at Renault Manchester 01](https://commons.wikimedia.org/wiki/File:2024_Renault_Clio_1.6_E-Tech_espirit_Alpine_at_Renault_Manchester_01.jpg) | [MoCars](https://commons.wikimedia.org/wiki/User:MoCars) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `estate_rr45` | [2023 Audi A4 B9 Avant Allroad - right back view](https://commons.wikimedia.org/wiki/File:2023_Audi_A4_B9_Avant_Allroad_-_right_back_view.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `old_hardlight` | [Mercedes W123 parked by a country road during golden hour near a small town in autumn landscape](https://commons.wikimedia.org/wiki/File:Mercedes_W123_parked_by_a_country_road_during_golden_hour_near_a_small_town_in_autumn_landscape.jpg) | [Shixart1985](https://commons.wikimedia.org/wiki/User:Shixart1985) | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/) |
| `too_far` | [Car parked at the northeast viewpoint at Alexandra Park, Bath 2025-07-22](https://commons.wikimedia.org/wiki/File:Car_parked_at_the_northeast_viewpoint_at_Alexandra_Park%2C_Bath_2025-07-22.jpg) | [Andy Li](https://commons.wikimedia.org/wiki/User:Onthewings) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `too_high` | [Lada Samara parked; Dnipro, Ukraine; 22.10.19](https://commons.wikimedia.org/wiki/File:Lada_Samara_parked%3B_Dnipro%2C_Ukraine%3B_22.10.19.jpg) | [VKras](https://commons.wikimedia.org/wiki/User:VKras) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| `side_left` | [Mitsubishi Lancer (2008) – left side view](https://commons.wikimedia.org/wiki/File:Mitsubishi_Lancer_%282008%29_%E2%80%93_left_side_view.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `side_right` | [Nissan X-Trail – right side view](https://commons.wikimedia.org/wiki/File:Nissan_X-Trail_%E2%80%93_right_side_view.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `front` | [Front view of a grey Toyota Yaris (GR Sport trim) parked outdoors](https://commons.wikimedia.org/wiki/File:Front_view_of_a_grey_Toyota_Yaris_%28GR_Sport_trim%29_parked_outdoors.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `rear` | [Rear view of a grey Toyota Yaris (GR Sport trim) parked outdoors](https://commons.wikimedia.org/wiki/File:Rear_view_of_a_grey_Toyota_Yaris_%28GR_Sport_trim%29_parked_outdoors.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `dark_paint_rl45` | [2023 Audi A4 B9 Avant Allroad - left back view](https://commons.wikimedia.org/wiki/File:2023_Audi_A4_B9_Avant_Allroad_-_left_back_view.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `white_car` | [Volkswagen Passat (B8) – front view (2017)](https://commons.wikimedia.org/wiki/File:Volkswagen_Passat_%28B8%29_%E2%80%93_front_view_%282017%29.jpg) | [Acgskup](https://commons.wikimedia.org/wiki/User:Acgskup) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `cropped` | [Broken car front bumper](https://commons.wikimedia.org/wiki/File:Broken_car_front_bumper.jpg) | [Santeri Viinamäki](https://commons.wikimedia.org/wiki/User:Zunter) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
