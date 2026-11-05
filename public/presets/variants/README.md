# Showroom look variants – the dealer studio in three shadow strengths

Complete presets for the dealer's own studio (plate set `../autoexperten-haendler/`).
The shipped preset `../autoexperten-standard.json` **is** `autoexperten-haendler.json`
("Schatten mittel") with the standard name and description; the other two strengths are
the alternatives. The processor only loads the standard file (`PRESET_FILES` in
`processor/app/presets.py`); a variant becomes active by copying it over that file (below).
Built from the dealer's reference images (AI-generated – only the room/composition counts,
never the car).

| File | Look |
| --- | --- |
| `autoexperten-haendler.json` | **Händler-Hintergrund** – the dealer's OWN showroom background (supplied by him, made with an image AI) as the fixed plate of all eight exterior shots: `../autoexperten-haendler/` (provenance, camera and recipe in its README). One level studio camera with lens shift, the car turned per shot (3/4 views 0.83 of the width, 1.47 m of floor to the wall; side views 0.92, front/rear 0.68 – a side view stands higher in the frame with one fixed camera, see the plate set's README); his AI lettering is removed from the plate and the official logo + "Schwetzingen" between two thin rules are composited in wall space at the place and size of his lettering (`branding`, measured by `processor/showroom3d/dealer_clean.py`); no website/phone. Shadow **"mittel"** (the recommendation – the dealer found the earlier `edgeOpacity` 0.7 / `underbodyFalloff` 0.25 / `ambientOpacity` 0.6 "far too black"): darkest only at the tyre contacts and right under sill/apron, soft and short elsewhere, the wood's hue kept (`shadow.tint` 1 1 1), the under-car terms limited to the car's footprint (`shadow.footprintReach`/`reflection.footprintReach` 0.3 m); the car's own reflection near the car (`reflection.strength` 1.5, `maxReflectance` 0.3, `fade` 0.5). `vehicleAdjustments.studioLight` (the dealer's decision, the same in all three files): the guarded global studio light match – exposure towards his studio's key light (≤ +0.8 EV), contrast 0.3 above the car's median, highlight shoulder (knee 0.85, ≥ 65 % detail), white balance = colour temperature only towards the studio (≤ ±6 %; reference outside the paint), paint guard (processor/README.md, "Studio light match"). Quality gate: the "photo from above" rule scaled to his studio camera (`highViewRiseFactor` 1.42, `highViewAspectFactor` 0.88 – the earlier absolute thresholds; see the plate set's README), the same in all three files. Written by `processor/showroom3d/dealer_backdrop.py --variant` together with the two other strengths below. |
| `autoexperten-haendler-leicht.json` | The same with **"Schatten leicht"**: lighter and shorter (`underbodyOpacity` 0.7, `edgeOpacity` 0.3, `underbodyFalloff` 0.08, `ambientOpacity` 0.2, `ambientReach` 0.3, contact 0.85 / crease 0.7), reflection 1.2 / 0.25 / `fade` 0.4. |
| `autoexperten-haendler-kraeftig.json` | The same with **"Schatten kräftig"**: `underbodyOpacity` 0.85, `edgeOpacity` 0.5, `underbodyFalloff` 0.14, `ambientOpacity` 0.4, `ambientReach` 0.5, contact 0.92 / crease 0.85, reflection 1.5 / 0.3 / `fade` 0.6 – still well below the earlier look. |

The three files differ only in `shadow`, `reflection` and `name`; every field is written out
(also `shadow.tint`, `shadow.footprintReach`, `reflection.footprintReach` and
`vehicleAdjustments.studioLight`, whose code defaults – tint 1 1 0.95, footprint limit off,
studio light match off – are the earlier 3D-room look).

The earlier look variants of the 3D room (`autoexperten-standard-v1.json`: reference branding
with a 60 % logo, glossier floor, larger 3/4 cars; `-v2.json`: + the car up to 0.45 m further
back) were removed with that room's plates (git history 990d9f7). The features they used stay
in the renderer and are tested: side lines and Lucide icons of the texts, `shotPlacement`,
`placement.widthScale` / `depthOffsetM` with the per-photo room fit below.

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
offset and scale, `reducedBy`, wall clearance). The shipped dealer studio uses
neither (`widthScale` 1.0, `depthOffsetM` 0: the car stands on the plate's
spot, 1.47 m of floor to the wall in the 3/4 views); on the earlier 3D room a
long saloon left ≈ 0.05 m there, so moving it back mostly needed re-rendered
plates.

## Switching the default

Copy the chosen variant over the standard preset (same `id`) and keep the standard `name`
and `description`:

```bash
python3 - <<'PY'
import json
std = json.load(open("public/presets/autoexperten-standard.json", encoding="utf-8"))
new = json.load(open("public/presets/variants/autoexperten-haendler-leicht.json", encoding="utf-8"))
new.update(name=std["name"], description=std["description"])
open("public/presets/autoexperten-standard.json", "w", encoding="utf-8").write(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
PY
```

The processor reloads the preset per job; the app reads the same file. Re-run `processor`
pytest – the tests that pin the shipped look
(`test_showroom_variants.py::test_the_shipped_preset_is_the_dealer_studio_with_medium_shadows`,
`test_presets.py::test_the_shipped_grounding_and_reflection_are_the_dealer_studio_medium_values`)
must then be updated to the new values. Individual parts can also be taken over separately
(only `shadow` + `reflection`).
