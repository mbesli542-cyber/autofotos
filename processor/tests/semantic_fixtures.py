"""Fakes for the semantic vehicle check and the window pass (no model needed).

``FakeDetector`` answers like the real RF-DETR-Seg export: for every registered
object (label, score, full-photo mask) whose mask meets the analysed window, an
instance with mask LOGITS at a fixed model resolution (``MASK_CELLS`` per window
side, like the 108 cells of the 432 px input) – so a large window loses small
details exactly like the real model, and a zoomed window resolves them.

``ColourSegmenter`` is a segmenter for any crop: alpha = pixels close to a paint
colour (plus listed extra colours), slightly blurred – the window pass of
portrait photos segments crops whose size no registered mask could match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.pipeline.detection import Instance

#: Mask resolution of the fake model (cells per window side).
MASK_CELLS = 108


@dataclass
class FakeObject:
    label: str
    score: float
    mask: np.ndarray  # bool, photo size


@dataclass
class FakeDetector:
    """Detector over registered ground-truth objects; records every window it is asked for."""

    objects: list[FakeObject] = field(default_factory=list)
    name: str = "fake-detector"
    loaded: bool = True
    calls: list[tuple[int, int, int, int] | None] = field(default_factory=list)

    def add(self, label: str, mask: np.ndarray, score: float = 0.9) -> FakeDetector:
        self.objects.append(FakeObject(label, score, np.asarray(mask) > 0))
        return self

    def warm_up(self) -> None:
        return None

    def detect(self, rgb: np.ndarray, window=None) -> list[Instance]:
        self.calls.append(window)
        height, width = rgb.shape[:2]
        x0, y0, x1, y1 = window or (0, 0, width, height)
        instances = []
        for obj in self.objects:
            crop = obj.mask[y0:y1, x0:x1]
            if not crop.any():
                continue
            # the model's coarse view: share of each cell inside the object → logits
            cells = cv2.resize(crop.astype(np.float32), (MASK_CELLS, MASK_CELLS), interpolation=cv2.INTER_AREA)
            logits = (cells - 0.5) * 12.0
            ys, xs = np.nonzero(crop)
            box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
            instances.append(Instance(obj.label, obj.score, box, logits.astype(np.float32)))
        return sorted(instances, key=lambda inst: -inst.score)


class ColourSegmenter:
    """Alpha = pixels within ``tolerance`` of one of ``colours`` (sum of abs RGB differences)."""

    name = "colour"

    def __init__(self, *colours: tuple[int, int, int], tolerance: int = 60, detector=None):
        self.colours = [np.array(c, np.int16) for c in colours]
        self.tolerance = tolerance
        self.detector = detector
        self.calls: list[tuple[int, int]] = []

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        self.calls.append(rgb.shape[:2])
        mask = np.zeros(rgb.shape[:2], bool)
        for colour in self.colours:
            mask |= np.abs(rgb.astype(np.int16) - colour).sum(axis=-1) < self.tolerance
        return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 1.2)


def side_view(width: int = 2400, height: int = 1500) -> np.ndarray:
    """Side-view car silhouette (bool): body, cabin, wheels – about 70 % of the width."""
    mask = np.zeros((height, width), np.uint8)
    x0, x1 = int(0.15 * width), int(0.85 * width)
    base = int(0.8 * height)
    cv2.rectangle(mask, (x0, base - int(0.2 * height)), (x1, base - int(0.04 * height)), 1, -1)
    roof = np.array(
        [[x0 + int(0.18 * (x1 - x0)), base - int(0.2 * height)], [x0 + int(0.3 * (x1 - x0)), base - int(0.36 * height)],
         [x0 + int(0.7 * (x1 - x0)), base - int(0.36 * height)], [x0 + int(0.84 * (x1 - x0)), base - int(0.2 * height)]],
        np.int32,
    )  # fmt: skip
    cv2.fillPoly(mask, [roof], 1)
    radius = int(0.07 * height)
    for wx in (x0 + int(0.2 * (x1 - x0)), x1 - int(0.2 * (x1 - x0))):
        cv2.circle(mask, (wx, base - radius), radius, 1, -1)
    return mask.astype(bool)
