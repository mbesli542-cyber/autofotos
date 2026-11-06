"""Automatic glass masks for interior shots with SAM 2.1 (the ``GlassMaskProvider`` of
``glass_backdrop.py``).

Dealer decision: the interior stays exactly as photographed – only the view THROUGH the
see-through glass (windscreen, side windows, rear window) becomes the showroom. Mirrors
(interior and exterior mirror glass) show reflections, not the outside, and are never
glass; pillars, headliner, dashboard, papers and stickers on the glass, the wiper, the
bonnet are never glass. When unsure the provider returns less (or nothing) – the gate in
``glass_backdrop.assess_glass`` keeps the original with a German hint.

SAM 2.1 does not know "the view through the windscreen" as one object: prompted with a
point it returns the object behind the glass (a parked car, the sky, a building), prompted
with a box it returns a patchy "background" that leaves those objects out. The mask is
therefore built from evidence (all on the photo's own pixels, nothing is drawn):

1. **Seeds.** Points on a grid over the upper part of the photo (``seedGrid``, rows down
   to ``seedRows`` of the height) and the centres of the vehicles the detector sees there
   (cars outside – the photographed car itself fills the frame and is ignored) each prompt
   SAM once (multimask: three nested masks). A seed is *interior* when its middle mask is
   dark (``darkObject``: trim, wheel, display), one of its masks touches the bottom border
   or reaches below ``lowBottom`` (dash, seats, door panels: glass never reaches the bottom
   of an interior photo), its largest mask covers more than ``maxObjectArea`` of the photo,
   or its largest mask is a thin band (``thinRadius``: an A-pillar, a trim strip). The
   others are *outside* seeds; the interior seeds' small masks form the interior region
   ``I``.
2. **View.** The outside seeds, clustered by overlapping masks, form one many-point prompt
   per cluster; SAM's single-mask output is the *view* ``V`` – the pane plus whatever
   bright interior it took along (a light-grey A-pillar, the glare on the dash top).
3. **Objects.** The outside seeds' own masks are kept when they lie almost entirely inside
   the hole-filled view (``insideView``): a parked car, the sky, a fence end at the window
   frame, while a pillar, the dash top or the headliner continue far beyond the view and
   drop out. Their union ``U`` is grown by the parts of the view that are dark or border
   on ``U`` and are not interior (``I``): dark trees or asphalt at the frame that no seed
   reached. Closed, with small enclosed holes filled (dark cars, windows of cars outside).
4. **Mirrors.** Compact convex blobs in the upper part of the view that the union left
   uncovered or that are dark are prompted once more; an object that looks like the
   interior mirror (``looks_like_mirror``: compact, convex, of mirror size, hanging in the
   upper band) is cut out and never filled. Blobs inside a detected car are parts of that
   car, never a mirror. Exterior mirror glass sits in its own dark housing below or beside
   a pane; its seed mask is a compact convex blob the view does not contain.

Each connected region of the result that lies in the upper part of the photo and is
bright (``maxPaneCentroid``, ``paneBottom``, ``minPaneLum``) is one ``GlassPane``; its
plate view is decided by shot and position in ``glass_backdrop.view_for_pane`` (the
provider says nothing about views). The alpha is the binary region feathered by
``featherPx`` (≤ 3 px), so the gate's ``maxUndecided`` passes for a clean outline.

Cost per photo (CPU): one encoder pass (≈ 1.7 s on four cores), the detector (≈ 1 s) and
one decoder pass per seed (≈ 80 ms each; ``seedGrid`` 8 × 6 with ``seedRows`` 0.5 = at
most 24, dark points skipped), per view and per mirror check.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .detection import VehicleDetector
from .glass_backdrop import GlassMask, GlassMaskProvider, GlassMasksUnavailable, GlassPane
from .sam2 import Sam2Embedding, Sam2Segmenter

log = logging.getLogger(__name__)

__all__ = [
    "GlassMaskConfig",
    "GlassMaskResult",
    "find_glass",
    "looks_like_mirror",
    "sam_glass_masks",
    "shared_glass_masks",
]

SOURCE = "sam2"
#: Seeds whose small masks always form the interior region I; a dark object (wheel, display, trim – but
#: also a dark car outside) joins it only when it lies outside every view.
INTERIOR_VERDICTS = frozenset({"bottom", "low", "thin"})


@dataclass(frozen=True)
class GlassMaskConfig:
    #: Working long edge (px) of the image statistics and of the masks SAM returns.
    work_px: int = 512
    #: Grid (columns, rows over the full height) of seed points; only rows above seedRows × height.
    seed_grid: tuple[int, int] = (8, 6)
    seed_rows: float = 0.5
    #: A seed on a point darker than this (sRGB luminance 0..1, 5 × 5 median) is skipped.
    seed_min_lum: float = 0.2
    #: Bright regions (luminance ≥ brightLum, at least brightRegion of the frame, in the seed rows) that
    #: hold no prompted grid seed get a seed at their centre.
    bright_lum: float = 0.45
    bright_region: float = 0.01
    #: Detector seeds: a detected vehicle (score ≥ detectorScore) whose box covers between detectorMinArea
    #: (a car reflected in the mirror is tiny) and detectorMaxArea of the photo (the photographed car
    #: itself fills the frame) seeds a point at its centre.
    detector_score: float = 0.3
    detector_min_area: float = 0.004
    detector_max_area: float = 0.5
    #: A seed whose middle mask has a median luminance below this is a dark object (trim, wheel, display).
    dark_object: float = 0.3
    #: A mask touching the bottom border over more than this share of the width is interior.
    max_bottom_touch: float = 0.02
    #: A seed is interior when one of its masks reaches below this share of the height and covers more
    #: than lowArea of the photo (dash, seats, door panels) – unless the seed lies in a detected car.
    low_bottom: float = 0.55
    low_area: float = 0.05
    #: A seed whose largest mask covers more than this share of the photo is interior.
    max_object_area: float = 0.35
    #: A largest mask that vanishes when eroded by this share of the long edge is a thin band (pillar, trim).
    thin_radius: float = 0.012
    #: Positive points of the view prompt per seed cluster; seeds whose middle masks grown by
    #: clusterRadius × the long edge overlap (or whose largest masks overlap) share a cluster.
    max_view_points: int = 10
    cluster_radius: float = 0.02
    #: Mask levels of the outside seeds that join the union (0 = smallest … 2 = largest).
    object_levels: tuple[int, ...] = (0, 1, 2)
    #: Objects must have this share of their pixels inside the hole-filled view dilated by viewDilate × the
    #: long edge.
    inside_view: float = 0.9
    view_dilate: float = 0.012
    #: Smallest object (share of the photo) worth keeping.
    min_object: float = 0.0005
    #: Recovery from the view: its pixels darker than recoverDark, or within recoverRim × the long edge of
    #: the union, join the union unless interior (I) – 0 turns the rim off.
    recover_dark: float = 0.25
    recover_rim: float = 0.03
    interior_dilate: float = 0.006
    #: Bottom bumps: columns whose lowest pane row hangs more than bumpDepth × the height below the median
    #: lowest row of the bumpWindow × width columns around them (a strip of dash top SAM grouped with the
    #: glass above it) are trimmed back to that baseline.
    bump_depth: float = 0.03
    bump_window: float = 0.25
    #: A pane (hole-filled, grown by 2 % of the long edge – detector boxes are loose) must cover at least
    #: this share of every detected car box it overlaps, else it is incomplete (half a windscreen).
    detector_coverage: float = 0.6
    #: Closing radius (share of the long edge) and the largest enclosed hole (share of the photo) filled.
    close_radius: float = 0.004
    max_hole: float = 0.012
    #: Mirror rule: compact (solidity) convex objects of this size (share of the photo), with this aspect
    #: (w/h) …
    mirror_solidity: float = 0.85
    mirror_min_area: float = 0.001
    mirror_max_area: float = 0.03
    mirror_aspect: tuple[float, float] = (1.2, 6.0)
    #: … whose centre lies in the upper part of the view (share of the view's height) and in its middle
    #: (share of the view's width around the centre) are the interior mirror.
    mirror_top_band: float = 0.55
    mirror_centre_band: float = 0.7
    #: Blobs (uncovered or darker than mirrorDark) that are checked with a prompt, at most mirrorChecks.
    mirror_dark: float = 0.3
    mirror_checks: int = 3
    #: Smallest pane (share of the photo) returned; a pane touching no photo border (a window fully inside
    #: the frame – or a white bag, a mirror glass in its housing) must cover minInnerPane.
    min_pane: float = 0.003
    min_inner_pane: float = 0.04
    #: A pane's centroid must lie above this share of the height and its lowest row above paneBottom.
    max_pane_centroid: float = 0.45
    pane_bottom: float = 0.7
    #: A pane's median luminance must reach this …
    min_pane_lum: float = 0.35
    #: … and the median luminance of the 2 % ring around it may not exceed this: glass is framed by
    #: darker interior (pillars, headliner, dash); a bright ring means one object cut out of the view
    #: (a car outside without the window around it) – never a pane.
    max_pane_ring: float = 0.65
    #: A pane whose bounding box matches a detected car's box this closely (IoU) and whose ring is brighter
    #: than carRing is just that car cut out of the view (a crop that cuts the window away around it) –
    #: never a pane; a car that fills a window is framed by the dark window frame and stays.
    car_box_iou: float = 0.6
    car_ring: float = 0.45
    #: Veto: a bright region (≥ brightLum) in the seed rows of at least this share of the frame that is
    #: no pane and touches the top border (a window, not a dash top) – a second window the search did
    #: not resolve – makes the photo unsure: no mask at all rather than one window in the showroom and
    #: the next in the car park. Mode "not_interior" instead ignores regions the interior seeds cover.
    unassigned_veto: float = 0.02
    unassigned_mode: str = "top_touching"
    #: Feather (Gaussian sigma, photo px) of the binary region.
    feather_px: float = 2.0


@dataclass
class GlassMaskResult:
    """What ``find_glass`` decided (debug / evaluation): the mask, every seed's verdict and the
    intermediate regions at the working scale."""

    mask: GlassMask | None
    width: int
    height: int
    timings_ms: dict[str, int] = field(default_factory=dict)
    prompts: int = 0
    seeds: list[dict] = field(default_factory=list)
    detector_seeds: list[tuple[int, int]] = field(default_factory=list)
    views: list[dict] = field(default_factory=list)
    panes: list[dict] = field(default_factory=list)
    #: Bright regions neither pane nor interior (share of the frame each); one ≥ unassignedVeto vetoes.
    unassigned: list[float] = field(default_factory=list)
    work: dict[str, np.ndarray] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers


def _disk(radius: int) -> np.ndarray:
    radius = max(int(radius), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return mask
    return cv2.dilate(mask.astype(np.uint8), _disk(radius)) > 0


def _components(mask: np.ndarray, min_area: int) -> list[np.ndarray]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [labels == index for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= min_area]


def _fill_holes(mask: np.ndarray, max_area: int) -> np.ndarray:
    """Fill enclosed holes (components of the complement not touching the border) up to max_area px."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    out = mask.copy()
    height, width = mask.shape
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        touches = x == 0 or y == 0 or x + w >= width or y + h >= height
        if not touches and area <= max_area:
            out[labels == index] = True
    return out


def _shape(mask: np.ndarray) -> dict:
    """Solidity, aspect (w/h of the bounding box), centroid and bounding box of a binary blob."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return {"area": 0, "solidity": 0.0, "aspect": 0.0, "cx": 0.0, "cy": 0.0, "box": (0, 0, 0, 0)}
    hull = cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.int32))
    hull_area = max(float(cv2.contourArea(hull)), 1.0)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    return {
        "area": int(xs.size),
        "solidity": float(xs.size) / hull_area,
        "aspect": float(x1 - x0) / max(float(y1 - y0), 1.0),
        "cx": float(xs.mean()),
        "cy": float(ys.mean()),
        "box": (x0, y0, x1, y1),
    }


def _spread(items: list, count: int) -> list:
    """At most ``count`` of ``items``, evenly spread along their order."""
    if len(items) <= count:
        return items
    idx = np.linspace(0, len(items) - 1, count).round().astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def _box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / float(union) if union > 0 else 0.0


def _bottom_touch(mask: np.ndarray) -> float:
    return float(np.mean(mask[-1])) if mask.size else 0.0


def _bottom_row(mask: np.ndarray) -> float:
    rows = np.nonzero(mask.any(axis=1))[0]
    return (rows.max() + 1) / float(mask.shape[0]) if rows.size else 0.0


def _is_thin(mask: np.ndarray, radius: int) -> bool:
    return not cv2.erode(mask.astype(np.uint8), _disk(radius)).any()


def looks_like_mirror(blob: np.ndarray, view_box: tuple[int, int, int, int], cfg: GlassMaskConfig) -> bool:
    """A compact convex blob (solidity ≥ mirrorSolidity, aspect within mirrorAspect) of mirror size whose
    centre lies in the upper ``mirrorTopBand`` and the middle ``mirrorCentreBand`` of the view's bounding
    box: the interior rear-view mirror hanging into the windscreen – never glass."""
    shape = _shape(blob)
    area = shape["area"] / float(blob.size)
    if not cfg.mirror_min_area <= area <= cfg.mirror_max_area or shape["solidity"] < cfg.mirror_solidity:
        return False
    if not cfg.mirror_aspect[0] <= shape["aspect"] <= cfg.mirror_aspect[1]:
        return False
    x0, y0, x1, y1 = view_box
    width, height = max(float(x1 - x0), 1.0), max(float(y1 - y0), 1.0)
    if abs(shape["cx"] - 0.5 * (x0 + x1)) > 0.5 * cfg.mirror_centre_band * width:
        return False
    return (shape["cy"] - y0) / height <= cfg.mirror_top_band


# --------------------------------------------------------------------------- the search


class _Work:
    """The photo at the working scale with its SAM embedding; prompts are counted."""

    def __init__(self, segmenter: Sam2Segmenter, rgb: np.ndarray, cfg: GlassMaskConfig):
        self.cfg = cfg
        self.height, self.width = rgb.shape[:2]
        self.scale = min(1.0, cfg.work_px / float(max(self.height, self.width)))
        self.size = (max(1, int(round(self.width * self.scale))), max(1, int(round(self.height * self.scale))))
        small = cv2.resize(rgb, self.size, interpolation=cv2.INTER_AREA) if self.scale < 1.0 else rgb
        self.lum = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        self.lum_median = cv2.medianBlur((self.lum * 255).astype(np.uint8), 5).astype(np.float32) / 255.0
        self.long_edge = float(max(self.size))
        self.pixels = self.size[0] * self.size[1]
        self.segmenter = segmenter
        self.embedding: Sam2Embedding | None = None
        self.prompts = 0

    def encode(self, rgb: np.ndarray) -> None:
        self.embedding = self.segmenter.encode(rgb)

    def to_photo(self, x: int, y: int) -> tuple[float, float]:
        return (x + 0.5) / self.scale, (y + 0.5) / self.scale

    def radius(self, share: float) -> int:
        return max(1, int(round(share * self.long_edge)))

    def predict(self, points: list[tuple[int, int, int]], *, multimask: bool) -> list[np.ndarray]:
        """Masks (working scale, bool) for prompt points given at the working scale."""
        assert self.embedding is not None
        photo_points = [(*self.to_photo(x, y), label) for x, y, label in points]
        low, _, _ = self.segmenter.decode(self.embedding, photo_points, None, multimask=multimask)
        self.prompts += 1
        return [cv2.resize(np.asarray(m, np.float32), self.size, interpolation=cv2.INTER_LINEAR) > 0 for m in low]


@dataclass
class _Seed:
    x: int
    y: int
    masks: list[np.ndarray]
    verdict: str
    cluster: int = -1


def _grid(work: _Work) -> list[tuple[int, int]]:
    nx, ny = work.cfg.seed_grid
    width, height = work.size
    points = []
    for j in range(ny):
        y = int((j + 0.5) / ny * height)
        if y > work.cfg.seed_rows * height:
            continue
        for i in range(nx):
            points.append((int((i + 0.5) / nx * width), y))
    return points


def _detector_seeds(work: _Work, rgb: np.ndarray, detector: VehicleDetector | None) -> tuple[list[tuple[int, int]], list]:
    """Centres (working scale) of vehicles seen in the upper part of the photo (cars outside) and their
    boxes at the working scale."""
    if detector is None:
        return [], []
    cfg = work.cfg
    points, boxes = [], []
    for instance in detector.detect(rgb):
        if not instance.is_vehicle or instance.score < cfg.detector_score:
            continue
        x0, y0, x1, y1 = instance.box
        share = (x1 - x0) * (y1 - y0) / float(work.width * work.height)
        if not cfg.detector_min_area <= share <= cfg.detector_max_area:
            continue
        cx, cy = 0.5 * (x0 + x1) * work.scale, 0.5 * (y0 + y1) * work.scale
        if cy > cfg.seed_rows * work.size[1]:
            continue
        points.append((int(np.clip(cx, 0, work.size[0] - 1)), int(np.clip(cy, 0, work.size[1] - 1))))
        boxes.append(tuple(int(round(v * work.scale)) for v in (x0, y0, x1, y1)))
    return points, boxes


def _unreached_bright(work: _Work, seeds: list[_Seed]) -> list[tuple[int, int]]:
    """Centres of bright regions in the seed rows (≥ brightRegion of the frame) that hold no outside
    seed: the grid is coarse, a fence or sky at a pane's edge must not stay unseeded."""
    cfg = work.cfg
    bright = work.lum_median >= cfg.bright_lum
    bright[int(cfg.seed_rows * work.size[1]) :, :] = False
    bright = cv2.morphologyEx(bright.astype(np.uint8), cv2.MORPH_OPEN, _disk(work.radius(0.008))) > 0
    points = []
    for region in _components(bright, int(cfg.bright_region * work.pixels)):
        if any(region[s.y, s.x] for s in seeds if s.verdict == "outside"):
            continue
        shape = _shape(region)
        cx, cy = int(shape["cx"]), int(shape["cy"])
        if not region[cy, cx]:
            ys, xs = np.nonzero(region)
            nearest = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
            cx, cy = int(xs[nearest]), int(ys[nearest])
        points.append((cx, cy))
    return points


def _classify_seeds(work: _Work, extra: list[tuple[int, int]], car_boxes: list) -> list[_Seed]:
    cfg = work.cfg
    thin = work.radius(cfg.thin_radius)
    seeds = _classify_points(work, _grid(work) + extra, car_boxes, thin)
    unreached = _unreached_bright(work, seeds)
    return seeds + _classify_points(work, unreached, car_boxes, thin)


def _classify_points(work: _Work, points: list[tuple[int, int]], car_boxes: list, thin: int) -> list[_Seed]:
    cfg = work.cfg
    seeds = []
    for x, y in points:
        if work.lum_median[y, x] < cfg.seed_min_lum:
            seeds.append(_Seed(x, y, [], "dark"))
            continue
        masks = work.predict([(x, y, 1)], multimask=True)
        verdict = "outside"
        in_car = any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in car_boxes)
        if in_car:
            # a point on a car seen outside is outside, whatever its largest mask groups (the pane and a
            # light dash below it); a mask that reaches the bottom border still says the box was wrong
            if any(_bottom_touch(mask) > cfg.max_bottom_touch for mask in masks):
                verdict = "bottom"
        elif masks[1].any() and float(np.median(work.lum[masks[1]])) < cfg.dark_object:
            verdict = "darkobj"
        else:
            for k, mask in enumerate(masks):
                area = float(mask.mean())
                if _bottom_touch(mask) > cfg.max_bottom_touch:
                    verdict = "bottom"
                    break
                if area > cfg.max_object_area:
                    verdict = "huge"
                    break
                if area > cfg.low_area and _bottom_row(mask) > cfg.low_bottom:
                    verdict = "low"
                    break
            else:
                if masks[-1].any() and _is_thin(masks[-1], thin):
                    verdict = "thin"
        seeds.append(_Seed(x, y, masks, verdict))
    return seeds


def _cluster(seeds: list[_Seed], radius: int) -> int:
    """Outside seeds whose largest masks overlap, or whose middle masks grown by ``radius`` overlap,
    share a cluster (union-find); returns the count."""
    outside = [s for s in seeds if s.verdict == "outside"]
    parent = list(range(len(outside)))
    grown = [_dilate(s.masks[1], radius) for s in outside]
    largest = [s.masks[-1] for s in outside]

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(outside)):
        for j in range(i + 1, len(outside)):
            if np.any(grown[i] & grown[j]) or np.any(largest[i] & largest[j]):
                parent[find(i)] = find(j)
    roots: dict[int, int] = {}
    for i, seed in enumerate(outside):
        seed.cluster = roots.setdefault(find(i), len(roots))
    return len(roots)


def _interior_region(work: _Work, seeds: list[_Seed], views: list[np.ndarray]) -> np.ndarray:
    """The small and middle masks of the interior seeds (dash top, pillars, seats, trim) and of the dark
    objects that lie outside every view (a dark dash top, the wheel – not a dark car behind the glass)."""
    region = np.zeros(work.lum.shape, bool)
    outside_views = np.ones(work.lum.shape, bool)
    for view in views:
        outside_views &= ~view
    for seed in seeds:
        if seed.verdict in INTERIOR_VERDICTS:
            masks = seed.masks[:2]
        elif seed.verdict == "darkobj" and seed.masks[1].any():
            masks = seed.masks[:2] if np.mean(outside_views[seed.masks[1]]) >= 0.5 else []
        else:
            continue
        for mask in masks:
            if mask.mean() <= work.cfg.max_object_area:
                region |= mask
    return region


def _find_mirrors(work: _Work, view: np.ndarray, union: np.ndarray, car_boxes: list, info: dict) -> np.ndarray:
    """Blobs in the upper band of the view that the union left out (uncovered, or an enclosed hole: the
    mirror glass whose mount got in) or that are dark: prompted once; an object that looks like the
    interior mirror is returned (union of all found)."""
    cfg = work.cfg
    view_box = _shape(view)["box"]
    x0, y0, x1, y1 = view_box
    band_bottom = y0 + cfg.mirror_top_band * (y1 - y0)
    uncovered = view & ~union
    holes = _fill_holes(union, int(cfg.mirror_max_area * work.pixels * 2)) & ~union
    dark = union & (work.lum_median < cfg.mirror_dark)
    blobs = []
    for candidates in (uncovered, holes, dark):
        candidates = cv2.morphologyEx(candidates.astype(np.uint8), cv2.MORPH_OPEN, _disk(work.radius(0.004))) > 0
        for blob in _components(candidates, int(cfg.mirror_min_area * work.pixels)):
            shape = _shape(blob)
            if shape["cy"] > band_bottom or shape["area"] > cfg.mirror_max_area * work.pixels * 2:
                continue
            bx0, by0, bx1, by1 = shape["box"]
            inside_car = any(cx0 <= bx0 and cy0 <= by0 and bx1 <= cx1 and by1 <= cy1 for cx0, cy0, cx1, cy1 in car_boxes)
            if inside_car:
                continue
            blobs.append((shape["area"], int(shape["cx"]), int(shape["cy"]), blob))
    blobs.sort(key=lambda b: -b[0])
    mirrors = np.zeros_like(view)
    checks = []
    for _, cx, cy, blob in blobs[: cfg.mirror_checks]:
        ys, xs = np.nonzero(blob)
        if not blob[cy, cx]:  # the centroid may fall outside a concave blob: take the nearest blob pixel
            nearest = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
            cx, cy = int(xs[nearest]), int(ys[nearest])
        masks = work.predict([(cx, cy, 1)], multimask=True)
        found = None
        for mask in masks:
            if looks_like_mirror(mask, view_box, cfg) and np.count_nonzero(mask & blob) >= 0.5 * blob.sum():
                found = mask if found is None else (mask if mask.sum() > found.sum() else found)
        checks.append({"at": (cx, cy), "blobArea": round(float(blob.mean()), 4), "mirror": found is not None})
        if found is not None:
            mirrors |= found
    info["mirrorChecks"] = checks
    return mirrors


def _glass_of_view(
    work: _Work, view: np.ndarray, seeds: list[_Seed], interior: np.ndarray, car_boxes: list, info: dict
) -> tuple[np.ndarray, np.ndarray]:
    """The glass of one view: the seeds' objects inside it, the view's dark / adjacent parts that are not
    interior, closed and hole-filled, minus mirrors."""
    cfg = work.cfg
    close = _disk(work.radius(cfg.close_radius))
    max_hole = int(cfg.max_hole * work.pixels)
    filled = _fill_holes(cv2.morphologyEx(view.astype(np.uint8), cv2.MORPH_CLOSE, close) > 0, max_hole)
    dilated = _dilate(filled, work.radius(cfg.view_dilate))
    union = np.zeros_like(view)
    decisions = []
    for seed in seeds:
        for k, mask in enumerate(seed.masks):
            if k not in cfg.object_levels:
                continue
            area = int(mask.sum())
            record = {"seed": (seed.x, seed.y), "k": k, "area": round(area / work.pixels, 4)}
            if area < cfg.min_object * work.pixels:
                record["verdict"] = "tiny"
            else:
                inside = float(np.count_nonzero(mask & dilated)) / float(area)
                record["inside"] = round(inside, 3)
                if inside < cfg.inside_view:
                    record["verdict"] = "beyond"
                else:
                    record["verdict"] = "kept"
                    union |= mask
            decisions.append(record)
    info["objects"] = decisions
    # never interior: the dash top, pillars, seats and trim the interior seeds found
    not_interior = ~_dilate(interior, work.radius(cfg.interior_dilate))
    union &= not_interior
    objects_only = union.copy()
    # recovery from the view: dark parts (trees, asphalt) and a rim around the objects, never interior
    recover = work.lum_median < cfg.recover_dark
    if cfg.recover_rim > 0:
        recover |= _dilate(union, work.radius(cfg.recover_rim))
    union |= filled & not_interior & recover
    union = cv2.morphologyEx(union.astype(np.uint8), cv2.MORPH_CLOSE, close) > 0
    union = _fill_holes(union, max_hole)
    info["recoveredArea"] = round(float(np.mean(union & ~objects_only)), 4)
    mirrors = _find_mirrors(work, view, union, car_boxes, info)
    if mirrors.any():
        grown = _dilate(mirrors, work.radius(0.003))
        # the mirror's mount and housing: dark recovered blobs attached to the mirror are not glass either
        attached = np.zeros_like(union)
        for blob in _components(union & ~objects_only & (work.lum_median < cfg.recover_dark), 1):
            if np.any(blob & _dilate(grown, work.radius(0.006))):
                attached |= blob
        union &= ~(grown | attached)
        info["mirrorMountArea"] = round(float(attached.mean()), 4)
    return union, mirrors


def _trim_bumps(pane: np.ndarray, cfg: GlassMaskConfig) -> np.ndarray:
    """Cut the parts of a pane that hang below its local bottom baseline (see ``bumpDepth``)."""
    height, width = pane.shape
    columns = np.nonzero(pane.any(axis=0))[0]
    if columns.size < 3:
        return pane
    bottoms = np.full(width, -1, np.int32)
    for x in columns:
        bottoms[x] = int(np.nonzero(pane[:, x])[0].max())
    half = max(2, int(round(0.5 * cfg.bump_window * width)))
    depth = cfg.bump_depth * height
    out = pane.copy()
    for x in columns:
        window = bottoms[max(0, x - half) : x + half + 1]
        baseline = float(np.median(window[window >= 0]))
        if bottoms[x] - baseline > depth:
            cut_from = int(round(baseline + 0.5 * depth))
            out[cut_from:, x] = False
    return out


def find_glass(
    segmenter: Sam2Segmenter,
    rgb: np.ndarray,
    shot_key: str | None,
    cfg: GlassMaskConfig,
    detector: VehicleDetector | None = None,
) -> GlassMaskResult:
    """The glass mask of an interior photo (see the module docstring) with every decision."""
    started = time.perf_counter()
    times: dict[str, int] = {}

    def mark(step: str) -> None:
        nonlocal started
        now = time.perf_counter()
        times[step] = int(round((now - started) * 1000))
        started = now

    work = _Work(segmenter, rgb, cfg)
    result = GlassMaskResult(None, work.width, work.height, times)
    work.encode(rgb)
    mark("encode")
    extra, car_boxes = _detector_seeds(work, rgb, detector)
    result.detector_seeds = extra
    mark("detector")

    seeds = _classify_seeds(work, extra, car_boxes)
    result.seeds = [{"x": s.x, "y": s.y, "verdict": s.verdict} for s in seeds]
    clusters = _cluster(seeds, work.radius(cfg.cluster_radius))
    mark("seeds")

    views: list[tuple[np.ndarray, list[_Seed], dict]] = []
    for index in range(clusters):
        members = [s for s in seeds if s.cluster == index]
        points = _spread([(s.x, s.y, 1) for s in members], cfg.max_view_points)
        view = work.predict(points, multimask=False)[0]
        info: dict = {"seeds": len(members), "viewArea": round(float(view.mean()), 4)}
        result.views.append(info)
        if _bottom_touch(view) > cfg.max_bottom_touch:
            info["verdict"] = "bottom"
            continue
        views.append((view, members, info))
    interior = _interior_region(work, seeds, [v for v, _, _ in views])
    result.work["interior"] = interior

    glass = np.zeros(work.lum.shape, bool)
    mirrors_all = np.zeros(work.lum.shape, bool)
    for index, (view, members, info) in enumerate(views):
        union, mirrors = _glass_of_view(work, view, members, interior, car_boxes, info)
        info.update(glassArea=round(float(union.mean()), 4), verdict="ok")
        result.work[f"view{index}"] = view
        glass |= union
        mirrors_all |= mirrors
    mark("views")
    result.work["glass"] = glass
    result.work["mirrors"] = mirrors_all
    result.prompts = work.prompts

    panes = []
    for pane in _components(glass, int(cfg.min_pane * work.pixels)):
        pane = _trim_bumps(pane, cfg)
        shape = _shape(pane)
        bottom = shape["box"][3] / float(work.size[1])
        lum = float(np.median(work.lum[pane]))
        ring = _dilate(pane, work.radius(0.02)) & ~pane
        ring_lum = float(np.median(work.lum[ring])) if ring.any() else 0.0
        x0, y0, x1, y1 = shape["box"]
        at_border = x0 == 0 or y0 == 0 or x1 >= work.size[0] or y1 >= work.size[1]
        verdict = "ok"
        if shape["cy"] / float(work.size[1]) > cfg.max_pane_centroid or bottom > cfg.pane_bottom:
            verdict = "low"
        elif lum < cfg.min_pane_lum:
            verdict = "dark"
        elif ring_lum > cfg.car_ring and any(_box_iou(shape["box"], box) >= cfg.car_box_iou for box in car_boxes):
            verdict = "just_a_car"
        elif ring_lum > cfg.max_pane_ring:
            verdict = "unframed"
        elif not at_border and shape["area"] < cfg.min_inner_pane * work.pixels:
            verdict = "inner"
        else:
            hull = _dilate(_fill_holes(pane, work.pixels), work.radius(0.02))
            for bx0, by0, bx1, by1 in car_boxes:
                box_area = max((bx1 - bx0) * (by1 - by0), 1)
                covered = int(hull[by0:by1, bx0:bx1].sum()) / float(box_area)
                if 0.1 < covered < cfg.detector_coverage:
                    verdict = "incomplete"  # the pane holds only part of a car seen through it
                    break
        result.panes.append(
            {
                "area": round(shape["area"] / work.pixels, 4),
                "bottom": round(bottom, 3),
                "lum": round(lum, 3),
                "ring": round(ring_lum, 3),
                "atBorder": at_border,
                "verdict": verdict,
            }
        )
        if verdict == "ok":
            panes.append(pane)
    if not panes:
        return result
    accepted = np.zeros(work.lum.shape, bool)
    for pane in panes:
        accepted |= pane
    bright = work.lum_median >= cfg.bright_lum
    bright[int(cfg.seed_rows * work.size[1]) :, :] = False
    bright &= ~_dilate(accepted, work.radius(0.02))
    if cfg.unassigned_mode == "not_interior":
        bright &= ~_dilate(interior, work.radius(0.01))
    bright = cv2.morphologyEx(bright.astype(np.uint8), cv2.MORPH_OPEN, _disk(work.radius(0.008))) > 0
    blobs = _components(bright, int(0.005 * work.pixels))
    if cfg.unassigned_mode == "top_touching":
        blobs = [b for b in blobs if b[0].any()]
    result.unassigned = [round(float(blob.mean()), 4) for blob in blobs]
    result.work["unassigned"] = bright
    if any(share >= cfg.unassigned_veto for share in result.unassigned):
        mark("panes")
        return result
    full_size = (work.width, work.height)
    pane_objects = []
    for pane in panes:
        alpha = cv2.resize(pane.astype(np.float32), full_size, interpolation=cv2.INTER_LINEAR)
        alpha = (alpha >= 0.5).astype(np.float32)
        if cfg.feather_px > 0:
            alpha = cv2.GaussianBlur(alpha, (0, 0), cfg.feather_px)
        pane_objects.append(GlassPane(np.clip(alpha, 0.0, 1.0).astype(np.float32)))
    total = np.clip(np.sum([p.alpha for p in pane_objects], axis=0), 0.0, 1.0).astype(np.float32)
    result.mask = GlassMask(alpha=total, source=SOURCE, panes=tuple(pane_objects))
    mark("panes")
    return result


def sam_glass_masks(
    segmenter: Sam2Segmenter, cfg: GlassMaskConfig | None = None, detector: VehicleDetector | None = None
) -> GlassMaskProvider:
    """The provider for ``run_glass`` / ``JobManager(glass_masks=)`` on a given SAM 2.1 segmenter
    (tests, tools): SAM loads on the first interior photo, never earlier. ``detector``: the vehicle
    detector for the cars seen outside (optional). The service uses ``shared_glass_masks``."""
    config = cfg or GlassMaskConfig()
    return _provider(lambda: (segmenter, detector), config)


def shared_glass_masks(vehicle_segmenter, cfg: GlassMaskConfig | None = None) -> GlassMaskProvider:
    """The provider on the process's ONE SAM 2.1 segmenter: ``vehicle_segmenter.sam2`` (attached by
    ``segmentation.create_segmenter`` – the same instance the contested cut uses, so the graphs are
    loaded once and shared) and its ``detector``, read on EVERY call: while SAM 2.1 is off
    (PROCESSOR_SAM2_ENABLED=false), its graphs are missing or were refused at warm-up (``sam2`` is
    None then) the call raises ``GlassMasksUnavailable`` and the shot stays the original with the
    ``no_mask_provider`` hint. Nothing is loaded before the first interior photo that needs it."""
    config = cfg or GlassMaskConfig()

    def models():
        sam2 = getattr(vehicle_segmenter, "sam2", None)
        if sam2 is None:
            raise GlassMasksUnavailable(
                "SAM 2.1 is not available (off, graphs not built or refused at warm-up – see /health sam2, sam2Error)"
            )
        return sam2, getattr(vehicle_segmenter, "detector", None)

    return _provider(models, config)


def _provider(models, config: GlassMaskConfig) -> GlassMaskProvider:
    def provide(rgb: np.ndarray, shot_key: str | None) -> GlassMask | None:
        segmenter, detector = models()
        result = find_glass(segmenter, rgb, shot_key, config, detector)
        log.info(
            "Glass mask (%s): %s pane(s), %d prompts, %s",
            shot_key,
            len(result.mask.panes) if result.mask else 0,
            result.prompts,
            result.timings_ms,
        )
        return result.mask

    return provide
