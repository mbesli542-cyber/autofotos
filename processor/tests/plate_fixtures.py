"""Synthetic showroom plate sets with exactly consistent geometry (for tests).

Mirrors the camera model of processor/showroom3d/render_plates.py in numpy:
one room (wall y = 0, floor z = 0, metres), one camera per shot (orbit around
the anchor, phone-like 66° HFOV, chest height), solved so that a typical
proxy car is centred and its lowest tyre contact lands on ``ground_v``. The
plates are simple ray-cast images (wall + wooden floor stripes), the shadow
map is a soft darkening around the proxy footprint – small and fast, but the
metadata (homographies, proxy bbox/contacts) is exact.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ANCHOR = np.array([0.0, -3.1, 0.0])
CAR_L, CAR_W, CAR_H = 4.75, 1.88, 1.46
WHEELBASE, TRACK = 2.86, 1.60
HFOV_DEG = 66.0
WALL_ALBEDO = (0.60, 0.585, 0.56)
BRAND_AREA = {"xMin": -3.35, "xMax": 3.35, "zMin": 0.0, "zMax": 4.2}

SHOTS = {
    "front_left_45": dict(view="front_left", orbit=-14.0, dist=4.55, height=1.35, ground_v=0.86, target_width=0.81),
    "front_right_45": dict(view="front_right", orbit=14.0, dist=4.55, height=1.35, ground_v=0.86, target_width=0.81),
    "rear_left_45": dict(view="rear_left", orbit=10.0, dist=4.55, height=1.4, ground_v=0.86, target_width=0.81),
    "rear_right_45": dict(view="rear_right", orbit=-10.0, dist=4.55, height=1.4, ground_v=0.86, target_width=0.81),
    "left_side": dict(view="left", orbit=-3.0, dist=5.25, height=1.3, ground_v=0.84, target_width=0.85),
    "right_side": dict(view="right", orbit=3.0, dist=5.25, height=1.3, ground_v=0.84, target_width=0.85),
    "front": dict(view="front", orbit=-2.0, dist=4.7, height=1.25, ground_v=0.84, target_width=0.62),
    "rear": dict(view="rear", orbit=2.0, dist=4.7, height=1.3, ground_v=0.84, target_width=0.62),
}
VIEW_ANGLES = {
    "front": 0.0, "front_left": 45.0, "left": 90.0, "rear_left": 135.0,
    "rear": 180.0, "rear_right": -135.0, "right": -90.0, "front_right": -45.0,
}  # fmt: skip


class Camera:
    def __init__(self, position: np.ndarray, forward: np.ndarray, width: int, height: int):
        self.position = np.asarray(position, np.float64)
        self.width, self.height = width, height
        self.focal = width / (2 * math.tan(math.radians(HFOV_DEG) / 2))
        self.set_forward(forward)

    def set_forward(self, forward: np.ndarray) -> None:
        f = np.asarray(forward, np.float64)
        f /= np.linalg.norm(f)
        right = np.cross(f, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, f)
        self.forward, self.right, self.up = f, right, up

    def project(self, p) -> tuple[float, float, float]:
        d = np.asarray(p, np.float64) - self.position
        z = float(d @ self.forward)
        u = (self.focal * float(d @ self.right) / z + self.width / 2) / self.width
        v = (self.height / 2 - self.focal * float(d @ self.up) / z) / self.height
        return u, v, z

    def rays(self, width: int, height: int) -> np.ndarray:
        scale = self.width / width
        xs = (np.arange(width) + 0.5) * scale - self.width / 2
        ys = self.height / 2 - (np.arange(height) + 0.5) * scale
        xx, yy = np.meshgrid(xs, ys)
        return (
            self.forward[None, None, :] * self.focal
            + self.right[None, None, :] * xx[..., None]
            + self.up[None, None, :] * yy[..., None]
        )


def homography(src, dst) -> list[list[float]]:
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    h = np.linalg.solve(np.array(rows, np.float64), np.array(rhs, np.float64))
    return [list(map(float, h[0:3])), list(map(float, h[3:6])), [float(h[6]), float(h[7]), 1.0]]


def _rot(heading_deg: float) -> np.ndarray:
    a = math.radians(heading_deg)
    return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])


def proxy_points(heading_deg: float) -> np.ndarray:
    """Corners of the proxy body + cabin boxes (world)."""
    rot = _rot(heading_deg)
    pts = []
    for lx in (-CAR_L / 2, CAR_L / 2):
        for ly in (-CAR_W / 2, CAR_W / 2):
            for lz in (0.2, 0.76):
                pts.append((lx, ly, lz))
    for lx in (-1.5, 1.2):
        for ly in (-(CAR_W - 0.24) / 2, (CAR_W - 0.24) / 2):
            pts.append((lx, ly, CAR_H))
    for sx in (-1, 1):
        for sy in (-1, 1):
            pts.append((sx * WHEELBASE / 2, sy * (TRACK / 2 + 0.115), 0.0))
    return np.array([ANCHOR + rot @ np.array(p) for p in pts])


def contact_points(heading_deg: float) -> dict[str, np.ndarray]:
    rot = _rot(heading_deg)
    labels = {
        "front_left": (WHEELBASE / 2, TRACK / 2),
        "front_right": (WHEELBASE / 2, -TRACK / 2),
        "rear_left": (-WHEELBASE / 2, TRACK / 2),
        "rear_right": (-WHEELBASE / 2, -TRACK / 2),
    }
    return {k: ANCHOR + rot @ np.array([x, y, 0.0]) for k, (x, y) in labels.items()}


def solve_camera(shot: dict, width: int, height: int) -> tuple[Camera, float, list[float]]:
    a = math.radians(shot["orbit"])
    direction = np.array([math.sin(a), -math.cos(a), 0.0])
    position = ANCHOR + direction * shot["dist"] + np.array([0.0, 0.0, shot["height"]])
    to_cam = math.degrees(math.atan2(direction[1], direction[0]))
    heading = to_cam - VIEW_ANGLES[shot["view"]]
    cam = Camera(position, np.array([ANCHOR[0], ANCHOR[1], 0.6]) - position, width, height)
    pts = proxy_points(heading)
    contacts = list(contact_points(heading).values())
    yaw, pitch = math.atan2(cam.forward[1], cam.forward[0]), math.asin(cam.forward[2])
    for _ in range(12):
        proj = [cam.project(p) for p in pts]
        us = [p[0] for p in proj]
        du = (min(us) + max(us)) / 2 - 0.5
        lowest = max(cam.project(c)[1] for c in contacts)
        dv = lowest - shot["ground_v"]
        yaw -= math.atan(du * width / cam.focal)
        pitch -= math.atan(dv * height / cam.focal)
        cam.set_forward(np.array([math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)]))
    proj = [cam.project(p) for p in pts]
    bbox = [min(p[0] for p in proj), min(p[1] for p in proj), max(p[0] for p in proj), max(p[1] for p in proj)]
    return cam, heading, [round(b, 5) for b in bbox]


def plate_metadata(name: str, shot: dict, cam: Camera, heading: float, bbox: list[float], shadow_size) -> dict:
    floor_pts = [(-3.0, -1.0), (3.0, -1.0), (3.0, -6.0), (-3.0, -6.0)]
    wall_pts = [(-3.0, 0.5), (3.0, 0.5), (3.0, 3.5), (-3.0, 3.5)]
    floor_img = [cam.project((x, y, 0.0))[:2] for x, y in floor_pts]
    wall_img = [cam.project((x, 0.0, z))[:2] for x, z in wall_pts]
    horizon_far = cam.position + np.array([cam.forward[0], cam.forward[1], 0.0]) * 1e5
    contacts = {}
    for label, w in contact_points(heading).items():
        u, v, depth = cam.project(w)
        contacts[label] = {"u": round(u, 5), "v": round(v, 5), "depthM": round(depth, 3), "floor": [round(w[0], 4), round(w[1], 4)]}
    au, av, ad = cam.project(ANCHOR)
    return {
        "image": f"{name}.jpg",
        "shadow": f"{name}-shadow.png",
        "size": [cam.width, cam.height],
        "shadowSize": list(shadow_size),
        "camera": {
            "position": [round(float(c), 4) for c in cam.position],
            "heightM": round(float(cam.position[2]), 4),
            "distanceToAnchorM": shot["dist"],
            "orbitDeg": shot["orbit"],
            "pitchDownDeg": round(math.degrees(math.asin(-cam.forward[2])), 3),
            "hfovDeg": HFOV_DEG,
            "focalPx": round(cam.focal, 3),
            "focalNorm": round(cam.focal / cam.width, 6),
        },
        "horizonV": round(cam.project(horizon_far)[1], 5),
        "floorHomography": homography(floor_pts, floor_img),
        "wallHomography": homography(wall_pts, wall_img),
        "anchor": {"floor": [float(ANCHOR[0]), float(ANCHOR[1])], "u": round(au, 5), "v": round(av, 5), "depthM": round(ad, 4), "widthPerMetre": round(cam.focal / ad / cam.width, 6)},
        "vehicle": {
            "view": shot["view"],
            "headingDeg": round(heading, 3),
            "targetWidthRatio": shot["target_width"],
            "proxy": {"lengthM": CAR_L, "widthM": CAR_W, "heightM": CAR_H, "wheelbaseM": WHEELBASE, "trackM": TRACK},
            "proxyBBox": bbox,
            "proxyContacts": contacts,
        },
        "wall": {"albedo": list(WALL_ALBEDO), "brandArea": dict(BRAND_AREA)},
    }


#: Synthetic floor reflection: a uniform sheen plus one LED streak at this floor x (m).
SHEEN = 0.04
STREAK_X = 1.2
STREAK = 0.25
REFLECTANCE = [[0.5, 0.16], [0.75, 0.15], [1.0, 0.13]]


def floor_reflection(meta: dict, width: int, height: int) -> np.ndarray:
    """LINEAR floor reflection (H, W, 3) of a plate: sheen + a bluish LED streak, floor only."""
    h = np.asarray(meta["floorHomography"], np.float64)
    inv = np.linalg.inv(h)
    us = (np.arange(width) + 0.5) / width
    vs = (np.arange(height) + 0.5) / height
    uu, vv = np.meshgrid(us, vs)
    pts = np.stack([uu, vv, np.ones_like(uu)], -1) @ inv.T
    w = pts[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = pts[..., 0] / w, pts[..., 1] / w
    valid = (w > 0) & (y < 0)
    streak = STREAK * np.exp(-(((x - STREAK_X) / 0.05) ** 2))
    out = np.zeros((height, width, 3), np.float32)
    out[..., 0] = SHEEN + 0.6 * streak
    out[..., 1] = SHEEN + 0.8 * streak
    out[..., 2] = SHEEN + 1.0 * streak
    return np.where(valid[..., None], out, 0.0).astype(np.float32)


def _srgb(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)


def _linear(srgb: np.ndarray) -> np.ndarray:
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def render_plate(cam: Camera, width: int, height: int) -> np.ndarray:
    d = cam.rays(width, height)
    pos = cam.position
    with np.errstate(divide="ignore", invalid="ignore"):
        t_floor = np.where(d[..., 2] < 0, -pos[2] / d[..., 2], np.inf)
        t_wall = np.where(d[..., 1] > 0, -pos[1] / d[..., 1], np.inf)
    floor = t_floor < t_wall
    hit = pos[None, None, :] + d * np.minimum(t_floor, t_wall)[..., None]
    out = np.zeros((height, width, 3), np.float32)
    # wall: warm white with soft light pools near the top
    z = np.clip(hit[..., 2], 0, 5)
    x = hit[..., 0]
    pools = sum(np.exp(-(((x - sx) / 0.7) ** 2) - ((z - 3.2) / 0.9) ** 2) for sx in (-2.45, -0.82, 0.82, 2.45))
    wall = np.array([226, 222, 216], np.float32) * (0.92 + 0.08 * pools[..., None])
    planks = 0.5 + 0.5 * np.sin(hit[..., 1] * 2 * math.pi / 0.19)
    wood = np.array([166, 118, 78], np.float32) * (0.92 + 0.08 * planks[..., None])
    out[:] = np.where(floor[..., None], wood, wall)
    sky = ~np.isfinite(np.minimum(t_floor, t_wall))
    out[sky] = (40, 40, 44)
    return np.clip(out, 0, 255).astype(np.uint8)


def render_shadow(meta: dict, width: int, height: int) -> np.ndarray:
    h = np.asarray(meta["floorHomography"], np.float64)
    inv = np.linalg.inv(h)
    us = (np.arange(width) + 0.5) / width
    vs = (np.arange(height) + 0.5) / height
    uu, vv = np.meshgrid(us, vs)
    pts = np.stack([uu, vv, np.ones_like(uu)], -1) @ inv.T
    w = pts[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = pts[..., 0] / w, pts[..., 1] / w
    valid = (w > 0) & (y < 0)
    heading = math.radians(meta["vehicle"]["headingDeg"])
    dx, dy = x - ANCHOR[0], y - ANCHOR[1]
    along = dx * math.cos(heading) + dy * math.sin(heading)
    across = -dx * math.sin(heading) + dy * math.cos(heading)
    dist = np.hypot(np.maximum(np.abs(along) - CAR_L / 2, 0), np.maximum(np.abs(across) - CAR_W / 2, 0))
    effect = np.where(valid, 0.65 * np.exp(-((dist / 0.45) ** 2)), 0.0)
    return np.clip(1.0 - effect, 0.0, 1.0).astype(np.float32)


def build_plate_set(
    directory: Path, width: int = 640, height: int = 480, shots=tuple(SHOTS), reflection: bool = True
) -> Path:
    """Write <shot>.jpg, <shot>-shadow.png, (<shot>-reflection.png) and plates.json into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"version": 1, "room": {"description": "synthetic test room", "anchor": [0.0, -3.1]}, "plates": {}}
    sw, sh = width // 2, height // 2
    for name in shots:
        shot = SHOTS[name]
        cam, heading, bbox = solve_camera(shot, width, height)
        plate = plate_metadata(name, shot, cam, heading, bbox, (sw, sh))
        rgb = render_plate(cam, width, height)
        if reflection:
            # the plate shows its floor reflection; the pass holds it (signed, offset 0.5)
            refl = floor_reflection(plate, width, height)
            rgb = (_srgb(_linear(rgb / 255.0) + refl) * 255.0 + 0.5).astype(np.uint8)
            small = cv2.resize(refl, (sw, sh), interpolation=cv2.INTER_AREA)
            code = (np.clip(small + 0.5, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
            cv2.imwrite(str(directory / f"{name}-reflection.png"), cv2.cvtColor(code, cv2.COLOR_RGB2BGR))
            plate.update(
                reflection=f"{name}-reflection.png", reflectionSize=[sw, sh], reflectionOffset=0.5,
                reflectance=REFLECTANCE,
            )  # fmt: skip
        Image.fromarray(rgb).save(directory / f"{name}.jpg", quality=92)
        mult = render_shadow(plate, sw, sh)
        cv2.imwrite(str(directory / f"{name}-shadow.png"), (mult * 65535 + 0.5).astype(np.uint16))
        meta["plates"][name] = plate
    path = directory / "plates.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path
