"""Process ONE local photo without the web stack.

    cd processor
    .venv/bin/python -m app.cli path/to/car.jpg -o result.jpg --debug-dir debug/

Options: --preset (default autoexperten_standard), --shot (default
front_left_45), --debug-dir (writes mask/cut-out/background/... images).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import Settings
from .pipeline.debug import NULL_DEBUG, DebugSink
from .pipeline.pipeline import process_photo
from .pipeline.segmentation import create_segmenter
from .presets import BackgroundProvider, load_preset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoExperten showroom processing – single photo")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--preset", default="autoexperten_standard")
    parser.add_argument("--shot", default="front_left_45")
    parser.add_argument("--debug-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    preset = load_preset(settings, args.preset)
    segmenter = create_segmenter(settings)
    backgrounds = BackgroundProvider(settings)
    debug = DebugSink(args.debug_dir) if args.debug_dir else NULL_DEBUG

    started = time.perf_counter()
    result = process_photo(
        args.input.read_bytes(),
        preset=preset,
        segmenter=segmenter,
        backgrounds=backgrounds,
        shot_key=args.shot,
        debug=debug,
        progress=lambda p, step: print(f"  {int(p * 100):3d}% {step}", file=sys.stderr),
    )
    args.output.write_bytes(result.jpeg)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "size": [result.width, result.height],
                "seconds": round(time.perf_counter() - started, 2),
                "warnings": [w.__dict__ for w in result.warnings],
                "metadata": result.metadata,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
