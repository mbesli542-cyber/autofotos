"""STEP 2b – what is the vehicle? A COCO instance-segmentation model next to BiRefNet.

BiRefNet (``segmentation.py``) cuts the salient foreground precisely but has no
notion of "car": an object right behind the car – the wheel or the rear of a car
parked behind it, a hubcap, a showroom sign on the roof, a side table, a bollard –
merges into the outline and ended up in accepted listing images. Shape rules alone
cannot tell such objects from mirrors, spoilers or roof boxes (``mask.py``).

This module adds the missing semantic signal: **RF-DETR-Seg** (Roboflow, Apache-2.0,
COCO: car, truck, bus, person, chair, table, … with an instance mask each), run with
ONNX Runtime on the CPU. Its masks are coarse (1/4 of a 432 px input), so it never
draws the cut line – it only says which BiRefNet pixels belong to THE vehicle, to
another object, or to nothing it knows, and which parts of THE vehicle BiRefNet
lost (a dark mirror in front of a dark car). BiRefNet stays the matting model;
nothing here changes or generates a pixel of the photo.

``vehicle_semantics`` (used by ``mask.clean_mask``):

1. full frame → THE vehicle: the car/truck/bus instance that agrees best with the
   BiRefNet mask (IoU >= ``VEHICLE_MIN_IOU``); none → no semantic check (warning);
2. a grid of zoomed tiles over the vehicle (side ``TILE_FRAC`` × the vehicle size,
   about 2.5× zoom – at full frame the vehicle mask swallows small glued objects):
   per pixel the tile's vehicle instance, another detected object (higher mask
   logit), or nothing; two more tiles at twice the zoom centred on the vehicle's
   left and right ends, where cars parked next to it touch it (a neighbour's
   wheel or hubcap); a pixel takes the verdict of the tile in which it lies most
   centrally;
3. ``verify_region``: a candidate region gets its own zoom tile centred on it, and
   only counts as foreign when the vehicle instance of that tile does not cover it
   and it reaches beyond that instance by more than the instance outline's own
   uncertainty (a few mask cells); a vehicle part the BiRefNet mask lost counts
   when that instance covers it (``mask.py`` decides what is a candidate and what
   happens to it).

The vehicle of a zoomed tile is the instance that matches the full-frame vehicle
mask best (a big sign on the roof was itself detected as a "car" in its tile).

``vehicle_box`` picks the vehicle before any segmentation (largest × most central
vehicle instance) – the window of the second BiRefNet pass of tall frames
(``segmentation.segment_vehicle``).

Model files are registered in ``DETECTOR_REGISTRY`` (pinned Hugging Face revision +
SHA-256, downloaded like the segmentation model and checked against it when loaded;
one blank inference at load checks the output layout); a missing, different or
broken file is a ``ModelUnavailableError`` – a service problem, exactly like a
missing BiRefNet, reported by the warm-up and ``/health``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from ..config import Settings
from .segmentation import ModelUnavailableError, SegmentationError, ensure_model

log = logging.getLogger(__name__)

#: (x0, y0, x1, y1) in photo px, x1/y1 exclusive.
Window = tuple[int, int, int, int]

#: COCO category ids of the RF-DETR head (91 logits, ids without a category are unused).
COCO_LABELS: dict[int, str] = {
    1: "person", 2: "bicycle", 3: "car", 4: "motorcycle", 5: "airplane", 6: "bus", 7: "train", 8: "truck",
    9: "boat", 10: "traffic light", 11: "fire hydrant", 13: "stop sign", 14: "parking meter", 15: "bench",
    16: "bird", 17: "cat", 18: "dog", 19: "horse", 20: "sheep", 21: "cow", 22: "elephant", 23: "bear", 24: "zebra",
    25: "giraffe", 27: "backpack", 28: "umbrella", 31: "handbag", 32: "tie", 33: "suitcase", 34: "frisbee",
    35: "skis", 36: "snowboard", 37: "sports ball", 38: "kite", 39: "baseball bat", 40: "baseball glove",
    41: "skateboard", 42: "surfboard", 43: "tennis racket", 44: "bottle", 46: "wine glass", 47: "cup", 48: "fork",
    49: "knife", 50: "spoon", 51: "bowl", 52: "banana", 53: "apple", 54: "sandwich", 55: "orange", 56: "broccoli",
    57: "carrot", 58: "hot dog", 59: "pizza", 60: "donut", 61: "cake", 62: "chair", 63: "couch",
    64: "potted plant", 65: "bed", 67: "dining table", 70: "toilet", 72: "tv", 73: "laptop", 74: "mouse",
    75: "remote", 76: "keyboard", 77: "cell phone", 78: "microwave", 79: "oven", 80: "toaster", 81: "sink",
    82: "refrigerator", 84: "book", 85: "clock", 86: "vase", 87: "scissors", 88: "teddy bear", 89: "hair drier",
    90: "toothbrush",
}  # fmt: skip
#: Classes that can be the photographed vehicle (vans, pick-ups and SUVs come as car or truck).
VEHICLE_LABELS = frozenset({"car", "truck", "bus"})

#: Instances below this class score are ignored (measured: the photographed car 0.73–0.98,
#: glued background cars 0.6–0.95 in their zoomed tile).
MIN_SCORE = 0.3
#: The full-frame vehicle instance must agree with the BiRefNet mask at least this much
#: (IoU; measured 0.95–0.99 on the 25 regression photos with a mask).
VEHICLE_MIN_IOU = 0.5
#: Long edge of the working resolution of the semantic maps.
WORK_PX = 1280
#: Tile side × the vehicle's larger bbox side (about 2.5× zoom over the full frame) …
TILE_FRAC = 0.45
#: … never smaller than the model input …
MIN_TILE_PX = 432
#: … overlapping by this share, at most MAX_TILES tiles (the side grows until they fit).
TILE_OVERLAP = 0.25
MAX_TILES = 6
#: Tiles reach this far (× vehicle width) beyond the BiRefNet bbox.
TILE_PAD = 0.06
#: End tiles: side × the vehicle width, centred on its left/right end at this
#: height (× bbox height from the top – bumpers, lights, wheel arches).
END_TILE_FRAC = 0.25
END_TILE_HEIGHT = 0.55
#: A zoomed tile's vehicle instance must overlap the full-frame vehicle mask this much (IoU).
TILE_VEHICLE_MIN_IOU = 0.3
#: Verification tile: side = this × the candidate's larger side, within these fractions
#: of the vehicle's larger side.
VERIFY_ZOOM = 4.0
VERIFY_MIN_FRAC = 0.2
VERIFY_MAX_FRAC = 0.45
#: Resolution of the verification maps (px per tile side).
VERIFY_PX = 256
#: Mask cells per tile side of the registered model (432 px input, masks at 1/4) – the
#: fallback when a tile returned no instance to read it from.
MASK_CELLS = 108


@dataclass(frozen=True)
class Instance:
    """One detected object in the analysed window."""

    label: str
    score: float
    #: (x0, y0, x1, y1) in px of the analysed window.
    box: tuple[float, float, float, float]
    #: Mask logits (> 0 = inside), any resolution, stretched over the analysed window.
    mask: np.ndarray

    @property
    def is_vehicle(self) -> bool:
        return self.label in VEHICLE_LABELS

    def logits(self, width: int, height: int) -> np.ndarray:
        """Mask logits resampled to width × height (float32)."""
        return cv2.resize(np.asarray(self.mask, np.float32), (width, height), interpolation=cv2.INTER_LINEAR)

    def as_dict(self) -> dict:
        return {"label": self.label, "score": round(self.score, 3), "box": [round(v) for v in self.box]}


@runtime_checkable
class VehicleDetector(Protocol):
    #: Short identifier reported in job metadata.
    name: str

    def detect(self, rgb: np.ndarray, window: Window | None = None) -> list[Instance]:
        """Instances in ``rgb[y0:y1, x0:x1]`` (whole photo without ``window``), boxes and
        masks relative to that window, highest score first."""
        ...


@dataclass(frozen=True)
class DetectorModelSpec:
    name: str
    filename: str
    url: str
    sha256: str
    #: Square input side of the (static-shape) ONNX graph.
    input_size: int
    license: str
    source: str


DETECTOR_REGISTRY: dict[str, DetectorModelSpec] = {
    # RF-DETR-Seg Medium (Roboflow, DINOv2 backbone, COCO instance segmentation,
    # 432 px input, masks at 108 px). Weights Apache-2.0: github.com/roboflow/rf-detr
    # (LICENSE Apache-2.0; README model table "RF-DETR-Seg-M … 432x432 … Apache 2.0" –
    # only the Atto/Femto/Pico/XL/2XL detection models are PML 1.0). ONNX export by
    # PierreMarieCurie/rf-detr-onnx (Hugging Face model repo apache-2.0, export code
    # MIT), pinned to the repository revision below (checked 2026-10-28). Its output
    # names are mislabeled in the export – outputs are identified by shape.
    "rf-detr-seg-medium": DetectorModelSpec(
        name="rf-detr-seg-medium",
        filename="rf-detr-seg-medium.onnx",
        url="https://huggingface.co/PierreMarieCurie/rf-detr-onnx/resolve/"
        "94f91976449c187be38e649863e3dadecc312437/rf-detr-seg-medium.onnx",
        sha256="061d65fb02de2ed6e1e6fa549e89405cf745d72855e967305f37317a19e5f572",
        input_size=432,
        license="Apache-2.0",
        source="https://github.com/roboflow/rf-detr (RF-DETR-Seg-M weights), ONNX export "
        "https://huggingface.co/PierreMarieCurie/rf-detr-onnx",
    ),
}

_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _split_outputs(outputs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(boxes [Q, 4] cxcywh 0..1, class logits [Q, 91], mask logits [Q, Hm, Wm]) by shape."""
    boxes = logits = masks = None
    for out in outputs:
        out = np.asarray(out)
        if out.ndim == 3 and out.shape[-1] == 4:
            boxes = out[0]
        elif out.ndim == 3 and out.shape[-1] > 4:
            logits = out[0]
        elif out.ndim == 4:
            masks = out[0]
    if boxes is None or logits is None or masks is None:
        # another model file than the registered export: a service problem, not the photo's
        raise ModelUnavailableError(
            "unexpected detector outputs: " + ", ".join(str(np.shape(o)) for o in outputs)
        )
    return boxes, logits, masks


def decode_instances(
    boxes: np.ndarray, logits: np.ndarray, masks: np.ndarray, width: int, height: int, min_score: float = MIN_SCORE
) -> list[Instance]:
    """RF-DETR outputs → instances (per query the best class, sigmoid score >= min_score)."""
    prob = 1.0 / (1.0 + np.exp(-np.asarray(logits, np.float32)))
    scores, classes = prob.max(axis=-1), prob.argmax(axis=-1)
    instances = []
    for q in np.argsort(-scores):
        score = float(scores[q])
        if score < min_score:
            break
        label = COCO_LABELS.get(int(classes[q]))
        if label is None:
            continue
        cx, cy, bw, bh = (float(v) for v in boxes[q])
        box = ((cx - bw / 2) * width, (cy - bh / 2) * height, (cx + bw / 2) * width, (cy + bh / 2) * height)
        instances.append(Instance(label, score, box, np.asarray(masks[q], np.float32)))
    return instances


class OnnxDetector:
    """RF-DETR-Seg through ONNX Runtime (CPU), loaded on first use."""

    def __init__(self, spec: DetectorModelSpec, models_dir: Path, *, auto_download: bool, threads: int = 0):
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
                options.enable_cpu_mem_arena = False  # activations are returned after each run
                try:
                    session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
                except Exception as error:  # unreadable/corrupt file, out of memory, …
                    raise ModelUnavailableError(f"cannot load model {self.spec.name}: {error}") from error
                self._validate(session)
                self._session = session
            return self._session

    def _validate(self, session) -> None:
        """One inference on a blank input (≈ 0.5 s, at warm-up): a model with another output
        layout fails here – /health reports it – instead of failing every exterior job."""
        size = self.spec.input_size
        blank = np.zeros((1, 3, size, size), np.float32)
        try:
            outputs = session.run(None, {session.get_inputs()[0].name: blank})
        except Exception as error:  # another input shape, …
            raise ModelUnavailableError(f"cannot run model {self.spec.name}: {error}") from error
        boxes, logits, masks = _split_outputs(outputs)
        if not len(boxes) == len(logits) == len(masks) or logits.shape[-1] <= max(COCO_LABELS):
            raise ModelUnavailableError(
                "unexpected detector outputs: " + ", ".join(str(np.shape(o)) for o in outputs)
            )

    def warm_up(self) -> None:
        self._get_session()

    def detect(self, rgb: np.ndarray, window: Window | None = None) -> list[Instance]:
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise SegmentationError("expected an RGB image")
        session = self._get_session()
        x0, y0, x1, y1 = window or (0, 0, rgb.shape[1], rgb.shape[0])
        crop = rgb[y0:y1, x0:x1]
        size = self.spec.input_size
        # bilinear without antialiasing, as in RF-DETR's own inference and training resize
        tensor = cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        tensor = np.ascontiguousarray(((tensor - _MEAN) / _STD).transpose(2, 0, 1)[None])
        outputs = session.run(None, {session.get_inputs()[0].name: tensor})
        boxes, logits, masks = _split_outputs(outputs)
        return decode_instances(boxes, logits, masks, x1 - x0, y1 - y0)


def create_detector(settings: Settings) -> OnnxDetector:
    spec = DETECTOR_REGISTRY.get(settings.detector_model)
    if spec is None:
        raise SegmentationError(
            f"Unknown detector model '{settings.detector_model}'. Available: {', '.join(sorted(DETECTOR_REGISTRY))}"
        )
    return OnnxDetector(spec, settings.models_dir, auto_download=settings.auto_download_models, threads=settings.threads)


# --------------------------------------------------------------------------- vehicle choice


def vehicle_box(instances: list[Instance], width: int, height: int) -> tuple[float, float, float, float] | None:
    """Box of the photographed vehicle before any segmentation: the vehicle instance with
    the largest area weighted by how central it is (phones frame the subject)."""
    best, best_weight = None, 0.0
    half_diagonal = 0.5 * float(np.hypot(width, height))
    for inst in instances:
        if not inst.is_vehicle:
            continue
        x0, y0, x1, y1 = inst.box
        area = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
        offset = float(np.hypot(0.5 * (x0 + x1) - 0.5 * width, 0.5 * (y0 + y1) - 0.5 * height)) / half_diagonal
        weight = area * (1.0 - 0.5 * min(offset, 1.0))
        if weight > best_weight:
            best, best_weight = inst, weight
    return best.box if best is not None else None


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int(np.count_nonzero(a | b))
    return int(np.count_nonzero(a & b)) / union if union else 0.0


# --------------------------------------------------------------------------- semantic maps


@dataclass
class SemanticMap:
    """Who claims which BiRefNet pixel (working resolution ``shape``, ``scale`` px per photo px)."""

    scale: float
    shape: tuple[int, int]
    #: The full-frame vehicle instance (photo px) and its IoU with the BiRefNet mask.
    vehicle: Instance | None = None
    vehicle_iou: float = 0.0
    #: Full-frame vehicle mask (bool, working resolution).
    vehicle_full: np.ndarray | None = None
    #: Zoomed tiles: claimed by THE vehicle / by another detected object (highest mask logit).
    vehicle_px: np.ndarray | None = None
    other_px: np.ndarray | None = None
    #: Label index (into ``labels``) of the object claiming an ``other_px`` pixel.
    other_label: np.ndarray | None = None
    labels: list[str] = field(default_factory=list)
    tiles: list[Window] = field(default_factory=list)
    #: Size of one mask cell of the (grid) tiles in working px – how coarse the claims are.
    cell: float = 0.0
    runs: int = 0
    ms: int = 0

    def as_dict(self) -> dict:
        return {
            "vehicle": None if self.vehicle is None else {**self.vehicle.as_dict(), "iou": round(self.vehicle_iou, 3)},
            "tiles": len(self.tiles),
            "runs": self.runs,
            "ms": self.ms,
        }


def _grid(start: int, stop: int, side: int, limit: int) -> list[int]:
    """Tile origins covering [start, stop) with side ``side`` inside [0, limit)."""
    span = stop - start
    if span <= side:
        return [int(np.clip(round(0.5 * (start + stop) - 0.5 * side), 0, max(limit - side, 0)))]
    count = int(np.ceil((span - side) / (side * (1.0 - TILE_OVERLAP)))) + 1
    return [int(np.clip(round(start + i * (span - side) / (count - 1)), 0, max(limit - side, 0))) for i in range(count)]


def tile_windows(bbox: Window, width: int, height: int) -> list[Window]:
    """Square tiles (photo px) over the vehicle bbox, at most MAX_TILES."""
    x0, y0, x1, y1 = bbox
    vw, vh = x1 - x0, y1 - y0
    pad = int(round(TILE_PAD * vw))
    rx0, ry0, rx1, ry1 = max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, width), min(y1 + pad, height)
    side = int(max(TILE_FRAC * max(vw, vh), MIN_TILE_PX))
    while True:
        side = min(side, width, height)
        xs, ys = _grid(rx0, rx1, side, width), _grid(ry0, ry1, side, height)
        if len(xs) * len(ys) <= MAX_TILES or side >= min(width, height):
            break
        side = int(side * 1.12) + 1
    return [(x, y, x + side, y + side) for y in ys for x in xs]


def end_windows(bbox: Window, width: int, height: int) -> list[Window]:
    """Square tiles at twice the zoom centred on the vehicle's left and right ends."""
    x0, y0, x1, y1 = bbox
    side = min(max(int(END_TILE_FRAC * (x1 - x0)), MIN_TILE_PX), width, height)
    cy = y0 + END_TILE_HEIGHT * (y1 - y0)
    wy0 = int(np.clip(round(cy - side / 2), 0, height - side))
    tiles = []
    for cx in (x0, x1):
        wx0 = int(np.clip(round(cx - side / 2), 0, width - side))
        tiles.append((wx0, wy0, wx0 + side, wy0 + side))
    return tiles


def _to_work(box: Window, scale: float, shape: tuple[int, int]) -> Window:
    h, w = shape
    x0, y0, x1, y1 = box
    return (
        int(np.clip(round(x0 * scale), 0, w)),
        int(np.clip(round(y0 * scale), 0, h)),
        int(np.clip(round(x1 * scale), 0, w)),
        int(np.clip(round(y1 * scale), 0, h)),
    )


def _tile_vehicle(instances: list[Instance], logits: list[np.ndarray], reference: np.ndarray) -> int | None:
    """Index of the instance that is THE vehicle in a zoomed tile (best IoU with the
    full-frame vehicle mask there)."""
    best, best_iou = None, TILE_VEHICLE_MIN_IOU
    for i, inst in enumerate(instances):
        if not inst.is_vehicle:
            continue
        iou = _iou(logits[i] > 0, reference)
        if iou >= best_iou:
            best, best_iou = i, iou
    return best


def vehicle_semantics(
    rgb: np.ndarray, solid: np.ndarray, detector: VehicleDetector, *, full: list[Instance] | None = None
) -> SemanticMap:
    """Semantic maps of the vehicle mask ``solid`` (bool, photo size); see module doc."""
    started = time.perf_counter()
    height, width = solid.shape
    scale = min(1.0, WORK_PX / float(max(height, width)))
    shape = (max(1, round(height * scale)), max(1, round(width * scale)))
    sem = SemanticMap(scale=scale, shape=shape)
    work = cv2.resize(solid.astype(np.uint8) * 255, (shape[1], shape[0]), interpolation=cv2.INTER_AREA) >= 128
    if not work.any():
        return sem
    if full is None:
        full = detector.detect(rgb)
        sem.runs += 1

    # 1. THE vehicle: best agreement with the BiRefNet mask
    for inst in full:
        if not inst.is_vehicle:
            continue
        mask = inst.logits(shape[1], shape[0]) > 0
        iou = _iou(mask, work)
        if iou > sem.vehicle_iou:
            sem.vehicle, sem.vehicle_iou, sem.vehicle_full = inst, iou, mask
    if sem.vehicle is None or sem.vehicle_iou < VEHICLE_MIN_IOU:
        sem.vehicle, sem.vehicle_full = None, None
        sem.ms = int(round((time.perf_counter() - started) * 1000))
        return sem

    # 2. zoomed tiles: per pixel vehicle / other object, the most central tile wins
    ys, xs = np.nonzero(solid)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    sem.vehicle_px = np.zeros(shape, bool)
    sem.other_px = np.zeros(shape, bool)
    sem.other_label = np.full(shape, -1, np.int16)
    centrality = np.full(shape, -1.0, np.float32)
    grid = tile_windows(bbox, width, height)
    for tile in grid + end_windows(bbox, width, height):
        tx0, ty0, tx1, ty1 = _to_work(tile, scale, shape)
        tw, th = tx1 - tx0, ty1 - ty0
        if tw < 2 or th < 2:
            continue
        instances = detector.detect(rgb, tile)
        sem.runs += 1
        sem.tiles.append(tile)
        if not sem.cell and tile in grid:
            cells = int(np.shape(instances[0].mask)[-1]) if instances else MASK_CELLS
            sem.cell = (tile[2] - tile[0]) * scale / max(cells, 1)
        logits = [inst.logits(tw, th) for inst in instances]
        reference = sem.vehicle_full[ty0:ty1, tx0:tx1]
        index = _tile_vehicle(instances, logits, reference)
        if index is None:  # the tile cannot tell: keep the full-frame vehicle mask there
            vehicle, other, label = reference, np.zeros((th, tw), bool), np.full((th, tw), -1, np.int16)
        else:
            stack = np.stack(logits)
            best = stack.argmax(axis=0)
            top = stack.max(axis=0)
            vehicle = logits[index] > 0
            other = (top > 0) & (best != index)
            label = np.full((th, tw), -1, np.int16)
            for i, inst in enumerate(instances):
                if i == index:
                    continue
                if inst.label not in sem.labels:
                    sem.labels.append(inst.label)
                label[other & (best == i)] = sem.labels.index(inst.label)
        # distance to the tile border; the photo border is no tile border
        yy, xx = np.mgrid[0:th, 0:tw].astype(np.float32)
        left = np.full_like(xx, 1e9) if tile[0] == 0 else xx
        right = np.full_like(xx, 1e9) if tile[2] >= width else tw - 1 - xx
        top_d = np.full_like(yy, 1e9) if tile[1] == 0 else yy
        bottom = np.full_like(yy, 1e9) if tile[3] >= height else th - 1 - yy
        central = np.minimum(np.minimum(left, right), np.minimum(top_d, bottom))
        region = centrality[ty0:ty1, tx0:tx1]
        better = central > region
        region[better] = central[better]
        sem.vehicle_px[ty0:ty1, tx0:tx1][better] = vehicle[better]
        sem.other_px[ty0:ty1, tx0:tx1][better] = other[better]
        sem.other_label[ty0:ty1, tx0:tx1][better] = label[better]
    undecided = centrality < 0
    sem.vehicle_px[undecided] = sem.vehicle_full[undecided]
    sem.ms = int(round((time.perf_counter() - started) * 1000))
    return sem


@dataclass(frozen=True)
class RegionCheck:
    """Verdict of a zoom tile centred on one candidate region."""

    window: Window
    #: Share of the region covered by the tile's vehicle instance / by another object.
    vehicle_cover: float
    other_cover: float
    labels: tuple[str, ...]
    #: False when no instance of the tile matched the vehicle (nothing to decide on).
    conclusive: bool
    #: How far the region reaches beyond the tile's vehicle instance (mask cells of the tile).
    reach_cells: float = 0.0

    def as_dict(self) -> dict:
        return {
            "window": list(self.window),
            "vehicleCover": round(self.vehicle_cover, 3),
            "otherCover": round(self.other_cover, 3),
            "labels": list(self.labels),
            "conclusive": self.conclusive,
            "reachCells": round(self.reach_cells, 2),
        }


def verify_region(
    rgb: np.ndarray, detector: VehicleDetector, sem: SemanticMap, region: np.ndarray, vehicle_size: int
) -> RegionCheck:
    """Zoom tile centred on ``region`` (bool, working resolution of ``sem``): how much of it
    does the vehicle instance of that tile cover, how much another detected object, and how
    far does it reach beyond that vehicle instance (in the tile's mask cells – within about
    two cells the coarse instance outline cannot tell)?"""
    height, width = rgb.shape[:2]
    ys, xs = np.nonzero(region)
    s = sem.scale
    x0, y0, x1, y1 = xs.min() / s, ys.min() / s, (xs.max() + 1) / s, (ys.max() + 1) / s
    side = VERIFY_ZOOM * max(x1 - x0, y1 - y0)
    side = int(np.clip(side, VERIFY_MIN_FRAC * vehicle_size, VERIFY_MAX_FRAC * vehicle_size))
    side = max(min(side, width, height), 1)
    wx0 = int(np.clip(round(0.5 * (x0 + x1) - 0.5 * side), 0, width - side))
    wy0 = int(np.clip(round(0.5 * (y0 + y1) - 0.5 * side), 0, height - side))
    window = (wx0, wy0, wx0 + side, wy0 + side)
    instances = detector.detect(rgb, window)
    sem.runs += 1
    n = VERIFY_PX
    logits = [inst.logits(n, n) for inst in instances]
    # the full-frame vehicle mask inside the window, as the reference for the tile's vehicle
    tx0, ty0, tx1, ty1 = _to_work(window, s, sem.shape)
    reference = sem.vehicle_full[ty0:ty1, tx0:tx1] if sem.vehicle_full is not None else None
    if reference is None or reference.size == 0:
        return RegionCheck(window, 0.0, 0.0, (), False)
    reference = cv2.resize(reference.astype(np.uint8), (n, n), interpolation=cv2.INTER_NEAREST) > 0
    # region pixels in tile coordinates
    px = np.clip(((xs + 0.5) / s - wx0) * n / side, 0, n - 1).astype(int)
    py = np.clip(((ys + 0.5) / s - wy0) * n / side, 0, n - 1).astype(int)
    index = _tile_vehicle(instances, logits, reference)
    if index is None:
        return RegionCheck(window, 0.0, 0.0, (), False)
    stack = np.stack(logits)
    best = stack.argmax(axis=0)[py, px]
    top = stack.max(axis=0)[py, px]
    # a pixel belongs to the instance with the highest mask logit (where masks overlap)
    vehicle_cover = float(np.mean((top > 0) & (best == index)))
    other = (top > 0) & (best != index)
    labels = tuple(sorted({instances[i].label for i in np.unique(best[other])}))
    beyond = cv2.distanceTransform((logits[index] <= 0).astype(np.uint8), cv2.DIST_L2, 5)
    cells = int(np.shape(instances[index].mask)[-1]) or MASK_CELLS
    reach = float(beyond[py, px].max()) * cells / n
    return RegionCheck(window, vehicle_cover, float(np.mean(other)), labels, True, reach)
