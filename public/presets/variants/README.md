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

| `autoexperten-haendler.json` | **Händler-Hintergrund** – the dealer's OWN showroom background (supplied by him, made with an image AI) as the fixed plate of all eight exterior shots: `../autoexperten-haendler/` (provenance, camera and recipe in its README). One level studio camera with lens shift, the car turned per shot (3/4 views 0.83 of the width, 1.47 m of floor to the wall; side views 0.92, front/rear 0.68 – a side view stands higher in the frame with one fixed camera, see the plate set's README); his AI lettering is removed from the plate and the official logo + "Schwetzingen" between two thin rules are composited in wall space at the place and size of his lettering (`branding`, measured by `processor/showroom3d/dealer_clean.py`); no website/phone. Shadow **"mittel"** (the recommendation – the dealer found the earlier `edgeOpacity` 0.7 / `underbodyFalloff` 0.25 / `ambientOpacity` 0.6 "far too black"): darkest only at the tyre contacts and right under sill/apron, soft and short elsewhere, the wood's hue kept (`shadow.tint` 1 1 1), the under-car terms limited to the car's footprint (`shadow.footprintReach`/`reflection.footprintReach` 0.3 m); the car's own reflection near the car (`reflection.strength` 1.5, `maxReflectance` 0.3, `fade` 0.5). `vehicleAdjustments.studioLight` (the dealer's decision, the same in all three files): the guarded global studio light match – exposure towards his studio's key light (≤ +0.8 EV), contrast 0.3 above the car's median, highlight shoulder (knee 0.85, ≥ 65 % detail), white balance = colour temperature only towards the studio (≤ ±6 %; reference outside the paint), paint guard (processor/README.md, "Studio light match"). Quality gate: the "photo from above" rule scaled to his studio camera (`highViewRiseFactor` 1.42, `highViewAspectFactor` 0.88 – the earlier absolute thresholds; see the plate set's README), the same in all three files. Written by `processor/showroom3d/dealer_backdrop.py --variant` together with the two other strengths below. |
| `autoexperten-haendler-leicht.json` | The same with **"Schatten leicht"**: lighter and shorter (`underbodyOpacity` 0.7, `edgeOpacity` 0.3, `underbodyFalloff` 0.08, `ambientOpacity` 0.2, `ambientReach` 0.3, contact 0.85 / crease 0.7), reflection 1.2 / 0.25 / `fade` 0.4. |
| `autoexperten-haendler-kraeftig.json` | The same with **"Schatten kräftig"**: `underbodyOpacity` 0.85, `edgeOpacity` 0.5, `underbodyFalloff` 0.14, `ambientOpacity` 0.4, `ambientReach` 0.5, contact 0.92 / crease 0.85, reflection 1.5 / 0.3 / `fade` 0.6 – still well below the earlier look. |

Everything not listed in a file's row (output in every file; shadow, reflection,
vehicle adjustments and quality gate unless the row names them – the three
Händler-Hintergrund files change all four) is identical to the standard preset. All new fields
are written out in all files – except `shadow.tint`, `shadow.footprintReach`,
`reflection.footprintReach` and `vehicleAdjustments.studioLight`, which only the dealer files set:
their defaults (tint 1 1 0.95, footprint limit off, studio light match off) are exactly the
shipped look, and the shipped preset stays byte-identical.

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
