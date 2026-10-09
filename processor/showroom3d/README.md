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

```bash
python3 processor/showroom3d/fetch_assets.py /opt/showroom-assets          # CC0 assets (once)
B=blender   # Blender 4.2 LTS
$B -b --factory-startup -P processor/showroom3d/render_plates.py -- \
    --assets /opt/showroom-assets --out /tmp/plates --pass shadow --shadow-width 1200 --shadow-samples 48
$B -b --factory-startup -P processor/showroom3d/render_plates.py -- \
    --assets /opt/showroom-assets --out /tmp/plates --pass plate --width 2400 --samples 32
cd processor && .venv/bin/python showroom3d/postprocess_plates.py /tmp/plates ../public/presets/autoexperten-standard
# floor reflection pass only (gloss-free floor, ≈ 3.5 min per shot), added to the existing set:
$B -b --factory-startup -P processor/showroom3d/render_plates.py -- \
    --assets /opt/showroom-assets --out /tmp/plates --pass reflection --shadow-width 1200 --shadow-samples 48
cd processor && .venv/bin/python showroom3d/postprocess_plates.py /tmp/plates ../public/presets/autoexperten-standard --reflection-only
```

Quick look: `--preview` (800 px, 16 samples), `--reference-view` (straight-on
view comparable to the design reference), `--debug-proxy` (makes the car proxy
visible to check the framing). A full 2400 px plate takes ≈ 15–25 min on 4 CPU
cores, the shadow pass ≈ 3 min per shot.

## Assets (all CC0, Poly Haven)

| Asset | Use |
| --- | --- |
| [laminate_floor_02](https://polyhaven.com/a/laminate_floor_02) | oak plank floor (warmed in the shader, lacquer coat) |
| [oak_veneer_01](https://polyhaven.com/a/oak_veneer_01) | slat panels (tinted to walnut) |
| [painted_plaster_wall](https://polyhaven.com/a/painted_plaster_wall) | wall micro structure (normal map only) |
| [island_tree_02](https://polyhaven.com/a/island_tree_02) | olive-like trees in the planters |

Everything else (room, slats, LEDs, planters, lights) is modelled in
`render_plates.py`.
