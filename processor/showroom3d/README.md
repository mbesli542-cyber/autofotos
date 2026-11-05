# AutoExperten Standard – 3D showroom and angle-specific plates

The background of every exterior photo is ONE physical 3D room, built after the
approved design (`public/presets/autoexperten-standard-reference.jpg`):
warm-white brand wall with wall-wash spots and floor up-lights, walnut slat
panels with vertical AutoExperten-blue LED strips, olive trees in black
planters, black ceiling tracks, lacquered oak plank floor.

It is rendered (Blender Cycles, deterministic, no generative AI) from eight
cameras, one per exterior shot. All eight plates show the same room – only the
camera position, height and direction change:

| Shot | Camera | Car in the frame (typical car) |
| --- | --- | --- |
| `front_left_45`, `rear_right_45` | left of the car, looking right (wall recedes with the car's long side) | ≈ 81 % of the width |
| `front_right_45`, `rear_left_45` | mirrored (right of the car) | ≈ 81 % |
| `left_side`, `right_side` | straight on, slightly off-centre | ≈ 85 % |
| `front`, `rear` | straight on | ≈ 62 % |

Cameras: 66° horizontal field of view (smartphone main camera, 4:3), 1.25–1.4 m
height (phone at chest height); the direction is solved so a typical car
(4.75 × 1.88 × 1.46 m) standing on the anchor is centred and its lowest tyre
contact sits at 86 % (3/4) / 84 % (side, front, rear) of the frame height.

The wall is rendered EMPTY. The official logo and the texts are composited by
the processor in wall space through the plate's wall homography
(`processor/app/showroom/wall_branding.py`), so they sit on the wall in the
correct perspective and under the plate's real lighting.

## Files (`public/presets/autoexperten-standard/`)

- `<shot>.jpg` – plate, 2400 × 1800, sRGB.
- `<shot>-shadow.png` – 16-bit linear multiplier (65535 = no shadow): soft
  shadow/occlusion of a typical car standing on the anchor, rendered with an
  invisible light-blocking car proxy (`render with proxy / render without`),
  limited to the floor around the car. The processor aligns it to the real car.
- `<shot>-reflection.png` – optional 16-bit RGB, the floor's own glossy
  reflection (LED streaks, wall glow, sheen) in linear light, signed
  (`code / 65535 − reflectionOffset`): the shipped plate (downsampled) minus a
  render of the same view with a gloss-free floor (`--pass reflection`). With
  `reflectance` in plates.json (the floor's measured specular reflectance per
  image row). The processor removes it where the car blocks it and adds the
  car's own reflection with that reflectance.
- `plates.json` – camera, horizon, floor and wall homographies, anchor, proxy
  car (bbox, tyre contacts) per plate. Read by `processor/app/showroom/plates.py`.

## Re-rendering

The commands – all run from the repository root, Blender called with
`--python-exit-code 1` before `-P` so that a missing script or a Python error
fails the step – are in
[`public/presets/README.md` → Rendering the plates](../../public/presets/README.md#rendering-the-plates):
`fetch_assets.py` (CC0 assets, once), `render_plates.py` (plates + shadow
passes, then `--pass reflection`), then
`processor/.venv/bin/python processor/showroom3d/postprocess_plates.py <render dir> public/presets/autoexperten-standard`
(`--reflection-only` to refresh only the reflection pass). Run the
post-processing only after the renders succeeded (the recipe chains the steps
with `&&`): without `<shot>-glossfree.png` it drops that shot's reflection
pass from `plates.json` and still exits with 0 – the full post-processing
silently, `--reflection-only` with one "no reflection pass" line per shot.

`render_plates.py` options: `--shots front_left_45,left_side`, `--width`
(default 3200; the shipped plates are 2400), `--samples` (default 160),
`--pass plate,shadow,reflection` (default `plate,shadow`), `--shadow-width` /
`--shadow-samples` (1200 / 48), `--no-shadow`. Quick look: `--preview`
(800 px, 16 samples), `--reference-view` (straight-on view comparable to the
design reference), `--debug-proxy` (makes the car proxy visible to check the
framing). A full 2400 px plate takes ≈ 15–25 min on 4 CPU cores, the shadow
pass ≈ 3 min per shot, the reflection pass ≈ 3.5 min per shot.

## Assets (all CC0, Poly Haven)

| Asset | Use |
| --- | --- |
| [laminate_floor_02](https://polyhaven.com/a/laminate_floor_02) | oak plank floor (warmed in the shader, lacquer coat) |
| [oak_veneer_01](https://polyhaven.com/a/oak_veneer_01) | slat panels (tinted to walnut) |
| [painted_plaster_wall](https://polyhaven.com/a/painted_plaster_wall) | wall micro structure (normal map only) |
| [island_tree_02](https://polyhaven.com/a/island_tree_02) | olive-like trees in the planters |

Everything else (room, slats, LEDs, planters, lights) is modelled in
`render_plates.py`.

## Dealer backdrop (variant "Händler-Hintergrund")

The dealer supplied his own showroom background (`dealer/haendler_hintergrund.png`, made with an
image AI). It is used as ONE fixed studio plate for all eight exterior shots (like a photo
studio: camera and backdrop stay, the car is turned) – plate set
`public/presets/autoexperten-haendler/`, preset `public/presets/variants/autoexperten-haendler.json`.

| Script | Step |
| --- | --- |
| `dealer_clean.py` | removes his AI-drawn lettering (wordmark, "Schwetzingen", rules) from the wall: ink + drop-shadow mask, harmonic (Laplace) fill, the wall's grain; writes `measure.json` (where the lettering was) and a seam check |
| `dealer_upscale.py` | upscales once to 2400 × 1800: `lanczos` (shipped: Lanczos-3 in linear light + mild unsharp mask) or `esrgan` (Real-ESRGAN x2plus/x4plus, BSD-3, own virtualenv – compared and rejected: invented floor grain) |
| `dealer_layout.py` | the fixed camera (level, lens shift, 66° HFOV, 1.6 m, 8.25 m to the wall – junction row measured in his image) and per shot the proxy-car pose (3/4 0.83, side 0.92, front/rear 0.68 of the width); pure numpy, also imported by Blender |
| `render_dealer_passes.py` | Blender: his studio rebuilt as geometry and lights (no external assets), `base.png` + `<shot>-proxy.png` (invisible light-blocking proxy car); `--check` renders a geometry check |
| `dealer_backdrop.py` | assembles the plate set: `plate.jpg`, `<shot>-shadow.png` (ratio of the renders), `reflection.png` (the floor's own reflection MEASURED on his floor: wood / wall-washer light / blue LED colour split, stored at full size and saturation-limited so plank seams inside a streak stay wood), `plates.json`, `branding.json`; `--variant` writes the variant preset |

The full recipe is in `public/presets/autoexperten-haendler/README.md`.
