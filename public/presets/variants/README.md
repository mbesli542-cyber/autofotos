# Showroom look variants (preview, not active)

Complete presets for the same 3D showroom and plate set as
`../autoexperten-standard.json` (`autoexperten-haendler.json`: the dealer's own
backdrop, plate set `../autoexperten-haendler/`). They are **not** loaded by the processor
(`PRESET_FILES` in `processor/app/presets.py` only knows the standard file);
they exist so the dealer can choose a look. Built from the dealer's reference
images (AI-generated – only the room/composition counts, never the car).

| File | Look |
| --- | --- |
| `autoexperten-standard-v1.json` | **Variante 1** – reference branding: the official logo PNG scaled to 60 % of the brand wall (was 40 %), centred (`centerX` 0.5); "Schwetzingen" (Inter Medium, wide tracking) between two thin lines that span the logo width (`ruleLength`/`ruleGap`/`ruleThickness`); no website/phone (no dealer contact data in listing photos, see `../README.md` – the right-hand contact block of the dealer's reference with Lucide icons, `align: "left"` + `icon: "globe"`/`"phone"`, `processor/app/showroom/icons.py`, is still supported by the renderer but left empty); glossier floor reflection inside the existing limits (`strength` 1.4, `maxReflectance` 0.26, `blurRate` 0.025, `fade` 1.3); 3/4 views at the car width measured in the references (`shotPlacement.*_45.widthScale` 1.06 → 0.86 of the frame) where the car stays clear of the wall (fitted per photo, see below); `branding.clearance` 0.02. For the branding alone without the larger car set the four `widthScale` values to 1.0. |
| `autoexperten-standard-v2.json` | **Variante 2** – V1 + the car stands up to 0.45 m further back in the room (`placement.depthOffsetM`): the plate's proxy car is moved along the camera's viewing ray (`move_proxy_back` in `processor/app/showroom/plates.py`), so ground line, scale (floor homography), tyre-contact pose, contact/ambient shadow and reflection follow. The proxy is limited so 0.5 m of floor stays to the wall (front/rear views: ≈ 0.19 m); the real car is then fitted per photo (below). |

| `autoexperten-haendler.json` | **Händler-Hintergrund** – the dealer's OWN showroom background (supplied by him, made with an image AI) as the fixed plate of all eight exterior shots: `../autoexperten-haendler/` (provenance, camera and recipe in its README). One level studio camera with lens shift, the car turned per shot (3/4 views 0.83 of the width, 1.47 m of floor to the wall; side views 0.92, front/rear 0.68 – a side view stands higher in the frame with one fixed camera, see the plate set's README); his AI lettering is removed from the plate and the official logo + "Schwetzingen" between two thin rules are composited in wall space at the place and size of his lettering (`branding`, measured by `processor/showroom3d/dealer_clean.py`); no website/phone. Stronger underbody shadow (`edgeOpacity` 0.7, `underbodyFalloff` 0.25, `ambientOpacity` 0.6), `reflection.strength` 1.0. Written by `processor/showroom3d/dealer_backdrop.py --variant`. |

Everything not listed (output, shadow, vehicle adjustments, quality gate) is
identical to the standard preset. All new fields are written out in all files.

## Fitted per photo

`depthOffsetM` and `widthScale` > 1 are maxima. `processor/app/pipeline/room_depth.py`
reduces them per photo (the offset first, then the width scale) until

- the floor rule (the far tyres stay in front of the wall line) binds no harder
  than on the plate's own spot – otherwise the car would only be shrunk on the
  same spot (a toy car) instead of standing further back, and
- the car's fitted footprint (the ground model's pose from the tyre contacts and
  overhangs from the silhouette – the real car, not the plate's 4.75 m proxy)
  keeps 0.5 m to the brand wall, or, where the plate's spot is already closer,
  gets no closer than there.

The metadata shows the result under `placement.roomFit` (requested/applied
offset and scale, `reducedBy`, wall clearance). With the shipped plates the car
stands close to the wall in the 3/4 views (a long saloon such as an S-Class
leaves ≈ 0.05 m on the plate's spot), so there Variante 2 mostly cannot move
the car back – that needs re-rendered plates with more floor behind the car.

## Switching the default

Copy the chosen variant over the standard preset (same `id`, same plate set):

```bash
cp public/presets/variants/autoexperten-standard-v2.json public/presets/autoexperten-standard.json
```

and adjust `description` if wanted. The processor reloads the preset per job;
the app reads the same file. Re-run `processor` pytest – the tests that pin
the shipped defaults (`test_showroom_variants.py::test_the_shipped_preset_keeps_the_current_look`,
`test_presets.py` default/limit checks) must then be updated to the new values.
Individual parts can also be taken over separately (only the `branding`
section, only `reflection`, only `placement.depthOffsetM`).
