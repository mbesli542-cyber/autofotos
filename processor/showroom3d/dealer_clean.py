"""Step 1 of the dealer backdrop: remove the AI-drawn lettering from the dealer's wall.

The dealer supplied the background he wants (``dealer/haendler_hintergrund.png``,
1448 × 1086, made with an image AI). Its wall carries an AI-drawn "AutoExperten"
wordmark, the city name "Schwetzingen" and two thin rules – NOT the official
logo, so they must not appear in listing photos. This script removes exactly
those pixels from the wall (environment-only clean-up, classical image
processing, no generative AI):

1. ink mask: the dark-grey and blue letters and the rules inside ``TEXT_BOX``,
   grown by their anti-aliased edges and the soft drop shadow of the mounted
   letters (pixels darker than the fitted wall that touch the ink);
2. harmonic fill (Laplace equation, Dirichlet boundary = the untouched wall
   pixels around and between the letters, coarse-to-fine): the wall-washer
   gradient and the spot pools continue smoothly through the letters – the
   wall is a smooth painted surface (high-pass σ ≈ 0.5 levels), so nothing has
   to be invented;
3. grain: the same fine noise as the surrounding wall (measured) so the filled
   area is not flatter than its neighbourhood.

Outputs (``--out``): ``clean.png`` (1448 × 1086, 8-bit sRGB), ``mask.png``
(the replaced pixels), ``seam-check.png`` (original | cleaned | high-pass ×12
of the cleaned wall – a seam or a smear shows up there) and ``measure.json``
(where the dealer's lettering was: wordmark, city line and rules in image px;
``dealer_backdrop.py`` turns it into the branding layout in wall space).

Usage (from the repository root, processor virtualenv)::

    processor/.venv/bin/python processor/showroom3d/dealer_clean.py --out /opt/ae-dealer/clean
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "dealer" / "haendler_hintergrund.png"
#: SHA-256 of the dealer's file – the box and thresholds below belong to exactly this image.
SOURCE_SHA256 = "d128a800862db15823f7088f3542644a2a05b4dff35df82aa69cd34dca859483"
SIZE = (1448, 1086)
#: Region of the AI lettering (x0, y0, x1, y1, image px): wordmark, city line and rules.
TEXT_BOX = (336, 52, 1062, 226)
#: Wordmark / city line split (image row), a rule row has more ink than this, width of the capital S
#: – for the measurement only.
CITY_TOP, RULE_MIN_RUN, CAPITAL_WIDTH = 182, 150, 14
#: Ink: darker than this (mean of R, G, B) or bluer than this (B − R).
INK_DARK, INK_BLUE = 150.0, 45.0
#: Anti-aliased edges and the drop shadow of the mounted letters (the wall washers light them
#: from above: the soft shadow falls straight down, ≈ 12–14 px): the ink mask is grown by
#: INK_GROW px on every side, by SHADOW_UP px upwards (the lit top faces of the letters spill a
#: thin highlight onto the wall) and by SHADOW_DOWN px downwards.
INK_GROW, SHADOW_UP, SHADOW_DOWN = 4, 12, 18
#: Wall slivers narrower than about 2 × this (px) between the letters are replaced too.
SLIVER = 9
#: Wall islands smaller than this (px) inside the lettering are replaced too.
ISLAND_AREA = 2500
#: Remaining shadow/halo: pixels deviating this many levels from the filled wall, within SHADOW_REACH px
#: of the mask.
SHADOW_DELTA, SHADOW_REACH = 0.8, 10
#: Grain added to the filled pixels (σ in 8-bit levels), measured on the wall beside the lettering.
GRAIN_BOX = (190, 236, 1130, 330)


def load_source(path: Path) -> np.ndarray:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"{path.name}: unexpected file (sha256 {digest}) – TEXT_BOX belongs to {SOURCE_SHA256[:12]}…")
    rgb = np.asarray(Image.open(path).convert("RGB"), np.float32)
    if (rgb.shape[1], rgb.shape[0]) != SIZE:
        raise SystemExit(f"{path.name}: expected {SIZE[0]}x{SIZE[1]}")
    return rgb


def raw_ink(rgb: np.ndarray) -> np.ndarray:
    """The letter and rule pixels themselves (inside TEXT_BOX)."""
    x0, y0, x1, y1 = TEXT_BOX
    luma = rgb.mean(axis=-1)
    blue = rgb[..., 2] - rgb[..., 0]
    ink = (luma < INK_DARK) | (blue > INK_BLUE)
    box = np.zeros(ink.shape, bool)
    box[y0:y1, x0:x1] = True
    return ink & box


def ink_mask(rgb: np.ndarray) -> np.ndarray:
    """raw_ink grown by the anti-aliased edges and the drop shadow (the pixels to replace)."""
    x0, y0, x1, y1 = TEXT_BOX
    ink = raw_ink(rgb)
    box = np.zeros(ink.shape, bool)
    box[y0:y1, x0:x1] = True
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * INK_GROW + 1, 2 * INK_GROW + 1))
    mask = cv2.dilate(ink.astype(np.uint8), grow).astype(bool)
    down = mask.copy()
    for dy in range(1, SHADOW_DOWN + 1):  # the drop shadow below every stroke
        down[dy:] |= mask[:-dy]
    for dy in range(1, SHADOW_UP + 1):  # the highlight above every stroke
        down[:-dy] |= mask[dy:]
    # thin slivers of wall between letters (bright letter rims on both sides): replaced as well
    close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * SLIVER + 1, 2 * SLIVER + 1))
    down = cv2.morphologyEx(down.astype(np.uint8), cv2.MORPH_CLOSE, close).astype(bool)
    down &= box
    # wall islands enclosed by letters (counters of o, e, p, the gaps of tight pairs) lie in the
    # letters' shadow too: replaced as well
    count, labels, stats, _ = cv2.connectedComponentsWithStats((~down).astype(np.uint8), connectivity=4)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < ISLAND_AREA:
            down[labels == label] = True
    return down


def harmonic_fill(image: np.ndarray, hole: np.ndarray, iterations: int = 600) -> np.ndarray:
    """Solve Laplace's equation inside `hole` (boundary = the known pixels), coarse to fine."""
    if not hole.any():
        return image.copy()
    h, w = hole.shape
    if min(h, w) > 32:
        # coarse solution as the start value (area average of the known pixels per coarse cell)
        known = (~hole).astype(np.float32)
        sw, sh = (w + 1) // 2, (h + 1) // 2
        small_known = cv2.resize(known, (sw, sh), interpolation=cv2.INTER_AREA)
        small_sum = cv2.resize(image * known[..., None], (sw, sh), interpolation=cv2.INTER_AREA)
        small = small_sum / np.maximum(small_known, 1e-6)[..., None]
        small_hole = small_known < 0.999
        small = harmonic_fill(small, small_hole, iterations)
        start = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        start = np.repeat(image[~hole].mean(axis=0, keepdims=True), h * w, axis=0).reshape(image.shape)
    out = image.copy()
    out[hole] = start[hole]
    for _ in range(iterations):
        avg = 0.25 * (
            np.roll(out, 1, axis=0) + np.roll(out, -1, axis=0) + np.roll(out, 1, axis=1) + np.roll(out, -1, axis=1)
        )
        out[hole] = avg[hole]
    return out


def measure(ink: np.ndarray) -> dict:
    """Where the dealer's lettering was (image px of the 1448 × 1086 image, end values exclusive).

    wordmark: left, cap top, right, bottom of "AutoExperten"; city: the text "Schwetzingen"
    (bbox, cap top and baseline of its capital S), the rows of the two rules, their outer ends
    and the gap between rules and text.
    """
    ys, xs = np.nonzero(ink[:CITY_TOP])
    wordmark = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    city = ink[CITY_TOP:].copy()
    runs = city.sum(axis=1)
    rule_rows = np.nonzero(runs > RULE_MIN_RUN)[0]
    r0, r1 = int(rule_rows.min()), int(rule_rows.max()) + 1
    cols = np.nonzero(city[r0:r1].any(axis=0))[0]
    # the rules are the two long runs of columns; the text lies between them
    gaps = np.nonzero(np.diff(cols) > 1)[0]
    left_end, right_start = int(cols[gaps[0]]) + 1, int(cols[gaps[-1] + 1])
    text = city.copy()
    text[r0:r1, : left_end] = False
    text[r0:r1, right_start:] = False
    text[:, :left_end] = False
    text[:, right_start:] = False
    ty, tx = np.nonzero(text)
    first = text[:, int(tx.min()) : int(tx.min()) + CAPITAL_WIDTH]  # the capital S
    sy = np.nonzero(first.any(axis=1))[0]
    return {
        "image": list(SIZE),
        "wordmark": wordmark,
        "city": {
            "text": [int(tx.min()), int(ty.min()) + CITY_TOP, int(tx.max()) + 1, int(ty.max()) + 1 + CITY_TOP],
            "capTop": int(sy.min()) + CITY_TOP,
            "baseline": int(sy.max()) + 1 + CITY_TOP,
            "ruleRows": [r0 + CITY_TOP, r1 + CITY_TOP],
            "rulesOuter": [int(cols.min()), int(cols.max()) + 1],
            "ruleGap": int(round(0.5 * ((int(tx.min()) - left_end) + (right_start - int(tx.max()) - 1)))),
        },
    }


def clean(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = TEXT_BOX
    pad = 24
    cy0, cy1, cx0, cx1 = y0 - pad, y1 + pad, x0 - pad, x1 + pad
    ink = ink_mask(rgb)
    crop = rgb[cy0:cy1, cx0:cx1].copy()
    hole = ink[cy0:cy1, cx0:cx1].copy()
    # 1st pass: wall without the ink → the drop shadow is what stays darker than that wall
    for _ in range(2):
        filled = harmonic_fill(crop, hole)
        darker = np.abs(filled.mean(axis=-1) - crop.mean(axis=-1)) > SHADOW_DELTA
        near = cv2.dilate(hole.astype(np.uint8), np.ones((2 * SHADOW_REACH + 1, 2 * SHADOW_REACH + 1), np.uint8)) > 0
        grown = hole | (darker & near)
        grown = cv2.dilate(grown.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        grown[:pad] = grown[-pad:] = False
        grown[:, :pad] = grown[:, -pad:] = False
        if (grown == hole).all():
            break
        hole = grown
    filled = harmonic_fill(crop, hole, iterations=1500)
    # grain of the surrounding wall (σ of the high-pass, white noise blurred like the image's own grain)
    gx0, gy0, gx1, gy1 = GRAIN_BOX
    wall = rgb[gy0:gy1, gx0:gx1].mean(axis=-1)
    sigma = float((wall - cv2.GaussianBlur(wall, (0, 0), 3.0)).std())
    rng = np.random.default_rng(20261105)
    noise = cv2.GaussianBlur(rng.normal(0.0, 1.0, hole.shape).astype(np.float32), (0, 0), 0.6)
    noise *= sigma / max(float((noise - cv2.GaussianBlur(noise, (0, 0), 3.0)).std()), 1e-6)
    filled[hole] += noise[hole][:, None]
    out = rgb.copy()
    out[cy0:cy1, cx0:cx1] = filled
    mask = np.zeros(ink.shape, bool)
    mask[cy0:cy1, cx0:cx1] = hole
    return out, mask


def seam_check(original: np.ndarray, cleaned: np.ndarray, mask: np.ndarray) -> np.ndarray:
    x0, y0, x1, y1 = TEXT_BOX
    pad = 60
    box = (slice(max(0, y0 - pad), y1 + pad), slice(max(0, x0 - pad), x1 + pad))
    a, b = original[box], cleaned[box]
    luma = b.mean(axis=-1)
    high = luma - cv2.GaussianBlur(luma, (0, 0), 6.0)
    hp = np.clip(128 + 12 * high, 0, 255)
    edge = cv2.morphologyEx(mask[box].astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    hp_rgb = np.dstack([hp, hp, hp])
    hp_rgb[edge] = (hp_rgb[edge] * 0.5 + np.array([255, 0, 0]) * 0.5)
    stretch = np.clip((luma - luma.mean()) * 4 + 128, 0, 255)
    rows = [a, b, np.dstack([stretch] * 3), hp_rgb]
    return np.clip(np.concatenate(rows, axis=0), 0, 255).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rgb = load_source(args.source)
    cleaned, mask = clean(rgb)
    u8 = np.clip(cleaned + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(u8).save(args.out / "clean.png", optimize=True)
    Image.fromarray((mask * 255).astype(np.uint8)).save(args.out / "mask.png")
    Image.fromarray(seam_check(rgb, u8.astype(np.float32), mask)).save(args.out / "seam-check.png")
    info = measure(raw_ink(rgb))
    info["replacedPixels"] = int(mask.sum())
    (args.out / "measure.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
