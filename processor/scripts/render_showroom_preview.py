"""Render the branded AutoExperten showroom plates WITHOUT a vehicle (checking/tuning).

Uses exactly what the pipeline uses: the plate set referenced by
``public/presets/autoexperten-standard.json`` → ``background.plates`` and the
wall-space branding configured in its ``branding`` section.

Usage (from processor/)::

    .venv/bin/python scripts/render_showroom_preview.py -o /tmp/plates.jpg            # all 8 plates, contact sheet
    .venv/bin/python scripts/render_showroom_preview.py -o /tmp/p.jpg --shot front --width 3200 --guides

Guides: cyan = wall/floor junction (from the floor homography), orange = the
plate's ground line (lowest proxy tyre contact), red = projected typical car
(proxy bbox) and its tyre contacts, magenta = branding boxes and the highest
point a vehicle roof may reach (branding bottom + clearance).
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
from app.showroom.plates import PLATE_SHOTS  # noqa: E402


def _render(provider, preset, shot: str, width: int, height: int, guides: bool) -> Image.Image:
    showroom = provider.get(preset, shot, width, height)
    image = Image.fromarray(np.asarray(showroom.rgb))
    if guides:
        draw = ImageDraw.Draw(image)
        line = max(2, width // 800)
        draw.line([(x, float(showroom.floor_top[x])) for x in range(0, width, 8)], fill=(0, 200, 255), width=line)
        ground = showroom.plate.ground_v * height
        draw.line([(0, ground), (width, ground)], fill=(255, 120, 0), width=line)
        u0, v0, u1, v1 = showroom.plate.proxy_bbox
        draw.rectangle([u0 * width, v0 * height, u1 * width, v1 * height], outline=(255, 40, 40), width=line)
        for c in showroom.plate.proxy_contacts.values():
            r = 3 * line
            draw.ellipse([c.u * width - r, c.v * height - r, c.u * width + r, c.v * height + r], outline=(255, 40, 40), width=line)
        if showroom.branding.boxes:
            headroom = showroom.branding.bottom + preset.branding.clearance * height
            draw.line([(0, headroom), (width, headroom)], fill=(255, 0, 200), width=line)
        for _, x0, y0, x1, y1 in showroom.branding.boxes:
            draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 200), width=line)
        draw.text((10, 10), shot, fill=(255, 255, 255))
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", required=True, help="output JPEG")
    parser.add_argument("--preset", default="autoexperten_standard")
    parser.add_argument("--shot", default=None, help="one exterior shot (default: contact sheet of all 8)")
    parser.add_argument("--width", type=int, default=None, help="width of one plate (4:3), default 2400 / 1200 for the sheet")
    parser.add_argument("--guides", action="store_true", help="draw junction, ground line, proxy car and branding boxes")
    args = parser.parse_args()

    settings = Settings.from_env()
    preset = load_preset(settings, args.preset)
    provider = BackgroundProvider(settings)
    aw, ah = preset.output.aspect
    width = args.width or (2400 if args.shot else 1200)
    width -= width % 2
    height = int(round(width * ah / aw))
    height -= height % 2
    shots = [args.shot] if args.shot else list(PLATE_SHOTS)
    tiles = [_render(provider, preset, shot, width, height, args.guides) for shot in shots]
    if len(tiles) == 1:
        image = tiles[0]
    else:
        image = Image.new("RGB", (2 * width, 4 * height))
        for i, tile in enumerate(tiles):
            image.paste(tile, ((i % 2) * width, (i // 2) * height))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, quality=90)
    print(f"wrote {args.output} ({image.width}x{image.height}, plates: {provider.plates_path(preset)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
