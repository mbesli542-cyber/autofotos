"""Turn a showroom reference image into the clean master background.

The AutoExperten showroom reference (``public/presets/autoexperten-standard-reference.jpg``)
has lettering painted into the wall (generated text, an invented car
silhouette – not the official logo). The master background must be the EMPTY
showroom: this script removes everything inside the branding box that is
darker or more saturated than the wall, rebuilds the wall from the
surrounding wall light (push-pull interpolation, deterministic – no
generative AI), restores the wall grain, upscales to the master size and
writes ``public/presets/autoexperten-standard-showroom.jpg``. The official
logo and texts are then composited by ``app/showroom/branding.py``.

Usage (from processor/)::

    .venv/bin/python scripts/prepare_showroom_master.py
    .venv/bin/python scripts/prepare_showroom_master.py --input ref.jpg --output out.jpg \\
        --box 0.33,0.18,0.67,0.46 --width 3200 --debug-dir /tmp/master-debug

Only needed for a reference with baked-in lettering; a real photo of the empty
showroom can be copied to the master path directly.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESETS = REPO_ROOT / "public" / "presets"
DEFAULT_INPUT = PRESETS / "autoexperten-standard-reference.jpg"
DEFAULT_OUTPUT = PRESETS / "autoexperten-standard-showroom.jpg"
#: Branding area of the reference (x0, y0, x1, y1 as fractions of width/height).
DEFAULT_BOX = (0.33, 0.18, 0.67, 0.46)


def _srgb_icc() -> bytes:
    data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    if len(data) >= 128 and data[36:40] == b"acsp":
        data[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)  # fixed date → reproducible bytes
    return bytes(data)


def _load_srgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        icc = image.info.get("icc_profile")
        if icc:
            import io

            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in (ImageCms.getProfileDescription(source) or "").lower():
                image = ImageCms.profileToProfile(
                    image.convert("RGB"), source, ImageCms.createProfile("sRGB"), outputMode="RGB"
                )
        return np.asarray(image.convert("RGB"), np.float32) / 255.0


def lettering_mask(rgb: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Pixels inside `box` that belong to lettering/signage (incl. its soft shadow)."""
    x0, y0, x1, y1 = box
    region = rgb[y0:y1, x0:x1]
    lum = region @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    # 1) letter strokes: much darker than any lit wall, or clearly blue
    core = (lum < 0.5) | ((region[..., 2] - region[..., 0]) > 0.12)
    # thin anti-aliased strokes: noticeably darker than the local (median) wall
    background = cv2.medianBlur((np.clip(lum, 0, 1) * 255).astype(np.uint8), 31).astype(np.float32) / 255.0
    core |= (background - lum) > 0.05
    # bright specular edges of the stand-off letters (only right next to dark strokes)
    dark = (lum < 0.5) | ((background - lum) > 0.05)
    beside = cv2.dilate(dark.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    core |= beside & ((lum - background) > 0.04)
    core = cv2.dilate(core.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
    # stand-off letters cast a soft shadow down and slightly sideways: cover it generously
    h = region.shape[0]
    reach = max(6, int(round(0.06 * h)))
    cover = core.copy()
    for dy in range(0, reach + 1, 2):
        shifted = np.zeros_like(core)
        shifted[dy:] = core[: core.shape[0] - dy]
        cover |= shifted
    cover = cv2.dilate(cover.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))) > 0
    core = cover
    mask = core
    near = cv2.dilate(core.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (51, 51))) > 0
    # 2) drop shadows: darker than the wall that is interpolated around the letters
    for _ in range(3):
        wall = push_pull_fill(region, (~mask).astype(np.float32)) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        shadow = near & ((wall - lum) > 0.006)
        mask = core | shadow
        mask = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0
    # thin wall slivers between strokes are unreliable samples (glare, shadow edges)
    known = (~mask).astype(np.uint8)
    opened = cv2.morphologyEx(known, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    mask = opened == 0
    # small wall islands enclosed by lettering (letter counters, gaps) are unreliable
    # samples – fill them too, otherwise their dark edges spread into the fill
    count, labels, stats, _ = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < 0.004 * mask.size:
            mask[labels == label] = True
    full = np.zeros(rgb.shape[:2], bool)
    full[y0:y1, x0:x1] = mask
    return full


def push_pull_fill(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Fill unknown pixels with a smooth interpolation of the known ones (push-pull)."""
    levels = []
    color = rgb * known[..., None]
    weight = known.astype(np.float32)
    while True:
        levels.append((color, weight))
        if min(weight.shape) < 4 or weight.min() > 0:
            break
        color = cv2.pyrDown(color)
        weight = cv2.pyrDown(weight)
    color, weight = levels[-1]
    estimate = color / np.maximum(weight, 1e-6)[..., None]
    for color, weight in reversed(levels[:-1]):
        size = (weight.shape[1], weight.shape[0])
        up = cv2.pyrUp(estimate, dstsize=size)
        w = np.clip(weight, 0.0, 1.0)[..., None]
        estimate = np.where(w > 1e-6, color / np.maximum(w, 1e-6), up)
        estimate = w * estimate + (1.0 - w) * up
    return estimate.astype(np.float32)


def smooth_fill(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Push-pull interpolation (continuous at the hole border), then smoothed inside
    the hole so that no seams or streaks of single border pixels remain."""
    out = push_pull_fill(rgb, known.astype(np.float32))
    hole = (~known).astype(np.float32)
    inner = cv2.erode(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    inner = cv2.GaussianBlur(inner, (0, 0), 3)[..., None]
    smooth = cv2.GaussianBlur(out, (0, 0), 7)
    return (out * (1 - inner) + smooth * inner).astype(np.float32)


def clean_wall(rgb: np.ndarray, box: tuple[int, int, int, int], seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    mask = lettering_mask(rgb, box)
    known = ~mask
    filled = smooth_fill(rgb, known)
    # restore the wall grain (measured on the clean wall inside the box)
    x0, y0, x1, y1 = box
    detail = rgb - cv2.GaussianBlur(rgb, (0, 0), 1.5)
    clean = known[y0:y1, x0:x1]
    sigma = float(detail[y0:y1, x0:x1][clean].std()) if clean.any() else 0.0
    grain = np.random.default_rng(seed).normal(0.0, sigma, rgb.shape).astype(np.float32)
    grain = cv2.GaussianBlur(grain, (0, 0), 0.6)
    grain *= sigma / max(float(grain.std()), 1e-6)
    filled = filled + grain
    feather = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2.0)[..., None]
    out = rgb * (1.0 - feather) + filled * feather
    return np.clip(out, 0.0, 1.0), mask


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--box", default=",".join(str(v) for v in DEFAULT_BOX), help="x0,y0,x1,y1 fractions")
    parser.add_argument("--width", type=int, default=3200, help="master width (4:3), default 3200")
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--debug-dir", default=None)
    args = parser.parse_args()

    rgb = _load_srgb(Path(args.input))
    h, w = rgb.shape[:2]
    fx0, fy0, fx1, fy1 = (float(v) for v in args.box.split(","))
    box = (int(fx0 * w), int(fy0 * h), int(fx1 * w), int(fy1 * h))
    cleaned, mask = clean_wall(rgb, box)

    width = args.width - args.width % 2
    height = int(round(width * 3 / 4))
    image = Image.fromarray((cleaned * 255.0 + 0.5).astype(np.uint8))
    image = ImageOps.fit(image, (width, height), Image.LANCZOS, centering=(0.5, 0.5))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "JPEG", quality=args.quality, subsampling=0, optimize=True, icc_profile=_srgb_icc())
    if args.debug_dir:
        debug = Path(args.debug_dir)
        debug.mkdir(parents=True, exist_ok=True)
        Image.fromarray((mask * 255).astype(np.uint8)).save(debug / "lettering-mask.png")
        Image.fromarray((cleaned * 255.0 + 0.5).astype(np.uint8)).save(debug / "cleaned-native.png")
    print(f"wrote {output} ({width}x{height}) from {args.input} ({w}x{h}), cleaned {int(mask.sum())} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
