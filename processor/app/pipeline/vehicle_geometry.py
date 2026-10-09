"""Vehicle geometry from the alpha mask (pure measurements, no pixels changed).

From the lower outline of the vehicle (the lowest solid pixel per column):

- tyre contact points: support points of the outline's floor-side convex hull,
  grouped per tyre; a contact is plausible when the outline rises on both
  sides (a tyre bottom, not a straight sill or a flat shadow), it connects
  to the other contacts by a floor-like (not too steep) line, its "foot" is
  about one tyre wide (a bumper's lowest point is a wide, shallow curve – in
  3/4 views it often sits LOWER in the image than the near tyre) and, when
  the source photo is given, there is dark, neutral rubber directly above it
  (bumpers and sills are body-coloured). Two tyres are never closer than
  MIN_TYRE_GAP. Per contact: position, visible tyre width, confidence and
  side (near/far in 3/4 views);
- which end of the car is nearer to the camera (3/4 views): the near end has
  the lowest contact, the long side of the car (wheelbase) extends away from
  it and its outline stands lower in the image;
- the contact rise (how much higher the far contacts are than the near ones)
  relative to the car width, and the bbox aspect – compared with the plate's
  proxy car by the quality gate (camera far higher/lower than the plate's).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from .mask import BBox

#: A floor line between two tyre contacts is never steeper than this (dy/dx).
#: Steeper hull segments end at a bumper/sill corner, not at a tyre.
MAX_FLOOR_SLOPE = 0.3
#: Slope limit for extending the floor line beyond the outermost contacts.
MAX_EXTRAPOLATION_SLOPE = 0.12
#: Tyre contacts may sit this much (× vehicle height) above the lowest one –
#: far-side wheels in steep 3/4 views touch the floor higher in the image.
MAX_CONTACT_RISE = 0.35
#: Two plausible tyre contacts are never connected by a steeper line than this (dy/dx):
#: close/high 3/4 photos show the near rear tyre clearly higher than the near front one.
MAX_CONTACT_SLOPE = 1.0
#: Contact candidates may sit this much (× vehicle width) above the lowest one.
CANDIDATE_RISE = 0.5
#: Hull vertices closer than this (× vehicle width) belong to the same tyre.
CLUSTER_GAP = 0.03
#: Plausible contacts have at least this confidence.
PLAUSIBLE = 0.5
#: Outer zone (× vehicle width) used for the near-end signals.
END_ZONE = 0.2


# --------------------------------------------------------------------------- outline


def bottom_profile(alpha: np.ndarray, x0: int, x1: int) -> tuple[np.ndarray, np.ndarray]:
    """Lowest solid vehicle pixel (row) per column in [x0, x1)."""
    solid = alpha[:, x0:x1] >= 0.5
    has = solid.any(axis=0)
    flipped_first = np.argmax(solid[::-1, :], axis=0)
    bottom = (alpha.shape[0] - 1 - flipped_first).astype(np.float32)
    return bottom, has


def top_profile(alpha: np.ndarray, x0: int, x1: int) -> tuple[np.ndarray, np.ndarray]:
    """Highest solid vehicle pixel (row) per column in [x0, x1)."""
    solid = alpha[:, x0:x1] >= 0.5
    has = solid.any(axis=0)
    return np.argmax(solid, axis=0).astype(np.float32), has


def _floor_side_hull(xs: np.ndarray, ys: np.ndarray) -> list[tuple[float, float]]:
    """Convex hull chain on the floor side (max y in image coordinates)."""
    hull: list[tuple[float, float]] = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            # keep the chain "below" (larger y): remove points making a non-convex turn
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if cross >= 0:
                hull.pop()
            else:
                break
        hull.append((float(x), float(y)))
    return hull


def _steep(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(b[1] - a[1]) > MAX_FLOOR_SLOPE * max(abs(b[0] - a[0]), 1.0)


def floor_contacts(bottom: np.ndarray, has: np.ndarray, vehicle_height: float) -> list[tuple[float, float]]:
    """Floor-side support points (x, y) of the vehicle outline (hull chain)."""
    columns = np.flatnonzero(has)
    if len(columns) == 0:
        return []
    hull = _floor_side_hull(columns.astype(np.float32), bottom[columns])
    lowest = max(y for _, y in hull)
    contacts = [(x, y) for x, y in hull if y >= lowest - MAX_CONTACT_RISE * vehicle_height]
    # Bumper/sill corners can be hull vertices too: drop outer contacts that are
    # reached by a segment steeper than any floor line could be.
    while len(contacts) > 1 and _steep(contacts[0], contacts[1]):
        contacts.pop(0 if contacts[0][1] < contacts[1][1] else 1)
    while len(contacts) > 1 and _steep(contacts[-2], contacts[-1]):
        contacts.pop(-1 if contacts[-1][1] < contacts[-2][1] else -2)
    return contacts


def contact_rise(bottom: np.ndarray, has: np.ndarray, vehicle_height: float) -> float:
    """How far (px) a floor contact may sit above the lowest one (upper estimate).

    Uses the tyre contacts of the outline hull and, because far-side wheels of
    photos taken from above are hidden inside the outline, also the lowest
    point within the outer 15 % of the vehicle width on each side. Bumper
    overhangs make this an upper estimate.
    """
    columns = np.flatnonzero(has)
    if len(columns) < 2:
        return 0.0
    lowest = float(bottom[columns].max())
    contacts = floor_contacts(bottom, has, vehicle_height)
    hull_rise = lowest - min(y for _, y in contacts) if contacts else 0.0
    zone = max(1, int(0.15 * len(columns)))
    left = _outer_tyre_contact(bottom[columns[:zone]], from_end=False, window=max(2, int(0.03 * len(columns))))
    right = _outer_tyre_contact(bottom[columns[-zone:]], from_end=True, window=max(2, int(0.03 * len(columns))))
    return float(max(hull_rise, lowest - min(left, right)))


def _outer_tyre_contact(profile: np.ndarray, *, from_end: bool, window: int) -> float:
    """Lowest point of the outermost tyre in an outer zone of the bottom profile.

    A tyre shows up as a local maximum (lowest image point) of the bottom
    profile. The sill next to it may sit even lower in the image when the photo
    is taken from above, so the outermost local maximum is used, not the zone
    maximum (which is only the fallback when no local maximum exists).
    """
    n = len(profile)
    if n == 0:
        return 0.0
    padded = np.pad(profile.astype(np.float32), window, mode="edge")
    local_max = np.array([profile[i] >= padded[i : i + 2 * window + 1].max() for i in range(n)])
    # ignore plateaus touching the zone's inner border (that is the sill/body)
    inner = slice(window, n) if from_end else slice(0, n - window)
    candidates = np.flatnonzero(local_max)
    candidates = [i for i in candidates if (i >= window if from_end else i < n - window)]
    if not candidates:
        return float(profile[inner].max() if len(profile[inner]) else profile.max())
    index = candidates[-1] if from_end else candidates[0]
    return float(profile[index])


def floor_contact_line(bottom: np.ndarray, has: np.ndarray, vehicle_height: float) -> np.ndarray:
    """Floor y per column, through the tyre contact points (piecewise linear)."""
    contacts = floor_contacts(bottom, has, vehicle_height)
    if not contacts:
        return np.full_like(bottom, float(bottom.max()))
    xs = np.arange(len(bottom), dtype=np.float32)
    if len(contacts) == 1:
        return np.full_like(bottom, contacts[0][1])
    cx = np.array([c[0] for c in contacts], np.float32)
    cy = np.array([c[1] for c in contacts], np.float32)
    line = np.interp(xs, cx, cy)
    # gentle linear extrapolation beyond the outermost contacts (floor perspective)
    lim = MAX_EXTRAPOLATION_SLOPE
    left_slope = float(np.clip((cy[1] - cy[0]) / max(cx[1] - cx[0], 1.0), -lim, lim))
    right_slope = float(np.clip((cy[-1] - cy[-2]) / max(cx[-1] - cx[-2], 1.0), -lim, lim))
    line = np.where(xs < cx[0], cy[0] + (xs - cx[0]) * left_slope, line)
    line = np.where(xs > cx[-1], cy[-1] + (xs - cx[-1]) * right_slope, line)
    return line.astype(np.float32)


# --------------------------------------------------------------------------- tyre evidence

#: Height (× vehicle width) above a contact at which the outline's "foot" width is measured.
FOOT_HEIGHT = 0.01
#: Foot width (× vehicle width) at FOOT_HEIGHT: a tyre (round, or a flat tread in front/rear
#: views) stays narrow; the lowest point of a bumper or sill is a wide, shallow curve.
FOOT_TYRE = 0.12
FOOT_BODY = 0.17
#: Rubber directly above a tyre contact is dark and neutral (median sRGB luma / chroma, 0..1):
#: fully rubber-like up to the first value, not at all from the second one on.
RUBBER_LUMA = (0.25, 0.5)
RUBBER_CHROMA = (0.15, 0.3)
#: Lower bound of the colour factor: a body-coloured (bright or colourful) lowest point is
#: not a plausible tyre on its own, a dark one (also the body of a black car) stays possible.
COLOUR_FLOOR = 0.45
#: Foot heights (× vehicle width) used to measure the visible tyre width; the last one is
#: ≈ 30 % of a wheel radius, where a round tyre's chord is WIDTH_CHORD × its width.
WIDTH_LEVELS = (0.005, 0.01, 0.015, 0.02)
WIDTH_CHORD = 0.8
#: Tyre width limits (× vehicle width).
TYRE_WIDTH_RANGE = (0.03, 0.3)


def _foot(bottom: np.ndarray, has: np.ndarray, index: int, y: float, rise: float, limit: int) -> tuple[int, int]:
    """Column range around `index` whose outline stays within `rise` px of the contact row `y`."""
    n = len(bottom)
    lo_lim, hi_lim = max(0, index - limit), min(n - 1, index + limit)
    lo = hi = index
    while lo > lo_lim and has[lo - 1] and bottom[lo - 1] >= y - rise:
        lo -= 1
    while hi < hi_lim and has[hi + 1] and bottom[hi + 1] >= y - rise:
        hi += 1
    return lo, hi


def _tyre_width(bottom: np.ndarray, has: np.ndarray, index: int, y: float, bw: float) -> tuple[float, float]:
    """(visible tyre width px, foot width px at FOOT_HEIGHT) of the contact at column `index`.

    The foot of a round tyre widens like a circle chord above the contact: at WIDTH_HEIGHT
    (≈ 30 % of a wheel radius) the chord is ≈ 0.75 × the tyre width; flat treads (front/rear
    views) are a bit narrower than that estimate. When the foot merges with a sill/bumper
    lower down (a jump in width) the last level before is used with the circle model.
    """
    limit = int(round(0.2 * bw))
    last: tuple[float, float] | None = None
    foot = None
    merged = False
    for level in WIDTH_LEVELS:
        rise = max(1.5, level * bw)
        lo, hi = _foot(bottom, has, index, y, rise, limit)
        width = float(hi - lo + 1)
        if abs(level - FOOT_HEIGHT) < 1e-9:
            foot = width
        clipped = lo <= index - limit or hi >= index + limit
        # a circle chord grows with sqrt(height); clearly faster = merged with the body
        if last is not None and (clipped or width > 1.3 * np.sqrt(rise / last[0]) * last[1] + 0.01 * bw):
            merged = True
            break
        last = (rise, width)
        if clipped:
            merged = True
            break
    if foot is None:  # merged below FOOT_HEIGHT: as wide as the merged run
        lo, hi = _foot(bottom, has, index, y, max(1.5, FOOT_HEIGHT * bw), limit)
        foot = float(hi - lo + 1)
    assert last is not None
    rise, width = last
    if merged:
        estimate = max(width, min(width * width / (4.0 * rise) + rise, 1.6 * width))
    else:
        estimate = width / WIDTH_CHORD
    lo_w, hi_w = TYRE_WIDTH_RANGE
    return float(np.clip(estimate, lo_w * bw, hi_w * bw)), foot


def _rubber(rgb: np.ndarray, alpha: np.ndarray, x: int, y: int, bw: float) -> dict | None:
    """Rubber evidence (0..1) of the solid pixels in a small window directly above (x, y)."""
    r = max(2, int(round(0.012 * bw)))
    h0 = max(1, int(round(0.002 * bw)))
    h1 = max(h0 + 3, int(round(0.02 * bw)))
    rows = slice(max(0, y - h1), max(0, y - h0))
    cols = slice(max(0, x - r), min(rgb.shape[1], x + r + 1))
    inside = alpha[rows, cols] >= 0.9
    if int(inside.sum()) < 6:
        return None
    px = rgb[rows, cols][inside].astype(np.float32) / 255.0
    luma = float(np.median(0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]))
    chroma = float(np.median(px.max(axis=1) - px.min(axis=1)))
    (l0, l1), (c0, c1) = RUBBER_LUMA, RUBBER_CHROMA
    dark = float(np.clip((l1 - luma) / (l1 - l0), 0.0, 1.0))
    neutral = float(np.clip((c1 - chroma) / (c1 - c0), 0.0, 1.0))
    return {"rubber": dark * neutral, "luma": luma, "chroma": chroma}


# --------------------------------------------------------------------------- analysis


@dataclass(frozen=True)
class TyreContact:
    #: Source pixel column (centre) and the bottom edge of the lowest solid pixel.
    x: float
    y: float
    confidence: float
    #: Visible tyre width (source px) – 0 when unknown.
    width: float = 0.0
    #: "near" / "far" side of the car (3/4 views), None when not inferable.
    side: str | None = None
    #: Individual evidence factors (geometry, foot, colour) for metadata/debugging.
    evidence: dict = field(default_factory=dict, compare=False, hash=False)

    def as_list(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.confidence, 3)]

    def as_dict(self) -> dict:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "confidence": round(self.confidence, 3),
            "width": round(self.width, 1),
            "side": self.side,
            "evidence": {k: round(v, 3) if isinstance(v, float) else v for k, v in self.evidence.items()},
        }


@dataclass(frozen=True)
class VehicleGeometry:
    bbox: BBox
    #: Every contact candidate (one per tyre-like hull cluster), sorted by x.
    candidates: tuple[TyreContact, ...]
    #: The plausible ones (confidence >= PLAUSIBLE), sorted by x.
    contacts: tuple[TyreContact, ...]
    contact_confidence: float
    #: Image side ("left"/"right") of the car end nearer to the camera, None if unclear.
    near_end: str | None
    near_end_confidence: float
    near_end_signals: dict = field(default_factory=dict)
    #: px between the lowest and the highest plausible contact.
    contact_rise: float = 0.0
    #: Upper estimate from the outer zones (also sees hidden far wheels of photos from above).
    outer_rise: float = 0.0
    #: Source row of the solid roof (thin antennas/roof-rack posts above it are ignored).
    roof_y: float | None = None
    #: Whether the source colours were used for the tyre evidence.
    colour_evidence: bool = False

    @property
    def aspect(self) -> float:
        return self.bbox.width / max(self.bbox.height, 1)

    @property
    def contact_rise_ratio(self) -> float:
        return self.contact_rise / max(self.bbox.width, 1)

    @property
    def outer_rise_ratio(self) -> float:
        return self.outer_rise / max(self.bbox.width, 1)

    @property
    def lowest(self) -> TyreContact | None:
        return max(self.contacts, key=lambda c: c.y) if self.contacts else None

    @property
    def ground_y(self) -> float:
        """Source y (bottom edge) that goes onto the plate's ground line."""
        lowest = self.lowest
        return lowest.y if lowest is not None else float(self.bbox.y1)

    @property
    def solid_top(self) -> float:
        return float(self.bbox.y0) if self.roof_y is None else self.roof_y

    @property
    def contact_span(self) -> float:
        """Horizontal spread of the plausible contacts / vehicle width."""
        if len(self.contacts) < 2:
            return 0.0
        return (self.contacts[-1].x - self.contacts[0].x) / max(self.bbox.width, 1)

    def as_dict(self) -> dict:
        return {
            "bbox": [self.bbox.x0, self.bbox.y0, self.bbox.x1, self.bbox.y1],
            "aspect": round(self.aspect, 4),
            "contacts": [c.as_list() for c in self.contacts],
            "candidates": [c.as_list() for c in self.candidates],
            "contactDetails": [c.as_dict() for c in self.candidates],
            "colourEvidence": self.colour_evidence,
            "contactConfidence": round(self.contact_confidence, 3),
            "contactSpan": round(self.contact_span, 4),
            "contactRise": round(self.contact_rise, 1),
            "contactRiseRatio": round(self.contact_rise_ratio, 4),
            "outerRiseRatio": round(self.outer_rise_ratio, 4),
            "groundY": round(self.ground_y, 1),
            "roofY": round(self.solid_top, 1),
            "nearEnd": self.near_end,
            "nearEndConfidence": round(self.near_end_confidence, 3),
            "nearEndSignals": {k: round(v, 3) for k, v in self.near_end_signals.items()},
        }


def _clusters(hull: list[tuple[float, float]], gap: float) -> list[list[tuple[float, float]]]:
    groups: list[list[tuple[float, float]]] = []
    for point in hull:
        if groups and point[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(point)
        else:
            groups.append([point])
    return groups


def _side_rise(bottom: np.ndarray, has: np.ndarray, edge: int, y: float, window: int, direction: int) -> float | None:
    """How much higher (px) the outline is beside a contact patch.

    Median of the outline in [edge + window/3, edge + window] (direction +1) or
    the mirrored range, where `edge` is the end of the flat contact patch –
    tyres seen at an angle have a wide flat bottom (tread) before they curve
    up. None when the range leaves the vehicle (outline ends – counts as rising).
    """
    near, far = max(1, window // 3), window
    lo, hi = (edge + near, edge + far) if direction > 0 else (edge - far, edge - near)
    lo, hi = max(lo, 0), min(hi, len(bottom) - 1)
    if hi < lo:
        return None
    cols = np.arange(lo, hi + 1)
    cols = cols[has[cols]]
    if len(cols) == 0:
        return None
    return float(y - np.median(bottom[cols]))


def _contact_candidates(
    bottom: np.ndarray,
    has: np.ndarray,
    bbox: BBox,
    rgb: np.ndarray | None = None,
    alpha: np.ndarray | None = None,
) -> list[TyreContact]:
    columns = np.flatnonzero(has)
    if len(columns) < 3:
        return []
    bw, vh = float(bbox.width), float(bbox.height)
    gap = max(3.0, CLUSTER_GAP * bw)
    hull = _floor_side_hull(columns.astype(np.float32), bottom[columns])
    lowest = max(y for _, y in hull)
    # close 3/4 photos show the near rear tyre far above the near front one – allow as
    # much rise as the perspective gate does (relative to the width, not the height)
    hull = [(x, y) for x, y in hull if y >= lowest - max(MAX_CONTACT_RISE * vh, CANDIDATE_RISE * bw)]
    reps: list[tuple[float, float, int, int]] = []  # (x, y, patch start, patch end)
    search = int(max(3, round(0.05 * bw)))
    for group in _clusters(hull, gap):
        # the hull touches a tyre on a tilted floor line beside its lowest point:
        # take the lowest outline point near the cluster
        centre = int(round(float(np.mean([x for x, _ in group]))))
        lo, hi = max(centre - search, 0), min(centre + search + 1, len(bottom))
        local = np.where(has[lo:hi], bottom[lo:hi], -np.inf)
        index = lo + int(np.argmax(local))
        y_max = float(bottom[index])
        # the flat contact patch around it
        lo = hi = index
        while lo > 0 and has[lo - 1] and abs(bottom[lo - 1] - y_max) <= 1.0:
            lo -= 1
        while hi < len(bottom) - 1 and has[hi + 1] and abs(bottom[hi + 1] - y_max) <= 1.0:
            hi += 1
        x = (lo + hi) / 2.0
        if reps and abs(x - reps[-1][0]) < gap:  # two hull clusters found the same tyre
            if y_max > reps[-1][1]:
                reps[-1] = (x, y_max, lo, hi)
            continue
        reps.append((x, y_max, lo, hi))

    window = int(max(4, round(0.05 * bw)))
    band = max(1.0, 0.004 * bw)
    use_colour = rgb is not None and alpha is not None
    out = []
    for i, (x, y, _, _) in enumerate(reps):
        index = int(round(x))
        # near-contact run: the outline within `band` of the contact height (tyre bottom,
        # tread seen at an angle, contact shadow); a long run is a sill, skirt or ground shadow
        run_l = index
        while run_l > 0 and has[run_l - 1] and abs(bottom[run_l - 1] - y) <= 1.0 + band:
            run_l -= 1
        run_r = index
        while run_r < len(bottom) - 1 and has[run_r + 1] and abs(bottom[run_r + 1] - y) <= 1.0 + band:
            run_r += 1
        run = (run_r - run_l + 1) / bw
        flat = float(np.clip((0.45 - run) / 0.15, 0.0, 1.0))
        # beyond the run the outline must come up on both sides (a tyre, not a straight edge)
        rises = [_side_rise(bottom, has, run_l, y, window, -1), _side_rise(bottom, has, run_r, y, window, 1)]
        known = [r for r in rises if r is not None]
        bulge = 1.0 if not known else float(np.clip(min(known) / (2.0 * band), 0.0, 1.0))
        # connected to a neighbouring contact by a floor-like line?
        neighbours = [reps[j][:2] for j in (i - 1, i + 1) if 0 <= j < len(reps)]
        floor_like = not neighbours or any(
            abs(n[1] - y) <= MAX_CONTACT_SLOPE * max(abs(n[0] - x), 1.0) for n in neighbours
        )
        geometric = bulge * (1.0 if floor_like else 0.3) * flat
        # a tyre is about one wheel wide just above the floor; a bumper/sill bottom is wider
        width, foot = _tyre_width(bottom, has, index, y, bw)
        foot_factor = float(np.clip((FOOT_BODY - foot / bw) / (FOOT_BODY - FOOT_TYRE), 0.0, 1.0))
        evidence: dict = {"geometry": geometric, "foot": foot / bw, "footFactor": foot_factor}
        colour_factor = 1.0
        if use_colour:
            rubber = _rubber(rgb, alpha, index + bbox.x0, int(y), bw)  # type: ignore[arg-type]
            if rubber is not None:
                colour_factor = COLOUR_FLOOR + (1.0 - COLOUR_FLOOR) * rubber["rubber"]
                evidence.update(rubber)
            evidence["colourFactor"] = colour_factor
        confidence = geometric * foot_factor * colour_factor
        out.append(
            TyreContact(
                x=x + bbox.x0 + 0.5, y=y + 1.0, confidence=confidence, width=width, evidence=evidence
            )
        )
    return out


def _near_end(
    contacts: list[TyreContact], bottom: np.ndarray, top: np.ndarray, has: np.ndarray, bbox: BBox
) -> tuple[str | None, float, dict]:
    """Which image side holds the car end nearer to the camera (+ = left)."""
    bw = float(bbox.width)
    signals: dict[str, float] = {}
    # (a) contacts: the long side (wheelbase) extends AWAY from the lowest (nearest) contact
    if len(contacts) >= 2:
        ordered = sorted(contacts, key=lambda c: c.y, reverse=True)
        low = ordered[0]
        span_l = max((low.x - c.x for c in contacts if c.x < low.x), default=0.0)
        span_r = max((c.x - low.x for c in contacts if c.x > low.x), default=0.0)
        longest = max(span_l, span_r)
        if longest >= 0.15 * bw:
            # equally low contacts (side view) say nothing about the near end
            clear = low.y - ordered[1].y >= 0.01 * bw
            signals["contacts"] = float(np.clip((span_r - span_l) / (0.5 * longest), -1.0, 1.0)) if clear else 0.0
    columns = np.flatnonzero(has)
    zone = max(2, int(END_ZONE * len(columns)))
    if len(columns) >= 2 * zone:
        left, right = columns[:zone], columns[-zone:]
        # (b) the near end stands lower in the image
        drop = (float(np.mean(bottom[left])) - float(np.mean(bottom[right]))) / (0.05 * bw)
        signals["bottom"] = float(np.clip(drop, -1.0, 1.0))
        # (c) column extent – reported only: the near end's outer zone shows the low
        # front/rear face, the far end the cabin, so "taller" is body-shape dependent
        ext_l = float(np.mean(bottom[left] - top[left]))
        ext_r = float(np.mean(bottom[right] - top[right]))
        signals["extent"] = float(np.clip((ext_l - ext_r) / (0.1 * max(bbox.height, 1)), -1.0, 1.0))
    weights = {"contacts": 0.6, "bottom": 0.4, "extent": 0.0}
    total = sum(weights[k] for k in signals)
    if total <= 0:
        return None, 0.0, signals
    score = sum(weights[k] * v for k, v in signals.items()) / total
    signals["score"] = score
    if abs(score) < 0.2:
        return None, abs(score), signals
    return ("left" if score > 0 else "right"), abs(score), signals


def _with_sides(contacts: list[TyreContact], near_end: str | None) -> list[TyreContact]:
    """Near/far side per contact (3/4 views, where the near end is known).

    The lowest contact is the near-end tyre on the near side; contacts towards the far
    end belong to the near side too (wheelbase), contacts beyond it towards the near
    end's outer edge are the far tyre of the same axle.
    """
    if near_end is None or len(contacts) < 2:
        return contacts
    low = max(contacts, key=lambda c: c.y)
    towards_far = 1.0 if near_end == "left" else -1.0
    out = []
    for c in contacts:
        side = "near" if c is low or (c.x - low.x) * towards_far > 0 else "far"
        out.append(replace(c, side=side))
    return out


#: Two tyres are never closer than this in the image (× vehicle width): the far tyre of an
#: axle peeks out a track width beside the near one. Closer pairs are a tyre and a bumper corner.
MIN_TYRE_GAP = 0.08


def _separate(contacts: list[TyreContact], bw: float) -> list[TyreContact]:
    """Keep the stronger (on a tie: the wider) of two plausible contacts closer than MIN_TYRE_GAP."""
    out: list[TyreContact] = []
    for c in sorted(contacts, key=lambda c: c.x):
        if out and c.x - out[-1].x < MIN_TYRE_GAP * bw:
            prev = out[-1]
            better = c.confidence > prev.confidence + 0.1 or (
                abs(c.confidence - prev.confidence) <= 0.1 and c.width > prev.width
            )
            if better:
                out[-1] = c
            continue
        out.append(c)
    return out


def _harmonise_widths(contacts: list[TyreContact], bw: float) -> list[TyreContact]:
    """Tyres at the same image height (one axle in front/rear views, both tyres of a side
    view) are about equally wide – a foot that merged with a bumper/skirt is clamped."""
    out = []
    for c in contacts:
        level = [o.width for o in contacts if o is not c and abs(o.y - c.y) <= 0.02 * bw and o.width > 0]
        if level and c.width > 1.2 * min(level):
            c = replace(c, width=1.2 * min(level))
        out.append(c)
    return out


def solid_roof(alpha: np.ndarray, bbox: BBox, min_width: float = 0.04) -> float:
    """First row from the top where the vehicle is at least `min_width` × its width wide."""
    rows = (alpha[bbox.y0 : bbox.y1, bbox.x0 : bbox.x1] >= 0.5).sum(axis=1)
    wide = np.flatnonzero(rows >= max(3.0, min_width * bbox.width))
    return float(bbox.y0 + (wide[0] if len(wide) else 0))


def analyse_vehicle(alpha: np.ndarray, bbox: BBox, rgb: np.ndarray | None = None) -> VehicleGeometry:
    """Measure contacts, near end and perspective of the vehicle in `alpha`.

    `rgb` (optional, sRGB uint8, same size as `alpha`): the source photo – tyre contacts
    then also need dark, neutral rubber directly above them (bumpers and sills are
    body-coloured), which keeps the lowest bumper point from being taken as a tyre.
    """
    if rgb is not None and rgb.shape[:2] != alpha.shape:
        raise ValueError("rgb and alpha must have the same size")
    bottom, has = bottom_profile(alpha, bbox.x0, bbox.x1)
    top, _ = top_profile(alpha, bbox.x0, bbox.x1)
    candidates = _contact_candidates(bottom, has, bbox, rgb, alpha)
    plausible = _separate([c for c in candidates if c.confidence >= PLAUSIBLE], bbox.width)
    contacts = _harmonise_widths(plausible, bbox.width)
    best = sorted((c.confidence for c in contacts), reverse=True)[:2]
    confidence = float(np.mean(best)) if len(best) >= 2 else (best[0] * 0.5 if best else 0.0)
    near_end, near_conf, signals = _near_end(contacts, bottom, top, has, bbox)
    contacts = _with_sides(contacts, near_end)
    sides = {(c.x, c.y): c.side for c in contacts}
    candidates = [replace(c, side=sides.get((c.x, c.y))) for c in candidates]
    rise = (max(c.y for c in contacts) - min(c.y for c in contacts)) if len(contacts) >= 2 else 0.0
    return VehicleGeometry(
        bbox=bbox,
        candidates=tuple(candidates),
        contacts=tuple(contacts),
        contact_confidence=confidence,
        near_end=near_end,
        near_end_confidence=near_conf,
        near_end_signals=signals,
        contact_rise=float(rise),
        outer_rise=contact_rise(bottom, has, bbox.height),
        roof_y=solid_roof(alpha, bbox),
        colour_evidence=rgb is not None,
    )
