"""Turn the Blender renders into the plate set the processor loads.

Input  (render dir, written by render_plates.py):
    <shot>.png                 plate, 16-bit sRGB (AgX view transform)
    <shot>-shadow-base.png     plate at shadow resolution, no car
    <shot>-shadow-proxy.png    same view with the invisible light-blocking car proxy
    <shot>-glossfree.png       (optional, --pass reflection) shadow-pass view with a gloss-free floor
    plates.json                camera + geometry metadata
Output (plate dir, e.g. public/presets/autoexperten-standard/):
    <shot>.jpg                 plate (sRGB JPEG, embedded sRGB profile)
    <shot>-shadow.png          16-bit grey: floor shadow/occlusion of a typical car standing at
                               the anchor, as a multiplier in LINEAR light (65535 = 1.0 = no shadow)
    <shot>-reflection.png      (optional) 16-bit RGB: the floor's own reflection (LED streaks,
                               wall-washer glow, wall sheen) in LINEAR light, SIGNED:
                               value = code / 65535 − reflectionOffset – the shipped plate
                               (downsampled) minus the gloss-free render, floor pixels only.
                               Signed because the view transform (AgX) mixes channels: a blue
                               LED streak lowers red/green slightly; clipping would leave a
                               yellow trace when the processor removes the reflection.
    plates.json                metadata (+ "shadowSize", "reflection", "reflectionSize",
                               "reflectionOffset", "reflectance": [[v, k], ...] – the floor's
                               measured specular reflectance per image row: reflection /
                               mirrored wall radiance, both linear)

The shadow multiplier is limited to the floor around the car (pixels whose
viewing ray hits the floor plane, within SHADOW_REACH_M of the car footprint),
clamped to <= 1 and lightly smoothed (render noise).

Usage (from processor/):
    .venv/bin/python showroom3d/postprocess_plates.py /opt/ae-plates/v1 ../public/presets/autoexperten-standard
    --plate-from-shadow-base   use the shadow-resolution base render as plate (quick previews)
    --reflection-only          only add/refresh the reflection pass of an existing plate set
                               (plates, shadows and the rest of its plates.json stay untouched)
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms

#: Floor shadow is kept within this distance (metres) of the car footprint.
SHADOW_REACH_M = 2.2
SHOTS = (
    "front_left_45",
    "front",
    "front_right_45",
    "left_side",
    "right_side",
    "rear_left_45",
    "rear",
    "rear_right_45",
)


def _srgb_icc() -> bytes:
    data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    if len(data) >= 128 and data[36:40] == b"acsp":
        data[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)  # fixed date → reproducible bytes
    return bytes(data)


def _read16(path: Path) -> np.ndarray:
    """RGB float32 0..1 from an 8/16-bit PNG (OpenCV keeps 16 bit, Pillow does not)."""
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(path)
    scale = 65535.0 if image.dtype == np.uint16 else 255.0
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / scale


def _to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _floor_points(h_floor: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Floor coordinates (x, y in metres) seen by every pixel; valid = ray hits the floor in front of the wall."""
    inv = np.linalg.inv(h_floor)
    us = (np.arange(width, dtype=np.float64) + 0.5) / width
    vs = (np.arange(height, dtype=np.float64) + 0.5) / height
    uu, vv = np.meshgrid(us, vs)
    pts = np.stack([uu, vv, np.ones_like(uu)], axis=-1) @ inv.T
    w = pts[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = pts[..., 0] / w
        y = pts[..., 1] / w
    valid = (w > 0) & np.isfinite(x) & np.isfinite(y) & (y < 0.0)
    return x, y, valid


def shadow_multiplier(base: np.ndarray, proxy: np.ndarray, plate: dict) -> np.ndarray:
    height, width = base.shape[:2]
    ratio = (_to_linear(proxy).mean(axis=-1) + 1e-4) / (_to_linear(base).mean(axis=-1) + 1e-4)
    ratio = np.clip(ratio, 0.0, 1.0).astype(np.float32)
    h_floor = np.asarray(plate["floorHomography"], np.float64)
    x, y, valid = _floor_points(h_floor, width, height)
    # distance of every floor point to the car footprint (rotated rectangle)
    proxy_cfg = plate["vehicle"]["proxy"]
    heading = np.radians(plate["vehicle"]["headingDeg"])
    ax, ay = plate["anchor"]["floor"]
    dx, dy = x - ax, y - ay
    along = dx * np.cos(heading) + dy * np.sin(heading)
    across = -dx * np.sin(heading) + dy * np.cos(heading)
    out_l = np.maximum(np.abs(along) - proxy_cfg["lengthM"] / 2, 0.0)
    out_w = np.maximum(np.abs(across) - proxy_cfg["widthM"] / 2, 0.0)
    distance = np.hypot(out_l, out_w)
    keep = valid & (distance < SHADOW_REACH_M)
    fade = np.clip(1.0 - distance / SHADOW_REACH_M, 0.0, 1.0) ** 0.5
    effect = np.where(keep, (1.0 - ratio) * fade, 0.0).astype(np.float32)
    # render noise: light smoothing (≈ 2 px at 1200 px width)
    effect = cv2.GaussianBlur(effect, (0, 0), max(1.0, width / 600.0))
    return np.clip(1.0 - effect, 0.0, 1.0)


#: Reflection pass: render-noise smoothing (px at 1200 px width), calibration rows, encoding offset.
REFLECTION_SMOOTH = 0.6
REFLECTANCE_STEP = 0.02
REFLECTION_OFFSET = 0.5


def _wall_columns(plate: dict, width: int) -> tuple[int, int]:
    """Column range of the white brand wall at the wall/floor junction (inner 90 %)."""
    h_wall = np.asarray(plate["wallHomography"], np.float64)
    area = plate["wall"]["brandArea"]
    us = []
    for x in (area["xMin"], area["xMax"]):
        p = h_wall @ np.array([x, 0.0, 1.0])
        us.append(p[0] / p[2])
    u0, u1 = min(us), max(us)
    pad = 0.05 * (u1 - u0)
    return max(0, int((u0 + pad) * width)), min(width, int((u1 - pad) * width))


def reflection_pass(plate_rgb: np.ndarray, glossfree: np.ndarray, plate: dict) -> tuple[np.ndarray, list[list[float]]]:
    """(SIGNED floor reflection RGB in linear light at the gloss-free render's size,
    reflectance samples [[v, k], ...]).

    The reflection is the shipped plate (sRGB, downsampled to the gloss-free render's
    size) minus the gloss-free render on floor pixels, both display-referred and compared
    in linear light like the processor does – referenced to the plate itself so that the
    glare of the different render resolutions cancels. The reflectance k per image row is
    the reflection of the white brand wall divided by the wall radiance at the mirrored row
    (mirror axis = the wall/floor junction), summed over the wall's columns – the
    calibration of the car's own reflection.
    """
    height, width = glossfree.shape[:2]
    lin_b = cv2.resize(_to_linear(plate_rgb).astype(np.float32), (width, height), interpolation=cv2.INTER_AREA)
    lin_g = _to_linear(glossfree)
    refl = lin_b - lin_g
    _, _, valid = _floor_points(np.asarray(plate["floorHomography"], np.float64), width, height)
    sigma = max(0.5, REFLECTION_SMOOTH * width / 1200.0)
    refl = cv2.GaussianBlur(refl, (0, 0), sigma) * valid[..., None]
    # calibration
    luma = np.array([0.2126, 0.7152, 0.0722], np.float32)
    top = np.where(valid.any(axis=0), np.argmax(valid, axis=0), height).astype(np.int64)
    c0, c1 = _wall_columns(plate, width)
    r_l = cv2.GaussianBlur((refl @ luma).astype(np.float32), (0, 0), sigmaX=2.0, sigmaY=6.0)
    l_l = cv2.GaussianBlur((lin_b @ luma).astype(np.float32), (0, 0), sigmaX=2.0, sigmaY=6.0)
    wall_top = 0
    ratios = []
    cols = np.arange(c0, c1)
    for y in range(int(top[cols].min()) + 4, height):
        ym = 2 * top[cols] - y
        ok = (ym > wall_top + 4) & (ym < top[cols] - 6) & (y > top[cols] + 4)
        if ok.sum() < 0.5 * len(cols):
            ratios.append(np.nan)
            continue
        num = float(r_l[y, cols[ok]].sum())
        den = float(l_l[ym[ok], cols[ok]].sum())
        ratios.append(num / den if den > 1e-6 else np.nan)
    rows = np.arange(int(top[cols].min()) + 4, height)
    ratios = np.array(ratios, np.float64)
    good = np.isfinite(ratios)
    samples: list[list[float]] = []
    if good.sum() >= 4:
        vs = (rows[good] + 0.5) / height
        ks = ratios[good]
        for v in np.arange(np.ceil(vs.min() / REFLECTANCE_STEP) * REFLECTANCE_STEP, vs.max() + 1e-9, REFLECTANCE_STEP):
            sel = np.abs(vs - v) <= REFLECTANCE_STEP
            if sel.any():
                samples.append([round(float(v), 4), round(float(np.clip(np.median(ks[sel]), 0.0, 0.5)), 4)])
    return refl.astype(np.float32), samples


def write_reflection(src: Path, dst: Path, name: str, plate: dict) -> bool:
    image, gloss = dst / plate.get("image", f"{name}.jpg"), src / f"{name}-glossfree.png"
    if not (image.is_file() and gloss.is_file()):
        for key in ("reflection", "reflectionSize", "reflectionOffset", "reflectance"):
            plate.pop(key, None)
        return False
    rgb = np.asarray(Image.open(image).convert("RGB"), dtype=np.float32) / 255.0
    refl, samples = reflection_pass(rgb, _read16(gloss), plate)
    if len(samples) < 2:
        raise SystemExit(f"{name}: reflection calibration failed (no usable wall rows)")
    out = cv2.cvtColor(np.clip(refl + REFLECTION_OFFSET, 0.0, 1.0), cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(dst / f"{name}-reflection.png"), (out * 65535.0 + 0.5).astype(np.uint16))
    plate["reflection"] = f"{name}-reflection.png"
    plate["reflectionSize"] = [int(refl.shape[1]), int(refl.shape[0])]
    plate["reflectionOffset"] = REFLECTION_OFFSET
    plate["reflectance"] = samples
    ks = [k for _, k in samples]
    print(f"{name}: reflection pass, reflectance {min(ks):.3f}..{max(ks):.3f}, max {refl.max():.3f}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("render_dir")
    parser.add_argument("plate_dir")
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--plate-from-shadow-base", action="store_true")
    parser.add_argument("--reflection-only", action="store_true")
    args = parser.parse_args()
    src, dst = Path(args.render_dir), Path(args.plate_dir)
    dst.mkdir(parents=True, exist_ok=True)
    if args.reflection_only:
        meta = json.loads((dst / "plates.json").read_text(encoding="utf-8"))
        for name in SHOTS:
            if name in meta["plates"] and not write_reflection(src, dst, name, meta["plates"][name]):
                print(f"{name}: no reflection pass in {src}", file=sys.stderr)
        (dst / "plates.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return 0
    meta = json.loads((src / "plates.json").read_text(encoding="utf-8"))
    icc = _srgb_icc()
    for name in SHOTS:
        plate = meta["plates"].get(name)
        if plate is None:
            print(f"skip {name}: no metadata", file=sys.stderr)
            continue
        plate_png = src / (f"{name}-shadow-base.png" if args.plate_from_shadow_base else f"{name}.png")
        if not plate_png.is_file():
            print(f"skip {name}: {plate_png.name} missing", file=sys.stderr)
            continue
        rgb = _read16(plate_png)
        u8 = (np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)
        Image.fromarray(u8).save(dst / f"{name}.jpg", "JPEG", quality=args.quality, subsampling=0, optimize=True, icc_profile=icc)
        plate["image"] = f"{name}.jpg"
        plate["size"] = [int(rgb.shape[1]), int(rgb.shape[0])]
        base, proxy = src / f"{name}-shadow-base.png", src / f"{name}-shadow-proxy.png"
        if base.is_file() and proxy.is_file():
            mult = shadow_multiplier(_read16(base), _read16(proxy), plate)
            cv2.imwrite(str(dst / f"{name}-shadow.png"), (mult * 65535.0 + 0.5).astype(np.uint16))
            plate["shadow"] = f"{name}-shadow.png"
            plate["shadowSize"] = [int(mult.shape[1]), int(mult.shape[0])]
            print(f"{name}: plate {rgb.shape[1]}x{rgb.shape[0]}, shadow min {mult.min():.3f}")
        else:
            plate.pop("shadow", None)
            print(f"{name}: plate {rgb.shape[1]}x{rgb.shape[0]}, no shadow pass")
        write_reflection(src, dst, name, plate)
    meta["plates"] = {k: v for k, v in meta["plates"].items() if (dst / v.get("image", "")).is_file()}
    (dst / "plates.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
