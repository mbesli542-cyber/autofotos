"""Grounding v3: the vehicle RESTS on the showroom floor of its plate.

:func:`build_ground_model` describes where the car stands on the plate's floor:

- the **car pose** – the plate's proxy car moved onto the detected tyre contacts
  in floor metres (rotation, translation, scale along/across the car axis;
  photo camera ≠ plate camera, so the two axes scale independently);
- the **footprint** – a rectangle in floor metres around the pose whose
  overhangs/sides are solved so that its projection covers the silhouette's
  extreme columns (overhang lengths from the photo, not from the proxy);
- the **floor line** per image column – the floor right below the car's lower
  outline, in the PHOTO's own perspective (:func:`floor_line_model`): the line
  through the contacts of same-side tyres between and beyond them (sills, side
  overhangs, the far-end overhang of a 3/4 view), the outline + a typical
  bumper clearance at the plate's local scale for faces (the end facing the
  camera in 3/4 views, front/rear views), and through every tyre's contact.
  The footprint projected through the plate's homography is drawn for
  debugging only: a photo taken from another camera position makes it miss
  the bumpers by tens of centimetres. The gap under the body, the reach of
  everything in front of the car and the reflection's mirror axis use the
  floor line;
- the **tyres** – per visible tyre its contact patch on the floor in physical
  metres (aligned with the pose: tread across the axle, rolling along the car;
  the pose's along/across stretch of the plate's floor metres is undone), its
  silhouette's x-extent at the contact and its radius.

:func:`apply_grounding` darkens the plate's floor in LINEAR light before the
vehicle is composited (nothing ever covers the car), multiplicatively, on the
floor only, never below ``shadow.minFloorLight``:

1. contact shadows – per tyre a near-black core on the contact patch whose
   falloff along the rolling direction follows the tyre/floor gap height, kept
   within the tyre's x-extent (+3 cm), plus a soft halo and a thin crease;
2. underbody – the floor seen between the lower outline and the floor line
   (deep under the car → floor line), then a soft falloff in front of it;
   rounded off beyond the silhouette's ends;
3. ambient – the plate's physically rendered proxy shadow (``<shot>-shadow.png``)
   resampled through the car pose, scaled by ``shadow.ambientOpacity`` and kept
   within ``shadow.ambientReach`` of the footprint and AMBIENT_FRONT_REACH of the
   floor line (no broad darkening);
4. front/rear views without visible tyres: soft occlusion pools at the hidden
   tyres inside the lower body's corners.

Deep shadows are darkened slightly less in blue (SHADOW_TINT), like the plates'
rendered proxy shadow – no saturated orange holes.

:func:`clean_ground_fringe` removes light ground/snow remnants of the cut-out at
the tyre bottoms and along the lower outline (they are not vehicle pixels; a
light fringe between rubber and contact shadow reads as a gap).

All deterministic image operations – no generative AI, no vehicle pixel changed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..presets import Preset, ShowroomPlate
from ..showroom.plates import Plate, ProxyContact
from .mask import BBox
from .placement import PlacementResult
from .shadow import (
    FloorFrame,
    Window,
    crease_line,
    floor_weight,
    polygon_rows,
    tyre_patch_shadow,
    underbody_profile,
)

#: Default floor-light minimum (the preset's shadow.minFloorLight overrides it).
MIN_FLOOR_LIGHT = 0.1

# ---- car pose (proxy → photo car, floor metres)
#: Scale limits (× the reference scale) along / across the car axis, max rotation.
POSE_ALONG_RANGE = (0.75, 1.6)
POSE_ACROSS_RANGE = (0.85, 1.2)
POSE_MAX_ROTATION = math.radians(30.0)
#: Matched contacts closer than this (proxy metres) along/across do not measure that scale.
SPREAD_MIN = 0.5

# ---- footprint (metres × reference scale)
#: Overhang beyond the axle, half width of the body.
OVERHANG_RANGE = (0.3, 1.5)
HALF_WIDTH_RANGE = (0.78, 1.1)
#: The silhouette's extreme columns are measured below this fraction of its height
#: (bumpers, not mirrors).
LOWER_BODY = 0.5
#: Corner rounding of the footprint (m) for the ambient distance.
FOOTPRINT_CORNER = 0.25
#: Floor line below a non-tyre lower outline: typical bumper/sill bottom height (m) – the
#: floor right below it lies this far below the outline in the picture (plate scale).
BODY_CLEARANCE = 0.15
#: Between two tyres of the same side – and beyond them, except at the end facing the
#: camera in 3/4 views – the floor line is the photo's own side line through their contacts,
#: between these clearances (m) below the outline.
MIN_CLEARANCE = 0.03
MAX_CLEARANCE = 1.0
#: A side line steeper than this (dy/dx) is a 3/4 view: its lower contact is the end facing
#: the camera, whose face recedes – there the outline + BODY_CLEARANCE is the floor line.
SIDE_VIEW_SLOPE = 0.12
#: The lower outline is smoothed over this fraction of the vehicle width for the floor line,
#: the underbody shadow sideways over this one.
OUTLINE_SMOOTH = 0.012
UNDERBODY_SMOOTH = 0.0015

# ---- tyres (physical sizes in metres, × the pose scale)
TREAD_HALF = 0.12
PATCH_HALF = 0.07
TYRE_RADIUS = 0.33
TYRE_RADIUS_RANGE = (0.28, 0.37)
TREAD_WIDTH = 0.23
#: Occlusion e-folding height of the tyre/floor gap, sidewall falloff across the axle (m).
GAP_SCALE = 0.01
SIDEWALL = 0.045
#: The hard core stays within the tyre silhouette's x-extent at the contact ± this (m).
CORE_MARGIN = 0.03
#: Height above the contact (m) where the tyre's x-extent is measured.
EXTENT_HEIGHT = 0.03
#: Soft occlusion halo around each tyre (opacity, radius m).
HALO_OPACITY = 0.3
HALO_RADIUS = 0.16
#: Crease thickness relative to the tyre width (min 1 px).
CREASE_THICKNESS = 0.012

# ---- front/rear views without visible tyres
#: Where the lowest outline point (the bumper lip) goes, between the proxy's tyre line (0)
#: and the proxy's lowest point (1, the proxy body's lower front/rear edge – cars sat ~5 %
#: low with 1).
NO_CONTACT_LIP = 0.5
#: Lip clearance (m): the floor line lies this far below the lip.
LIP_CLEARANCE = 0.14
#: Occlusion pools at the hidden tyres: opacity, radius (m), tyre centre inset from the lower
#: body's sides (× its width), distance (m) behind the face's floor line.
HIDDEN_POOL_OPACITY = 0.5
HIDDEN_POOL_RADIUS = 0.3
HIDDEN_TYRE_INSET = 0.11
HIDDEN_AXLE_OFFSET = 0.6

#: Relative darkening per channel (R, G, B), measured on the plates' rendered proxy shadow.
SHADOW_TINT = (1.0, 1.0, 0.95)

# ---- ambient
#: The (soft, calibrated) ambient occlusion reaches at most this far (m of floor depth)
#: in front of the floor line.
AMBIENT_FRONT_REACH = 0.3
#: Work window margin around the vehicle (× its width).
WINDOW_MARGIN = 0.35
#: Metadata: floor farther than this (m, plate scale) from the car and the floor seen under it
#: counts as "far" for farFloorDarkening.
FAR_FLOOR = 0.5


@dataclass(frozen=True)
class GroundContact:
    """A tyre contact of the PLACED vehicle (output px)."""

    x: float
    #: Bottom edge of the tyre = the floor contact line.
    y: float
    #: Visible tyre width (output px), 0 = unknown.
    width: float = 0.0
    confidence: float = 1.0
    side: str | None = None
    #: Hidden tyre placed from the car pose (front/rear views without visible tyres).
    inferred: bool = False

    def as_dict(self) -> dict:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "width": round(self.width, 1),
            "confidence": round(self.confidence, 3),
            "side": self.side,
            "inferred": self.inferred,
        }


def ground_contacts(contacts: Sequence, placement: PlacementResult, bbox: BBox) -> list[GroundContact]:
    """Source tyre contacts (vehicle_geometry.TyreContact) → contacts of the placed vehicle."""
    out = []
    for c in contacts:
        x, y = placement.to_output(bbox, c.x, c.y)
        out.append(
            GroundContact(
                x=x,
                y=y,
                width=float(getattr(c, "width", 0.0)) * placement.scale,
                confidence=float(getattr(c, "confidence", 1.0)),
                side=getattr(c, "side", None),
            )
        )
    return out


def _as_contact(value) -> GroundContact:
    if isinstance(value, GroundContact):
        return value
    x, y = value[0], value[1]
    return GroundContact(x=float(x), y=float(y))


def ground_line_without_contacts(plate: Plate) -> float:
    """Ground line v for the bbox bottom when no plausible tyre contact is visible."""
    tyre_v, lowest_v = plate.ground_v, max(plate.ground_v, plate.proxy_bbox[3])
    return tyre_v + NO_CONTACT_LIP * (lowest_v - tyre_v)


# --------------------------------------------------------------------------- car pose


def proxy_footprint(plate: Plate) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """(centre between the axles, unit axis rear→front, half (length, width)) of the proxy (m)."""
    c = plate.proxy_contacts
    front = np.mean([c["front_left"].floor, c["front_right"].floor], axis=0)
    rear = np.mean([c["rear_left"].floor, c["rear_right"].floor], axis=0)
    centre = (front + rear) / 2.0
    axis = front - rear
    axis = axis / max(float(np.linalg.norm(axis)), 1e-9)
    half = (0.5 * float(plate.proxy["lengthM"]), 0.5 * float(plate.proxy["widthM"]))
    return (float(centre[0]), float(centre[1])), (float(axis[0]), float(axis[1])), half


def _local(plate: Plate, p) -> tuple[float, float]:
    """Proxy floor point → (along, left) metres relative to the proxy's axle centre."""
    centre, axis, _ = proxy_footprint(plate)
    dx, dy = p[0] - centre[0], p[1] - centre[1]
    return dx * axis[0] + dy * axis[1], -dx * axis[1] + dy * axis[0]


@dataclass(frozen=True)
class CarPose:
    """Proxy car → photo car on the floor (metres).

    A proxy point at (along, left) of the proxy's axle centre lands on
    ``centre + along·scale_along·axis + left·scale_across·side`` (side = left of axis).
    """

    centre: tuple[float, float]
    #: Rotation of the car axis relative to the proxy's axis (rad).
    angle: float
    scale_along: float
    scale_across: float
    matched: int
    reference_scale: float
    proxy_centre: tuple[float, float]
    proxy_axis: tuple[float, float]
    #: Which limits the fit hit ("rotation", "along", "across").
    clamped: tuple[str, ...] = ()

    @property
    def scale(self) -> float:
        return math.sqrt(self.scale_along * self.scale_across)

    @property
    def axis(self) -> tuple[float, float]:
        a = math.atan2(self.proxy_axis[1], self.proxy_axis[0]) + self.angle
        return math.cos(a), math.sin(a)

    @property
    def side(self) -> tuple[float, float]:
        ux, uy = self.axis
        return -uy, ux

    def apply(self, px, py):
        """Proxy floor point(s) → photo-car floor point(s)."""
        px, py = np.asarray(px, np.float64), np.asarray(py, np.float64)
        ax0, ay0 = self.proxy_axis
        dx, dy = px - self.proxy_centre[0], py - self.proxy_centre[1]
        along = (dx * ax0 + dy * ay0) * self.scale_along
        left = (-dx * ay0 + dy * ax0) * self.scale_across
        (ux, uy), (vx, vy) = self.axis, self.side
        return self.centre[0] + along * ux + left * vx, self.centre[1] + along * uy + left * vy

    def invert(self, qx, qy):
        """Photo-car floor point(s) → proxy floor point(s)."""
        qx, qy = np.asarray(qx, np.float64), np.asarray(qy, np.float64)
        (ux, uy), (vx, vy) = self.axis, self.side
        dx, dy = qx - self.centre[0], qy - self.centre[1]
        along = (dx * ux + dy * uy) / self.scale_along
        left = (dx * vx + dy * vy) / self.scale_across
        ax0, ay0 = self.proxy_axis
        return self.proxy_centre[0] + along * ax0 - left * ay0, self.proxy_centre[1] + along * ay0 + left * ax0

    def local(self, qx, qy):
        """Photo-car floor point(s) → (along, left) metres from the car's axle centre."""
        (ux, uy), (vx, vy) = self.axis, self.side
        dx, dy = np.asarray(qx, np.float64) - self.centre[0], np.asarray(qy, np.float64) - self.centre[1]
        return dx * ux + dy * uy, dx * vx + dy * vy

    def as_dict(self) -> dict:
        return {
            "scale": round(self.scale, 4),
            "scaleAlong": round(self.scale_along, 4),
            "scaleAcross": round(self.scale_across, 4),
            "referenceScale": round(self.reference_scale, 4),
            "rotationDeg": round(math.degrees(self.angle), 2),
            "centreM": [round(self.centre[0], 3), round(self.centre[1], 3)],
            "matched": self.matched,
            "clamped": list(self.clamped),
        }


def _match(visible: list[ProxyContact], contacts: list[GroundContact]) -> list[tuple[ProxyContact, GroundContact]]:
    """Pair the plate proxy's visible contacts with the detected ones (image order)."""
    if not visible or not contacts:
        return []
    detected = sorted(contacts, key=lambda c: c.x)
    if len(detected) == len(visible):
        return list(zip(visible, detected))
    p_low = max(visible, key=lambda p: p.v)
    c_low = max(detected, key=lambda c: c.y)
    pairs = [(p_low, c_low)]
    for side in (-1, 1):
        p_side = [p for p in visible if (p.u - p_low.u) * side > 0]
        c_side = [c for c in detected if (c.x - c_low.x) * side > 0]
        if p_side and c_side:
            pairs.append(
                (max(p_side, key=lambda p: abs(p.u - p_low.u)), max(c_side, key=lambda c: abs(c.x - c_low.x)))
            )
    return pairs


def reference_scale(plate: Plate, placement: PlacementResult) -> float:
    """Placed vehicle width / the proxy's projected width (≈ physical size ratio)."""
    u0, _, u1, _ = plate.proxy_bbox
    return placement.width / max((u1 - u0) * placement.output_width, 1.0)


def _clamp(value: float, lo: float, hi: float, name: str, clamped: list[str]) -> float:
    if value < lo or value > hi:
        clamped.append(name)
    return float(min(max(value, lo), hi))


def fit_pose(
    plate: Plate,
    frame: FloorFrame,
    placement: PlacementResult,
    contacts: Sequence[GroundContact],
    anchor_px: tuple[float, float] | None = None,
) -> CarPose:
    """Fit the proxy car's floor pose to the detected tyre contacts.

    ≥ 2 matched contacts: rotation from a weighted similarity fit; the scale ALONG the car
    from contacts that spread along it (wheelbase), ACROSS from contacts that spread across
    (track) – the other one stays at the reference scale. Limits are reported (`clamped`).
    1: translation at the reference scale; none: the proxy's camera-facing end goes onto the
    floor point `anchor_px` (output px – the floor right below the bumper lip).
    """
    s0 = reference_scale(plate, placement)
    centre0, axis0, half0 = proxy_footprint(plate)
    pairs = _match(plate.visible_contacts(), [c for c in contacts if not c.inferred])
    clamped: list[str] = []
    base = dict(reference_scale=s0, proxy_centre=centre0, proxy_axis=axis0)
    if len(pairs) >= 2:
        loc = np.array([_local(plate, pc.floor) for pc, _ in pairs])
        q = np.array([[float(v) for v in frame.to_floor(c.x, c.y)] for _, c in pairs])
        w = np.array([max(c.confidence, 0.1) for _, c in pairs])
        p_c = loc[:, 0] + 1j * loc[:, 1]
        q_c = q[:, 0] + 1j * q[:, 1]
        mp, mq = np.sum(w * p_c) / w.sum(), np.sum(w * q_c) / w.sum()
        a = np.sum(w * np.conj(p_c - mp) * (q_c - mq)) / max(float(np.sum(w * np.abs(p_c - mp) ** 2)), 1e-9)
        world = float(np.angle(a))  # direction of the car's axis in the floor frame
        angle0 = math.atan2(axis0[1], axis0[0])
        rel = (world - angle0 + math.pi) % (2 * math.pi) - math.pi
        rel = _clamp(rel, -POSE_MAX_ROTATION, POSE_MAX_ROTATION, "rotation", clamped)
        ux, uy = math.cos(angle0 + rel), math.sin(angle0 + rel)
        vx, vy = -uy, ux
        da = loc[:, 0] - np.sum(w * loc[:, 0]) / w.sum()
        db = loc[:, 1] - np.sum(w * loc[:, 1]) / w.sum()
        dq = q - np.sum(w[:, None] * q, axis=0) / w.sum()
        qa, qb = dq[:, 0] * ux + dq[:, 1] * uy, dq[:, 0] * vx + dq[:, 1] * vy
        lo_a, hi_a = POSE_ALONG_RANGE
        lo_c, hi_c = POSE_ACROSS_RANGE
        along_spread = float(np.sqrt(np.sum(w * da * da) / w.sum()))
        across_spread = float(np.sqrt(np.sum(w * db * db) / w.sum()))
        sa = float(np.sum(w * da * qa) / np.sum(w * da * da)) if along_spread * 2 >= SPREAD_MIN else s0
        sc = float(np.sum(w * db * qb) / np.sum(w * db * db)) if across_spread * 2 >= SPREAD_MIN else s0
        sa = _clamp(sa, lo_a * s0, hi_a * s0, "along", clamped)
        sc = _clamp(sc, lo_c * s0, hi_c * s0, "across", clamped)
        # translation: weighted centroids coincide
        ma, mb = float(np.sum(w * loc[:, 0]) / w.sum()), float(np.sum(w * loc[:, 1]) / w.sum())
        mqx, mqy = float(mq.real), float(mq.imag)
        cx = mqx - (sa * ma * ux + sc * mb * vx)
        cy = mqy - (sa * ma * uy + sc * mb * vy)
        return CarPose((cx, cy), rel, sa, sc, len(pairs), clamped=tuple(clamped), **base)
    if len(pairs) == 1:
        pc, c = pairs[0]
        qx, qy = (float(v) for v in frame.to_floor(c.x, c.y))
        la, lb = _local(plate, pc.floor)
        cx = qx - s0 * (la * axis0[0] - lb * axis0[1])
        cy = qy - s0 * (la * axis0[1] + lb * axis0[0])
        return CarPose((cx, cy), 0.0, s0, s0, 1, **base)
    if anchor_px is None:
        anchor_px = (placement.center_x, placement.ground_y)
    qx, qy = (float(v) for v in frame.to_floor(*anchor_px))
    # the end (front/rear face) of the proxy that faces the camera
    def camera_distance(k: float) -> float:
        end = (centre0[0] + k * half0[0] * axis0[0], centre0[1] + k * half0[0] * axis0[1])
        return math.hypot(end[0] - frame.camera_xy[0], end[1] - frame.camera_xy[1])

    k = min((-1.0, 1.0), key=camera_distance)
    cx, cy = qx - s0 * k * half0[0] * axis0[0], qy - s0 * k * half0[0] * axis0[1]
    return CarPose((cx, cy), 0.0, s0, s0, 0, **base)


# --------------------------------------------------------------------------- footprint


@dataclass(frozen=True)
class Footprint:
    """The car's footprint on the floor: a rectangle around the pose's axle centre (m)."""

    centre: tuple[float, float]
    axis: tuple[float, float]
    front: float
    rear: float
    left: float
    right: float

    @property
    def side(self) -> tuple[float, float]:
        return -self.axis[1], self.axis[0]

    def point(self, along: float, left: float) -> tuple[float, float]:
        (ux, uy), (vx, vy) = self.axis, self.side
        return self.centre[0] + along * ux + left * vx, self.centre[1] + along * uy + left * vy

    def corners(self) -> list[tuple[float, float]]:
        """front-left, front-right, rear-right, rear-left (polygon order)."""
        return [
            self.point(self.front, self.left),
            self.point(self.front, -self.right),
            self.point(-self.rear, -self.right),
            self.point(-self.rear, self.left),
        ]

    def sdf(self, fx, fy, corner: float) -> np.ndarray:
        """Signed distance (m) to the rounded rectangle, negative inside."""
        (ux, uy), (vx, vy) = self.axis, self.side
        dx, dy = np.asarray(fx) - self.centre[0], np.asarray(fy) - self.centre[1]
        a = dx * ux + dy * uy - 0.5 * (self.front - self.rear)
        b = dx * vx + dy * vy - 0.5 * (self.left - self.right)
        ha, hb = 0.5 * (self.front + self.rear), 0.5 * (self.left + self.right)
        corner = min(corner, 0.95 * min(ha, hb))
        qa, qb = np.abs(a) - (ha - corner), np.abs(b) - (hb - corner)
        outside = np.hypot(np.maximum(qa, 0.0), np.maximum(qb, 0.0))
        inside = np.minimum(np.maximum(qa, qb), 0.0)
        return outside + inside - corner

    def as_dict(self) -> dict:
        return {
            "centreM": [round(self.centre[0], 3), round(self.centre[1], 3)],
            "axis": [round(self.axis[0], 4), round(self.axis[1], 4)],
            "frontM": round(self.front, 3),
            "rearM": round(self.rear, 3),
            "leftM": round(self.left, 3),
            "rightM": round(self.right, 3),
        }


def initial_footprint(plate: Plate, pose: CarPose) -> Footprint:
    half_wb = 0.5 * float(plate.proxy["wheelbaseM"]) * pose.scale_along
    overhang = (0.5 * float(plate.proxy["lengthM"]) - 0.5 * float(plate.proxy["wheelbaseM"])) * pose.reference_scale
    half_w = 0.5 * float(plate.proxy["widthM"]) * pose.scale_across
    return Footprint(pose.centre, pose.axis, half_wb + overhang, half_wb + overhang, half_w, half_w)


def lower_body_extent(alpha: np.ndarray, placement: PlacementResult) -> tuple[float, float] | None:
    """(left, right) pixel edges of the silhouette's lower part (bumpers/sills, no mirrors)."""
    height = alpha.shape[0]
    top = int(np.clip(math.floor(placement.top + LOWER_BODY * placement.height), 0, height - 1))
    cols = np.flatnonzero((alpha[top:] >= 0.5).any(axis=0))
    if len(cols) == 0:
        return None
    return float(cols[0]), float(cols[-1] + 1)


def solve_footprint(
    plate: Plate, frame: FloorFrame, pose: CarPose, extent: tuple[float, float] | None, fixed_end: str | None = None
) -> Footprint:
    """Overhangs/sides of the footprint so that its projection spans the silhouette's
    lower-body columns `extent` (per image side: the parameter that moves the extreme
    corner the most – an overhang in 3/4 and side views, a half width in front/rear views).
    `fixed_end` ("front"/"rear") keeps that end (anchored under a bumper lip)."""
    fp = initial_footprint(plate, pose)
    if extent is None:
        return fp
    s0 = pose.reference_scale
    half_wb = 0.5 * float(plate.proxy["wheelbaseM"]) * pose.scale_along
    ranges = {
        "front": (half_wb + OVERHANG_RANGE[0] * s0, half_wb + OVERHANG_RANGE[1] * s0),
        "rear": (half_wb + OVERHANG_RANGE[0] * s0, half_wb + OVERHANG_RANGE[1] * s0),
        "left": (HALF_WIDTH_RANGE[0] * s0, HALF_WIDTH_RANGE[1] * s0),
        "right": (HALF_WIDTH_RANGE[0] * s0, HALF_WIDTH_RANGE[1] * s0),
    }
    # corner index → (end parameter, side parameter)
    params = [("front", "left"), ("front", "right"), ("rear", "right"), ("rear", "left")]
    values = {k: getattr(fp, k) for k in ranges}

    def xs(vals: dict) -> np.ndarray:
        f = Footprint(fp.centre, fp.axis, **vals)
        return np.array([float(frame.to_px(*c)[0]) for c in f.corners()])

    for _ in range(8):
        for target, pick in ((extent[0], np.argmin), (extent[1], np.argmax)):
            current = xs(values)
            k = int(pick(current))
            best, slope = None, 0.0
            for name in params[k]:
                if name == fixed_end:
                    continue
                trial = dict(values)
                trial[name] += 0.01
                d = (xs(trial)[k] - current[k]) / 0.01
                if abs(d) > abs(slope):
                    best, slope = name, d
            if best is None or abs(slope) < 1e-3:
                continue
            lo, hi = ranges[best]
            values[best] = float(np.clip(values[best] + (target - current[k]) / slope, lo, hi))
    return Footprint(fp.centre, fp.axis, **values)


# --------------------------------------------------------------------------- outline / floor line


def lower_outline(alpha: np.ndarray, x0: int, x1: int) -> np.ndarray:
    """Bottom edge (row + 1 of the lowest pixel with alpha ≥ 0.5) per column, NaN without vehicle."""
    solid = alpha[:, x0:x1] >= 0.5
    has = solid.any(axis=0)
    last = alpha.shape[0] - np.argmax(solid[::-1, :], axis=0)
    return np.where(has, last.astype(np.float64), np.nan)


def _fill_nearest(values: np.ndarray) -> np.ndarray:
    """NaNs replaced by the nearest finite value (edge extension)."""
    finite = np.flatnonzero(np.isfinite(values))
    if len(finite) == 0:
        return values
    idx = np.arange(len(values))
    return np.interp(idx, finite, values[finite])


def _steepness(values: np.ndarray, sigma: float) -> np.ndarray:
    """sqrt(1 + slope²) of a per-column line (smoothed): vertical falloffs × this are
    perpendicular falloffs – a steep line (3/4 views) keeps a soft edge."""
    if not np.isfinite(values).any():
        return np.ones_like(values)
    smooth = cv2.GaussianBlur(values.astype(np.float64).reshape(1, -1), (0, 0), sigmaX=max(1.0, sigma)).ravel()
    slope = np.clip(np.gradient(smooth), -4.0, 4.0)
    return np.sqrt(1.0 + slope * slope)


# --------------------------------------------------------------------------- tyres


@dataclass(frozen=True)
class Tyre:
    contact: GroundContact
    #: Patch centre (floor m), rolling direction, half tread/patch (m), radius (m).
    centre: tuple[float, float]
    roll: tuple[float, float]
    half_tread: float
    half_patch: float
    radius: float
    #: Silhouette x-extent (output px) at EXTENT_HEIGHT above the contact.
    extent: tuple[float, float]
    #: px per metre (across) at the contact.
    px_per_m: float
    #: The pose's (along, across) scale of the plate's floor metres relative to physical sizes.
    stretch: tuple[float, float] = (1.0, 1.0)

    def as_dict(self) -> dict:
        return {
            **self.contact.as_dict(),
            "floorM": [round(self.centre[0], 3), round(self.centre[1], 3)],
            "halfTreadM": round(self.half_tread, 3),
            "halfPatchM": round(self.half_patch, 3),
            "radiusM": round(self.radius, 3),
            "extentPx": [round(self.extent[0], 1), round(self.extent[1], 1)],
        }


def tyre_extent(alpha: np.ndarray, x: float, row: float, fallback: float) -> tuple[float, float]:
    """x-extent (pixel edges) of the solid run around column `x` in `row`."""
    height, width = alpha.shape
    y = int(np.clip(round(row), 0, height - 1))
    xi = int(np.clip(round(x), 0, width - 1))
    line = alpha[y] >= 0.5
    if not line[xi]:
        near = np.flatnonzero(line[max(0, xi - int(fallback)) : xi + int(fallback) + 1])
        if len(near) == 0:
            return x - fallback, x + fallback
        xi = max(0, xi - int(fallback)) + int(near[np.argmin(np.abs(near + max(0, xi - int(fallback)) - xi))])
    lo = xi
    while lo > 0 and line[lo - 1]:
        lo -= 1
    hi = xi
    while hi < width - 1 and line[hi + 1]:
        hi += 1
    return float(lo), float(hi + 1)


def _tyre(frame: FloorFrame, pose: CarPose, alpha: np.ndarray, contact: GroundContact) -> Tyre:
    across_px, _ = frame.px_per_metre(contact.x, min(contact.y, frame.height - 1.0))
    p0 = tuple(float(v) for v in frame.to_floor(contact.x, contact.y))
    roll, side = pose.axis, pose.side
    half_tread = TREAD_HALF * pose.reference_scale
    half_patch = PATCH_HALF * pose.reference_scale
    # the visible lowest point is the patch edge nearest to the camera: the centre lies half a
    # tread (side views) / half a patch (front/rear) / both (3/4) further away from the camera
    vx, vy = p0[0] - frame.camera_xy[0], p0[1] - frame.camera_xy[1]
    norm = max(math.hypot(vx, vy), 1e-6)
    facing_side = (vx * side[0] + vy * side[1]) / norm  # signed cosines of the patch axes
    facing_roll = (vx * roll[0] + vy * roll[1]) / norm
    shift_s = half_tread * facing_side * pose.scale_across / max(pose.reference_scale, 1e-6)
    shift_r = half_patch * facing_roll * pose.scale_along / max(pose.reference_scale, 1e-6)
    centre = (p0[0] + shift_s * side[0] + shift_r * roll[0], p0[1] + shift_s * side[1] + shift_r * roll[1])
    extent = tyre_extent(alpha, contact.x, contact.y - max(2.0, EXTENT_HEIGHT * across_px) - 1.0,
                         max(4.0, 0.5 * contact.width, 0.1 * across_px))  # fmt: skip
    # radius from the visible width: width ≈ D·|cos φ| + tread·|sin φ| (φ: rolling vs image x)
    hx, hy = frame.horizontal_direction(contact.x, min(contact.y, frame.height - 1.0))
    cos_phi, sin_phi = abs(roll[0] * hx + roll[1] * hy), abs(side[0] * hx + side[1] * hy)
    radius = TYRE_RADIUS * pose.reference_scale
    visible_m = max(contact.width, extent[1] - extent[0]) / across_px
    if contact.width > 0 and cos_phi > 0.45:
        radius = 0.5 * (visible_m - TREAD_WIDTH * sin_phi) / cos_phi
    lo, hi = TYRE_RADIUS_RANGE
    radius = float(np.clip(radius, lo * pose.reference_scale, hi * pose.reference_scale))
    stretch = (pose.scale_along / pose.reference_scale, pose.scale_across / pose.reference_scale)
    return Tyre(contact, centre, roll, half_tread, half_patch, radius, extent, across_px, stretch=stretch)


def contact_sides(plate: Plate, visible: Sequence[GroundContact]) -> list[tuple[GroundContact, GroundContact]]:
    """Pairs of visible contacts on the SAME side of the car (wheelbase pairs), from the
    matching with the plate proxy's contacts (front_left ↔ rear_left …)."""
    pairs = _match(plate.visible_contacts(), [c for c in visible if not c.inferred])
    out = []
    for i, (pa, ca) in enumerate(pairs):
        for pb, cb in pairs[i + 1 :]:
            side_a, side_b = pa.label.split("_")[1], pb.label.split("_")[1]
            if side_a == side_b and pa.label != pb.label:
                out.append((ca, cb) if ca.x <= cb.x else (cb, ca))
    return out


def smooth_outline(edge: np.ndarray, vehicle_width: float) -> np.ndarray:
    """The lower outline, median-filtered (jagged matte, rounded tips) and lightly blurred."""
    smooth = _fill_nearest(edge)
    k = max(3, int(round(OUTLINE_SMOOTH * vehicle_width)) | 1)
    if len(smooth) >= k:
        padded = np.pad(smooth, k // 2, mode="edge")
        smooth = np.median(np.lib.stride_tricks.sliding_window_view(padded, k), axis=1)
        smooth = cv2.GaussianBlur(smooth.reshape(1, -1), (0, 0), sigmaX=max(1.0, k / 2.0)).ravel()
    return smooth


def floor_line_model(
    edge: np.ndarray,
    cols: np.ndarray,
    px_per_m: np.ndarray,
    tyres: Sequence[Tyre],
    sides: Sequence[tuple[GroundContact, GroundContact]],
    vehicle_width: float,
    clearance: float,
) -> np.ndarray:
    """Per column: image row of the floor right below the car's lower outline.

    The photo's own perspective decides (the plate's floor homography only gives the local
    scale – photo camera ≠ plate camera, a footprint projected through it can miss the
    bumpers by tens of centimetres):

    - between two tyres of the same side and beyond them along that side (sills, the
      overhangs of a side view, the far-end overhang of a 3/4 view): the straight side line
      through their contacts, `MIN_CLEARANCE`..`MAX_CLEARANCE` m below the outline;
    - faces (front/rear views, the end facing the camera in 3/4 views, everything without a
      side pair): the smoothed outline + `clearance` m at the plate's local scale;
    - over every visible tyre: through its contact (it stands on the floor), blended over
      half a tyre width. Never above the outline. NaN without vehicle.
    """
    has = np.isfinite(edge)
    if not has.any():
        return edge.copy()
    smooth_e = smooth_outline(edge, vehicle_width)
    smooth_e = np.maximum(smooth_e, np.where(has, edge, smooth_e) - 0.5 * clearance * px_per_m)
    line = smooth_e + clearance * px_per_m
    weight_total = np.zeros_like(line)
    target_sum = np.zeros_like(line)
    lo, hi = edge + MIN_CLEARANCE * px_per_m, edge + MAX_CLEARANCE * px_per_m
    for a, b in sides:
        slope = (b.y - a.y) / max(b.x - a.x, 1.0)
        side_line = a.y + slope * (cols - a.x)
        along = (cols >= a.x) & (cols <= b.x)
        facing = None  # the end facing the camera (3/4 views): its overhang is a receding face
        if abs(slope) > SIDE_VIEW_SLOPE:
            facing = a if a.y > b.y else b
        if facing is not a:
            along |= cols < a.x
        if facing is not b:
            along |= cols > b.x
        floor = np.clip(side_line, lo, hi)
        target_sum += np.where(along & has, floor, 0.0)
        weight_total += along & has
    for t in tyres:
        c = t.contact
        x_l, x_r = t.extent
        taper = max(4.0, 0.5 * (x_r - x_l))
        dist = np.maximum(np.maximum(x_l - cols, cols - x_r), 0.0)
        w = np.where(dist <= 0, 1.0, np.cos(np.clip(dist / taper, 0.0, 1.0) * math.pi / 2) ** 2)
        w = np.where(dist > taper, 0.0, w)
        target_sum += w * c.y * 3.0
        weight_total += w * 3.0
    blend = np.clip(weight_total, 0.0, 1.0)
    target = np.where(weight_total > 0, target_sum / np.maximum(weight_total, 1e-9), line)
    line = blend * target + (1.0 - blend) * line
    return np.where(has, np.maximum(line, edge), np.nan)


# --------------------------------------------------------------------------- model


@dataclass
class GroundModel:
    """Where the placed car stands on the plate's floor (output px / floor metres)."""

    frame: FloorFrame
    pose: CarPose
    footprint: Footprint
    window: Window
    #: Per window column (pixel centres): lower outline row and floor line row (NaN = no car).
    cols: np.ndarray
    edge: np.ndarray
    line: np.ndarray
    #: The footprint's projected near edge per window column (NaN outside it).
    near_edge: np.ndarray
    tyres: list[Tyre]
    #: Hidden tyres (front/rear views without visible tyres) on the floor (m).
    hidden: list[tuple[float, float]] = field(default_factory=list)
    visible: list[GroundContact] = field(default_factory=list)
    #: The vehicle alpha the model was built from (before clean_ground_fringe).
    alpha: np.ndarray | None = None

    def line_filled(self) -> np.ndarray:
        return _fill_nearest(self.line)

    @property
    def car_columns(self) -> tuple[int, int] | None:
        idx = np.flatnonzero(np.isfinite(self.edge))
        return (int(idx[0]), int(idx[-1])) if len(idx) else None

    def as_dict(self) -> dict:
        finite = np.isfinite(self.line)
        samples = []
        if finite.any():
            for i in np.linspace(np.flatnonzero(finite)[0], np.flatnonzero(finite)[-1], 9).round().astype(int):
                samples.append([round(float(self.cols[i]), 1), round(float(self.edge[i]), 1), round(float(self.line[i]), 1)])
        return {
            "pose": self.pose.as_dict(),
            "poseClamped": bool(self.pose.clamped),
            "footprint": self.footprint.as_dict(),
            "floorLine": samples,
            "tyres": [t.as_dict() for t in self.tyres],
            "hiddenTyresM": [[round(x, 3), round(y, 3)] for x, y in self.hidden],
            "window": [self.window.x0, self.window.y0, self.window.x1, self.window.y1],
        }


def build_ground_model(
    vehicle_alpha_out: np.ndarray,
    placement: PlacementResult,
    contacts_out: Sequence,
    plate: ShowroomPlate,
) -> GroundModel:
    height, width = vehicle_alpha_out.shape
    meta = plate.plate
    frame = FloorFrame(meta.floor_h, width, height, meta.camera.position[:2])
    contacts = [_as_contact(c) for c in contacts_out]
    visible = [c for c in contacts if not c.inferred]

    vw = max(placement.width, 1.0)
    margin = int(round(WINDOW_MARGIN * vw))
    span = plate.floor_top[max(0, int(placement.left) - margin) : max(1, int(placement.left + vw) + margin + 1)]
    top = float(np.min(span)) if len(span) else 0.0
    window = Window(
        int(math.floor(placement.left)) - margin, int(math.floor(top)) - 2,
        int(math.ceil(placement.left + vw)) + margin, height,
    ).clip(width, height)  # fmt: skip
    cols = np.arange(window.x0, window.x1, dtype=np.float64) + 0.5
    edge = lower_outline(vehicle_alpha_out, window.x0, window.x1) if not window.empty else np.zeros(0)

    # pose: tyre contacts, or (no visible tyre) the floor right below the bumper lip
    anchor = None
    if not visible:
        central = np.where(np.abs(cols - placement.center_x) < 0.3 * vw, edge, np.nan)
        if not np.isfinite(central).any():
            central = edge
        if np.isfinite(central).any():
            i = int(np.nanargmax(central))
            across_px, _ = frame.px_per_metre(float(cols[i]), min(float(central[i]), height - 1.0))
            anchor = (float(cols[i]), float(central[i]) + LIP_CLEARANCE * across_px)
    pose = fit_pose(meta, frame, placement, visible, anchor_px=anchor)
    fixed_end = None
    if not visible:
        # the camera-facing end stays under the lip
        cam_along, _ = pose.local(*frame.camera_xy)
        fixed_end = "front" if cam_along > 0 else "rear"
    footprint = solve_footprint(meta, frame, pose, lower_body_extent(vehicle_alpha_out, placement), fixed_end)
    near, _ = polygon_rows(frame, footprint.corners(), cols)

    has = np.isfinite(edge)
    probe = np.where(has, edge, np.where(np.isfinite(near), near, placement.ground_y))
    across_px, _ = frame.scales(cols, np.clip(_fill_nearest(probe), 0, height - 1))

    tyres = [_tyre(frame, pose, vehicle_alpha_out, c) for c in visible]
    line = floor_line_model(edge, cols, across_px, tyres, contact_sides(meta, visible), vw,
                            LIP_CLEARANCE if not visible else BODY_CLEARANCE)  # fmt: skip

    hidden: list[tuple[float, float]] = []
    extent = lower_body_extent(vehicle_alpha_out, placement)
    if not visible and extent is not None and np.isfinite(line).any():
        # the hidden tyres stand inside the lower body's corners, an axle behind the face
        x_l, x_r = extent
        filled = _fill_nearest(line)
        for x in (x_l + HIDDEN_TYRE_INSET * (x_r - x_l), x_r - HIDDEN_TYRE_INSET * (x_r - x_l)):
            i = int(np.clip(round(x - window.x0 - 0.5), 0, len(cols) - 1))
            fx, fy = (float(v) for v in frame.to_floor(x, min(filled[i], height - 1.0)))
            tx, ty = frame.towards_camera(fx, fy)
            back = HIDDEN_AXLE_OFFSET * pose.reference_scale
            hidden.append((fx - back * tx, fy - back * ty))
    return GroundModel(frame, pose, footprint, window, cols, edge, line, near, tyres, hidden, visible, vehicle_alpha_out)


# --------------------------------------------------------------------------- matte clean-up

#: Tyre bottoms: light remnants are removed up to this height (m) above the contact; a pixel
#: is a remnant when its luma exceeds the rubber's (30th percentile) by these factors.
FRINGE_TYRE_HEIGHT = 0.02
FRINGE_TYRE_FACTOR = (1.8, 0.03)
#: Lower outline elsewhere: at most this height (m) of a light bottom fringe; lighter than
#: the body just above by these factors.
FRINGE_BODY_HEIGHT = 0.015
FRINGE_BODY_FACTOR = (1.3, 0.025)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def _cut_rows(
    luma: np.ndarray, alpha: np.ndarray, x0: int, x1: int, bottom: np.ndarray, top: np.ndarray, threshold: np.ndarray
) -> np.ndarray:
    """Per column x0..x1: first row of a light fringe hanging at the bottom of the cut-out
    (pixels above `threshold` make up at least half of the rows from there down to the
    bottom, scanning no higher than `top`); `bottom` + 1 where there is none."""
    cuts = bottom.astype(np.float64) + 1.0
    for k, x in enumerate(range(x0, x1)):
        b, t, thr = int(bottom[k]), int(top[k]), float(threshold[k])
        if b <= t:
            continue
        col_l = luma[t : b + 1, x]
        col_a = alpha[t : b + 1, x]
        light = (col_l > thr) & (col_a > 0.02)
        if not light.any():
            continue
        # fraction of light pixels from row r down to the bottom
        tail = np.cumsum(light[::-1])[::-1] / np.arange(len(light), 0, -1)
        ok = np.flatnonzero(light & (tail >= 0.5))
        if len(ok):
            cuts[k] = t + ok[0]
    return cuts


def _smooth_cut(cuts: np.ndarray, bottom: np.ndarray, width: int) -> np.ndarray:
    """Median then Gaussian smoothing of the cut DEPTH above the bottom (a clean edge that
    follows the outline, no comb of single columns, never deeper than the bottom)."""
    depth = np.maximum(bottom + 1.0 - cuts, 0.0)
    k = max(3, width | 1)
    if len(depth) >= k:
        padded = np.pad(depth, k // 2, mode="edge")
        depth = np.median(np.lib.stride_tricks.sliding_window_view(padded, k), axis=1)
        depth = cv2.GaussianBlur(depth.reshape(1, -1).astype(np.float64), (0, 0), sigmaX=max(1.0, k / 3.0)).ravel()
    return bottom + 1.0 - depth


def _apply_cut(alpha: np.ndarray, x0: int, cuts: np.ndarray, bottom: np.ndarray) -> int:
    """Fade the alpha out below the cut rows (1.5 px feather); returns the removed alpha mass."""
    removed = 0.0
    for k, cut in enumerate(cuts):
        b = int(bottom[k])
        if cut > b + 0.5:
            continue
        x = x0 + k
        y0 = max(0, int(math.floor(cut - 1.0)))
        rows = np.arange(y0, b + 4)
        rows = rows[rows < alpha.shape[0]]
        keep = np.clip((cut - (rows + 0.5)) / 1.5 + 0.5, 0.0, 1.0).astype(np.float32)
        before = alpha[rows, x].copy()
        alpha[rows, x] = before * keep
        removed += float((before - alpha[rows, x]).sum())
    return int(round(removed))


def clean_ground_fringe(
    vehicle_rgb: np.ndarray, vehicle_alpha: np.ndarray, model: GroundModel, *, info: dict | None = None
) -> np.ndarray:
    """Alpha (copy) without light ground/snow remnants hanging at the bottom of the cut-out.

    At every visible tyre: a light fringe within FRINGE_TYRE_HEIGHT above the contact,
    clearly lighter than the tyre's rubber (snow/ground at the tread – the contact shadow
    shows instead). Along the rest of the lower outline: a thin light bottom fringe
    (≤ FRINGE_BODY_HEIGHT) clearly lighter than the body right above it. The cut is a
    smoothed per-column height with a feathered edge (no comb of single columns); the
    vehicle's own pixels above it stay untouched.
    """
    alpha = np.array(vehicle_alpha, dtype=np.float32, copy=True)
    height, width = alpha.shape
    luma = _luma(np.asarray(vehicle_rgb, np.float32))
    w = model.window
    if w.empty or model.car_columns is None:
        if info is not None:
            info.update({"tyreAlphaRemoved": 0, "outlineAlphaRemoved": 0})
        return alpha
    edge = model.edge
    has = np.isfinite(edge)
    bottom_all = np.where(has, edge - 1.0, -1.0)  # last solid row per window column
    scale, _ = model.frame.scales(model.cols, np.clip(_fill_nearest(edge), 0, height - 1))
    removed_tyre = removed_body = 0
    tyre_cols = np.zeros(len(edge), bool)
    for t in model.tyres:
        c = t.contact
        pxm = t.px_per_m
        x0 = int(max(w.x0, math.floor(t.extent[0]) - 2))
        x1 = int(min(w.x1, math.ceil(t.extent[1]) + 2))
        if x1 - x0 < 3:
            continue
        r0 = int(max(0, c.y - 0.1 * pxm))
        r1 = int(max(r0 + 1, c.y - 0.045 * pxm))
        inner = slice(int(x0 + 0.2 * (x1 - x0)), int(x1 - 0.2 * (x1 - x0)) + 1)
        ref = luma[r0:r1, inner][alpha[r0:r1, inner] >= 0.95]
        if ref.size < 8:
            continue
        rubber = float(np.percentile(ref, 30))
        thr = max(rubber * FRINGE_TYRE_FACTOR[0], rubber + FRINGE_TYRE_FACTOR[1])
        sl = slice(x0 - w.x0, x1 - w.x0)
        tyre_cols[sl] = True
        bottom = np.maximum(bottom_all[sl], 0)
        top = np.maximum(bottom - max(3.0, FRINGE_TYRE_HEIGHT * pxm), 0)
        cuts = _cut_rows(luma, alpha, x0, x1, bottom, top, np.full(x1 - x0, thr))
        cuts = _smooth_cut(cuts, bottom, int(round(0.03 * pxm)))
        removed_tyre += _apply_cut(alpha, x0, cuts, bottom)
    # thin light fringe along the rest of the lower outline (runs between the tyres)
    body = has & ~tyre_cols
    idx = np.flatnonzero(body)
    if len(idx):
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        for run in runs:
            if len(run) < 3:
                continue
            xs = run + w.x0
            bottom = bottom_all[run].astype(np.float64)
            band = np.maximum(2.0, FRINGE_BODY_HEIGHT * scale[run])
            top = np.maximum(bottom - band, 0)
            thr = np.zeros(len(run))
            for k, x in enumerate(xs):
                r1 = int(max(0, bottom[k] - band[k]))
                r0 = int(max(0, r1 - 0.04 * scale[run][k]))
                ref = luma[r0:r1, x][alpha[r0:r1, x] >= 0.95]
                if ref.size < 3:
                    thr[k] = np.inf
                    continue
                body_l = float(np.median(ref))
                thr[k] = max(body_l * FRINGE_BODY_FACTOR[0], body_l + FRINGE_BODY_FACTOR[1])
            cuts = _cut_rows(luma, alpha, int(xs[0]), int(xs[-1]) + 1, bottom, top, thr)
            cuts = _smooth_cut(cuts, bottom, int(round(0.02 * float(np.median(scale[run])))))
            removed_body += _apply_cut(alpha, int(xs[0]), cuts, bottom)
    if info is not None:
        info.update({"tyreAlphaRemoved": removed_tyre, "outlineAlphaRemoved": removed_body})
    return alpha


# --------------------------------------------------------------------------- shadows


def apply_grounding(
    background_linear: np.ndarray,
    vehicle_alpha_out: np.ndarray,
    placement: PlacementResult,
    contacts_out: Sequence,
    plate: ShowroomPlate,
    preset_cfg: Preset,
    *,
    info: dict | None = None,
    model: GroundModel | None = None,
) -> np.ndarray:
    """Return the plate floor (LINEAR, H×W×3) with the vehicle's contact/underbody/ambient shadow.

    `contacts_out`: plausible tyre contacts of the placed vehicle – :class:`GroundContact`
    or plain (x, y) output px. `model`: from :func:`build_ground_model` (built here when
    omitted). `info` (optional) receives a summary for metadata/debugging.
    """
    cfg = preset_cfg.shadow
    height, width = vehicle_alpha_out.shape
    if model is None:
        model = build_ground_model(vehicle_alpha_out, placement, contacts_out, plate)
    window, frame, pose, fp = model.window, model.frame, model.pose, model.footprint
    grounded = np.array(background_linear, dtype=np.float32, copy=True)
    if window.empty or model.car_columns is None:
        if info is not None:
            info.update({"version": 3, **model.as_dict(), "maxDarkening": 0.0, "farFloorDarkening": 0.0})
        return grounded

    cols, edge = model.cols, model.edge
    first, last = model.car_columns
    line_f = model.line_filled()
    edge_f = _fill_nearest(edge)
    across_px, depth_px = frame.scales(cols, np.clip(line_f, 0, height - 1))
    vw = max(placement.width, 1.0)
    line_steep = _steepness(line_f, 0.02 * vw)

    # 2) underbody: floor seen under the body, soft falloff in front of the floor line,
    #    rounded off beyond the silhouette's ends
    falloff = cfg.underbody_falloff * depth_px * line_steep
    behind = max(4.0, 0.025 * placement.height)
    source = model.alpha if model.alpha is not None else vehicle_alpha_out
    car = np.maximum(
        np.asarray(source[window.y0 : window.y1, window.x0 : window.x1], np.float32),
        np.asarray(vehicle_alpha_out[window.y0 : window.y1, window.x0 : window.x1], np.float32),
    )
    cover = cv2.dilate((car > 0.02).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    under = underbody_profile(
        window, edge_f, line_f, cfg.underbody_opacity, cfg.edge_opacity, falloff, behind, cover,
        smooth_outline(edge_f, vw),
    )  # fmt: skip
    # beyond the silhouette's ends: the end column's profile, rounded off sideways; smoothed
    # sideways (the rounded tips of bumpers make single columns jump – no vertical seams)
    beyond = np.maximum(np.maximum(cols[first] - cols, cols - cols[last]), 0.0)
    sideways = np.exp(-((beyond / np.maximum(cfg.underbody_falloff * across_px, 1.0)) ** 2))
    under = (under * sideways[None, :]).astype(np.float32)
    under = cv2.GaussianBlur(under, (0, 0), sigmaX=max(0.8, UNDERBODY_SMOOTH * vw), sigmaY=0.1)
    keep = 1.0 - under

    # 3) ambient: the plate's rendered proxy shadow through the car pose, near the car only
    gx, gy = window.grid()
    fx, fy = frame.to_floor(gx, gy)
    rows = gy[:, :1]
    ahead = np.maximum(rows - line_f[None, :], 0.0) / np.maximum(AMBIENT_FRONT_REACH * depth_px * line_steep, 1.0)[None, :]
    px_, py_ = plate.plate.floor_to_uv(*pose.invert(fx, fy))
    map_x = (np.asarray(px_) * width - 0.5).astype(np.float32)
    map_y = (np.asarray(py_) * height - 0.5).astype(np.float32)
    proxy = cv2.remap(
        np.asarray(plate.shadow, np.float32), map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    sd = fp.sdf(fx, fy, FOOTPRINT_CORNER * pose.reference_scale)
    near_car = np.exp(-((np.maximum(sd, 0.0) / max(cfg.ambient_reach * pose.reference_scale, 1e-3)) ** 2))
    ambient = cfg.ambient_opacity * (1.0 - np.clip(proxy, 0.0, 1.0)) * near_car * np.exp(-(ahead**2))
    keep *= (1.0 - ambient).astype(np.float32)

    # 1) per-tyre contact shadow (+ halo, crease)
    contact_keep = np.ones(window.shape, np.float32)
    for t in model.tyres:
        reach_m = (t.half_patch + 0.5 * t.radius + 2.5 * HALO_RADIUS) * t.stretch[0]
        reach_b = (t.half_tread + 2.5 * HALO_RADIUS) * t.stretch[1]
        corners = [
            (t.centre[0] + a * reach_m * t.roll[0] - b * reach_b * t.roll[1],
             t.centre[1] + a * reach_m * t.roll[1] + b * reach_b * t.roll[0])
            for a in (-1, 1) for b in (-1, 1)
        ]  # fmt: skip
        px_c, py_c = frame.to_px(*np.array(corners).T)
        sub = Window(
            int(math.floor(px_c.min())) - 2, int(math.floor(py_c.min())) - 2,
            int(math.ceil(px_c.max())) + 3, int(math.ceil(py_c.max())) + 3,
        ).intersect(window)  # fmt: skip
        if sub.empty:
            continue
        ys, xs_ = slice(sub.y0 - window.y0, sub.y1 - window.y0), slice(sub.x0 - window.x0, sub.x1 - window.x0)
        core, dist = tyre_patch_shadow(
            frame, sub, t.centre, t.roll, t.half_tread, t.half_patch, t.radius, GAP_SCALE, SIDEWALL, t.stretch
        )
        # the hard core never protrudes beyond the tyre's silhouette (+ CORE_MARGIN): no blades
        m = max(1.0, CORE_MARGIN * t.px_per_m)
        sub_cols = np.arange(sub.x0, sub.x1, dtype=np.float64) + 0.5
        outside = np.maximum(np.maximum(t.extent[0] - m - sub_cols, sub_cols - t.extent[1] - m), 0.0)
        core *= np.exp(-((outside / m) ** 2)).astype(np.float32)[None, :]
        halo = HALO_OPACITY * np.exp(-((dist / (HALO_RADIUS * pose.reference_scale)) ** 2))
        contact_keep[ys, xs_] *= (1.0 - cfg.contact_opacity * core) * (1.0 - halo)
        if t.contact.width > 0 or t.extent[1] > t.extent[0]:
            tw = max(t.contact.width, t.extent[1] - t.extent[0])
            crease = crease_line(sub, edge[xs_], t.contact.x, t.contact.y, tw, max(1.0, CREASE_THICKNESS * tw))
            contact_keep[ys, xs_] *= 1.0 - cfg.crease_opacity * crease
    # 4) hidden tyres (front/rear views without visible tyres): soft pools beside the corners
    for hx, hy in model.hidden:
        r = HIDDEN_POOL_RADIUS * pose.reference_scale
        d = np.hypot(fx - hx, fy - hy)
        contact_keep *= (1.0 - HIDDEN_POOL_OPACITY * np.exp(-((d / r) ** 2))).astype(np.float32)
    keep *= contact_keep

    # floor only, never below the floor-light minimum
    floor = floor_weight(plate.floor_top, height, width)[window.y0 : window.y1, window.x0 : window.x1]
    keep = 1.0 - (1.0 - keep) * floor
    keep = np.maximum(keep, cfg.min_floor_light).astype(np.float32)
    # per channel: the rendered proxy shadow is slightly cooler than the lit wood (blue ≈ 5 %
    # less darkened) – deep shadows do not turn into saturated orange
    tint = np.asarray(SHADOW_TINT, np.float32)
    grounded[window.y0 : window.y1, window.x0 : window.x1] *= 1.0 - (1.0 - keep[..., None]) * tint

    if info is not None:
        # the reviewer's complaint: broad darkening of the lower foreground. Measured on floor
        # pixels at least 1 m (plate scale) away from the car's silhouette and the floor seen
        # under it, in the picture; the old footprint-based measure is reported as well (the
        # footprint no longer places the shadow, so it also counts pixels right beside the car)
        rows = np.arange(window.y0, window.y1, dtype=np.float64)[:, None] + 0.5
        near = (car > 0.5) | ((rows >= np.where(np.isfinite(edge), edge, np.inf)[None, :]) & (rows <= line_f[None, :]))
        dist = cv2.distanceTransform((~near).astype(np.uint8), cv2.DIST_L2, 5)
        metre = float(np.median(across_px)) if len(across_px) else 1.0
        far_img = (dist > FAR_FLOOR * metre) & (floor > 0.5)
        far = (sd > 1.0) & (floor > 0.5)
        info.update(
            {
                "version": 3,
                **model.as_dict(),
                "maxDarkening": round(float(1.0 - keep.min()), 4),
                "farFloorDarkening": round(float((1.0 - keep[far_img]).mean()) if far_img.any() else 0.0, 4),
                "farFloorDarkeningFootprint": round(float((1.0 - keep[far]).mean()) if far.any() else 0.0, 4),
            }
        )
    return grounded


# --------------------------------------------------------------------------- debug


def draw_ground_model(rgb: np.ndarray, model: GroundModel, transform=None) -> np.ndarray:
    """Debug overlay (uint8 RGB copy): projected footprint (cyan), its near edge (blue), the
    floor line (yellow), tyre contact patches (red), tyre x-extents (green), hidden tyres
    (magenta). `transform(x, y)` maps output px into `rgb` (default: identity)."""
    out = np.array(rgb, dtype=np.uint8, copy=True)
    frame, fp = model.frame, model.footprint
    tf = transform or (lambda x, y: (x, y))

    def pts(xs, ys) -> np.ndarray:
        tx, ty = tf(np.asarray(xs, np.float64), np.asarray(ys, np.float64))
        return np.round(np.stack([np.ravel(tx), np.ravel(ty)], axis=1)).astype(np.int32)

    corners = np.array([[float(v) for v in frame.to_px(*c)] for c in fp.corners()], np.float64)
    cv2.polylines(out, [pts(corners[:, 0], corners[:, 1])], True, (0, 220, 255), 2, cv2.LINE_AA)
    for values, colour, thickness in ((model.near_edge, (40, 90, 255), 1), (model.line, (255, 230, 0), 2)):
        ok = np.isfinite(values)
        if ok.sum() >= 2:
            cv2.polylines(out, [pts(model.cols[ok], values[ok])], False, colour, thickness, cv2.LINE_AA)
    for t in model.tyres:
        ring = []
        for k in np.linspace(0, 2 * math.pi, 40, endpoint=False):
            a, b = t.half_patch * t.stretch[0] * math.cos(k), t.half_tread * t.stretch[1] * math.sin(k)
            ring.append((t.centre[0] + a * t.roll[0] - b * t.roll[1], t.centre[1] + a * t.roll[1] + b * t.roll[0]))
        px, py = frame.to_px(*np.array(ring).T)
        cv2.polylines(out, [pts(px, py)], True, (255, 40, 40), 2, cv2.LINE_AA)
        y = t.contact.y + 6
        cv2.polylines(out, [pts([t.extent[0], t.extent[1]], [y, y])], False, (40, 255, 40), 2)
    for hx, hy in model.hidden:
        px, py = frame.to_px(hx, hy)
        (cx, cy), = pts([float(px)], [float(py)])
        cv2.circle(out, (int(cx), int(cy)), 10, (255, 40, 255), 2)
    return out
