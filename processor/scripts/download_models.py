"""Download (and verify) the model weights ahead of time.

Usage (from processor/):
    python scripts/download_models.py                  # configured segmentation model + vehicle detector
                                                       # + the configured SAM 2.1 graphs when they are there
    python scripts/download_models.py --all            # every downloadable model + the configured SAM 2.1 graphs
    python scripts/download_models.py isnet-general-use rf-detr-seg-medium
    python scripts/download_models.py sam2.1-hiera-small   # verify the BUILT SAM 2.1 graphs (required)

Every file is verified against the SHA-256 pinned in app/pipeline/segmentation.py
(MODEL_REGISTRY, BiRefNet/IS-Net) or app/pipeline/detection.py (DETECTOR_REGISTRY,
RF-DETR-Seg). An existing file with another checksum (damaged, or another export
under the registered name – e.g. in the Docker build cache) is downloaded again;
the service itself never replaces a file, it refuses to load it.

The SAM 2.1 graphs (SAM2_REGISTRY in app/pipeline/sam2.py) have no download source:
they are built from the pinned checkpoint by scripts/export_sam2.py (torch export
venv; the Docker build does it in its sam2-export stage). Named explicitly they are
only verified – a missing or different file is reported with the build hint and
exit code 1. Without names and with --all the CONFIGURED SAM 2.1 model
(PROCESSOR_SAM2_MODEL) is verified when its graphs are there and reported as
"not built" otherwise (exit code 0: the service runs without them – contested cut
and automatic glass masks off); the other registered SAM 2.1 models are never
checked by --all (they are alternatives, not a download).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.pipeline.detection import DETECTOR_REGISTRY  # noqa: E402
from app.pipeline.sam2 import SAM2_REGISTRY, ensure_built_model  # noqa: E402
from app.pipeline.segmentation import MODEL_REGISTRY, ModelUnavailableError, _sha256, ensure_model  # noqa: E402

REGISTRY = {**MODEL_REGISTRY, **DETECTOR_REGISTRY, **SAM2_REGISTRY}


def verify_built(name: str, models_dir: Path, *, required: bool = True) -> int:
    """SAM 2.1: verify the two built graphs (never downloaded, never deleted). ``required=False``
    (the configured model in a run without names / --all): graphs that are not built at all are
    reported and tolerated – a built but foreign file is always an error."""
    spec = SAM2_REGISTRY[name]
    if not required and not any((models_dir / filename).is_file() for filename in spec.filenames):
        print(
            f"{name}: not built ({models_dir}/{spec.encoder_filename}, {spec.decoder_filename}) – the service runs "
            "without SAM 2.1 (no contested cut, no automatic glass masks); build them with scripts/export_sam2.py"
        )
        return 0
    for filename, sha256 in ((spec.encoder_filename, spec.encoder_sha256), (spec.decoder_filename, spec.decoder_sha256)):
        try:
            path = ensure_built_model(filename, sha256, models_dir, name=name)
        except ModelUnavailableError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
        print(f"{name}: OK ({path}, {path.stat().st_size / 1e6:.0f} MB, SHA-256 verified)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", help=f"model names ({', '.join(REGISTRY)})")
    parser.add_argument("--all", action="store_true", help="download every registered model")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.models and not args.all:
        names, required = args.models, True
    else:
        downloadable = [*MODEL_REGISTRY, *DETECTOR_REGISTRY] if args.all else [settings.segmentation_model, settings.detector_model]
        names, required = [*downloadable, settings.sam2_model], False
    for name in names:
        spec = REGISTRY.get(name)
        if spec is None:
            print(f"unknown model: {name}", file=sys.stderr)
            return 2
        if name in SAM2_REGISTRY:
            if verify_built(name, settings.models_dir, required=required):
                return 1
            continue
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
