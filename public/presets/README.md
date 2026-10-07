# Showroom presets

Assets for the AutoExperten showroom processing (used by the processor in
`processor/`, see `processor/README.md`).

| File | Purpose |
| --- | --- |
| `autoexperten-standard.json` | Processing preset "AutoExperten Standard": output size/quality, vehicle placement, shadow, adjustment limits. |
| `autoexperten-standard-showroom.jpg` | **Showroom master image** – the one fixed background every exterior photo is placed on. |

## Replacing the showroom master image

The current `autoexperten-standard-showroom.jpg` is a deterministic
**placeholder** rendered by `processor/scripts/render_showroom_placeholder.py`.
To use the real AutoExperten showroom:

1. Photograph the empty showroom (no vehicle, no people), 4:3, at least
   3200 × 2400 px, camera at car-photo height, brand wall centred.
   The wall/floor junction should be at about 62 % of the image height
   (`floorHorizon`); keep the floor area in the middle free – the vehicle is
   placed there (≈ 78 % of the width, tyres at 84 % of the height).
2. Save it as `autoexperten-standard-showroom.jpg` in this folder (same name).
3. In `autoexperten-standard.json` set `"background": { …, "placeholder": false }`.
4. Restart the processor (Docker: rebuild the image).

No code changes are needed. The showroom is never generated per photo – every
listing uses this same image.

`autoexperten-dark` (dark showroom) is planned; no assets yet.
