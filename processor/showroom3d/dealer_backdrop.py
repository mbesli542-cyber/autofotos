"""Step 4 of the dealer backdrop: assemble the plate set the processor loads.

Input
    --plate    the cleaned, upscaled dealer image (dealer_clean.py → dealer_upscale.py), 2400 × 1800
    --render   the Blender passes (render_dealer_passes.py): base.png, <shot>-proxy.png, layout.json
    --measure  dealer_clean.py's measure.json (where his lettering was)
Output (``public/presets/autoexperten-haendler/``)
    plate.jpg              THE plate of all eight exterior shots (one fixed studio camera; sRGB
                           JPEG, embedded sRGB profile, no text or logo)
    <shot>-shadow.png      16-bit floor shadow/occlusion multiplier of the proxy car at the shot's
                           pose (LINEAR, 65535 = no shadow) – the ratio of the two Blender renders,
                           limited to the floor around the car (postprocess_plates.shadow_multiplier)
    reflection.png         16-bit RGB, the floor's OWN glossy reflection (light streaks of the wall
                           washers, LED lines, wall glow) in LINEAR light (code / 65535 −
                           reflectionOffset, offset 0: never negative); one file – the camera
                           never moves
    plates.json            camera, horizon, floor/wall homographies, per shot the proxy car pose,
                           bbox and tyre contacts, wall albedo, brand area, reflectance per row
    branding.json          the official branding layout in wall space that reproduces the position
                           and size of his lettering (copied into the preset's ``branding``)

The reflection pass is MEASURED on his floor, not rendered: his image was made with an image AI,
so a rendered gloss (any roughness) never matches the streaks he drew. In linear light every
floor pixel (low-passed, the wood grain averaged out) is split into three known colours –
the wood (the most saturated floor pixels), the warm white light of the wall washers (the
pools on his wall) and the AutoExperten-blue LEDs (his corner LED) – so that
``floor = wood · D + light · R + blue · B``. ``light · R + blue · B`` is the floor's own
reflection (stored as that smooth per-channel fraction of the plate's own pixels, so it follows
the sharp streak cores); what stays (``wood · D``) is the matte wood the processor shows where a
car blocks that reflection. ``reflectance`` per image row is calibrated exactly like the rendered
plates
(postprocess_plates.reflection_pass: reflection of the white wall / wall radiance at the mirrored
row – the frontal level camera mirrors the wall about the junction row).

Usage (from the repository root, processor virtualenv)::

    processor/.venv/bin/python processor/showroom3d/dealer_backdrop.py \
        --plate /opt/ae-dealer/up/lanczos.png --render /opt/ae-dealer/render \
        --measure /opt/ae-dealer/clean/measure.json --out public/presets/autoexperten-haendler \
        --standard public/presets/autoexperten-standard.json \
        --variant public/presets/variants/autoexperten-haendler.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dealer_layout as dl  # noqa: E402
import postprocess_plates as pp  # noqa: E402

PLATE_NAME = "plate.jpg"
REFLECTION_NAME = "reflection.png"
PASS_SIZE = (1200, 900)
#: Low-pass (px at PASS_SIZE) before the colour split: averages the wood grain, keeps the streaks.
GRAIN_SIGMA = 2.5
#: Floor pixels whose chroma is among the most saturated this fraction define the wood colour.
WOOD_QUANTILE = 0.08
#: Columns (normalised) of the corner LED (blue reference) and the pool rows of the wall (light reference).
LED_COLUMNS = (0.792, 0.800)
LIGHT_BOX = (0.15, 0.0, 0.75, 0.06)  # u0, v0, u1, v1 of the pool band on the wall
#: Wood under the streak cores: at least this fraction of the surrounding floor's wood (Gaussian σ px).
DIFFUSE_FLOOR, DIFFUSE_SIGMA = 0.9, (30.0, 10.0)
#: The reflection fraction (reflection / plate, per channel) is smoothed by this σ (px at PASS_SIZE).
FRACTION_SIGMA = 2.0
#: Encoding offset of reflection.png (code / 65535 − offset). The measured reflection is never
#: negative but reaches ≈ 0.97 in the hottest streak cores – the rendered plates' 0.5 (room for the
#: negative channel mixing of AgX) would clip it there and leave yellow remains under a car.
REFLECTION_OFFSET = 0.0
#: The reflection is kept this many px (PASS_SIZE) below the actual floor line (skirting, LEDs, planter).
FLOOR_PAD = 3


def _to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def floor_region(width: int, height: int, room: dict) -> np.ndarray:
    """Pixels that really show the floor: below the main junction, below the right wall's floor
    line, not the planter."""
    v = (np.arange(height) + 0.5)[:, None] / height
    u = (np.arange(width) + 0.5)[None, :] / width
    floor = np.broadcast_to(v > dl.JUNCTION_V, (height, width)).copy()
    # right wall: its floor line seen per column (through the corner along its direction)
    corner = np.array(room["rightWall"]["corner"])
    direction = np.array(room["rightWall"]["direction"])
    cu = dl.project(np.array([corner[0], corner[1], 0.0]))[0]
    for col in range(width):
        if u[0, col] <= cu:
            continue
        p = dl._solve_column(corner, direction, float(u[0, col]))
        _, pv, _ = dl.project(np.array([p[0], p[1], 0.0]))
        floor[: int(np.ceil(float(pv) * height)) + FLOOR_PAD, col] = False
    first = int(np.ceil(dl.JUNCTION_V * height)) + FLOOR_PAD
    floor[:first] = False
    # planter: its front face down to its bottom edge
    pl = room["planter"]
    pu, pv, _ = dl.project(np.array([pl["rightX"], pl["frontY"], 0.0]))
    floor[: int(np.ceil(float(pv) * height)) + FLOOR_PAD, : int(np.ceil(float(pu) * width)) + FLOOR_PAD] = False
    return floor


def plate_linear(plate_rgb: np.ndarray) -> np.ndarray:
    """The plate in linear light at PASS_SIZE (linearised first, then area-averaged – exactly as
    postprocess_plates.reflection_pass reads the plate)."""
    lin = _to_linear(plate_rgb.astype(np.float32) / 255.0).astype(np.float32)
    return cv2.resize(lin, PASS_SIZE, interpolation=cv2.INTER_AREA)


def split_reflection(plate_rgb: np.ndarray, room: dict) -> tuple[np.ndarray, dict]:
    """The floor's own reflection (linear RGB at PASS_SIZE) and the colours used."""
    width, height = PASS_SIZE
    lin = plate_linear(plate_rgb)
    low = cv2.GaussianBlur(lin, (0, 0), GRAIN_SIGMA)
    floor = floor_region(width, height, room)
    # wood: the most saturated floor colour (lowest blue/red ratio)
    pix = low[floor]
    ratio = pix[:, 2] / np.maximum(pix[:, 0], 1e-6)
    wood = pix[ratio <= np.quantile(ratio, WOOD_QUANTILE)].mean(axis=0)
    # light: the wall-washer pools on the wall; blue: the corner LED strip
    u0, v0, u1, v1 = LIGHT_BOX
    pool = lin[int(v0 * height) : int(v1 * height) + 1, int(u0 * width) : int(u1 * width)].reshape(-1, 3)
    pool = pool[pool.sum(axis=1) >= np.quantile(pool.sum(axis=1), 0.9)]
    light = pool.mean(axis=0)
    led = lin[int(0.1 * height) : int(0.4 * height), int(LED_COLUMNS[0] * width) : int(LED_COLUMNS[1] * width) + 1].reshape(-1, 3)
    led = led[(led[:, 2] - led[:, 0]) >= np.quantile(led[:, 2] - led[:, 0], 0.9)]
    blue = led.mean(axis=0)
    basis = np.stack([wood / np.linalg.norm(wood), light / np.linalg.norm(light), blue / np.linalg.norm(blue)], axis=1)
    coeff = low.reshape(-1, 3) @ np.linalg.inv(basis).T  # (D, R, B) per pixel
    diffuse = np.clip(coeff.reshape(height, width, 3)[..., 0], 0.0, None)
    # in the hottest streak cores the floor clips towards white and the split would leave almost
    # no wood ("burnt" spots once the processor removes the reflection under a car): the wood
    # there is at least DIFFUSE_FLOOR × the wood of the surrounding floor
    wood_floor = np.where(floor, diffuse, 0.0).astype(np.float32)
    weight = cv2.GaussianBlur(floor.astype(np.float32), (0, 0), sigmaX=DIFFUSE_SIGMA[0], sigmaY=DIFFUSE_SIGMA[1])
    around = cv2.GaussianBlur(wood_floor, (0, 0), sigmaX=DIFFUSE_SIGMA[0], sigmaY=DIFFUSE_SIGMA[1]) / np.maximum(weight, 1e-6)
    diffuse = np.maximum(diffuse, DIFFUSE_FLOOR * around)
    # the reflection = everything that is not wood, as a smooth per-channel FRACTION of the plate:
    # applied to the plate's own pixels it follows the sharp streak cores exactly – a smoothed
    # absolute layer would leave bright/coloured remains of the cores when the processor removes it
    rest = np.clip(low - diffuse[..., None] * basis[:, 0], 0.0, None)
    fraction = np.clip(rest / np.maximum(low, 1e-6), 0.0, 1.0)
    if FRACTION_SIGMA > 0:
        fraction = cv2.GaussianBlur(fraction.astype(np.float32), (0, 0), FRACTION_SIGMA)
    # floor only, soft towards the excluded areas
    weight = cv2.GaussianBlur(floor.astype(np.float32), (0, 0), 1.5) * floor
    refl = lin * fraction * weight[..., None]
    info = {
        "wood": [round(float(x), 5) for x in wood],
        "light": [round(float(x), 5) for x in light],
        "blue": [round(float(x), 5) for x in blue],
        "reflectionMax": round(float(refl.max()), 4),
        "reflectionMeanFloor": round(float(refl[floor].mean()), 4),
        "floorMean": round(float(low[floor].mean()), 4),
    }
    return refl.astype(np.float32), info


def wall_albedo(plate_rgb: np.ndarray, measure: dict) -> list[float]:
    """Median linear colour of the wall around the branding (the matte print is lit by it)."""
    x0, y0, x1, y1 = measure["wordmark"]
    sx = plate_rgb.shape[1] / measure["image"][0]
    sy = plate_rgb.shape[0] / measure["image"][1]
    band = plate_rgb[int(y0 * sy) : int(measure["city"]["text"][3] * sy), int(x0 * sx) : int(x1 * sx)]
    lin = _to_linear(band.reshape(-1, 3).astype(np.float32) / 255.0)
    return [round(float(np.median(lin[:, i])), 4) for i in range(3)]


def branding_layout(measure: dict, room: dict) -> dict:
    """Fractions of the brand area that put the official logo and the city line where his were."""
    area = room["brandArea"]
    aw, ah = area["xMax"] - area["xMin"], area["zMax"] - area["zMin"]
    iw, ih = measure["image"]

    def wall(px, py):
        x, z = dl.wall_point(px / iw, py / ih)
        return (float(x) - area["xMin"]) / aw, (area["zMax"] - float(z)) / ah

    word = measure["wordmark"]
    (lx0, ltop), (lx1, _) = wall(word[0], word[1]), wall(word[2], word[1])
    city = measure["city"]
    (cx0, ctop), (cx1, cbottom) = wall(city["text"][0], city["capTop"]), wall(city["text"][2], city["baseline"])
    (rx0, _), (rx1, _) = wall(city["rulesOuter"][0], city["capTop"]), wall(city["rulesOuter"][1], city["capTop"])
    _, r0 = wall(0, city["ruleRows"][0])
    _, r1 = wall(0, city["ruleRows"][1])
    centre = 0.5 * (lx0 + lx1)
    text_w = cx1 - cx0
    rule_gap = city["ruleGap"] / iw * (dl.WALL_DISTANCE_M / dl.FOCAL_NORM) / aw
    rule_len = 0.5 * ((rx1 - rx0) - text_w) - rule_gap
    return {
        "logo": {"centerX": round(centre, 4), "top": round(ltop, 4), "maxWidth": round(lx1 - lx0, 4)},
        "city": {
            "centerX": round(centre, 4),
            "top": round(ctop, 4),
            "capHeight": round(cbottom - ctop, 4),
            "textWidth": round(text_w, 4),
            "ruleLength": round(rule_len, 4),
            "ruleGap": round(rule_gap, 4),
            "ruleThickness": round(r1 - r0, 4),
        },
    }


def fit_tracking(text: str, cap_height: float, width: float, aspect: float, weight: str = "medium") -> float:
    """Letter spacing (em) at which the bundled branding font sets `text` `width` wide (fractions of
    the brand area; `aspect` = brand area width / height) – the renderer's own text mask."""
    sys.path.insert(0, str(HERE.parent))
    from app.showroom.branding import TextConfig, _text_mask

    height = 2400

    def measured(tracking: float) -> float:
        mask = _text_mask(TextConfig(text=text, cap_height=cap_height, tracking=tracking, weight=weight), height)
        return mask.shape[1] / (height * aspect)

    lo, hi = 0.0, 1.5
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if measured(mid) < width else (lo, mid)
    return round(0.5 * (lo + hi), 3)


#: The variant preset: the standard preset with these changes (everything else identical).
VARIANT_NAME = "AutoExperten Händler-Hintergrund"
VARIANT_DESCRIPTION = (
    "Showroom-Hintergrund des Händlers (helle Paneelwand, blaue LED-Linien, Walnuss-Lamellen, "
    "Olivenbaum, glänzendes Parkett) mit offiziellem AutoExperten Logo und Schwetzingen."
)
VARIANT_SHADOW = {"edgeOpacity": 0.7, "underbodyFalloff": 0.25, "ambientOpacity": 0.6}
VARIANT_REFLECTION = {"strength": 1.0}


def write_variant(standard: Path, target: Path, plates: str, layout: dict, room: dict) -> dict:
    data = json.loads(standard.read_text(encoding="utf-8"))
    data["name"] = VARIANT_NAME
    data["description"] = VARIANT_DESCRIPTION
    data["background"] = {
        "plates": plates,
        "notes": "The dealer's own showroom image (made with an image AI): AI lettering removed "
        "(processor/showroom3d/dealer_clean.py), upscaled once to 2400 x 1800 (dealer_upscale.py, Lanczos), "
        "ONE plate.jpg for all eight exterior shots - one fixed level studio camera, the car is turned "
        "(dealer_layout.py); <shot>-shadow.png = Blender shadow pass of the proxy car at the shot's pose "
        "(render_dealer_passes.py), reflection.png = the floor's own reflection measured on the plate "
        "(dealer_backdrop.py). Official logo + Schwetzingen composited in wall space at the place of his lettering.",
    }
    area = room["brandArea"]
    aspect = (area["xMax"] - area["xMin"]) / (area["zMax"] - area["zMin"])
    city = layout["city"]
    branding = data["branding"]
    branding["logo"].update(
        {"centerX": layout["logo"]["centerX"], "top": layout["logo"]["top"], "maxWidth": layout["logo"]["maxWidth"], "maxHeight": 0.25}
    )
    branding["city"].update(
        {
            "text": "Schwetzingen",
            "centerX": city["centerX"],
            "top": city["top"],
            "capHeight": city["capHeight"],
            "tracking": fit_tracking("Schwetzingen", city["capHeight"], city["textWidth"], aspect),
            "weight": "medium",
            "mounted": True,
            "ruleLength": city["ruleLength"],
            "ruleGap": city["ruleGap"],
            "ruleThickness": city["ruleThickness"],
        }
    )
    for name in ("website", "phone"):  # no dealer contact data in listing photos (mobile.de)
        branding[name]["text"] = ""
    data["shadow"].update(VARIANT_SHADOW)
    data["reflection"].update(VARIANT_REFLECTION)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plate", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--measure", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--standard", type=Path, help="the shipped preset (base of the variant)")
    parser.add_argument("--variant", type=Path, help="write the variant preset here (needs --standard)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    info = json.loads((args.render / "layout.json").read_text(encoding="utf-8"))
    if info != json.loads(json.dumps(dl.layout())):
        raise SystemExit("layout.json of the renders differs from dealer_layout.layout() – re-render the passes")
    measure = json.loads(args.measure.read_text(encoding="utf-8"))
    rgb = np.asarray(Image.open(args.plate).convert("RGB"))
    size = (rgb.shape[1], rgb.shape[0])
    if abs(size[0] / size[1] - 4 / 3) > 1e-3:
        raise SystemExit(f"{args.plate.name}: expected a 4:3 image, got {size}")
    Image.fromarray(rgb).save(args.out / PLATE_NAME, "JPEG", quality=args.quality, subsampling=0, optimize=True, icc_profile=pp._srgb_icc())

    room = info["room"]
    albedo = wall_albedo(rgb, measure)
    plates = {}
    base = pp._read16(args.render / "base.png")
    for key, shot in info["shots"].items():
        entry = dl.plate_entry(key, shot, size, room, PLATE_NAME, albedo)
        proxy = pp._read16(args.render / f"{key}-proxy.png")
        mult = pp.shadow_multiplier(base, proxy, entry)
        cv2.imwrite(str(args.out / f"{key}-shadow.png"), (mult * 65535.0 + 0.5).astype(np.uint16))
        entry["shadow"] = f"{key}-shadow.png"
        entry["shadowSize"] = [int(mult.shape[1]), int(mult.shape[0])]
        plates[key] = entry
        print(f"{key}: shadow min {mult.min():.3f}, wall clearance {shot['wallClearanceM']} m, width {entry['vehicle']['targetWidthRatio']}")

    refl, split = split_reflection(rgb, room)
    # calibration exactly like the rendered plates: plate vs. "gloss-free" = plate − reflection
    glossfree = _to_srgb(np.clip(plate_linear(rgb) - refl, 0.0, 1.0)).astype(np.float32)
    first = next(iter(plates.values()))
    calibrated, samples = pp.reflection_pass(rgb.astype(np.float32) / 255.0, glossfree, first)
    if len(samples) < 2:
        raise SystemExit("reflection calibration failed")
    if calibrated.min() < -REFLECTION_OFFSET - 1e-6 or calibrated.max() > 1.0 - REFLECTION_OFFSET + 1e-6:
        raise SystemExit("reflection pass outside the encodable range")
    out = cv2.cvtColor(np.clip(calibrated + REFLECTION_OFFSET, 0.0, 1.0), cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(args.out / REFLECTION_NAME), (out * 65535.0 + 0.5).astype(np.uint16))
    for entry in plates.values():
        entry["reflection"] = REFLECTION_NAME
        entry["reflectionSize"] = [int(calibrated.shape[1]), int(calibrated.shape[0])]
        entry["reflectionOffset"] = REFLECTION_OFFSET
        entry["reflectance"] = samples
    ks = [k for _, k in samples]
    print(f"reflection: {split}, reflectance {min(ks):.3f}..{max(ks):.3f}")

    meta = {
        "version": 1,
        "plates": plates,
        "room": {
            "description": "AutoExperten dealer backdrop: the dealer's own showroom image (made with an image AI), "
            "lettering removed, upscaled; ONE fixed level studio camera (lens shift) for all eight shots, the car "
            "turned (processor/showroom3d/dealer_*.py, render_dealer_passes.py)",
            "wallPlane": "y = 0 (x right, z up), floor z = 0, metres",
            "hfovDeg": dl.HFOV_DEG,
            "camera": info["camera"],
            "features": room,
            "reflectionSplit": split,
        },
    }
    (args.out / "plates.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    layout = branding_layout(measure, room)
    (args.out / "branding.json").write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    print(f"branding: {json.dumps(layout)}")
    if args.variant is not None:
        if args.standard is None:
            raise SystemExit("--variant needs --standard")
        plates_rel = f"{args.out.name}/plates.json"
        variant = write_variant(args.standard, args.variant, plates_rel, layout, room)
        print(f"variant preset → {args.variant} (city tracking {variant['branding']['city']['tracking']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
