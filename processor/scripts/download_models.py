"""Download (and verify) the model weights ahead of time.

Usage (from processor/):
    python scripts/download_models.py                  # configured segmentation model + vehicle detector
    python scripts/download_models.py --all            # every registered model
    python scripts/download_models.py isnet-general-use rf-detr-seg-medium

Every file is verified against the SHA-256 pinned in app/pipeline/segmentation.py
(MODEL_REGISTRY, BiRefNet/IS-Net) or app/pipeline/detection.py (DETECTOR_REGISTRY,
RF-DETR-Seg). An existing file with another checksum (damaged, or another export
under the registered name – e.g. in the Docker build cache) is downloaded again;
the service itself never replaces a file, it refuses to load it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.pipeline.detection import DETECTOR_REGISTRY  # noqa: E402
from app.pipeline.segmentation import MODEL_REGISTRY, ModelUnavailableError, _sha256, ensure_model  # noqa: E402

REGISTRY = {**MODEL_REGISTRY, **DETECTOR_REGISTRY}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", help=f"model names ({', '.join(REGISTRY)})")
    parser.add_argument("--all", action="store_true", help="download every registered model")
    args = parser.parse_args()

    settings = Settings.from_env()
    names = list(REGISTRY) if args.all else (args.models or [settings.segmentation_model, settings.detector_model])
    for name in names:
        spec = REGISTRY.get(name)
        if spec is None:
            print(f"unknown model: {name}", file=sys.stderr)
            return 2
        existing = settings.models_dir / spec.filename
        if existing.is_file() and _sha256(existing) != spec.sha256:
            print(f"{name}: {existing} is not the registered file – downloading it again", file=sys.stderr)
            existing.unlink()
        try:
            path = ensure_model(spec, settings.models_dir, auto_download=True)
        except ModelUnavailableError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
        print(f"{name}: OK ({path}, {path.stat().st_size / 1e6:.0f} MB, SHA-256 verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
