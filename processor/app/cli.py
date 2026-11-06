"""Process ONE local photo without the web stack.

    cd processor
    .venv/bin/python -m app.cli path/to/car.jpg -o result.jpg --debug-dir debug/

Options: --preset (default autoexperten_standard), --shot (default
front_left_45), --debug-dir (writes mask/geometry/cut-out/plate/... images),
--no-detector (skip the vehicle detector: no semantic check of the mask – neither
foreign objects nor missing vehicle parts – and the window of portrait photos
comes from a whole-frame pass; for comparisons only, the service always uses
the detector), --no-sam2 (no SAM 2.1: neither the cut of a foreign object glued
to the outline – only the zoom cut, as before the dealer's decision of
2026-11-06 – nor automatic glass masks; comparisons only), --glass-mask
(interior shots with the preset's glassBackdrop enabled: a grey PNG, 255 =
see-through glass; default <photo>-glass.png next to the photo when it exists).
A photo rejected by the quality
gate prints the German message and the gate details and exits with code 2.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import Settings
from .main import create_glass_masks
from .pipeline.debug import NULL_DEBUG, DebugSink
from .pipeline.glass_backdrop import file_glass_masks, glass_mask_beside
from .pipeline.pipeline import process_photo
from .pipeline.quality import QualityGateError
from .pipeline.segmentation import create_segmenter
from .presets import BackgroundProvider, load_preset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoExperten showroom processing – single photo")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--preset", default="autoexperten_standard")
    parser.add_argument("--shot", default="front_left_45")
    parser.add_argument("--debug-dir", type=Path, default=None)
    parser.add_argument("--no-detector", action="store_true", help="comparison only: without the vehicle detector")
    parser.add_argument(
        "--no-sam2", action="store_true", help="comparison only: without SAM 2.1 (contested cut, automatic glass masks)"
    )
    parser.add_argument(
        "--glass-mask",
        type=Path,
        default=None,
        help="glass mask PNG of an interior shot (default: <photo>-glass.png; without one the automatic SAM 2.1 mask, PROCESSOR_GLASS_MASKS)",
    )
    args = parser.parse_args(argv)
    glass_mask = args.glass_mask or glass_mask_beside(args.input)

    settings = Settings.from_env()
    preset = load_preset(settings, args.preset)
    segmenter = create_segmenter(settings)
    if args.no_detector:
        segmenter.detector = None
    if args.no_sam2:
        segmenter.sam2 = None
    backgrounds = BackgroundProvider(settings)
    debug = DebugSink(args.debug_dir) if args.debug_dir else NULL_DEBUG
    glass_masks = file_glass_masks(glass_mask) if glass_mask.is_file() else create_glass_masks(settings, segmenter)

    started = time.perf_counter()
    try:
        result = process_photo(
            args.input.read_bytes(),
            preset=preset,
            segmenter=segmenter,
            backgrounds=backgrounds,
            shot_key=args.shot,
            debug=debug,
            progress=lambda p, step: print(f"  {int(p * 100):3d}% {step}", file=sys.stderr),
            glass_masks=glass_masks,
        )
    except QualityGateError as error:
        print(
            json.dumps(
                {"rejected": error.code, "message": error.user_message, "details": error.details},
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 2
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
