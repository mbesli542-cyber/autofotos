"""Render the branded AutoExperten showroom WITHOUT a vehicle (for checking/tuning).

Uses exactly what the pipeline uses: the master photo
``public/presets/autoexperten-standard-showroom.jpg`` (or the emergency
fallback while it is missing) plus the deterministic branding layer
configured in ``public/presets/autoexperten-standard.json`` → ``branding``.

Usage (from processor/)::

    .venv/bin/python scripts/render_showroom_preview.py -o /tmp/showroom-preview.jpg
    .venv/bin/python scripts/render_showroom_preview.py -o /tmp/p.jpg --width 3200 --guides
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.presets import BackgroundProvider, load_preset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", required=True, help="output JPEG")
    parser.add_argument("--preset", default="autoexperten_standard")
    parser.add_argument("--width", type=int, default=2400, help="output width (4:3), default 2400")
    parser.add_argument(
        "--guides", action="store_true", help="draw floor horizon, ground line, branding boxes and vehicle headroom"
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    preset = load_preset(settings, args.preset)
    aw, ah = preset.output.aspect
    width = args.width - args.width % 2
    height = int(round(width * ah / aw))
    height -= height % 2
    showroom = BackgroundProvider(settings).get(preset, width, height)
    image = Image.fromarray(np.asarray(showroom.rgb))
    if args.guides:
        draw = ImageDraw.Draw(image)
        horizon = showroom.floor_horizon * height
        ground = preset.placement.ground_line * height
        headroom = showroom.branding.bottom + preset.branding.clearance * height
        draw.line([(0, horizon), (width, horizon)], fill=(0, 200, 255), width=3)
        draw.line([(0, ground), (width, ground)], fill=(255, 80, 0), width=3)
        draw.line([(0, headroom), (width, headroom)], fill=(255, 0, 200), width=2)
        for _, x0, y0, x1, y1 in showroom.branding.boxes:
            draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 200), width=2)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, quality=92)
    print(f"wrote {args.output} ({width}x{height}, showroom source: {showroom.source})")
    if showroom.is_fallback:
        print("NOTE: the final master photo is missing – this is the emergency fallback plate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
