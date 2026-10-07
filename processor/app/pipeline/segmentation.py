"""STEP 2 – vehicle segmentation behind a replaceable interface.

`VehicleSegmenter.segment(rgb) -> alpha` is the only thing the pipeline knows.
The default implementation runs an open-source salient-object model through
ONNX Runtime on the CPU. Models are registered in MODEL_REGISTRY and selected
with PROCESSOR_SEGMENTATION_MODEL; weights are downloaded on first use and
verified by SHA-256. Adding another backend (e.g. a car-specific instance
model or a hosted API) = implement `segment` and register it in
`create_segmenter`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from ..config import Settings

log = logging.getLogger(__name__)


class SegmentationError(Exception):
    """The segmenter could not produce a usable mask."""


@runtime_checkable
class VehicleSegmenter(Protocol):
    #: Short identifier reported in job metadata.
    name: str

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        """Return a float32 alpha mask in [0, 1] with exactly rgb.shape[:2]."""
        ...


@dataclass(frozen=True)
class OnnxModelSpec:
    name: str
    filename: str
    url: str
    sha256: str
    input_size: tuple[int, int]
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    #: True if the network outputs logits.
    apply_sigmoid: bool
    license: str
    source: str


MODEL_REGISTRY: dict[str, OnnxModelSpec] = {
    # Default: BiRefNet (Bilateral Reference Network), general dichotomous
    # segmentation, Swin-T backbone. Best edge quality of the tested models
    # (mirrors, wheels, antennas) while ignoring background clutter.
    "birefnet-general-lite": OnnxModelSpec(
        name="birefnet-general-lite",
        filename="birefnet-general-lite.onnx",
        url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
        "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
        sha256="5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
        input_size=(1024, 1024),
        mean=(0.485, 0.456, 0.406),
        std=(0.229, 0.224, 0.225),
        apply_sigmoid=True,
        license="MIT",
        source="https://github.com/ZhengPeng7/BiRefNet (ONNX export distributed by rembg)",
    ),
    # Faster fallback (~10x), slightly worse with floor shadows/background objects.
    "isnet-general-use": OnnxModelSpec(
        name="isnet-general-use",
        filename="isnet-general-use.onnx",
        url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx",
        sha256="60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
        input_size=(1024, 1024),
        mean=(0.5, 0.5, 0.5),
        std=(1.0, 1.0, 1.0),
        apply_sigmoid=False,
        license="Apache-2.0",
        source="https://github.com/xuebinqin/DIS (ONNX export distributed by rembg)",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(spec: OnnxModelSpec, models_dir: Path, auto_download: bool) -> Path:
    """Return the verified local model path, downloading it if allowed."""
    path = models_dir / spec.filename
    if path.is_file():
        return path
    if not auto_download:
        raise SegmentationError(
            f"Model file {path} missing and PROCESSOR_AUTO_DOWNLOAD_MODELS is disabled"
        )
    models_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading segmentation model %s …", spec.name)
    fd, tmp_name = tempfile.mkstemp(dir=models_dir, suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        urllib.request.urlretrieve(spec.url, tmp)  # noqa: S310 - fixed https URL
        actual = _sha256(tmp)
        if actual != spec.sha256:
            raise SegmentationError(f"Checksum mismatch for {spec.name}: {actual}")
        tmp.chmod(0o644)  # mkstemp creates 0600; the service may run as another user
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


class OnnxSegmenter:
    """Salient-object segmentation with an ONNX model (CPU)."""

    def __init__(self, spec: OnnxModelSpec, models_dir: Path, *, auto_download: bool, threads: int = 0):
        self.spec = spec
        self.name = f"onnx:{spec.name}"
        self._models_dir = models_dir
        self._auto_download = auto_download
        self._threads = threads
        self._session = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def _get_session(self):
        with self._lock:
            if self._session is None:
                import onnxruntime as ort

                path = ensure_model(self.spec, self._models_dir, self._auto_download)
                options = ort.SessionOptions()
                if self._threads:
                    options.intra_op_num_threads = self._threads
                options.log_severity_level = 3
                # Without the arena ONNX Runtime returns the activation memory
                # (several GB for BiRefNet) after each run instead of keeping it
                # for the lifetime of the service; measured: same speed.
                options.enable_cpu_mem_arena = False
                self._session = ort.InferenceSession(
                    str(path), options, providers=["CPUExecutionProvider"]
                )
            return self._session

    def warm_up(self) -> None:
        self._get_session()

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise SegmentationError("expected an RGB image")
        session = self._get_session()
        height, width = rgb.shape[:2]
        in_w, in_h = self.spec.input_size
        resized = cv2.resize(rgb, (in_w, in_h), interpolation=cv2.INTER_AREA)
        tensor = resized.astype(np.float32) / 255.0
        tensor = (tensor - np.array(self.spec.mean, np.float32)) / np.array(self.spec.std, np.float32)
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None])
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: tensor})[0]
        pred = np.asarray(output, dtype=np.float32).reshape(output.shape[-2], output.shape[-1])
        if self.spec.apply_sigmoid:
            pred = 1.0 / (1.0 + np.exp(-pred))
        lo, hi = float(pred.min()), float(pred.max())
        if hi - lo < 1e-6:
            raise SegmentationError("segmentation produced an empty mask")
        pred = (pred - lo) / (hi - lo)
        alpha = cv2.resize(pred, (width, height), interpolation=cv2.INTER_CUBIC)
        return np.clip(alpha, 0.0, 1.0).astype(np.float32)


def create_segmenter(settings: Settings) -> VehicleSegmenter:
    if settings.segmenter != "onnx":
        raise SegmentationError(f"Unknown segmenter backend '{settings.segmenter}'")
    spec = MODEL_REGISTRY.get(settings.segmentation_model)
    if spec is None:
        raise SegmentationError(
            f"Unknown segmentation model '{settings.segmentation_model}'. "
            f"Available: {', '.join(sorted(MODEL_REGISTRY))}"
        )
    return OnnxSegmenter(
        spec,
        settings.models_dir,
        auto_download=settings.auto_download_models,
        threads=settings.threads,
    )
