"""STEP 2 – vehicle segmentation behind a replaceable interface.

`VehicleSegmenter.segment(rgb) -> alpha` is the matting model the pipeline
knows. The default implementation runs an open-source salient-object model
(BiRefNet) through ONNX Runtime on the CPU. Models are registered in
MODEL_REGISTRY and selected with PROCESSOR_SEGMENTATION_MODEL; weights are
downloaded on first use and verified by SHA-256. Adding another backend (e.g. a
hosted API) = implement `segment` and register it in `create_segmenter`.

The segmenter created by `create_segmenter` also carries the semantic vehicle
detector (`segmenter.detector`, an instance-segmentation model, see
`detection.py`): BiRefNet cuts the outline, the detector says which part of it
is the vehicle. Both are loaded by `warm_up` and reported by `loaded`; a
missing or broken file of either is a `ModelUnavailableError`.

`segment_vehicle` decides what BiRefNet sees: the whole frame, or – for tall
(portrait or square) frames, where the car is only a thin band of the
1024 x 1024 model input and BiRefNet merged a car behind it, a cone or snow into
the outline – the ORIGINAL pixels of a window around the vehicle. When the
window pass lost a vehicle part that the detector sees (a dark side mirror) or a
piece of one (the base of a mirror whose head it kept), `merge_parts` takes that
part – and only that part, bounded by colour edges – from the whole-frame pass.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import cv2
import numpy as np

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover
    from .detection import VehicleDetector

log = logging.getLogger(__name__)


#: A photo without a confident foreground object is rejected instead of
#: stretching model noise into a mask (measured: car photos reach 1.0 with
#: ≥ 5 % of the frame above 0.5; empty walls/pocket shots stay below 0.1).
MIN_CONFIDENCE = 0.5
MIN_FOREGROUND_FRACTION = 0.002


class ModelUnavailableError(Exception):
    """The segmentation model cannot be loaded – a service problem, not the photo's."""


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


class ModelFile(Protocol):
    """A registered model file (segmentation or detector spec)."""

    name: str
    filename: str
    url: str
    sha256: str


def ensure_model(spec: ModelFile, models_dir: Path, auto_download: bool) -> Path:
    """Return the verified local model path, downloading it if allowed. An existing file is
    verified too (once per load, ≈ 0.5 s for BiRefNet): another export under the registered
    name, or a damaged file, is a ``ModelUnavailableError`` at warm-up – /health reports it –
    instead of wrong results or an error at the first job."""
    path = models_dir / spec.filename
    if path.is_file():
        actual = _sha256(path)
        if actual != spec.sha256:
            raise ModelUnavailableError(
                f"Checksum mismatch for {spec.name}: {path} is not the registered file ({actual}) – "
                "delete it and run scripts/download_models.py"
            )
        return path
    if not auto_download:
        raise ModelUnavailableError(
            f"Model file {path} missing and PROCESSOR_AUTO_DOWNLOAD_MODELS is disabled"
        )
    models_dir.mkdir(parents=True, exist_ok=True)
    log.info("Downloading model %s …", spec.name)
    fd, tmp_name = tempfile.mkstemp(dir=models_dir, suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        urllib.request.urlretrieve(spec.url, tmp)  # noqa: S310 - fixed https URL
        actual = _sha256(tmp)
        if actual != spec.sha256:
            raise ModelUnavailableError(f"Checksum mismatch for {spec.name}: {actual}")
        tmp.chmod(0o644)  # mkstemp creates 0600; the service may run as another user
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


class OnnxSegmenter:
    """Salient-object segmentation with an ONNX model (CPU)."""

    def __init__(
        self,
        spec: OnnxModelSpec,
        models_dir: Path,
        *,
        auto_download: bool,
        threads: int = 0,
        detector: VehicleDetector | None = None,
    ):
        self.spec = spec
        self.name = f"onnx:{spec.name}"
        #: Semantic vehicle detector used next to this model (see detection.py).
        self.detector = detector
        self._models_dir = models_dir
        self._auto_download = auto_download
        self._threads = threads
        self._session = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """The segmentation model and (if any) the detector are loaded."""
        return self._session is not None and bool(getattr(self.detector, "loaded", True))

    def _get_session(self):
        with self._lock:
            if self._session is None:
                import onnxruntime as ort

                try:
                    path = ensure_model(self.spec, self._models_dir, self._auto_download)
                except ModelUnavailableError:
                    raise
                except Exception as error:  # download failed (network, disk, …)
                    raise ModelUnavailableError(f"cannot obtain model {self.spec.name}: {error}") from error
                options = ort.SessionOptions()
                if self._threads:
                    options.intra_op_num_threads = self._threads
                options.log_severity_level = 3
                # Without the arena ONNX Runtime returns the activation memory
                # (several GB for BiRefNet) after each run instead of keeping it
                # for the lifetime of the service; measured: same speed.
                options.enable_cpu_mem_arena = False
                try:
                    self._session = ort.InferenceSession(
                        str(path), options, providers=["CPUExecutionProvider"]
                    )
                except Exception as error:  # unreadable/corrupt file, out of memory, …
                    raise ModelUnavailableError(f"cannot load model {self.spec.name}: {error}") from error
            return self._session

    def warm_up(self) -> None:
        self._get_session()
        if self.detector is not None and hasattr(self.detector, "warm_up"):
            self.detector.warm_up()

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
        if hi < MIN_CONFIDENCE or float(np.mean(pred > 0.5)) < MIN_FOREGROUND_FRACTION or hi - lo < 1e-6:
            raise SegmentationError("no confident foreground object in the photo")
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
    from .detection import create_detector

    return OnnxSegmenter(
        spec,
        settings.models_dir,
        auto_download=settings.auto_download_models,
        threads=settings.threads,
        detector=create_detector(settings),
    )


# --------------------------------------------------------------------------- window pass

# Frames that are not landscape (portrait and square) are segmented inside a window
# around the vehicle: in one whole-frame pass the car is a thin band of the model
# input and BiRefNet merged a car parked behind it, a traffic cone and snow into the
# outline (portrait 9:16 and 3:4 composites of the regression photos) – a window
# around the car was clean. Landscape frames (4:3, 16:9) keep the whole-frame pass,
# which the regression set validates.

#: The window: the vehicle box grown by this share of its size on every side …
WINDOW_MARGIN = 0.2
#: … then widened to at least square and heightened to at most 4:3 – the framing of
#: a landscape phone photo, which the regression set validates (a 2160 × 1620 cut of
#: a portrait frame around a side view was clean; a 16:9 window merged a car behind
#: the bonnet) – clamped to the photo.
WINDOW_MAX_ASPECT = 4 / 3
#: The window pass is only used when its vehicle does not run into an edge of the
#: window inside the photo (solid pixels over this share of that edge).
WINDOW_EDGE_TOUCH = 0.02


def vehicle_window(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    """Window (x0, y0, x1, y1) of the second pass around ``box`` (photo px)."""
    x0, y0, x1, y1 = box
    bw, bh = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    x0, x1 = x0 - WINDOW_MARGIN * bw, x1 + WINDOW_MARGIN * bw
    y0, y1 = y0 - WINDOW_MARGIN * bh, y1 + WINDOW_MARGIN * bh
    w, h = min(x1 - x0, float(width)), min(y1 - y0, float(height))
    w = min(max(w, h), float(width))  # at least square (as far as the photo allows) …
    h = min(max(h, w / WINDOW_MAX_ASPECT), float(height))  # … at most 4:3
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    wx0 = int(round(np.clip(cx - 0.5 * w, 0, width - w)))
    wy0 = int(round(np.clip(cy - 0.5 * h, 0, height - h)))
    return wx0, wy0, min(wx0 + int(round(w)), width), min(wy0 + int(round(h)), height)


def _solid_bbox(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha >= 0.5)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def window_edges_touched(alpha: np.ndarray, window: tuple[int, int, int, int], width: int, height: int) -> list[str]:
    """Edges of the window INSIDE the photo that the solid window alpha runs into."""
    solid = alpha >= 0.5
    x0, y0, x1, y1 = window
    h, w = solid.shape
    edges = []
    if x0 > 0 and solid[:, :2].any(axis=1).sum() > WINDOW_EDGE_TOUCH * h:
        edges.append("left")
    if x1 < width and solid[:, -2:].any(axis=1).sum() > WINDOW_EDGE_TOUCH * h:
        edges.append("right")
    if y0 > 0 and solid[:2, :].any(axis=0).sum() > WINDOW_EDGE_TOUCH * w:
        edges.append("top")
    if y1 < height and solid[-2:, :].any(axis=0).sum() > WINDOW_EDGE_TOUCH * w:
        edges.append("bottom")
    return edges


def segment_vehicle(
    rgb: np.ndarray, segmenter: VehicleSegmenter, vehicle_box: tuple[float, float, float, float] | None = None
) -> tuple[np.ndarray, dict]:
    """Coarse vehicle alpha for the whole photo and how it was made.

    Landscape frames: one pass on the whole frame. Portrait/square frames: one pass
    on the ORIGINAL pixels of ``vehicle_window`` around ``vehicle_box`` (the
    detector's vehicle) – or, without it, around the bbox of a whole-frame pass –
    with the alpha zero outside the window. When the vehicle of the window pass
    runs into a window edge inside the photo (the window did not contain the whole
    vehicle), the whole-frame alpha is used.
    """
    height, width = rgb.shape[:2]
    info: dict = {"passes": []}

    def run(window: tuple[int, int, int, int] | None) -> np.ndarray:
        image = rgb if window is None else np.ascontiguousarray(rgb[window[1] : window[3], window[0] : window[2]])
        started = time.perf_counter()
        alpha = segmenter.segment(image)
        expected = image.shape[:2]
        if alpha.shape != expected:
            raise ValueError("segmenter returned a mask of the wrong size")
        info["passes"].append(
            {"window": list(window) if window else None, "ms": int(round((time.perf_counter() - started) * 1000))}
        )
        return alpha

    if width > height:  # landscape
        return run(None), info
    full = None
    box = vehicle_box
    info["boxSource"] = "detector" if box is not None else "firstPass"
    if box is None:
        full = run(None)
        box = _solid_bbox(full)
        if box is None:
            return full, info
    window = vehicle_window(box, width, height)
    info["window"] = list(window)
    if window == (0, 0, width, height):
        info["windowUsed"] = False
        return (full if full is not None else run(None)), info
    part = run(window)
    touched = window_edges_touched(part, window, width, height)
    if touched:
        info["windowUsed"] = False
        info["windowEdgesTouched"] = touched
        return (full if full is not None else run(None)), info
    alpha = np.zeros((height, width), np.float32)
    alpha[window[1] : window[3], window[0] : window[2]] = part
    info["windowUsed"] = True
    return alpha, info


# --------------------------------------------------------------------------- missing parts

#: A part missing from the window pass is taken from the whole-frame pass only where that pass
#: has it: solid and attached to the vehicle over at least this share of the part's core (its
#: claim without the instance mask's coarse band).
PART_MIN_SHARE = 0.5
#: The other pass's soft edge around a taken part (photo px).
PART_FRINGE_PX = 4
#: The claim's outline is about one mask cell coarse: the part may reach this far (× one cell)
#: beyond it – the top rim of a merged mirror lay 2–5 px outside its claim and was faded out.
PART_REACH_CELLS = 0.5
#: Colour edge (CIE ΔE between pixels two apart) that bounds what is taken: the other pass's solid
#: pixels in the claim's coarse band are taken only in regions between such edges that hold the
#: part's interior (its core one mask cell in) or lie mostly (PART_REGION_SHARE) in its core, plus
#: the edge band next to them up to PART_EDGE_CELLS × one mask cell (the part's anti-aliased rim, a
#: lens, trim; 7 px at the 10.7-px cells of front_crop3x4, where the indicator lens needed 7–8 px)
#: – along the vehicle of the window pass only PART_JOIN_PX (that band leads around what lies
#: beyond the part). Background the other pass merged into the band – the white car behind, seen
#: between a mirror and the A-pillar, a 10–15 px wedge – lies mostly outside the core and stays out.
PART_EDGE_DE = 20.0
PART_REGION_SHARE = 0.5
PART_EDGE_CELLS = 2 / 3
PART_JOIN_PX = 2
#: Where the other pass continues solid beyond what is taken, the alpha is cut with an
#: anti-aliasing ramp of this width (photo px) – along a colour edge, not faded over PART_FRINGE_PX.
PART_CUT_PX = 1.5


def _to_photo(region: np.ndarray, box: tuple[int, int, int, int], scale: float, shape: tuple[int, int]) -> np.ndarray:
    """``region`` (bool, working resolution) inside the photo-px ``box`` of the given crop
    shape – interpolated, so its outline has no working-resolution stair-steps."""
    cx0, cy0, cx1, cy1 = box
    if not region.any():
        return np.zeros(shape, bool)
    # the working px covering the crop (+1 for the interpolation), resampled to the crop
    wx0, wy0 = max(int(np.floor(cx0 * scale)) - 1, 0), max(int(np.floor(cy0 * scale)) - 1, 0)
    wx1 = min(int(np.ceil(cx1 * scale)) + 2, region.shape[1])
    wy1 = min(int(np.ceil(cy1 * scale)) + 2, region.shape[0])
    patch = region[wy0:wy1, wx0:wx1].astype(np.float32)
    # photo px centre (x + 0.5) lies at working px (x + 0.5) * scale - 0.5 of the full map
    map_x = ((np.arange(cx0, cx1, dtype=np.float32) + 0.5) * scale - 0.5 - wx0)[None, :].repeat(shape[0], axis=0)
    map_y = ((np.arange(cy0, cy1, dtype=np.float32) + 0.5) * scale - 0.5 - wy0)[:, None].repeat(shape[1], axis=1)
    values = cv2.remap(patch, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    return values >= 0.5


def colour_edges(rgb: np.ndarray, threshold: float = PART_EDGE_DE) -> np.ndarray:
    """Colour edges (bool): pixels where the colour (CIE Lab) differs by ``threshold`` ΔE between
    the neighbours two apart (horizontally or vertically), grown by 1 px – a thin strip between
    two edges (a mirror's light rim) is all edge band, and regions do not leak through 1-px gaps."""
    lab = cv2.cvtColor(np.asarray(rgb, np.float32) / 255.0, cv2.COLOR_RGB2Lab)
    dx = np.zeros(lab.shape[:2], np.float32)
    dy = np.zeros(lab.shape[:2], np.float32)
    dx[:, 1:-1] = np.linalg.norm(lab[:, 2:] - lab[:, :-2], axis=-1)
    dy[1:-1] = np.linalg.norm(lab[2:] - lab[:-2], axis=-1)
    edges = (np.maximum(dx, dy) >= threshold).astype(np.uint8)
    return cv2.dilate(edges, np.ones((3, 3), np.uint8)) > 0


def _disk(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def merge_parts(
    coarse: np.ndarray,
    other: np.ndarray,
    parts: list[tuple[np.ndarray, np.ndarray]],
    scale: float,
    *,
    rgb: np.ndarray,
    cell: float,
) -> tuple[np.ndarray, list[list[int]]]:
    """``coarse`` with the alpha of ``other`` (another BiRefNet pass of the same photo ``rgb``) for
    each part the detector claims but ``coarse`` lacks (``parts``: (core, claim), bool at ``scale``
    working px per photo px, see ``mask.MaskInfo.part_regions``; ``cell``: one mask cell of the
    instance masks in photo px).

    Only solid pixels of ``other`` are taken, only within the claim (grown by PART_REACH_CELLS –
    its outline is that coarse), only where they belong to the part between colour edges (see
    PART_EDGE_DE: background ``other`` merged into the claim's band stays out), only attached to
    the vehicle of ``coarse`` – with ``other``'s own soft edge, cut with a short anti-aliasing ramp
    where ``other`` continues beyond them – and only when they cover the part's core: nothing else
    changes. Returns the merged alpha and the photo-px boxes of the parts taken."""
    height, width = coarse.shape
    merged = coarse.copy()
    taken: list[list[int]] = []
    reach = max(1, int(round(PART_REACH_CELLS * cell)))
    depth = max(1, int(round(cell)))
    rim_px = max(3, int(round(PART_EDGE_CELLS * cell)))
    margin = reach + PART_FRINGE_PX + 2
    for core, claim in parts:
        ys, xs = np.nonzero(claim)
        if len(xs) == 0:
            continue
        x0, y0 = int(xs.min() / scale), int(ys.min() / scale)
        x1, y1 = min(int(np.ceil((xs.max() + 1) / scale)), width), min(int(np.ceil((ys.max() + 1) / scale)), height)
        # a crop with a margin for the reach and the soft edge
        box = (max(x0 - margin, 0), max(y0 - margin, 0), min(x1 + margin, width), min(y1 + margin, height))
        cx0, cy0, cx1, cy1 = box
        shape = (cy1 - cy0, cx1 - cx0)
        claimed = _to_photo(claim, box, scale, shape)
        centre = _to_photo(core, box, scale, shape) & claimed
        current = merged[cy0:cy1, cx0:cx1] >= 0.5
        whole = other[cy0:cy1, cx0:cx1]
        solid = whole >= 0.5
        candidate = solid & (cv2.dilate(claimed.astype(np.uint8), _disk(reach)) > 0) & ~current
        # the part's interior: its core one mask cell in (the core's own outline is that coarse)
        inner = cv2.erode(centre.astype(np.uint8), _disk(depth)) > 0
        seeds = (inner if (inner & candidate).any() else centre) & candidate
        # regions between colour edges (not through the vehicle of this pass) that hold the part's
        # interior or lie mostly in its core – plus the edge band next to them (the part's rim)
        edges = colour_edges(rgb[cy0:cy1, cx0:cx1])
        smooth = ~edges & ~current
        count, regions = cv2.connectedComponents(smooth.astype(np.uint8), connectivity=4)
        sizes = np.bincount(regions.ravel(), minlength=count)
        inside = np.bincount(regions.ravel(), weights=centre.ravel().astype(np.float64), minlength=count)
        chosen = inside >= PART_REGION_SHARE * sizes
        chosen[regions[seeds & smooth]] = True
        chosen[0] = False  # the edge band and the vehicle
        part = (chosen[regions] & candidate) | seeds
        near = cv2.dilate(current.astype(np.uint8), _disk(PART_JOIN_PX)) > 0
        for band, steps in ((candidate & edges & ~near, rim_px), (candidate & edges & near, PART_JOIN_PX)):
            for _ in range(steps):
                part |= (cv2.dilate(part.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & band
        vehicle = cv2.dilate(current.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        count, labels = cv2.connectedComponents(part.astype(np.uint8), connectivity=8)
        attached = [k for k in range(1, count) if (vehicle & (labels == k)).any()]
        keep = np.isin(labels, attached) if attached else np.zeros_like(part)
        if not centre.any() or int((keep & centre).sum()) < PART_MIN_SHARE * int(centre.sum()):
            continue  # the other pass does not have the part either
        # the part with the other pass's own soft edge; where that pass continues solid beyond it
        # (background it merged into the part) a short anti-aliased cut
        beyond = cv2.distanceTransform((~keep).astype(np.uint8), cv2.DIST_L2, 3)
        cut = np.clip(1.0 - (beyond - 0.5) / PART_CUT_PX, 0.0, 1.0)
        weight = np.where(keep, 1.0, np.where(solid, cut, (beyond <= PART_FRINGE_PX).astype(np.float32)))
        local = merged[cy0:cy1, cx0:cx1]
        np.maximum(local, whole * weight, out=local)
        kys, kxs = np.nonzero(keep)
        taken.append([cx0 + int(kxs.min()), cy0 + int(kys.min()), cx0 + int(kxs.max()) + 1, cy0 + int(kys.max()) + 1])
    return merged, taken

