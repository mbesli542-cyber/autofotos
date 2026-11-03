"""Where the car stands in the room: plate spot, optional depth offset and width scale.

Default (``placement.depthOffsetM`` 0, ``placement.widthScale`` ≤ 1): exactly the plate's
spot – one placement, no search (the shipped look).

Preview variants (``depthOffsetM`` > 0 – the car further back in the room – and/or
``widthScale`` > 1 – the car larger on its spot) are fitted PER PHOTO so that the result
stays physically plausible. A configured value is the maximum; it is reduced (offset
first, then the width scale) until

- the floor rule (the far tyres stay in front of the wall line, placement.py) binds no
  harder than it does on the plate's own spot – otherwise the car would only be shrunk
  on the same spot (a toy car) instead of standing further back, and
- the car's fitted footprint (the pipeline's own ground model: pose from the tyre
  contacts, overhangs from the silhouette – the real car's length, not the plate's proxy)
  keeps ``WALL_CLEARANCE_M`` to the brand wall, or – where the plate's spot is already
  closer – gets no closer than there.

Nothing here touches pixels; the check uses a cheap bilinear placement of the alpha.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from ..presets import BackgroundProvider, Placement, Preset, ShowroomPlate
from ..showroom.plates import WALL_CLEARANCE_M, Plate, move_proxy_back
from .grounding import build_ground_model, ground_contacts, ground_line_without_contacts
from .mask import BBox
from .placement import PlacementError, PlacementResult, compute_placement, output_size
from .vehicle_geometry import VehicleGeometry

#: A depth/scale variant may lose at most this much of its target width to the floor rule
#: beyond what the plate's own spot loses.
FLOOR_TOLERANCE = 0.005
#: Bisection steps per parameter (offset 2 m → 3 cm, scale 0.2 → 0.3 %).
SEARCH_STEPS = 6


@dataclass(frozen=True)
class RoomPlacement:
    plate: Plate
    showroom: ShowroomPlate
    placement: PlacementResult
    width: int
    height: int
    target_width_ratio: float
    #: Applied depth offset (m) and width scale.
    depth_offset_m: float
    width_scale: float
    #: Search details for the metadata ({} on the default path).
    info: dict = field(default_factory=dict)


def _place(
    plate: Plate,
    plate_key: str,
    offset: float,
    scale: float,
    *,
    preset: Preset,
    placement_cfg: Placement,
    backgrounds: BackgroundProvider,
    bbox: BBox,
    geometry: VehicleGeometry,
) -> RoomPlacement:
    moved, applied = move_proxy_back(plate, offset)
    target_width_ratio = moved.target_width_ratio * scale
    width, height = output_size(bbox, preset.output, target_width_ratio)
    showroom = backgrounds.get(preset, plate_key, width, height)
    if moved is not showroom.plate:
        showroom = replace(showroom, plate=moved)
    # the roof stays below every branding element on the wall (minus the clearance)
    boxes = showroom.branding.boxes
    min_top = max(b[4] for b in boxes) + preset.branding.clearance * height if boxes else None
    x0 = int(np.clip(round(0.5 * width - 0.5 * target_width_ratio * width), 0, width - 1))
    x1 = int(np.clip(round(0.5 * width + 0.5 * target_width_ratio * width), x0 + 1, width))
    # lowest tyre contact onto the proxy's lowest tyre contact; without visible tyre
    # contacts (front/rear: the bumper hides them) lowest point onto the proxy's lowest point
    ground_v = moved.ground_v if geometry.contacts else ground_line_without_contacts(moved)
    placement = compute_placement(
        bbox,
        width,
        height,
        placement_cfg,
        target_width_ratio=target_width_ratio,
        ground_v=min(ground_v, 0.97),
        ground_y_src=geometry.ground_y,
        roof_y_src=geometry.solid_top,
        contact_rise=geometry.contact_rise,
        floor_y=float(showroom.floor_top[x0:x1].max()),
        min_top=min_top,
    )
    return RoomPlacement(moved, showroom, placement, width, height, target_width_ratio, applied, scale)


def footprint_wall_y(candidate: RoomPlacement, alpha: np.ndarray, bbox: BBox, geometry: VehicleGeometry) -> float:
    """Largest floor y (m; wall at y = 0, the room at y < 0) of the car's fitted footprint."""
    p = candidate.placement
    s = p.scale
    matrix = np.array([[s, 0.0, p.left - bbox.x0 * s], [0.0, s, p.top - bbox.y0 * s]], np.float64)
    placed = cv2.warpAffine(
        np.ascontiguousarray(alpha, np.float32), matrix, (candidate.width, candidate.height), flags=cv2.INTER_LINEAR
    )
    contacts = ground_contacts(geometry.contacts, p, bbox)
    model = build_ground_model(placed, p, contacts, candidate.showroom)
    return max(float(y) for _, y in model.footprint.corners())


def place_in_room(
    plate: Plate,
    plate_key: str,
    *,
    preset: Preset,
    placement_cfg: Placement,
    backgrounds: BackgroundProvider,
    bbox: BBox,
    geometry: VehicleGeometry,
    alpha: np.ndarray,
) -> RoomPlacement:
    """The vehicle's placement on the plate (see the module doc)."""
    kw = dict(preset=preset, placement_cfg=placement_cfg, backgrounds=backgrounds, bbox=bbox, geometry=geometry)
    want_offset, want_scale = placement_cfg.depth_offset_m, placement_cfg.width_scale
    if want_offset <= 0.0 and want_scale <= 1.0:
        return _place(plate, plate_key, 0.0, want_scale, **kw)

    base_scale = min(want_scale, 1.0)
    base = _place(plate, plate_key, 0.0, base_scale, **kw)
    base_floor = base.placement.target_fraction if base.placement.limited_by == "floor" else 1.0
    base_wall = footprint_wall_y(base, alpha, bbox, geometry)
    wall_limit = max(-WALL_CLEARANCE_M, base_wall)
    reasons: set[str] = set()

    def evaluate(offset: float, scale: float) -> RoomPlacement | None:
        try:
            candidate = _place(plate, plate_key, offset, scale, **kw)
        except PlacementError:
            reasons.add("placement")
            return None
        p = candidate.placement
        if p.limited_by == "floor" and p.target_fraction < base_floor - FLOOR_TOLERANCE:
            reasons.add("floor")
            return None
        if footprint_wall_y(candidate, alpha, bbox, geometry) > wall_limit + 1e-3:
            reasons.add("wall")
            return None
        return candidate

    def search(make) -> RoomPlacement | None:
        """Largest t in (0, 1] whose candidate `make(t)` passes (bisection; t = 0 is the base)."""
        best = evaluate(*make(1.0))
        if best is not None:
            return best
        lo, hi = 0.0, 1.0
        for _ in range(SEARCH_STEPS):
            mid = 0.5 * (lo + hi)
            candidate = evaluate(*make(mid))
            if candidate is None:
                hi = mid
            else:
                lo, best = mid, candidate
        return best

    chosen = None
    if want_offset > 0.0:
        # the configured width scale, as far back as fits
        chosen = search(lambda t: (t * want_offset, want_scale))
    if chosen is None and want_scale > base_scale:
        # on the plate's spot, as large as fits
        chosen = search(lambda t: (0.0, base_scale + t * (want_scale - base_scale)))
    if chosen is None:
        chosen = base
    info = {
        "requestedDepthOffsetM": round(want_offset, 3),
        "requestedWidthScale": round(want_scale, 4),
        "depthOffsetM": round(chosen.depth_offset_m, 3),
        "widthScale": round(chosen.width_scale, 4),
        "reducedBy": sorted(reasons),
        "wallClearanceM": round(-footprint_wall_y(chosen, alpha, bbox, geometry), 3),
        "baseWallClearanceM": round(-base_wall, 3),
    }
    return replace(chosen, info=info)
