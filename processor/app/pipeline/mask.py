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
3. Fill enclosed holes in the UPPER part of the vehicle (windows/glass roofs
   that the model treated as see-through). Holes near the ground (the gap
   under the car between the wheels) are left open.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .segmentation import SegmentationError


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
    """A protrusion found by ``find_foreign_spikes`` (work-resolution geometry)."""

    mask: np.ndarray  # bool, work resolution
    contact_y: float  # work px: where it stands on the body
    scale: float  # work px per photo px

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
    spikes = []
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
        if (
            contact_y < y + SPIKE_BASE_ATTACH * h  # hangs on the body sideways (mirror) or from above
            or rise < SPIKE_MIN_RISE * body_h
            or rise < SPIKE_MIN_SLENDER * w
            or 2.0 * float(thickness[sel].max()) < SPIKE_MIN_THICKNESS * vehicle_w  # antenna rod
            or y < body_top + SPIKE_ROOF_ZONE * body_h  # reaches the roof level: roof part
            or _support(sel[y : y + h, x : x + w], small[y:, x : x + w], max(2, int(round(SPIKE_SUPPORT_DEPTH * rise))))
            < SPIKE_MIN_SUPPORT
        ):
            continue
        spikes.append(ForeignSpike(mask=sel, contact_y=contact_y, scale=scale))
    return spikes


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


def clean_mask(
    alpha: np.ndarray,
    *,
    rgb: np.ndarray | None = None,
    model: np.ndarray | None = None,
    upper_fill_ratio: float = 0.62,
) -> tuple[np.ndarray, MaskInfo]:
    """Keep the vehicle in ``alpha``. ``rgb`` (the photo) lets a foreign spike be
    cut where the car's outline runs along one of its edges; ``model`` (the
    segmentation model's own alpha) is where ``model_fragments`` are counted
    (default: ``alpha``)."""
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
    for spike in find_foreign_spikes(keep_mask.astype(bool)):
        cut = cut_foreign_spike(keep_mask.astype(bool), spike, rgb)
        if cut is None:
            info.foreign_suspected.append(spike.box())
            continue
        info.foreign_removed.append(cut.box())
        keep_mask[cut.region] = 0
        cuts.append(cut)

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
                "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel oder Pfosten) ragte in die Fahrzeugkontur "
                "und wurde entfernt – bitte das Ergebnis prüfen.",
            )
        )
    if info.foreign_suspected:
        warnings.append(
            QualityWarning(
                "background_object_suspected",
                "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel oder Pfosten) ragt in die Fahrzeugkontur und "
                "konnte nicht sicher entfernt werden – bitte ohne Hindernis neu fotografieren.",
            )
        )
    return warnings
