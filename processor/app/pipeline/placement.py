"""STEP 6 – consistent vehicle size and position (pure geometry, no pixels).

The vehicle is scaled UNIFORMLY (aspect ratio preserved, never distorted),
centred horizontally, and its lowest pixel (tyre contact) is put on the
preset's ground line. The scale is the smallest of: target width, maximum
height, frame margins (never crop), maximum up-scaling and – for 3/4 views
whose far wheels touch the floor higher up – the showroom floor horizon (every
tyre contact must stay on the floor, never in front of the wall) and the
branding headroom (the vehicle's roof stays below the logo/texts on the wall).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..presets import OutputConfig, Placement
from .mask import BBox


@dataclass(frozen=True)
class PlacementResult:
    #: Uniform scale factor applied to the source vehicle.
    scale: float
    #: Top-left corner of the scaled vehicle bbox in the output frame.
    left: float
    top: float
    #: Size of the scaled vehicle bbox.
    width: float
    height: float
    output_width: int
    output_height: int
    #: Which rule determined the scale: width | height | margin | upscale | horizon | headroom.
    limited_by: str

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2.0


def output_size(bbox: BBox, output: OutputConfig, placement: Placement) -> tuple[int, int]:
    """Pick the output resolution from the source detail.

    The ideal output is the one where the vehicle keeps its native pixel size
    (scale 1.0 – no invented detail, no wasted resolution), clamped to
    [long_edge_min, long_edge_max]. width:height = output.aspect.
    """
    aw, ah = output.aspect
    wanted = bbox.width / max(placement.width_ratio, 1e-3)
    long_edge = int(round(min(max(wanted, output.long_edge_min), output.long_edge_max)))
    if aw >= ah:
        width = long_edge
        height = int(round(width * ah / aw))
    else:
        height = long_edge
        width = int(round(height * aw / ah))
    # even dimensions are friendlier for JPEG chroma subsampling / video tools
    return width - width % 2, height - height % 2


#: Highest tyre contact stays at least this far (× image height) below the horizon.
HORIZON_CLEARANCE = 0.02
#: The vehicle needs at least this much height (× image height) – less means the
#: preset is misconfigured (branding or horizon too low), not a small car.
MIN_ROOM = 0.2


class PlacementError(ValueError):
    """The preset leaves no sensible room for the vehicle."""


def compute_placement(
    bbox: BBox,
    width: int,
    height: int,
    placement: Placement,
    *,
    floor_horizon: float | None = None,
    contact_rise: float = 0.0,
    min_top: float | None = None,
) -> PlacementResult:
    """`contact_rise`: source pixels between the lowest and the highest tyre contact.
    `min_top`: highest allowed vehicle top (fraction of the height), e.g. below the branding."""
    if bbox.width <= 0 or bbox.height <= 0:
        raise ValueError("empty bounding box")
    margin_x = placement.min_margin * width
    margin_y = placement.min_margin * height
    ground_y = placement.ground_line * height

    candidates = {
        "width": placement.width_ratio * width / bbox.width,
        "height": placement.max_height_ratio * height / bbox.height,
        "margin": min(
            (width - 2 * margin_x) / bbox.width,
            max(ground_y - margin_y, 1.0) / bbox.height,
        ),
        "upscale": placement.max_upscale,
    }
    if min_top is not None:
        room = ground_y - min_top * height
        if room < MIN_ROOM * height:
            raise PlacementError(f"only {room / height:.0%} of the height between branding and ground line")
        candidates["headroom"] = room / bbox.height
    if floor_horizon is not None:
        room = ground_y - (floor_horizon + HORIZON_CLEARANCE) * height
        if room <= 0:
            raise PlacementError("ground line is not below the floor horizon")
        if contact_rise > 0:
            candidates["horizon"] = room / contact_rise
    limited_by = min(candidates, key=lambda key: candidates[key])
    scale = candidates[limited_by]
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid scale")

    scaled_w = bbox.width * scale
    scaled_h = bbox.height * scale
    left = placement.center_x * width - scaled_w / 2.0
    left = min(max(left, margin_x), width - margin_x - scaled_w)
    top = ground_y - scaled_h
    if top < margin_y:  # cannot happen with the margin rule, kept as a guard
        top = margin_y
    return PlacementResult(
        scale=scale,
        left=left,
        top=top,
        width=scaled_w,
        height=scaled_h,
        output_width=width,
        output_height=height,
        limited_by=limited_by,
    )
