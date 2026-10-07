"""Download (and verify) the segmentation model weights ahead of time.

Usage (from processor/):
    python scripts/download_models.py                  # default model
    python scripts/download_models.py --all            # every registered model
    python scripts/download_models.py isnet-general-use

Every file is verified against the SHA-256 pinned in app/pipeline/segmentation.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.pipeline.segmentation import MODEL_REGISTRY, ModelUnavailableError, _sha256, ensure_model  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", help=f"model names ({', '.join(MODEL_REGISTRY)})")
    parser.add_argument("--all", action="store_true", help="download every registered model")
    args = parser.parse_args()

    settings = Settings.from_env()
    names = list(MODEL_REGISTRY) if args.all else (args.models or [settings.segmentation_model])
    for name in names:
        spec = MODEL_REGISTRY.get(name)
        if spec is None:
            print(f"unknown model: {name}", file=sys.stderr)
            return 2
        try:
            path = ensure_model(spec, settings.models_dir, auto_download=True)
        except ModelUnavailableError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
        if _sha256(path) != spec.sha256:
            print(f"{name}: checksum mismatch for {path} – delete the file and retry", file=sys.stderr)
            return 1
        print(f"{name}: OK ({path}, {path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
