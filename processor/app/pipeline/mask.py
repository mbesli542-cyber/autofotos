"""STEP 3 – turn the segmentation alpha into a clean vehicle alpha.

The alpha itself comes from ``matting`` (the model alpha snapped onto colour
edges). Here:

1. Keep the vehicle: the largest component plus parts close to it (mirrors,
   antenna pieces) – drop unrelated blobs (people, other cars, blobs inside the
   vehicle's box but far from the body).
2. Remove foreign "spikes": a slender object standing BEHIND the car (traffic
   cone, bollard, post) that the model merged into the outline and that sticks
   out of it below the roofline. Only the outline is cut (alpha only, the cut
   edge against the car anti-aliased); antennas, mirrors, roof rails/racks and
   spoilers do not match the shape rules. A spike whose cut line is not certain
   is not cut but reported (``foreign_suspected``) – the quality gate then
   rejects the job (``mask_low_confidence``, reason ``foreign_object``).
3. Semantic check (with the vehicle detector, ``detection.py``): shape rules
   cannot tell a neighbour car's wheel, a hubcap, the rear of a car parked
   behind, a sign on the roof or a bollard behind a mirror from vehicle parts.
   The detector's instance masks can: BiRefNet pixels that the vehicle instance
   of a zoomed tile does not claim, that are thick and large enough, and that
   another detected object claims – or that have a spike's shape but do not
   reach far enough beyond the body, or form a LARGE protrusion at roof level –
   are verified in a zoom tile centred on them (foreign: that tile's vehicle
   instance does not cover them, and they reach beyond it by more than its
   coarse outline's uncertainty). Verified foreign regions are
   dropped when they are a separate part of the mask (nothing of the car is
   touched); glued to the outline, the cut line between car and object is not
   certain (the instance masks are coarse), so they are reported
   (``foreign_suspected``) and the job is rejected. Thin parts (antenna masts,
   mirror arms, wipers, roof rails, spoilers) are below the thickness limit.
   The other direction as well: what the zoomed vehicle instance claims beyond
   the BiRefNet mask – thick and large, in the upper part of the vehicle,
   confirmed by a zoom tile centred on it – is a vehicle part the mask lost
   (e.g. a dark side mirror in front of a dark car). It is reported
   (``parts_missing``); the pipeline may take it from another segmentation pass,
   otherwise the quality gate rejects the job – a listing image must never miss
   a mirror.
4. Fill enclosed holes in the UPPER part of the vehicle (windows/glass roofs
   that the model treated as see-through). Holes near the ground (the gap
   under the car between the wheels) are left open.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .segmentation import SegmentationError

if TYPE_CHECKING:  # pragma: no cover
    from .detection import Instance, VehicleDetector


@dataclass(frozen=True)
class BBox:
    x0: int
    y0: int
    x1: int  # exclusive
    y1: int  # exclusive

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def aspect(self) -> float:
        return self.width / max(self.height, 1)


@dataclass
class MaskInfo:
    components_total: int = 0
    components_kept: int = 0
    holes_filled: int = 0
    hole_area_filled: int = 0
    uncertain_fraction: float = 0.0
    coverage: float = 0.0
    #: Large parts separated from the main part (e.g. by a pole in front of the car).
    split_parts: int = 0
    #: Fragments (components >= 0.5) of the segmentation model's own alpha – a
    #: fragmented model output means a busy background (``busy_background``).
    model_fragments: int = 0
    #: Small blobs inside the vehicle's box dropped because they are not close to it.
    detached_dropped: int = 0
    #: Foreign objects cut from the outline (photo px boxes [x0, y0, x1, y1]).
    foreign_removed: list[list[int]] = field(default_factory=list)
    #: Foreign objects detected but NOT cut because the cut line was not certain.
    foreign_suspected: list[list[int]] = field(default_factory=list)
    #: Vehicle parts the detector sees but the mask lacks (photo px boxes [x0, y0, x1, y1]).
    parts_missing: list[list[int]] = field(default_factory=list)
    #: Their (core, claim) regions (bool, semantic working resolution, ``semantic_scale`` working
    #: px per photo px; core: the claim without the instance mask's coarse band) – where the
    #: pipeline may take the part from another segmentation pass.
    part_regions: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list, repr=False)
    semantic_scale: float = 1.0
    #: The semantic check found the vehicle (None: no detector / no photo given).
    vehicle_detected: bool | None = None
    #: Semantic check details (metadata): vehicle instance, tiles, verified regions.
    semantic: dict = field(default_factory=dict)


@dataclass(frozen=True)
class QualityWarning:
    code: str
    message: str


#: Enclosed holes lower than this (relative to the vehicle height) stay open.
MIN_FILL_HOLE_HEIGHT = 0.08
#: A small blob inside the vehicle's box is a part of it (mirror head, antenna
#: piece the model lost the thin link of) only within this gap (× vehicle width)
#: of the vehicle or of another kept part.
MAX_PART_GAP = 0.03

# Foreign spikes (see ``find_foreign_spikes``). Shape analysis at ~1024 px.
SPIKE_WORK_PX = 1024
#: Opening radius × vehicle width: everything thinner than 6 % of the width that
#: sticks out of the body is a protrusion (antennas, mirror arms, cones, posts …).
SPIKE_OPEN_RATIO = 0.03
#: A protrusion must reach this far beyond the opened body (× opening radius).
SPIKE_MIN_REACH = 1.5
#: … rise this far above the point where it stands on the body (× body height) …
SPIKE_MIN_RISE = 0.06
#: … be this slender (rise / width) – shark fins, mirrors, wings, boxes are not …
SPIKE_MIN_SLENDER = 1.6
#: … be thicker than an antenna rod (× vehicle width) …
SPIKE_MIN_THICKNESS = 0.012
#: … stand on the body (contact in the lowest 25 % of the protrusion) …
SPIKE_BASE_ATTACH = 0.75
#: … with the car directly beneath most of its width (within this × its rise below
#: its lowest pixel; a mirror head hangs BESIDE the body with background below) …
SPIKE_SUPPORT_DEPTH = 0.25
SPIKE_MIN_SUPPORT = 0.6
#: … and end clearly below the roofline (× body height): roof parts (antennas,
#: rails, racks, boxes, light bars, snorkels) reach the roof level and are kept.
SPIKE_ROOF_ZONE = 0.08
#: A roof-level protrusion is NOT exempt as a roof part when it is large: taller
#: than this (× body height) – or wider than ROOF_LARGE_WIDTH (× vehicle width) and
#: taller than ROOF_LARGE_MIN_HEIGHT (rails and spoilers are flat) – and thicker
#: than an antenna rod. It is then a semantic candidate (kept only when the
#: detector's vehicle instance covers it, e.g. a roof box; a sign on the wall
#: behind the roof is not).
ROOF_LARGE_HEIGHT = 0.10
ROOF_LARGE_WIDTH = 0.20
ROOF_LARGE_MIN_HEIGHT = 0.05
#: The car's outline must meet the spike within this depth (× spike base width)
#: below its contact – otherwise the cut is not certain and nothing is cut.
SPIKE_MAX_DEPTH = 2.0
#: Colour edge (CIE ΔE, Lab) that marks where the car's outline crosses a spike
#: edge when the outline runs along that edge (first edge from above).
SPIKE_EDGE_DE = 20.0
#: The cut below the spike's contact may not exceed this × the spike's own area.
SPIKE_MAX_STUB = 2.0


@dataclass
class ForeignSpike:
    """A protrusion found by ``find_protrusions`` (work-resolution geometry)."""

    mask: np.ndarray  # bool, work resolution
    contact_y: float  # work px: where it stands on the body
    scale: float  # work px per photo px
    #: "spike" (every shape rule holds: cut, or suspected when the cut is not certain)
    #: or "roof" (a large roof-level protrusion – only counts with the semantic check).
    kind: str = "spike"

    def box(self) -> list[int]:
        ys, xs = np.nonzero(self.mask)
        s = self.scale
        x1, y1 = int(np.ceil((xs.max() + 1) / s)), int(np.ceil((ys.max() + 1) / s))
        return [int(xs.min() / s), int(ys.min() / s), x1, y1]


@dataclass
class SpikeCut:
    """What ``cut_foreign_spike`` removes: ``region`` (bool, photo size) and, along
    the chord where the cut meets the car, ``edge`` – the share of each pixel to
    remove (signed-distance ramp, NaN outside the spike's corridor) for the rows
    and columns starting at ``edge_origin`` (y, x)."""

    region: np.ndarray
    edge: np.ndarray
    edge_origin: tuple[int, int]

    def box(self) -> list[int]:
        ys, xs = np.nonzero(self.region)
        return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _ellipse(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def find_foreign_spikes(binary: np.ndarray) -> list[ForeignSpike]:
    """Slender objects standing behind the car that stick out of its outline.

    The body is the morphological opening of the vehicle mask with a disk of
    ``SPIKE_OPEN_RATIO`` × vehicle width; everything else is a protrusion. A
    protrusion is a foreign spike only if ALL shape rules hold (see the
    ``SPIKE_*`` constants): it reaches clearly beyond the body, stands on it
    (contact at its bottom, the car directly beneath most of its width), rises
    above it, is slender, thicker than an antenna rod and ends clearly below the
    roofline. Mirrors hang beside the body and are rarely slender, antennas are
    rods or short fins, rails/racks/boxes/spoilers are wide or at roof level –
    they do not match.
    """
    return [p for p in find_protrusions(binary) if p.kind == "spike"]


def find_protrusions(binary: np.ndarray) -> list[ForeignSpike]:
    """Foreign spikes (``find_foreign_spikes``) plus the large protrusions at roof level
    ("roof", see ``ROOF_LARGE_*``) – a semantic candidate, not a cut."""
    height, width = binary.shape
    scale = min(1.0, SPIKE_WORK_PX / float(max(height, width)))
    small = binary.astype(np.uint8)
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        small = (cv2.resize(small * 255, size, interpolation=cv2.INTER_AREA) >= 128).astype(np.uint8)
    ys, xs = np.nonzero(small)
    if len(xs) == 0:
        return []
    vehicle_w = int(xs.max() - xs.min() + 1)
    radius = max(2, int(round(SPIKE_OPEN_RATIO * vehicle_w)))
    pad = radius + 2
    padded = cv2.copyMakeBorder(small, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    opened = cv2.morphologyEx(padded, cv2.MORPH_OPEN, _ellipse(radius))[pad:-pad, pad:-pad]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    if count <= 1:
        return []
    body = (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    body_ys = np.nonzero(body)[0]
    body_top, body_h = int(body_ys.min()), int(body_ys.max() - body_ys.min() + 1)
    protrusions = small & (1 - body)
    pcount, plabels, pstats, _ = cv2.connectedComponentsWithStats(protrusions, connectivity=8)
    if pcount <= 1:
        return []
    dist_body = cv2.distanceTransform(1 - body, cv2.DIST_L2, 5)
    thickness = cv2.distanceTransform(small, cv2.DIST_L2, 5)
    near_body = cv2.dilate(body, np.ones((3, 3), np.uint8)).astype(bool)
    found = []
    for label in range(1, pcount):
        x, y, w, h, _area = (int(v) for v in pstats[label])
        sel = plabels == label
        if float(dist_body[sel].max()) < SPIKE_MIN_REACH * radius:
            continue  # a bump or a shaved corner of the body
        contact = sel & near_body
        if not contact.any():
            continue
        contact_y = float(np.nonzero(contact)[0].mean())
        rise = contact_y - y
        rod = 2.0 * float(thickness[sel].max()) < SPIKE_MIN_THICKNESS * vehicle_w  # antenna rod
        roof = y < body_top + SPIKE_ROOF_ZONE * body_h  # reaches the roof level
        if roof:
            large = h >= ROOF_LARGE_HEIGHT * body_h or (
                w >= ROOF_LARGE_WIDTH * vehicle_w and h >= ROOF_LARGE_MIN_HEIGHT * body_h
            )
            if large and not rod:
                found.append(ForeignSpike(mask=sel, contact_y=contact_y, scale=scale, kind="roof"))
            continue  # otherwise a roof part (antenna, rails, rack, fin, light bar): kept
        if (
            contact_y < y + SPIKE_BASE_ATTACH * h  # hangs on the body sideways (mirror) or from above
            or rise < SPIKE_MIN_RISE * body_h
            or rise < SPIKE_MIN_SLENDER * w
            or rod
            or _support(sel[y : y + h, x : x + w], small[y:, x : x + w], max(2, int(round(SPIKE_SUPPORT_DEPTH * rise))))
            < SPIKE_MIN_SUPPORT
        ):
            continue
        found.append(ForeignSpike(mask=sel, contact_y=contact_y, scale=scale))
    return found


def _support(part: np.ndarray, below: np.ndarray, depth: int) -> float:
    """Share of the part's columns with other vehicle pixels within ``depth``
    below the part's lowest pixel in that column (``below`` starts at the part's
    top row)."""
    rows = np.arange(part.shape[0])[:, None]
    lowest = np.where(part, rows, -1).max(axis=0)
    columns = np.nonzero(lowest >= 0)[0]
    if len(columns) == 0:
        return 0.0
    hits = 0
    for c in columns:
        start = int(lowest[c]) + 1
        if below[start : start + depth, c].any():
            hits += 1
    return hits / len(columns)


def _run_ends(row: np.ndarray, x: int) -> tuple[int, int]:
    """Ends (inclusive) of the run of True pixels that contains ``row[x]``."""
    left = np.nonzero(~row[: x + 1])[0]
    right = np.nonzero(~row[x:])[0]
    return (int(left[-1]) + 1 if len(left) else 0), (x + int(right[0]) - 1 if len(right) else len(row) - 1)


def _first_edge(
    lab: np.ndarray, line: tuple[float, float], inward: int, depth: int, y_from: int, y_to: int
) -> int | None:
    """First row (from above) where the colour inside the spike's wall changes by
    ``SPIKE_EDGE_DE`` – where the car's outline crosses that wall."""
    height, width = lab.shape[:2]
    slope, offset = line
    for y in range(max(y_from, 4), min(y_to, height - 4)):
        x = slope * y + offset
        x0, x1 = sorted((int(round(x + inward * 3)), int(round(x + inward * (3 + depth)))))
        x0, x1 = max(x0, 0), min(x1 + 1, width)
        if x1 - x0 < 2:
            continue
        above = lab[y - 4 : y - 1, x0:x1].reshape(-1, 3).mean(axis=0)
        below = lab[y + 1 : y + 4, x0:x1].reshape(-1, 3).mean(axis=0)
        if float(np.linalg.norm(above - below)) >= SPIKE_EDGE_DE:
            return y
    return None


#: Width (photo px) of the alpha ramp across the chord where a cut meets the car:
#: the new edge is anti-aliased by its signed distance to the chord (no row-wise
#: stair-steps on a sloped chord).
CUT_EDGE_PX = 1.5


def cut_foreign_spike(binary: np.ndarray, spike: ForeignSpike, rgb: np.ndarray | None) -> SpikeCut | None:
    """Photo-resolution cut of the spike from the vehicle – or None when the cut
    line is not certain (then nothing is cut).

    The spike's side edges are fitted on its free upper part and extended
    downwards. Where the car's outline meets them (the mask run leaves the edge
    line outwards; if the outline runs ALONG an edge, the first colour edge
    inside that wall below the other junction) are the two junctions; the cut
    follows the chord between them, so the visible base of the spike in front
    of the car's outline goes as well. Pixels below the chord stay vehicle; the
    chord itself gets a ``CUT_EDGE_PX`` signed-distance ramp.
    """
    height, width = binary.shape
    s = spike.scale
    bx0, by0, bx1, by1 = spike.box()
    bw = bx1 - bx0
    contact = int(round(spike.contact_y / s))
    # everything happens in a window around the spike (the full frame is 12 MP)
    wx0, wx1 = max(bx0 - 3 * bw - 8, 0), min(bx1 + 3 * bw + 8, width)
    wy0, wy1 = max(by0 - 8, 0), min(contact + int(3 * SPIKE_MAX_DEPTH * bw) + 8, height)
    win = binary[wy0:wy1, wx0:wx1]
    wh, ww = win.shape
    up = np.zeros((wh, ww), np.uint8)
    sx0, sy0 = int(np.floor(bx0 * s)), int(np.floor(by0 * s))
    sx1, sy1 = int(np.ceil(bx1 * s)), int(np.ceil(by1 * s))
    piece = spike.mask[sy0:sy1, sx0:sx1].astype(np.uint8)
    px0, py0 = int(round(sx0 / s)) - wx0, int(round(sy0 / s)) - wy0
    pw, ph = max(1, int(round((sx1 - sx0) / s))), max(1, int(round((sy1 - sy0) / s)))
    piece = cv2.resize(piece, (pw, ph), interpolation=cv2.INTER_NEAREST)
    qx0, qy0 = max(px0, 0), max(py0, 0)
    qx1, qy1 = min(px0 + pw, ww), min(py0 + ph, wh)
    up[qy0:qy1, qx0:qx1] = piece[qy0 - py0 : qy1 - py0, qx0 - px0 : qx1 - px0]
    spread = int(np.ceil(1.0 / s)) + 1
    above_contact = (np.arange(wh) < contact - wy0)[:, None]
    own = win & (cv2.dilate(up, _ellipse(spread)) > 0) & above_contact
    ys, _ = np.nonzero(own)
    if len(ys) == 0:
        return None
    top, contact_w = int(ys.min()), contact - wy0
    rows, lefts, rights = [], [], []
    for y in range(top + 1, int(top + 0.8 * (contact_w - top))):
        xs = np.nonzero(own[y])[0]
        if len(xs) == 0:
            continue
        x0, x1 = _run_ends(win[y], int(xs.mean()))
        rows.append(y)
        lefts.append(x0)
        rights.append(x1)
    if len(rows) < 5:
        return None
    left = tuple(np.polyfit(rows, lefts, 1))
    right = tuple(np.polyfit(rows, rights, 1))

    def at(line: tuple[float, float], y: float) -> float:
        return line[0] * y + line[1]

    base_w = max(at(right, contact_w) - at(left, contact_w), 4.0)
    tol = max(3, int(round(0.1 * base_w)))
    y_end = min(wh, contact_w + int(SPIKE_MAX_DEPTH * base_w))
    junction: dict[str, int | None] = {"left": None, "right": None}
    for y in range(top + 1, y_end):
        lx, rx = at(left, y), at(right, y)
        xc = int(round(0.5 * (lx + rx)))
        if not 0 <= xc < ww or not win[y, xc]:
            continue
        x0, x1 = _run_ends(win[y], xc)
        if junction["left"] is None and x0 < lx - tol:
            junction["left"] = y
        if junction["right"] is None and x1 > rx + tol:
            junction["right"] = y
        if junction["left"] is not None and junction["right"] is not None:
            break
    found = [side for side, y in junction.items() if y is not None]
    if not found:
        return None
    if len(found) == 1:
        if rgb is None:
            return None
        missing = "right" if found[0] == "left" else "left"
        lab = cv2.cvtColor(rgb[wy0:wy1, wx0:wx1].astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)
        depth = max(4, int(round(0.1 * base_w)))
        line, inward = (right, -1) if missing == "right" else (left, 1)
        junction[missing] = _first_edge(lab, line, inward, depth, int(junction[found[0]]), y_end)
        if junction[missing] is None:
            return None
    ya, yb = int(junction["left"]), int(junction["right"])
    xa, xb = at(left, ya), at(right, yb)
    y0 = max(top - 2, 0)
    y1 = min(max(ya, yb) + int(np.ceil(CUT_EDGE_PX)) + 2, wh)  # a few rows below the chord for its ramp
    yy, xx = np.mgrid[y0:y1, 0:ww].astype(np.float32)
    span = max(xb - xa, 1.0)
    t = np.clip((xx - xa) / span, 0.0, 1.0)
    chord = ya + t * (yb - ya)
    # signed distance to the chord (> 0 above it, on the spike's side): the sloped
    # segment between the junctions, horizontal continuations beside them
    slope = (yb - ya) / span
    on_segment = (xx >= xa) & (xx <= xb)
    distance = (chord - yy) * np.where(on_segment, 1.0 / np.sqrt(1.0 + slope * slope), 1.0)
    wall = 2
    corridor = (xx >= at(left, yy) - wall) & (xx <= at(right, yy) + wall)
    region = np.zeros_like(win)
    region[y0:y1] = win[y0:y1] & corridor & (distance > 0)
    region |= own
    if int(region[contact_w:].sum()) > SPIKE_MAX_STUB * max(int(own.sum()), 1):
        return None
    full = np.zeros_like(binary)
    full[wy0:wy1, wx0:wx1] = region
    edge = np.where(corridor, np.clip(0.5 + distance / CUT_EDGE_PX, 0.0, 1.0), np.nan).astype(np.float32)
    return SpikeCut(region=full, edge=edge, edge_origin=(wy0 + y0, wx0))


#: Thin thorns of the old outline left at the junctions of a cut (the tip of the
#: notch between car and spike) are opened away within this radius (photo px).
CUT_THORN_PX = 6


def _apply_cut(alpha: np.ndarray, cut: SpikeCut) -> np.ndarray:
    """Alpha only: the region and its own soft fringe become transparent, thin
    thorns left next to the cut are removed; where the cut meets the car (the
    chord) the alpha follows the anti-aliased signed-distance ramp."""
    region = cut.region
    ys, xs = np.nonzero(region)
    margin = 4 * CUT_THORN_PX
    y0, y1 = max(int(ys.min()) - margin, 0), min(int(ys.max()) + margin + 1, alpha.shape[0])
    x0, x1 = max(int(xs.min()) - margin, 0), min(int(xs.max()) + margin + 1, alpha.shape[1])
    local = alpha[y0:y1, x0:x1]
    removed = region[y0:y1, x0:x1]
    remaining = (local >= 0.5) & ~removed
    near_cut = cv2.dilate(removed.astype(np.uint8), _ellipse(2 * CUT_THORN_PX)).astype(bool)
    opened = cv2.morphologyEx(remaining.astype(np.uint8), cv2.MORPH_OPEN, _ellipse(CUT_THORN_PX)).astype(bool)
    thorns = remaining & ~opened & near_cut
    removed = removed | thorns
    remaining = remaining & ~removed
    fringe = cv2.dilate(removed.astype(np.uint8), _ellipse(4)).astype(bool) & ~cv2.dilate(
        remaining.astype(np.uint8), _ellipse(2)
    ).astype(bool)
    soft = cv2.GaussianBlur(removed.astype(np.float32), (0, 0), 1.0)
    # the chord: share to remove from the signed distance (no stair-steps, no blur below it)
    ramp = np.full(local.shape, np.nan, np.float32)
    ey, ex = cut.edge_origin[0] - y0, cut.edge_origin[1] - x0
    eh, ew = cut.edge.shape
    ty0, ty1, tx0, tx1 = max(ey, 0), min(ey + eh, ramp.shape[0]), max(ex, 0), min(ex + ew, ramp.shape[1])
    if ty1 > ty0 and tx1 > tx0:
        ramp[ty0:ty1, tx0:tx1] = cut.edge[ty0 - ey : ty1 - ey, tx0 - ex : tx1 - ex]
    known = np.isfinite(ramp)
    share = np.where(known, ramp, 1.0)
    # pixels the ramp keeps (partly): inside the corridor, not a thorn, not a part of
    # the spike itself below the chord
    chord_zone = known & (share < 1.0) & ~thorns & ~(region[y0:y1, x0:x1] & (share < 0.5))
    soft = np.where(chord_zone, share, soft)
    local = local * (1.0 - soft)
    local[(removed | fringe) & ~chord_zone] = 0.0
    out = alpha.copy()
    out[y0:y1, x0:x1] = local
    return out


#: Semantic candidates (step 3): BiRefNet pixels at least this far (× vehicle width)
#: from what the zoomed vehicle instance claims …
SEMANTIC_MARGIN = 0.004
#: … thicker than this (× vehicle width; antenna masts, mirror arms, wipers, roof
#: rails and the cowl are thinner – measured 0.6–0.8 % of the width where a car
#: behind claimed them, the glued wheels, hubcaps, car rears, table, sign 1.1–8 %) …
SEMANTIC_MIN_THICKNESS = 0.009
#: … and larger than this (× vehicle width²) …
SEMANTIC_MIN_AREA = 1.0e-4
#: … that another detected object claims (share of the region) – anywhere along the outline.
SEMANTIC_OTHER_SHARE = 0.5
#: Regions no detected object claims (e.g. a bollard: no COCO class) only count in the
#: upper part of the vehicle (centre above this share of the bbox height – lower down
#: they are ground, shadow, sills and tyres the instance masks are coarse at) and when
#: thicker and larger than these (× vehicle width, × vehicle width²).
SEMANTIC_UPPER_ZONE = 0.5
SEMANTIC_UNCLAIMED_MIN_THICKNESS = 0.01
SEMANTIC_UNCLAIMED_MIN_AREA = 2.0e-4
#: A candidate is foreign when the vehicle instance of the zoom tile centred on it
#: covers less than this share of it (glued objects 0.0–0.12, the car's own tail
#: light that a coarse tile had given to a scooter behind it 0.98) …
SEMANTIC_MAX_VEHICLE_COVER = 0.25
#: … and when it reaches beyond that vehicle instance by more than this many mask cells of
#: the tile – closer, the coarse instance outline cannot tell car from background (glued
#: objects 5.7–43 cells; the car's own bonnet edge in front of a snowy car, which that car's
#: instance claimed: 1.5 cells, vehicle cover 0.14).
SEMANTIC_MIN_REACH_CELLS = 3.0
#: At most this many candidates are verified (largest first, one detector run each).
SEMANTIC_MAX_VERIFY = 6
#: A verified region separate from the main body is dropped when it makes up at
#: least this share of each mask component it touches.
SEMANTIC_PART_SHARE = 0.5

#: Missing vehicle parts: what the zoomed vehicle instance claims beyond the mask, after an
#: opening of this many mask cells (the coarse band every instance mask has around the
#: outline) …
PART_OPEN_CELLS = 1.5
#: … thicker than this (× vehicle width; side mirrors the mask lost 3.8–4.4 %; the remaining
#: claims of the 57 evaluated masks: clipped edges such as an indicator lens or the gap under
#: a mirror ≤ 1.5 %, ground under the bumpers up to 4 % but in the lowest quarter, PART_ZONE) …
PART_MIN_THICKNESS = 0.025
#: … larger than this (× vehicle width²; lost mirrors 0.0020–0.0025) …
PART_MIN_AREA = 8.0e-4
#: … with its centre in the upper part of the vehicle (× bbox height from the top; lower down
#: the instance masks claim ground, shadow and the gap under the car) …
PART_ZONE = 0.75
#: … and covered by the vehicle instance of a zoom tile centred on it (lost mirrors 0.95–0.99).
PART_MIN_VEHICLE_COVER = 0.6
#: At most this many missing-part candidates are verified (largest first).
PART_MAX_VERIFY = 4


@dataclass
class SemanticFinding:
    """A region the semantic check verified (photo px)."""

    box: list[int]
    area: int
    evidence: str  # "other_object" | "unclaimed" | "roof_protrusion" | "missing_part"
    labels: list[str]
    other_share: float
    check: dict
    foreign: bool
    region: np.ndarray | None = None  # bool, semantic working resolution
    action: str = "kept"  # "removed" | "suspected" | "kept" | "missing"
    #: A vehicle part the mask lacks (evidence "missing_part", confirmed by the zoom tile).
    missing: bool = False
    #: The part as the vehicle instance claims it (bool, working resolution).
    claim: np.ndarray | None = None

    def as_dict(self) -> dict:
        return {
            "box": self.box,
            "area": self.area,
            "evidence": self.evidence,
            "labels": self.labels,
            "otherShare": round(self.other_share, 3),
            "verify": self.check,
            "foreign": self.foreign,
            "missing": self.missing,
            "action": self.action,
        }


def _region_box(region: np.ndarray, scale: float, shape: tuple[int, int]) -> list[int]:
    ys, xs = np.nonzero(region)
    h, w = shape
    return [
        int(xs.min() / scale),
        int(ys.min() / scale),
        min(int(np.ceil((xs.max() + 1) / scale)), w),
        min(int(np.ceil((ys.max() + 1) / scale)), h),
    ]


def _missing_part_candidates(
    sem, work: np.ndarray, vehicle_w: int, vehicle_h: int, top: int
) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """(region, claim, area) of what the zoomed vehicle instance claims beyond the mask
    ``work`` (working resolution): the coarse band removed (PART_OPEN_CELLS), thick and large
    enough, centred in the upper part of the vehicle (``top``/``vehicle_*``: photo px);
    ``claim`` is the region grown back over the band it lost. Largest first."""
    s = sem.scale
    claimed = sem.vehicle_px & ~work
    radius = max(1, int(round(PART_OPEN_CELLS * max(sem.cell, 1.0))))
    opened = cv2.morphologyEx(claimed.astype(np.uint8), cv2.MORPH_OPEN, _ellipse(radius))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(opened, connectivity=8)
    if count <= 1:
        return []
    dist = cv2.distanceTransform(opened, cv2.DIST_L2, 5)
    found = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA]) / (s * s)
        region = labels == label
        thick = 2.0 * float(dist[region].max()) / s
        centre = (float(centroids[label][1]) / s - top) / max(vehicle_h, 1)
        if thick < PART_MIN_THICKNESS * vehicle_w or area < PART_MIN_AREA * vehicle_w**2 or centre > PART_ZONE:
            continue
        claim = claimed & cv2.dilate(region.astype(np.uint8), _ellipse(radius + 1)).astype(bool)
        found.append((region, claim, int(area)))
    found.sort(key=lambda c: -c[2])
    return found


def semantic_findings(
    binary: np.ndarray,
    rgb: np.ndarray,
    detector: VehicleDetector,
    *,
    detections: list[Instance] | None = None,
    protrusions: Iterable[ForeignSpike] = (),
):
    """Step 3 of the module doc: (semantic map, verified findings) for the vehicle mask
    ``binary`` (bool, photo size). Candidates beyond SEMANTIC_MAX_VERIFY are listed but
    not verified (kept)."""
    from .detection import vehicle_semantics, verify_region

    sem = vehicle_semantics(rgb, binary, detector, full=detections)
    findings: list[SemanticFinding] = []
    if sem.vehicle is None or sem.vehicle_px is None:
        return sem, findings
    s, shape = sem.scale, sem.shape
    work = cv2.resize(binary.astype(np.uint8) * 255, (shape[1], shape[0]), interpolation=cv2.INTER_AREA) >= 128
    ys, xs = np.nonzero(binary)
    vw, vh = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    margin = max(1, int(round(SEMANTIC_MARGIN * vw * s)))
    free = work & ~cv2.dilate(sem.vehicle_px.astype(np.uint8), _ellipse(margin)).astype(bool)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(free.astype(np.uint8), connectivity=8)
    dist = cv2.distanceTransform(free.astype(np.uint8), cv2.DIST_L2, 5)
    upper = (ys.min() + SEMANTIC_UPPER_ZONE * vh) * s  # working px
    candidates: list[tuple[str, np.ndarray, float, int]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA]) / (s * s)
        region = labels == label
        thick = 2.0 * float(dist[region].max()) / s
        if thick < SEMANTIC_MIN_THICKNESS * vw or area < SEMANTIC_MIN_AREA * vw * vw:
            continue
        other = float(np.mean(sem.other_px[region]))
        if other >= SEMANTIC_OTHER_SHARE:
            candidates.append(("other_object", region, other, int(area)))
        elif (
            float(np.nonzero(region)[0].mean()) < upper
            and thick >= SEMANTIC_UNCLAIMED_MIN_THICKNESS * vw
            and area >= SEMANTIC_UNCLAIMED_MIN_AREA * vw * vw
        ):
            candidates.append(("unclaimed", region, other, int(area)))
    taken = np.zeros(shape, bool)
    for _, region, _, _ in candidates:
        taken |= region
    for spike in protrusions:
        if spike.kind != "roof":
            continue
        region = cv2.resize(spike.mask.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        region &= work
        if not region.any() or float(np.mean(taken[region])) >= 0.5:
            continue
        candidates.append(("roof_protrusion", region, float(np.mean(sem.other_px[region])), int(region.sum() / (s * s))))
    candidates.sort(key=lambda c: -c[3])
    for evidence, region, other, area in candidates[:SEMANTIC_MAX_VERIFY]:
        check = verify_region(rgb, detector, sem, region, max(vw, vh))
        foreign = (
            check.conclusive
            and check.vehicle_cover < SEMANTIC_MAX_VEHICLE_COVER
            and check.reach_cells >= SEMANTIC_MIN_REACH_CELLS
        )
        labels_found = sorted({sem.labels[i] for i in np.unique(sem.other_label[region]) if i >= 0} | set(check.labels))
        findings.append(
            SemanticFinding(
                box=_region_box(region, s, binary.shape),
                area=area,
                evidence=evidence,
                labels=labels_found,
                other_share=other,
                check=check.as_dict(),
                foreign=foreign,
                region=region,
            )
        )
    for evidence, region, other, area in candidates[SEMANTIC_MAX_VERIFY:]:
        findings.append(
            SemanticFinding(_region_box(region, s, binary.shape), area, evidence, [], other, {"skipped": True}, False)
        )
    # the other direction: vehicle parts the mask lost (a dark mirror in front of a dark car)
    for region, claim, area in _missing_part_candidates(sem, work, vw, vh, int(ys.min()))[:PART_MAX_VERIFY]:
        check = verify_region(rgb, detector, sem, region, max(vw, vh))
        missing = check.conclusive and check.vehicle_cover >= PART_MIN_VEHICLE_COVER
        findings.append(
            SemanticFinding(
                box=_region_box(region, s, binary.shape),
                area=area,
                evidence="missing_part",
                labels=[],
                other_share=float(np.mean(sem.other_px[region])),
                check=check.as_dict(),
                foreign=False,
                region=region,
                action="missing" if missing else "kept",
                missing=missing,
                claim=claim,
            )
        )
    return sem, findings


def _apply_semantic(
    keep_mask: np.ndarray,
    labels: np.ndarray,
    main: int,
    findings: list[SemanticFinding],
    scale: float,
    info: MaskInfo,
) -> np.ndarray:
    """Foreign findings: a separate part of the mask is dropped (the removed pixels are
    returned, photo size; nothing of the car is touched), a region glued to the main
    body is reported as suspected (its cut line is not certain)."""
    removed = np.zeros(keep_mask.shape, bool)
    for finding in findings:
        if not finding.foreign or finding.region is None:
            continue
        ys, xs = np.nonzero(finding.region)
        wx0, wy0, wx1, wy1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        x0, y0 = int(wx0 / scale), int(wy0 / scale)
        x1, y1 = min(int(np.ceil(wx1 / scale)), keep_mask.shape[1]), min(int(np.ceil(wy1 / scale)), keep_mask.shape[0])
        crop = finding.region[wy0:wy1, wx0:wx1].astype(np.uint8)
        region = cv2.resize(crop, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST).astype(bool)
        region &= keep_mask[y0:y1, x0:x1] > 0
        hit = labels[y0:y1, x0:x1][region]
        parts = [int(v) for v in np.unique(hit) if v > 0]
        detached = bool(parts) and main not in parts
        for part in parts if detached else ():
            share = np.count_nonzero(hit == part) / max(np.count_nonzero((labels == part) & (keep_mask > 0)), 1)
            detached = detached and share >= SEMANTIC_PART_SHARE
        if detached:
            for part in parts:
                part_px = labels == part
                removed |= part_px & (keep_mask > 0)
                keep_mask[part_px] = 0
            finding.action = "removed"
            info.foreign_removed.append(finding.box)
        else:
            finding.action = "suspected"
            info.foreign_suspected.append(finding.box)
    return removed


def clean_mask(
    alpha: np.ndarray,
    *,
    rgb: np.ndarray | None = None,
    model: np.ndarray | None = None,
    detector: VehicleDetector | None = None,
    detections: list[Instance] | None = None,
    upper_fill_ratio: float = 0.62,
) -> tuple[np.ndarray, MaskInfo]:
    """Keep the vehicle in ``alpha``. ``rgb`` (the photo) lets a foreign spike be
    cut where the car's outline runs along one of its edges; ``model`` (the
    segmentation model's own alpha) is where ``model_fragments`` are counted
    (default: ``alpha``). With ``detector`` (and ``rgb``) the semantic check runs
    (step 3; ``detections``: the detector's full-frame instances, if already known)."""
    info = MaskInfo()
    binary = (alpha >= 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    info.components_total = max(count - 1, 0)
    if count <= 1:
        raise SegmentationError("no vehicle found in the photo")
    if model is None:
        info.model_fragments = info.components_total
    else:
        info.model_fragments = cv2.connectedComponents((model >= 0.5).astype(np.uint8), connectivity=8)[0] - 1

    areas = stats[1:, cv2.CC_STAT_AREA]
    main = int(np.argmax(areas)) + 1
    main_area = int(stats[main, cv2.CC_STAT_AREA])
    mx, my, mw, mh = (int(v) for v in stats[main, :4])
    pad_x, pad_y = int(0.06 * mw), int(0.06 * mh)
    ex0, ey0, ex1, ey1 = mx - pad_x, my - pad_y, mx + mw + pad_x, my + mh + pad_y

    keep = [main]
    parts, outside = [], []  # small blobs: kept only when close to the vehicle
    for label in range(1, count):
        if label == main:
            continue
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 0.002 * main_area:
            continue
        x, y, w, h = (int(v) for v in stats[label, :4])
        ix = max(0, min(x + w, ex1) - max(x, ex0))
        iy = max(0, min(y + h, ey1) - max(y, ey0))
        # Parts (mirror heads, antenna pieces whose thin link the model lost) lie
        # inside the vehicle's box or – a mirror sticking out sideways – next to it.
        if ix * iy >= 0.8 * w * h:
            if area >= 0.1 * main_area:
                keep.append(label)
            else:
                parts.append(label)
            continue
        if area < 0.1 * main_area:
            outside.append(label)
            continue
        if area >= 0.1 * main_area:
            # A big part right next to the main part at the same height: the car
            # was cut by something in front of it (pole, sign, person). Keep both
            # so nothing of the car is lost – and warn, the gap is not invented.
            gap = max(x - (mx + mw), mx - (x + w), 0)
            overlap = max(0, min(y + h, my + mh) - max(y, my))
            if gap <= 0.1 * mw and overlap >= 0.6 * min(h, mh):
                keep.append(label)
            info.split_parts += 1
    max_gap = max(4.0, MAX_PART_GAP * mw)
    if outside:  # outside the box only right next to the main part (mirror head)
        dist = cv2.distanceTransform((labels != main).astype(np.uint8), cv2.DIST_L2, 3)
        close = [label for label in outside if float(dist[labels == label].min()) <= max_gap]
        keep.extend(close)
        info.detached_dropped += len(outside) - len(close)
    while parts:  # inside the box chained: an antenna piece may hang on another kept piece
        dist = cv2.distanceTransform((~np.isin(labels, keep)).astype(np.uint8), cv2.DIST_L2, 3)
        close = [label for label in parts if float(dist[labels == label].min()) <= max_gap]
        if not close:
            break
        keep.extend(close)
        parts = [label for label in parts if label not in close]
    info.detached_dropped += len(parts)
    info.components_kept = len(keep)
    keep_mask = np.isin(labels, keep).astype(np.uint8)

    # Foreign spikes standing behind the car (cone, bollard, post).
    cuts: list[SpikeCut] = []
    protrusions = find_protrusions(keep_mask.astype(bool))
    for spike in protrusions:
        if spike.kind != "spike":
            continue
        cut = cut_foreign_spike(keep_mask.astype(bool), spike, rgb)
        if cut is None:
            info.foreign_suspected.append(spike.box())
            continue
        info.foreign_removed.append(cut.box())
        keep_mask[cut.region] = 0
        cuts.append(cut)

    # Semantic check: what the detector does not see as part of THE vehicle.
    semantic_removed = None
    if detector is not None and rgb is not None:
        sem, findings = semantic_findings(
            keep_mask.astype(bool), rgb, detector, detections=detections, protrusions=protrusions
        )
        info.vehicle_detected = sem.vehicle is not None
        semantic_removed = _apply_semantic(keep_mask, labels, main, findings, sem.scale, info)
        info.semantic_scale = sem.scale
        for finding in findings:
            if finding.missing and finding.region is not None and finding.claim is not None:
                info.parts_missing.append(finding.box)
                info.part_regions.append((finding.region, finding.claim))
        info.semantic = {
            "detector": getattr(detector, "name", type(detector).__name__),
            **sem.as_dict(),
            "findings": [f.as_dict() for f in findings],
        }

    # Enclosed holes (background components not touching the border).
    ys, xs = np.nonzero(keep_mask)
    top, bottom = int(ys.min()), int(ys.max())
    vehicle_area = int(keep_mask.sum())
    inverse = (1 - keep_mask).astype(np.uint8)
    hcount, hlabels, hstats, hcent = cv2.connectedComponentsWithStats(inverse, connectivity=4)
    filled = np.zeros_like(keep_mask)
    height, width = keep_mask.shape
    limit_y = top + upper_fill_ratio * (bottom - top)
    for label in range(1, hcount):
        x, y, w, h, area = (int(v) for v in hstats[label])
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            continue  # connected to the outside → real background
        if area > 0.35 * vehicle_area:
            continue
        if hcent[label][1] > limit_y:
            continue  # near the ground: gap under the car, keep it open
        if h < MIN_FILL_HOLE_HEIGHT * (bottom - top) or area < 0.25 * w * h:
            # thin gaps (under roof rails, spoilers, roof boxes) show real
            # background – only window-sized holes are glass the model missed
            continue
        filled[hlabels == label] = 1
        info.holes_filled += 1
        info.hole_area_filled += area

    gate = cv2.dilate(keep_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    result = alpha * gate.astype(np.float32)
    result = np.maximum(result, filled.astype(np.float32))
    for cut in cuts:
        result = _apply_cut(result, cut)
    if semantic_removed is not None and semantic_removed.any():
        # a dropped part and its own soft fringe go, the body's fringe stays
        fringe = cv2.dilate(semantic_removed.astype(np.uint8), _ellipse(4)).astype(bool) & ~cv2.dilate(
            keep_mask, _ellipse(2)
        ).astype(bool)
        result[semantic_removed | fringe] = 0.0
    vehicle = result >= 0.5
    total = int(vehicle.sum())
    info.coverage = total / float(result.size)
    soft = (result > 0.15) & (result < 0.85)
    info.uncertain_fraction = float(soft.sum()) / max(total, 1)
    return result.astype(np.float32), info


def mask_bbox(alpha: np.ndarray, threshold: float = 0.5) -> BBox:
    ys, xs = np.nonzero(alpha >= threshold)
    if len(xs) == 0:
        raise SegmentationError("empty vehicle mask")
    return BBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


SIDE_LABELS = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}

#: More fragments (components >= 0.5) in the segmentation model's OWN alpha mean
#: a busy background. Counted on the model output, not on the refined alpha: the
#: former count on the grey-guided refinement measured that filter's speckle
#: (25 was calibrated on it). BiRefNet returns 1–2 fragments on every one of the
#: 22 cached real photos, so with it this warning practically never fires; it
#: is kept as a guard for a fragmented model output (e.g. another segmenter).
BUSY_BACKGROUND_FRAGMENTS = 25


def assess_mask(alpha: np.ndarray, bbox: BBox, info: MaskInfo) -> list[QualityWarning]:
    height, width = alpha.shape
    warnings: list[QualityWarning] = []
    margin_x, margin_y = max(2, int(0.004 * width)), max(2, int(0.004 * height))
    sides = []
    if bbox.x0 <= margin_x:
        sides.append("left")
    if bbox.x1 >= width - margin_x:
        sides.append("right")
    if bbox.y0 <= margin_y:
        sides.append("top")
    if bbox.y1 >= height - margin_y:
        sides.append("bottom")
    if sides:
        warnings.append(
            QualityWarning(
                "vehicle_cropped",
                "Das Fahrzeug ist im Originalfoto angeschnitten ("
                + ", ".join(SIDE_LABELS[s] for s in sides)
                + "). Bitte das ganze Fahrzeug fotografieren.",
            )
        )
    if info.coverage < 0.02:
        warnings.append(
            QualityWarning("vehicle_small", "Das Fahrzeug ist im Foto sehr klein – bitte näher herangehen.")
        )
    if info.uncertain_fraction > 0.12:
        warnings.append(
            QualityWarning("mask_uncertain", "Die Freistellung ist unsicher – bitte das Ergebnis prüfen.")
        )
    if info.split_parts:
        warnings.append(
            QualityWarning(
                "vehicle_occluded",
                "Das Fahrzeug ist im Foto teilweise verdeckt oder unterbrochen (z. B. durch einen Pfosten) – "
                "bitte das Ergebnis prüfen und ggf. ohne Hindernis neu fotografieren.",
            )
        )
    if info.model_fragments > BUSY_BACKGROUND_FRAGMENTS:
        warnings.append(
            QualityWarning(
                "busy_background",
                "Unruhiger Hintergrund erkannt – bitte Kanten des Fahrzeugs prüfen.",
            )
        )
    if info.foreign_removed:
        warnings.append(
            QualityWarning(
                "background_object_removed",
                "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die "
                "Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
            )
        )
    if info.foreign_suspected:
        warnings.append(
            QualityWarning(
                "background_object_suspected",
                "Ein anderes Objekt (z. B. ein Fahrzeug, Schild oder Pfosten im Hintergrund) berührt die "
                "Fahrzeugkontur und konnte nicht sicher entfernt werden – bitte so fotografieren, dass hinter dem "
                "Fahrzeug freier Hintergrund ist.",
            )
        )
    if info.vehicle_detected is False:
        warnings.append(
            QualityWarning(
                "vehicle_not_detected",
                "Die Fahrzeugerkennung hat das Fahrzeug nicht sicher erkannt – fremde Objekte an der "
                "Fahrzeugkontur wurden nicht geprüft. Bitte das Ergebnis prüfen.",
            )
        )
    return warnings
