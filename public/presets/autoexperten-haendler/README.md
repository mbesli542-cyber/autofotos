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
| side views | 0.92 of the width (≈ 4 % margin each side), ground line v = 0.655, 2.4 m to the wall |
| front / rear | 0.68 of the width, nose ≈ 2.0 m from the camera, tyre contacts v = 0.80, 1.38 m to the wall |

**Side views stand higher than the 3/4 views – that is the fixed camera, not a placement
error.** For a level camera at height h the ground line of a car seen side-on lies
h · w · W / L below the horizon (w = width ratio, W = frame width, L = car length) –
independent of the focal length. With h = 1.6 m and w = 0.92 (the largest width that keeps a
margin) a 4.85 m car stands at v ≈ 0.655, so about a third of the frame below it is floor
(where the processor adds the car's own reflection). Standing at v ≈ 0.75 would need a camera
≈ 2.0 m high, which would push the 3/4 views' front wheel from v ≈ 0.81 (his references) to
≈ 0.94; a per-shot crop of the plate would break "one camera, one backdrop" and cost
resolution (the plate is already enlarged 1.66 ×). The 3/4 views have their near corner
≈ 2 m closer to the camera, so they stand lower.

**His lettering sits low on the wall (bottom of the rules at v ≈ 0.20)** and the processor keeps
every roof below the branding plus `branding.clearance` (0.025 of the height). His own
reference photos keep the roof 0.03–0.08 of the height below his rules (ref2/ref4 close-ups ≈ 0.03,
ref3 ≈ 0.08) – the clearance already allows the closest of them, so it is kept, and the logo
stays where he put it. Cars whose photo is taller than the 4.85 × 1.46 m proxy at the target
width are placed smaller ("headroom"): a 4.05 m hatchback at ≈ 0.82 of the target (≈ 0.68 of
the frame – about what a car of that length occupies on that spot of a real studio:
0.83 · 4.05 / 4.85 = 0.69), SUVs and estates likewise; below 0.9 of the target the result gets
the `vehicle_reduced` hint, as with the shipped plates (which reduce the same hatchback to 0.84).

Regression photos on this plate set (real pipeline; target fraction / share of the frame width):

| photo | view | here | shipped plates |
| --- | --- | --- | --- |
| Mercedes S63 (chat copy, relaxed gate) | front_left_45 | 1.00 / 0.83 | 1.00 / 0.81 |
| Peugeot 407 saloon | front_left_45 | 0.96 / 0.80 | 1.00 / 0.81 |
| Renault Clio hatchback | front_left_45 | 0.82 / 0.68 ⚑ | 0.84 / 0.68 ⚑ |
| VW Passat estate (white) | front_left_45 | 0.899 / 0.75 ⚑ | 0.89 / 0.72 ⚑ |
| Citroën C-Crosser SUV | front_right_45 | 0.84 / 0.70 ⚑ | 0.92 / 0.74 |
| Audi A5 coupé | front_right_45 | 1.00 / 0.83 | 1.00 / 0.81 |
| Audi A4 allroad estate | rear_left_45 | 0.93 / 0.77 | 1.00 / 0.81 |
| Honda CR-V SUV | rear_left_45 | 0.79 / 0.65 ⚑ | 0.86 / 0.69 ⚑ |
| Audi RS4 Avant | rear_right_45 | 0.87 / 0.72 ⚑ | 0.95 / 0.77 |
| Mitsubishi Lancer | left_side | 0.95 / 0.88 | 1.00 / 0.85 |
| Mercedes W123 | right_side | 1.00 / 0.92 | 1.00 / 0.85 |
| Toyota Yaris (rear) | rear | 0.88 / 0.60 ⚑ | 1.00 / 0.62 |

⚑ = `vehicle_reduced` hint. The four photos not in the shipped regression set (C-Crosser, A5,
CR-V, RS4 – candidates of the same Wikimedia set, CC0 / CC BY-SA) are listed in
`processor/showroom3d/dealer/extra_regression_cases.json` (`visual_regression.py --manifest … --preset-file
public/presets/variants/autoexperten-haendler.json`). No accepted straight FRONT photo exists in the regression set (its
Yaris front is rejected for a lost mirror); the front plate uses the rear's camera pose turned by
180°, its loading and shadow pass are covered by the tests.

## Files

- `plate.jpg` – THE plate of all eight shots, 2400 × 1800, sRGB (embedded profile).
- `<shot>-shadow.png` – 16-bit linear floor shadow/occlusion multiplier of the proxy car at the
  shot's pose: Blender render of his studio rebuilt as geometry (`render_dealer_passes.py`:
  matte floor – the occlusion of the diffuse floor light; the gloss is the reflection pass –, white panel, recess with the vertical blue LED, walnut slats, skirting with the LED
  line, the right wall section through his junction points, the planter; one warm wall-washer
  spot per light pool, soft ceiling panels) with / without the invisible light-blocking proxy,
  ratio limited to the floor around the car (`postprocess_plates.shadow_multiplier`).
- `reflection.png` – 16-bit RGB at the plate's full size (2400 × 1800, code step 64 ≈ 10-bit
  precision, ≤ 0.0005 linear), the floor's OWN glossy reflection (light streaks, LED lines,
  wall glow), linear (`code / 65535 − reflectionOffset`, offset 0 – the measured reflection is never
  negative and reaches ≈ 0.97 in the streak cores), one file for all shots.
  MEASURED on his floor (`dealer_backdrop.py`): every low-passed floor pixel is split into his
  wood colour, the warm wall-washer light and the AutoExperten-blue LED colour; the light and
  LED parts are the reflection the processor removes where the car blocks it (the wood stays –
  no milky halo, no dark hole), `reflectance` per row calibrated like the rendered plates.
  The smooth split is applied to the decoded `plate.jpg` pixels at full size, so it follows the
  plank seams, and is limited per pixel: what is left after removing it is never more saturated
  than the floor's own reflection-poor wood (1st percentile of g/r and b/r) – the seams inside a
  streak carry no gloss and stay wood instead of turning into orange-red lines.
  **Streak cores keep the unlit wood** (`lift_streak_cores`): in the hot cores of his streaks
  the floor is near white and the split left too little wood under the light – removed in a
  car's mirror image every streak became a dark grey band (cores 6–25 % darker and greyer than
  the wood between the streaks). Per channel the wood left in a core is scaled up to the unlit
  wood next to it in the same rows (smooth gain ≤ 1.8, the plate's grain stays; 3.6 % less
  light removed in all): removed, a core now has 0.98–1.05 of the unlit wood beside it, same
  colour (`tests/test_dealer_backdrop.py` checks the stored pass).
  The car's own reflection uses that `reflectance` (≈ 0.10 where the cars' bumpers stand,
  v ≈ 0.8–0.9 – what a lacquered floor reflects at that viewing angle: Fresnel for n = 1.5 at
  ≈ 60° gives ≈ 0.09; ≈ 0.4 near the wall) × `reflection.strength` (1.5 in "mittel"), capped by
  `reflection.maxReflectance` (0.3), and fades out within ½ car height (`fade` 0.5) – right under
  the car dark paint reflects darker and chrome / light paint lighter, further out his streaks
  come back, as in his reference photos.
- `plates.json` – camera, horizon, floor/wall homographies (identical for all shots), per shot
  the proxy pose, bbox, tyre contacts, wall clearance; `room` documents the measured features.
- `branding.json` – where his lettering was, as fractions of the brand area (the values in the
  variant preset's `branding`).

## Shadows: three strengths

The dealer (on the first results): "the shadows underneath are not good at all, they stay far too
black". Measured on his S63 / Lancer / Clio / C-Crosser with the earlier values (`edgeOpacity`
0.7, `underbodyFalloff` 0.25 m, `ambientOpacity` 0.6; linear floor luminance result ÷ empty plate):
the floor at the tyres and under the sill kept 0.04–0.08 (L\* 73 → 14–19, nearly black) – the
underbody (0.20–0.32) and the rendered ambient pass (0.40–0.50, darkest under the car)
multiplied, plus the removed floor reflection (0.56–0.66); the default `tint` turned those deep
shadows reddish-grey (hue 64° → 33–47°); 0.3 m in front of the car the shadow alone still
kept only 0.67–0.81, beside the bumper ends 0.41–0.64 (a tall dark bar below the rising bumper
tip, the underbody band continued sideways); the removed streaks left dark grey bands (above).

All three strengths keep the wood's hue (`shadow.tint` 1 1 1: hue 63–64° in every shadow),
limit the under-car shadow and the removed-reflection band to the car's fitted footprint
(`footprintReach` 0.3 m: no bar beside the ends; one continuous factor across the floor line –
a first version applied it only above the line and left a hard 10–33 % step on the floor beside
the bumper corners of 10 of the 12 photos, incl. the dark smudge left of the S63's bumper and the
arcs at the CR-V's and A4's rear corners) and keep the car's own reflection near the
car (`reflection.fade` ≤ 0.6, strength 1.2–1.5 of the measured Fresnel reflectance, capped at
0.25–0.3): dark paint darker, chrome and light paint lighter, blurred like the plate's gloss.

| | tyres | under the sill | shadow 0.3 m in front | beside the ends | variant file |
| --- | --- | --- | --- | --- | --- |
| earlier | 0.04–0.08 (L\* 14–19) | 0.05–0.06 (L\* 17–18) | 0.67–0.81 | 0.41–0.64 | – |
| leicht | 0.16–0.27 (L\* 30–36) | 0.20–0.23 (L\* 36–38) | 0.93–0.98 | 0.99–1.0 | `../variants/autoexperten-haendler-leicht.json` |
| **mittel** | 0.12–0.22 (L\* 26–32) | 0.14–0.16 (L\* 30–31) | 0.89–0.97 | 0.97–0.99 | `../variants/autoexperten-haendler.json` |
| kräftig | 0.09–0.17 (L\* 22–27) | 0.10–0.11 (L\* 24–26) | 0.86–0.96 | 0.95–0.99 | `../variants/autoexperten-haendler-kraeftig.json` |

"Mittel" is the recommendation and the variant file; 1 m in front of the car the shadow is 1.0
in all three (the floor there only loses its streaks inside the car's mirror image and keeps
the unlit wood).

The RS4 from behind is the one photo whose tyre contacts are wrong: its exhaust tip and the
diffuser corner are taken as the rear tyres and the real rear tyre as the front one (the front
tyre is not found – the far sill lies lower in the picture than its bottom). The side line
through them ran far below the sill and drew a brown wedge from the rear wheel to the front. Their
floor distance is 0.24 × the proxy's wheelbase (real photos 0.69–2.5), so the ground model no
longer trusts them (`MIN_WHEELBASE_RATIO` 0.5 in `grounding.py`): the floor line follows the
outline + 15 cm there, and the result carries the check hint `ground_contact_uncertain`.

## Quality gate: photos from above

The gate's "photo from above" rule compares the photo with the plate's proxy car (contact rise
of the 3/4 views and bbox aspect, `quality.highViewRiseFactor` / `highViewAspectFactor`). His
studio camera (1.6 m, the car 4.5 m away) sees the proxy with more rise (0.1905 vs 0.1546 /
0.1591 in the earlier 3D room) and a lower aspect (1.934 / 1.937 vs 2.144 / 2.136). With the
shipped factors 1.7 / 0.8 the Lada photographed from a window above (regression case
`too_high`: rise factor 1.48, aspect factor 0.798) was accepted – a toy car seen from above in a
level studio. All three files therefore use the earlier room's ABSOLUTE thresholds on this plate,
the more generous of its front/rear 3/4 plates: 1.42 (= 1.7 × 0.1591 / 0.1905) and 0.88
(≈ 0.8 × 2.1355 / 1.9372) – never stricter than before for a normal photo (here at most 1.35:
Clio, 1.34: MG 5), `too_high` is rejected again ("Die Perspektive passt nicht zum Showroom …").
`dealer_backdrop.py` writes them (`VARIANT_QUALITY`); `test_the_dealer_gate_keeps_the_earlier_rooms_high_view_thresholds`
checks the relation on the plate set.

## Car light: studio light match

The dealer on the results with the old car light: "the lighting is very bad – the car is too
dark / different from the room". All three strengths therefore carry the same
`vehicleAdjustments.studioLight` block (his decision, 2026-11-05; written by
`dealer_backdrop.py`, `VARIANT_STUDIO_LIGHT`): ONE guarded global light mapping of the original
vehicle pixels towards the key light of this plate around the car's spot – exposure ≤ +0.8 EV
(half of the measured gap), contrast 0.3 only above the car's median, highlight shoulder (knee
0.85, ≥ 65 % of the highlight detail), white balance = colour temperature only towards the warm
studio (≤ ±6 %, reference outside the paint), never darker than the original, the paint guard
(hue ≤ 2.5°, saturation ±10 %, near-neutral paint ≤ 1.5 ΔE, tone class ≤ 6 L\*) scaling it back.
How it works and its limits: `processor/README.md`, "Studio light match".

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

`base.png` renders pixel-identically (fixed seed), so after a pose change in `dealer_layout.py` only
the changed shots need a new proxy render (`--shots left_side,right_side,front,rear`, the other
`<shot>-proxy.png` copied next to it); `dealer_backdrop.py` refuses renders whose `layout.json`
differs from `dealer_layout.layout()`.

Geometry check of the Blender studio against his image: `render_dealer_passes.py -- --out DIR --check`
(724 px render with the proxy car visible; compare its edges with `clean.png`).

Real-ESRGAN comparison (optional, its own virtualenv with PyTorch; weights from the GitHub
releases of xinntao/Real-ESRGAN, SHA-256 pinned in `dealer_upscale.py`):

```bash
/opt/ae-ai/esrgan-venv/bin/python processor/showroom3d/dealer_upscale.py esrgan \
  /opt/ae-dealer/clean/clean.png /opt/ae-dealer/up/esrgan-x2.png \
  --weights /opt/ae-ai/esrgan/weights/RealESRGAN_x2plus.pth --scale 2
```
