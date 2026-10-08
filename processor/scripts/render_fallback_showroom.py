"""Render the EMERGENCY FALLBACK showroom plate (not the AutoExperten design).

Default output: ``public/presets/fallback/autoexperten-standard-fallback.jpg``
(3200x2400, sRGB, JPEG quality 92, no branding – the official logo and texts
are composited by app/showroom/branding.py). The pipeline only uses it while
the real master photo ``public/presets/autoexperten-standard-showroom.jpg`` is
missing.

Usage (from the repository root)::

    processor/.venv/bin/python processor/scripts/render_fallback_showroom.py
    processor/.venv/bin/python processor/scripts/render_fallback_showroom.py \\
        --width 2400 --height 1800 --output /tmp/fallback.jpg

The rendering is deterministic: the same arguments always give the same pixels.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_ROOT = REPO_ROOT / "processor"
if str(PROCESSOR_ROOT) not in sys.path:
    sys.path.insert(0, str(PROCESSOR_ROOT))

from app.showroom.fallback import render_fallback_showroom  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "public" / "presets" / "fallback" / "autoexperten-standard-fallback.jpg"
PRESET_JSON = REPO_ROOT / "public" / "presets" / "autoexperten-standard.json"


def _resolve(path: str | Path) -> Path:
    """Relative paths are resolved against the repository root."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


def _preset_floor_horizon(default: float = 0.62) -> float:
    try:
        data = json.loads(PRESET_JSON.read_text(encoding="utf-8"))
        return float((data.get("background") or {}).get("floorHorizon", default))
    except (OSError, ValueError, TypeError):
        return default


def _srgb_icc_profile() -> bytes | None:
    """Standard sRGB ICC profile with a fixed creation date (byte-reproducible JPEGs)."""
    try:
        from PIL import ImageCms

        data = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    except Exception:  # littlecms not available - the JPEG is still plain sRGB
        return None
    if len(data) >= 128 and data[36:40] == b"acsp":
        data[24:36] = struct.pack(">6H", 2026, 1, 1, 0, 0, 0)  # header dateTimeNumber
    return bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--width", type=int, default=3200, help="output width in px (default 3200)")
    parser.add_argument("--height", type=int, default=2400, help="output height in px (default 2400)")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="output JPEG path (default public/presets/fallback/autoexperten-standard-fallback.jpg)",
    )
    parser.add_argument(
        "--floor-horizon",
        type=float,
        default=None,
        help="wall/floor junction as a fraction of the height (default: floorHorizon of the preset, 0.62)",
    )
    parser.add_argument("--seed", type=int, default=7, help="random seed (default 7)")
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality (default 92)")
    args = parser.parse_args(argv)

    if args.width < 64 or args.height < 64:
        parser.error("width and height must be at least 64 px")
    output = _resolve(args.output)
    floor_horizon = args.floor_horizon if args.floor_horizon is not None else _preset_floor_horizon()

    started = time.perf_counter()
    image = render_fallback_showroom(args.width, args.height, floor_horizon=floor_horizon, seed=args.seed)
    elapsed = time.perf_counter() - started

    output.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs: dict = {"quality": int(args.quality), "subsampling": 0, "optimize": True}
    icc = _srgb_icc_profile()
    if icc:
        save_kwargs["icc_profile"] = icc
    image.convert("RGB").save(output, "JPEG", **save_kwargs)

    try:
        shown = output.relative_to(REPO_ROOT)
    except ValueError:
        shown = output
    print(
        f"wrote {shown} ({image.width}x{image.height}, "
        f"floor_horizon={floor_horizon:.3f}, render {elapsed:.1f} s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
