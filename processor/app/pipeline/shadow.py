"""STEP 7 – shadow primitives of the grounding (app/pipeline/grounding.py).

Everything here is a deterministic opacity map (0..1) on the plate's FLOOR,
computed in a window of the output frame; app/pipeline/grounding.py combines
them multiplicatively in linear light before the vehicle is composited, so a
shadow never covers the car:

- :func:`floor_weight` – 0 above the wall/floor junction, 1 on the floor;
- :class:`FloorFrame` – the plate's floor homography at output resolution
  (floor metres ↔ output px), used for perspective-correct sizes;
- :func:`polygon_rows` – near/far image row of a projected floor polygon per
  column (the car footprint's near edge = the floor line under the car);
- :func:`tyre_patch_shadow` – contact shadow of ONE tyre: its contact patch on
  the floor in metres (tread width across the axle, patch length along the
  rolling direction) with a falloff along the rolling direction that follows
  the tyre/floor gap height ``R − sqrt(R² − s²)`` – projected by the floor
  homography, no generic ellipse, no hard edge;
- :func:`crease_line` – thin occlusion line where a tyre's outline meets the floor;
- :func:`underbody_profile` – the floor seen under the body (between the lower
  outline and the floor line) and its soft falloff in front of the car.

No generative AI, no pixels of the vehicle are touched.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .vehicle_geometry import (  # noqa: F401 - re-exported for older callers
    MAX_CONTACT_RISE,
    MAX_EXTRAPOLATION_SLOPE,
    MAX_FLOOR_SLOPE,
    bottom_profile,
    contact_rise,
    floor_contact_line,
    floor_contacts,
)

#: Shadows fade out over this fraction of the image height above the
#: wall/floor junction instead of ending in a hard line.
HORIZON_FADE = 0.005


def floor_weight(floor_top: np.ndarray | float, height: int, width: int) -> np.ndarray:
    """0 above the wall/floor junction, 1 on the floor, soft transition (H, W)."""
    fade = max(2.0, HORIZON_FADE * height)
    ys = np.arange(height, dtype=np.float32)[:, None]
    top = np.broadcast_to(np.asarray(floor_top, np.float32).reshape(1, -1), (1, width))
    return np.clip((ys - top) / fade, 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------- floor frame


@dataclass(frozen=True)
class Window:
    """Rows [y0, y1) × columns [x0, x1) of the output frame."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.y1 - self.y0, self.x1 - self.x0

    def grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Pixel-centre coordinates (x, y) of the window, each (h, w) float64."""
        xs = np.arange(self.x0, self.x1, dtype=np.float64) + 0.5
        ys = np.arange(self.y0, self.y1, dtype=np.float64) + 0.5
        return np.meshgrid(xs, ys)

    def clip(self, width: int, height: int) -> Window:
        x0, y0 = max(0, self.x0), max(0, self.y0)
        return Window(x0, y0, max(x0, min(width, self.x1)), max(y0, min(height, self.y1)))

    def intersect(self, other: Window) -> Window:
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        return Window(x0, y0, max(x0, min(self.x1, other.x1)), max(y0, min(self.y1, other.y1)))

    @property
    def empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0


class FloorFrame:
    """The plate floor at output resolution: floor metres (x, y; z = 0) ↔ output px."""

    def __init__(self, floor_h: np.ndarray, width: int, height: int, camera_xy: tuple[float, float]):
        self.width, self.height = int(width), int(height)
        self.h = np.diag([float(width), float(height), 1.0]) @ np.asarray(floor_h, np.float64)
        self.inv = np.linalg.inv(self.h)
        self.camera_xy = (float(camera_xy[0]), float(camera_xy[1]))

    def to_px(self, fx, fy) -> tuple[np.ndarray, np.ndarray]:
        fx, fy = np.asarray(fx, np.float64), np.asarray(fy, np.float64)
        h = self.h
        w = h[2, 0] * fx + h[2, 1] * fy + h[2, 2]
        return (h[0, 0] * fx + h[0, 1] * fy + h[0, 2]) / w, (h[1, 0] * fx + h[1, 1] * fy + h[1, 2]) / w

    def to_floor(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Output px → floor metres (only meaningful for pixels that show the floor)."""
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        h = self.inv
        w = h[2, 0] * x + h[2, 1] * y + h[2, 2]
        w = np.where(np.abs(w) < 1e-12, 1e-12, w)
        return (h[0, 0] * x + h[0, 1] * y + h[0, 2]) / w, (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / w

    def towards_camera(self, fx: float, fy: float) -> tuple[float, float]:
        """Unit floor direction from (fx, fy) towards the camera's foot point."""
        dx, dy = self.camera_xy[0] - fx, self.camera_xy[1] - fy
        norm = max(float(np.hypot(dx, dy)), 1e-6)
        return dx / norm, dy / norm

    def px_per_metre(self, x: float, y: float) -> tuple[float, float]:
        """Local floor scale at output px (x, y): (horizontal px per metre across the view –
        also ≈ the vertical px per metre of HEIGHT there –, vertical px per metre of floor
        depth towards the camera)."""
        fx, fy = self.to_floor(x, y)
        tx, ty = self.towards_camera(float(fx), float(fy))
        step = 0.05
        ax, ay = self.to_px(fx + step * tx, fy + step * ty)  # 5 cm towards the camera
        bx, by = self.to_px(fx - step * ty, fy + step * tx)  # 5 cm across the view
        depth = float(np.hypot(ax - x, ay - y)) / step
        across = float(np.hypot(bx - x, by - y)) / step
        return max(across, 1e-3), max(depth, 1e-3)

    def scales(self, xs: np.ndarray, ys: np.ndarray, samples: int = 24) -> tuple[np.ndarray, np.ndarray]:
        """:meth:`px_per_metre` along a polyline (xs, ys), sampled and interpolated."""
        n = len(xs)
        if n == 0:
            return np.zeros(0), np.zeros(0)
        picks = np.unique(np.linspace(0, n - 1, min(samples, n)).round().astype(int))
        across, depth = [], []
        for i in picks:
            y = float(np.clip(ys[i], 0.0, self.height - 1.0))
            a, d = self.px_per_metre(float(xs[i]), y)
            across.append(a)
            depth.append(d)
        idx = np.arange(n)
        return np.interp(idx, picks, across), np.interp(idx, picks, depth)

    def horizontal_direction(self, x: float, y: float) -> tuple[float, float]:
        """Unit floor direction that the image's +x direction shows at output px (x, y)."""
        ax, ay = self.to_floor(x, y)
        bx, by = self.to_floor(x + 4.0, y)
        dx, dy = float(bx - ax), float(by - ay)
        norm = max(float(np.hypot(dx, dy)), 1e-9)
        return dx / norm, dy / norm


def polygon_rows(frame: FloorFrame, corners, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(near, far) per column (pixel centres `cols`): largest/smallest image row of the
    projected floor polygon `corners` (floor metres, in order) – NaN outside it.
    A homography maps floor lines to image lines, so the polygon edges stay straight."""
    pts = [tuple(float(v) for v in frame.to_px(*c)) for c in corners]
    near = np.full(len(cols), -np.inf)
    far = np.full(len(cols), np.inf)
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
        lo, hi = min(ax, bx), max(ax, bx)
        inside = (cols >= lo) & (cols <= hi)
        if not inside.any():
            continue
        if hi - lo < 1e-9:
            ys = np.full(len(cols), max(ay, by))
            ys_far = np.full(len(cols), min(ay, by))
        else:
            ys = ay + (cols - ax) * (by - ay) / (bx - ax)
            ys_far = ys
        near = np.where(inside, np.maximum(near, ys), near)
        far = np.where(inside, np.minimum(far, ys_far), far)
    return np.where(np.isfinite(near), near, np.nan), np.where(np.isfinite(far), far, np.nan)


# --------------------------------------------------------------------------- tyres


def tyre_gap(s: np.ndarray, radius: float, half_patch: float) -> np.ndarray:
    """Height (m) of the gap between a tyre and the floor at rolling distance `s` (m) from
    the patch centre: 0 on the flat contact patch, R − sqrt(R² − s'²) beyond it."""
    s_out = np.maximum(np.abs(s) - half_patch, 0.0)
    r = max(radius, 1e-3)
    inside = np.minimum(s_out, r)
    return (r - np.sqrt(np.maximum(r * r - inside * inside, 0.0))) + np.maximum(s_out - r, 0.0)


def tyre_patch_shadow(
    frame: FloorFrame,
    window: Window,
    centre: tuple[float, float],
    roll: tuple[float, float],
    half_tread: float,
    half_patch: float,
    radius: float,
    gap_scale: float,
    sidewall: float,
    stretch: tuple[float, float] = (1.0, 1.0),
) -> tuple[np.ndarray, np.ndarray]:
    """(core, distance) of one tyre's contact in `window`.

    `core` (0..1): 1 on the contact patch (|across| ≤ half_tread, |s| ≤ half_patch),
    along the rolling direction exp(−gap/gap_scale) with the tyre/floor gap height,
    across the axle a smooth sidewall falloff over `sidewall` m – no cut-off anywhere.
    `distance` (m): floor distance outside the patch rectangle (for the soft halo).
    All sizes are physical metres; `stretch` (along, across) is the car pose's scale of
    the plate's floor metres (photo camera ≠ plate camera) – floor offsets are divided by it.
    """
    gx, gy = window.grid()
    fx, fy = frame.to_floor(gx, gy)
    dx, dy = fx - centre[0], fy - centre[1]
    ux, uy = roll
    s = (dx * ux + dy * uy) / max(stretch[0], 1e-6)
    b = (-dx * uy + dy * ux) / max(stretch[1], 1e-6)
    b_out = np.maximum(np.abs(b) - half_tread, 0.0)
    s_out = np.maximum(np.abs(s) - half_patch, 0.0)
    along = np.exp(-tyre_gap(s, radius, half_patch) / max(gap_scale, 1e-4))
    across = np.exp(-((b_out / max(sidewall, 1e-4)) ** 2))
    core = (along * across).astype(np.float32)
    return core, np.hypot(s_out, b_out).astype(np.float32)


def crease_line(
    window: Window, edge: np.ndarray, x: float, y: float, tyre_width: float, thickness: float
) -> np.ndarray:
    """Thin dark line hugging the tyre outline where it touches the floor.

    `edge`: bottom edge (row, float) of the vehicle outline for the window's columns
    (NaN without vehicle). Strongest where the outline is at the contact height, fading
    where the tyre curves up; only a few px below the outline (the tyre covers the rest).
    """
    h, w = window.shape
    cols = np.arange(window.x0, window.x1, dtype=np.float64) + 0.5
    rows = np.arange(window.y0, window.y1, dtype=np.float64)[:, None] + 0.5
    valid = np.isfinite(edge)
    e = np.where(valid, edge, -1e6)
    touch = np.exp(-(((y - e) / max(0.035 * tyre_width, 1.0)) ** 2)) * valid
    taper = np.exp(-(((cols - x) / max(0.45 * tyre_width, 1.0)) ** 4))
    d = rows - e[None, :]
    # also a few px up under the soft edge of the tyre (no light fringe)
    falloff = np.exp(-((np.maximum(d, 0.0) / max(thickness, 0.6)) ** 2))
    profile = np.where(d >= -3.0 * max(thickness, 1.0), falloff, 0.0)
    out = profile * (touch * taper)[None, :]
    return out.astype(np.float32).reshape(h, w)


# --------------------------------------------------------------------------- body


def underbody_profile(
    window: Window,
    edge: np.ndarray,
    line: np.ndarray,
    deep: float,
    rim: float,
    falloff: np.ndarray,
    behind: float,
    cover: np.ndarray | None = None,
    edge_smooth: np.ndarray | None = None,
) -> np.ndarray:
    """Opacity of the floor under and in front of the body, per window column.

    `edge`: lower outline row, `line`: floor line row (≥ edge) – the floor between them is
    the floor seen under the car: `deep` right below the outline (deep under the car),
    `rim` at the floor line; beyond the line a soft falloff over `falloff` px (perpendicular
    falloff already folded in); `behind` px up under the outline at `deep` (the car's soft
    edge pixels never mix with unshadowed floor) – only where `cover` (window-sized, e.g. the
    slightly dilated car alpha) says so, when given. `edge_smooth` (optional): a smoothed
    outline that normalises the gap profile (a rounded bumper tip makes single columns
    jump); the floor below the raw outline down to it counts as deep. NaN columns → 0.
    """
    rows = np.arange(window.y0, window.y1, dtype=np.float64)[:, None] + 0.5
    valid = np.isfinite(edge) & np.isfinite(line)
    e = np.where(valid, edge, 1e9)[None, :]
    f = np.where(valid, np.maximum(line, edge), 1e9)[None, :]
    es = e if edge_smooth is None else np.where(valid, np.minimum(edge_smooth, line), 1e9)[None, :]
    t = np.clip((rows - es) / np.maximum(f - es, 1.0), 0.0, 1.0)
    gap = deep + (rim - deep) * t * t  # dark deep under the car, lightening towards the floor line
    beyond = np.maximum(rows - f, 0.0) / np.maximum(falloff, 1.0)[None, :]
    above = deep if cover is None else deep * cover
    out = np.where(rows < e - behind, 0.0, np.where(rows <= f, np.where(rows < e, above, gap), rim * np.exp(-(beyond**2))))
    return (out * valid[None, :]).astype(np.float32)
