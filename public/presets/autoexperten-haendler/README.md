# AutoExperten Händler-Hintergrund – the dealer's own showroom as a fixed studio plate

**Provenance.** `plate.jpg` is the showroom background **supplied by the dealer**
(AutoExperten Schwetzingen, 1448 × 1086, `processor/showroom3d/dealer/haendler_hintergrund.png`),
**made with an image AI**; **cleaned and upscaled with classical image processing**:

1. the AI-drawn lettering on the wall ("AutoExperten" wordmark, "Schwetzingen", the two
   rules) – NOT the official logo – was removed by harmonic inpainting of the smooth wall
   (`processor/showroom3d/dealer_clean.py`: ink + drop-shadow mask, Laplace fill from the wall
   around and between the letters, the wall's own grain σ ≈ 0.5 levels; no generative AI).
   The cleaned plate contains no text and no logo;
2. upscaled once to the preset's output size 2400 × 1800 with **Lanczos-3 in linear light +
   a mild luminance unsharp mask** (`dealer_upscale.py lanczos`, σ 0.8 px, amount 0.35,
   threshold 0.5 levels). Real-ESRGAN (`RealESRGAN_x2plus` / `RealESRGAN_x4plus`, BSD-3-Clause
   code and weights) was compared at 100 %: it repaints the floor grain into smeared strokes
   with invented plank edges and outlines the olive leaves – rejected. The Lanczos plate has
   no invented texture; at 100 % it is as soft as a 1.66× enlargement is (the car itself is
   never upscaled more than the quality gate allows).

Nothing in this folder is generated per photo; the processor composites the ORIGINAL vehicle
pixels onto `plate.jpg`, the official logo and "Schwetzingen" are composited in wall space at
the place and size of his lettering (`branding` of `../variants/autoexperten-haendler.json`).

## One fixed studio camera, the car is turned

His image shows the wall frontally, the vertical edges vertical – like a photo studio his
reference photos keep the camera and the backdrop and only turn the car. All eight exterior
shots therefore use **the same plate and the same camera** (`processor/showroom3d/dealer_layout.py`):

| | |
| --- | --- |
| camera | level, vertical lens shift (horizon at v = 0.254), 66° HFOV (phone main camera), 1.6 m high, 8.25 m from the wall |
| wall/floor junction | v = 0.453 (bottom of his skirting, row 492 of 1086) |
| brand area | the white panel, x −4.04 … 3.14 m (7.18 m), z 0 … 4.0 m |
| proxy car | 4.85 × 1.88 × 1.46 m, wheelbase 2.9 m |
| 3/4 views | 45° to the camera's line of sight, 0.83 of the frame width, 1.47 m of floor between the far corner and the wall |
| side views | 0.88 of the width, 2.2 m to the wall |
| front / rear | 0.60 of the width, 1.10 m to the wall |

With a fixed camera the car stands where a 4.85 m car of that width has to stand: in the side
views (car ≈ 4.4 m away) the ground line is at v ≈ 0.65, so more floor shows below the car
than in the 3/4 views (front wheel at v ≈ 0.81).

His lettering sits low on the wall (bottom at v ≈ 0.20). The processor keeps every roof below
the branding (`branding.clearance`), so taller cars are placed somewhat smaller than the target
width ("headroom": in the regression set 0.90–0.96 of the target for saloons/estates, 0.82 for
a hatchback – the shipped plates reduce the same hatchback to 0.84 for the floor rule).

## Files

- `plate.jpg` – THE plate of all eight shots, 2400 × 1800, sRGB (embedded profile).
- `<shot>-shadow.png` – 16-bit linear floor shadow/occlusion multiplier of the proxy car at the
  shot's pose: Blender render of his studio rebuilt as geometry (`render_dealer_passes.py`:
  matte floor – the occlusion of the diffuse floor light; the gloss is the reflection pass –, white panel, recess with the vertical blue LED, walnut slats, skirting with the LED
  line, the right wall section through his junction points, the planter; one warm wall-washer
  spot per light pool, soft ceiling panels) with / without the invisible light-blocking proxy,
  ratio limited to the floor around the car (`postprocess_plates.shadow_multiplier`).
- `reflection.png` – 16-bit RGB, the floor's OWN glossy reflection (light streaks, LED lines,
  wall glow), linear (`code / 65535 − reflectionOffset`, offset 0 – the measured reflection is never
  negative and reaches ≈ 0.97 in the streak cores), one file for all shots.
  MEASURED on his floor (`dealer_backdrop.py`): every low-passed floor pixel is split into his
  wood colour, the warm wall-washer light and the AutoExperten-blue LED colour; the light and
  LED parts are the reflection the processor removes where the car blocks it (the wood stays –
  no milky halo, no dark hole), `reflectance` per row calibrated like the rendered plates.
- `plates.json` – camera, horizon, floor/wall homographies (identical for all shots), per shot
  the proxy pose, bbox, tyre contacts, wall clearance; `room` documents the measured features.
- `branding.json` – where his lettering was, as fractions of the brand area (the values in the
  variant preset's `branding`).

## Reproducing

From the repository root (`B` = Blender 4.2 LTS):

```bash
processor/.venv/bin/python processor/showroom3d/dealer_clean.py --out /opt/ae-dealer/clean \
  && processor/.venv/bin/python processor/showroom3d/dealer_upscale.py lanczos \
       /opt/ae-dealer/clean/clean.png /opt/ae-dealer/up/lanczos.png \
  && $B -b --factory-startup --python-exit-code 1 -P processor/showroom3d/render_dealer_passes.py -- \
       --out /opt/ae-dealer/render --width 1200 --samples 64 \
  && processor/.venv/bin/python processor/showroom3d/dealer_backdrop.py \
       --plate /opt/ae-dealer/up/lanczos.png --render /opt/ae-dealer/render \
       --measure /opt/ae-dealer/clean/measure.json --out public/presets/autoexperten-haendler \
       --standard public/presets/autoexperten-standard.json \
       --variant public/presets/variants/autoexperten-haendler.json
```

Geometry check of the Blender studio against his image: `render_dealer_passes.py -- --out DIR --check`
(724 px render with the proxy car visible; compare its edges with `clean.png`).

Real-ESRGAN comparison (optional, its own virtualenv with PyTorch; weights from the GitHub
releases of xinntao/Real-ESRGAN, SHA-256 pinned in `dealer_upscale.py`):

```bash
/opt/ae-ai/esrgan-venv/bin/python processor/showroom3d/dealer_upscale.py esrgan \
  /opt/ae-dealer/clean/clean.png /opt/ae-dealer/up/esrgan-x2.png \
  --weights /opt/ae-ai/esrgan/weights/RealESRGAN_x2plus.pth --scale 2
```
