"""Vehicle size and position on the showroom plate (pure geometry, no pixels).

Placement v2 follows the plate's camera: the vehicle is scaled UNIFORMLY
(aspect ratio preserved, never distorted) so that its bbox width is the
plate's ``vehicle.targetWidthRatio`` of the frame, centred horizontally, and
its lowest tyre contact stands on the plate's ground line (the v of the proxy
car's lowest tyre contact – where a car stands in that view).

Limits can only make it smaller: maximum height, side/top margins (never
cropped), the branding headroom (the roof stays below the projected logo and
texts minus a clearance) and the floor (the highest tyre contact stays on the
floor, below the wall junction). If they shrink the vehicle below
``placement.minTargetFraction`` of the target it is still placed, and the
pipeline adds a warning. Enlarging is checked afterwards by the quality gate.
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
    #: Which rule determined the scale: width | height | margin | headroom | floor.
    limited_by: str
    #: Plate target (vehicle bbox width / frame width) and the ratio actually reached.
    target_width_ratio: float = 0.0
    #: Output row of the ground line (lowest tyre contact).
    ground_y: float = 0.0
    #: Scale that the target width alone would need.
    target_scale: float = 0.0

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2.0

    @property
    def achieved_width_ratio(self) -> float:
        return self.width / max(self.output_width, 1)

    @property
    def target_fraction(self) -> float:
        """Reached width / target width (1.0 = target reached)."""
        return self.scale / self.target_scale if self.target_scale > 0 else 1.0

    def to_output(self, bbox: BBox, x: float, y: float) -> tuple[float, float]:
        """Source pixel coordinates → output pixel coordinates."""
        return self.left + (x - bbox.x0) * self.scale, self.top + (y - bbox.y0) * self.scale

    def as_dict(self) -> dict:
        return {
            "scale": round(self.scale, 4),
            "targetScale": round(self.target_scale, 4),
            "left": round(self.left, 1),
            "top": round(self.top, 1),
            "width": round(self.width, 1),
            "height": round(self.height, 1),
            "limitedBy": self.limited_by,
            "targetWidthRatio": round(self.target_width_ratio, 4),
            "achievedWidthRatio": round(self.achieved_width_ratio, 4),
            "targetFraction": round(self.target_fraction, 4),
            "groundY": round(self.ground_y, 1),
        }


def output_size(bbox: BBox, output: OutputConfig, target_width_ratio: float) -> tuple[int, int]:
    """Pick the output resolution from the source detail.

    The ideal output is the one where the vehicle keeps its native pixel size
    (scale 1.0 – no invented detail, no wasted resolution), clamped to
    [long_edge_min, long_edge_max]. width:height = output.aspect.
    """
    aw, ah = output.aspect
    wanted = bbox.width / max(target_width_ratio, 1e-3)
    long_edge = int(round(min(max(wanted, output.long_edge_min), output.long_edge_max)))
    if aw >= ah:
        width = long_edge
        height = int(round(width * ah / aw))
    else:
        height = long_edge
        width = int(round(height * aw / ah))
    # even dimensions are friendlier for JPEG chroma subsampling / video tools
    return width - width % 2, height - height % 2


#: Highest tyre contact stays at least this far (× image height) below the wall junction.
FLOOR_CLEARANCE = 0.02
#: The vehicle needs at least this much height (× image height) – less means the
#: preset/plate is misconfigured (branding or horizon too low), not a small car.
MIN_ROOM = 0.2


class PlacementError(ValueError):
    """The preset/plate leaves no sensible room for the vehicle."""


def compute_placement(
    bbox: BBox,
    width: int,
    height: int,
    placement: Placement,
    *,
    target_width_ratio: float,
    ground_v: float,
    ground_y_src: float | None = None,
    roof_y_src: float | None = None,
    contact_rise: float = 0.0,
    floor_y: float | None = None,
    min_top: float | None = None,
) -> PlacementResult:
    """Scale and position the vehicle bbox on the plate.

    `ground_v`: the plate's ground line (fraction of the height).
    `ground_y_src`: source row (bottom edge) of the lowest tyre contact (default: bbox bottom).
    `roof_y_src`: source row of the solid roof (default: bbox top) – a thin antenna above
    it may reach into the branding headroom, but never out of the frame.
    `contact_rise`: source px between the lowest and the highest tyre contact.
    `floor_y`: lowest output row of the wall/floor junction under the vehicle.
    `min_top`: highest allowed vehicle top (output px), e.g. below the branding.
    """
    if bbox.width <= 0 or bbox.height <= 0:
        raise ValueError("empty bounding box")
    contact = float(bbox.y1 if ground_y_src is None else ground_y_src)
    contact = min(max(contact, bbox.y0 + 1.0), float(bbox.y1))
    above = contact - bbox.y0  # source px from the bbox top to the ground contact
    roof = float(bbox.y0 if roof_y_src is None else min(max(roof_y_src, bbox.y0), contact - 1.0))
    body = contact - roof  # source px from the solid roof to the ground contact
    below = bbox.y1 - contact  # source px hanging below the contact (usually 0)
    margin_x = placement.min_margin * width
    margin_y = placement.min_margin * height
    ground_y = ground_v * height

    target = target_width_ratio * width / bbox.width
    candidates = {
        "width": target,
        "height": placement.max_height_ratio * height / (body + below),
        "margin": min(
            (width - 2 * margin_x) / bbox.width,
            max(ground_y - margin_y, 1.0) / above,
            (max(height - margin_y - ground_y, 0.0) / below) if below > 0 else math.inf,
        ),
    }
    if min_top is not None:
        room = ground_y - min_top
        if room < MIN_ROOM * height:
            raise PlacementError(f"only {room / height:.0%} of the height between branding and ground line")
        candidates["headroom"] = room / body
    if floor_y is not None and contact_rise > 0:
        room = ground_y - (floor_y + FLOOR_CLEARANCE * height)
        if room <= 0:
            raise PlacementError("ground line is not below the wall/floor junction")
        candidates["floor"] = room / contact_rise
    limited_by = min(candidates, key=lambda key: candidates[key])
    scale = candidates[limited_by]
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid scale")
    if scale >= target * (1 - 1e-9):
        limited_by, scale = "width", target

    scaled_w = bbox.width * scale
    scaled_h = bbox.height * scale
    left = 0.5 * width - scaled_w / 2.0
    left = min(max(left, margin_x), width - margin_x - scaled_w)
    top = ground_y - above * scale
    return PlacementResult(
        scale=scale,
        left=left,
        top=top,
        width=scaled_w,
        height=scaled_h,
        output_width=width,
        output_height=height,
        limited_by=limited_by,
        target_width_ratio=target_width_ratio,
        ground_y=ground_y,
        target_scale=target,
    )
