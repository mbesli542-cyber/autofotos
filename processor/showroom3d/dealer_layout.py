"""Geometry of the dealer backdrop: ONE fixed studio camera, eight car poses (pure numpy).

The dealer's background (``dealer/haendler_hintergrund.png``) shows his showroom
wall FRONTALLY: the wall/floor junction is a horizontal line, all vertical
edges are vertical. Like in a real photo studio the camera and the backdrop
stay; only the car is turned (his reference photos show exactly that). The
camera is therefore modelled as a LEVEL camera with a vertical lens shift
(vertical lines stay vertical, the horizon above the image centre):

- horizontal field of view 66° (smartphone main camera in 4:3, like the
  standard plates – the car photos come from such a camera);
- camera height ``CAMERA_HEIGHT_M`` and wall distance ``WALL_DISTANCE_M``:
  the only free scale of a frontal wall. Chosen so that a typical 4.85 m car
  in a 3/4 view fills ≈ 0.83 of the frame width (his references: 0.80–0.86)
  with ≈ 1.4 m of floor between its far corner and the wall, and its front
  wheel stands at ≈ 0.80 of the height (references ≈ 0.82). It also gives
  plausible sizes for what the image shows: the white brand panel is ≈ 7.2 m
  wide, the black skirting ≈ 0.17 m high, the planter ≈ 0.9 m;
- the junction row ``JUNCTION_V`` is measured in his image (bottom of the
  skirting, 492 / 1086).

His image is not geometrically exact (it was made with an image AI: the
right wall's skirting lines do not meet on one horizon), so nothing else is
fitted to it; the right wall section is placed so that its floor line runs
through the measured junction points.

World: wall plane y = 0 (x right, z up), floor z = 0, metres, room in y < 0;
camera at (0, −WALL_DISTANCE_M, CAMERA_HEIGHT_M) looking along +y.
Image: normalised u (right) and v (down), 0..1.

``layout()`` returns the camera, the room features (for the Blender scene,
``render_dealer_passes.py``) and per shot the proxy car pose: heading from the
shot's view angle, position solved so that the proxy car is centred and as
wide as the shot's target. ``plate_entry()`` turns a shot into the plates.json
entry the processor loads (app/showroom/plates.py).
"""

from __future__ import annotations

import itertools
import math

import numpy as np

#: Size of the dealer's image (px) – all measured image positions refer to it.
SOURCE_SIZE = (1448, 1086)
HFOV_DEG = 66.0
FOCAL_NORM = 1.0 / (2.0 * math.tan(math.radians(HFOV_DEG) / 2.0))  # focal length / image width
ASPECT = 4.0 / 3.0
CAMERA_HEIGHT_M = 1.6
WALL_DISTANCE_M = 8.25
#: Bottom of the skirting of the main wall (= where the floor starts), measured.
JUNCTION_V = 492.0 / 1086.0
#: Horizon (camera height on the wall): from the junction, camera height and wall distance.
HORIZON_V = JUNCTION_V - FOCAL_NORM * ASPECT * CAMERA_HEIGHT_M / WALL_DISTANCE_M
#: Blender lens shift (fraction of the sensor width; negative = frame moved down): principal point at v = HORIZON_V.
SHIFT_Y = (HORIZON_V - 0.5) / ASPECT

#: Features measured in the dealer's image (px of SOURCE_SIZE), see dealer_clean.py for the lettering.
FEATURES_PX = {
    # main white panel (brand wall): dark frame at its left edge, LED corner at its right edge
    "panel_left_u": 178.0,
    "panel_right_u": 1148.0,
    # vertical blue LED left of the panel (in the recess before the slats) and the slats
    "left_led_u": 94.0,
    "slats_right_u": 88.0,
    # skirting (main wall): top and bottom rows
    "skirting_top_v": 468.0,
    # right wall section: floor line through these junction points
    "right_junction": ((1160.0, 492.0), (1424.0, 518.0)),
    # planter: right edge, top, bottom (front face)
    "planter_right_u": 102.0,
    "planter_top_v": 377.0,
    "planter_bottom_v": 515.0,
    # wall-washer pools on the main wall and on the right wall section (centre columns)
    "pools_u": (318.0, 536.0, 760.0, 985.0, 1270.0),
}

#: Typical car (proxy of the shadow pass): 4.85 × 1.88 × 1.46 m (Mercedes E-Class / BMW 5 Series size).
CAR = {"lengthM": 4.85, "widthM": 1.88, "heightM": 1.46, "wheelbaseM": 2.9, "trackM": 1.6}
WHEEL_R = 0.335
#: Rounded proxy (as built in Blender, render_plates.build_proxy): body and cabin boxes + bevel radius.
BODY = {"z0": 0.2, "z1": 0.76, "bevel": 0.14}
CABIN = {"x": -0.15, "length": 2.7, "inset": 0.24, "z0": 0.75, "bevel": 0.18}

#: Angle between the car's heading and the direction car → camera, per view (render_plates.VIEW_ANGLES).
VIEW_ANGLES = {
    "front": 0.0,
    "front_left": 45.0,
    "left": 90.0,
    "rear_left": 135.0,
    "rear": 180.0,
    "rear_right": -135.0,
    "right": -90.0,
    "front_right": -45.0,
}
#: Per shot: view and target width (car bbox width / frame width).
SHOTS = {
    "front_left_45": {"view": "front_left", "target": 0.83},
    "front_right_45": {"view": "front_right", "target": 0.83},
    "rear_left_45": {"view": "rear_left", "target": 0.83},
    "rear_right_45": {"view": "rear_right", "target": 0.83},
    # side views: with ONE level camera the ground line of a car seen side-on lies
    # h·w·W/L below the horizon (camera height h, width ratio w, car length L – independent
    # of the focal length), so they stand higher than the 3/4 views (whose near corner is
    # closer to the camera). 0.92 (≈ 4 % margin each side) keeps them as low and large as
    # the frame allows; lower would need a higher camera, which drops the 3/4 views' front
    # wheel out of his references (≈ 0.81 of the height).
    "left_side": {"view": "left", "target": 0.92},
    "right_side": {"view": "right", "target": 0.92},
    # front/rear: the car well in front of the wall (≈ 1.4 m), nose ≈ 2 m from the camera –
    # large enough that a tall front (headroom below the branding) is not shrunk to a toy car
    "front": {"view": "front", "target": 0.68},
    "rear": {"view": "rear", "target": 0.68},
}
#: Brand area (the main white panel) height: up to the (not visible) panel top.
BRAND_TOP_M = 4.0


# --------------------------------------------------------------------------- camera


def camera_position() -> np.ndarray:
    return np.array([0.0, -WALL_DISTANCE_M, CAMERA_HEIGHT_M])


def project(points) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World points (..., 3) → (u, v, depth along the viewing axis)."""
    p = np.asarray(points, np.float64)
    cam = camera_position()
    depth = p[..., 1] - cam[1]
    u = 0.5 + FOCAL_NORM * (p[..., 0] - cam[0]) / depth
    v = HORIZON_V - FOCAL_NORM * ASPECT * (p[..., 2] - cam[2]) / depth
    return u, v, depth


def floor_point(u: float, v: float) -> np.ndarray:
    """Floor point (x, y) seen at image position (u, v) (v below the horizon)."""
    depth = FOCAL_NORM * ASPECT * CAMERA_HEIGHT_M / (v - HORIZON_V)
    return np.array([(u - 0.5) * depth / FOCAL_NORM, depth - WALL_DISTANCE_M])


def wall_point(u: float, v: float) -> np.ndarray:
    """Point (x, z) on the main wall (y = 0) seen at (u, v)."""
    d = WALL_DISTANCE_M
    return np.array([(u - 0.5) * d / FOCAL_NORM, CAMERA_HEIGHT_M - (v - HORIZON_V) * d / (FOCAL_NORM * ASPECT)])


def homography(src, dst) -> list[list[float]]:
    """3×3 homography from 4 point pairs (DLT, normalised so H[2][2] = 1)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    h = np.linalg.solve(np.array(rows, np.float64), np.array(rhs, np.float64))
    return [[float(h[0]), float(h[1]), float(h[2])], [float(h[3]), float(h[4]), float(h[5])], [float(h[6]), float(h[7]), 1.0]]


def floor_homography() -> list[list[float]]:
    src = [(-3.0, -1.0), (3.0, -1.0), (3.0, -6.0), (-3.0, -6.0)]
    u, v, _ = project(np.array([(x, y, 0.0) for x, y in src]))
    return homography(src, list(zip(u, v)))


def wall_homography() -> list[list[float]]:
    src = [(-3.0, 0.5), (3.0, 0.5), (3.0, 3.5), (-3.0, 3.5)]
    u, v, _ = project(np.array([(x, 0.0, z) for x, z in src]))
    return homography(src, list(zip(u, v)))


# --------------------------------------------------------------------------- room features


def _u(px: float) -> float:
    return px / SOURCE_SIZE[0]


def _v(px: float) -> float:
    return px / SOURCE_SIZE[1]


def room() -> dict:
    """Room features in metres (for the Blender scene and the brand area)."""
    f = FEATURES_PX
    panel_x0 = float(wall_point(_u(f["panel_left_u"]), JUNCTION_V)[0])
    panel_x1 = float(wall_point(_u(f["panel_right_u"]), JUNCTION_V)[0])
    (au, av), (bu, bv) = f["right_junction"]
    a, b = floor_point(_u(au), _v(av)), floor_point(_u(bu), _v(bv))
    direction = (b - a) / np.linalg.norm(b - a)
    # the right wall's floor line, intersected with the main wall (y = 0)
    t = -a[1] / direction[1]
    corner = a + t * direction
    planter = floor_point(_u(f["planter_right_u"]), _v(f["planter_bottom_v"]))
    planter_depth = planter[1] + WALL_DISTANCE_M
    planter_top = CAMERA_HEIGHT_M - (_v(f["planter_top_v"]) - HORIZON_V) * planter_depth / (FOCAL_NORM * ASPECT)
    skirting = float(wall_point(0.5, _v(f["skirting_top_v"]))[1])
    pools = []
    for pu in f["pools_u"]:
        if pu <= f["panel_right_u"]:
            pools.append([round(float(wall_point(_u(pu), JUNCTION_V)[0]), 4), 0.0])
        else:  # on the right wall: where its floor line is seen in that column
            s = _solve_column(a, direction, _u(pu))
            pools.append([round(float(s[0]), 4), round(float(s[1]), 4)])
    return {
        "panelX": [round(panel_x0, 4), round(panel_x1, 4)],
        "leftLedX": round(float(wall_point(_u(f["left_led_u"]), JUNCTION_V)[0]), 4),
        "slatsRightX": round(float(wall_point(_u(f["slats_right_u"]), JUNCTION_V)[0]), 4),
        "skirtingHeightM": round(skirting, 4),
        "rightWall": {
            "corner": [round(float(corner[0]), 4), 0.0],
            "direction": [round(float(direction[0]), 6), round(float(direction[1]), 6)],
            "angleDeg": round(math.degrees(math.atan2(-direction[1], direction[0])), 2),
        },
        "planter": {
            "rightX": round(float(planter[0]), 4),
            "frontY": round(float(planter[1]), 4),
            "topZ": round(float(planter_top), 4),
        },
        "pools": pools,
        "brandArea": {"xMin": round(panel_x0, 4), "xMax": round(panel_x1, 4), "zMin": 0.0, "zMax": BRAND_TOP_M},
    }


def _solve_column(a: np.ndarray, direction: np.ndarray, u: float) -> np.ndarray:
    """Point of the floor line a + t·direction seen in image column u."""
    lo, hi = -20.0, 20.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        p = a + mid * direction
        pu, _, _ = project(np.array([p[0], p[1], 0.0]))
        if (pu - u) * (direction[0]) > 0:
            hi = mid
        else:
            lo = mid
    return a + 0.5 * (lo + hi) * direction


# --------------------------------------------------------------------------- proxy car


def _rounded_box(centre, size, radius) -> np.ndarray:
    """Surface sample points of a box with rounded edges (silhouette extremes)."""
    half = np.asarray(size, np.float64) / 2.0
    inner = half - radius
    dirs = []
    for theta in np.linspace(0.0, math.pi / 2, 7):
        for phi in np.linspace(0.0, math.pi / 2, 7):
            dirs.append((math.cos(theta) * math.cos(phi), math.cos(theta) * math.sin(phi), math.sin(theta)))
    dirs = np.array(dirs)
    points = []
    for sx, sy, sz in itertools.product((-1, 1), repeat=3):
        sign = np.array([sx, sy, sz], np.float64)
        points.append(centre + sign * inner + dirs * sign * radius)
    return np.concatenate(points)


def proxy_points(anchor, heading_deg: float) -> np.ndarray:
    length, width, height = CAR["lengthM"], CAR["widthM"], CAR["heightM"]
    body = _rounded_box(
        np.array([0.0, 0.0, (BODY["z0"] + BODY["z1"]) / 2]), (length, width, BODY["z1"] - BODY["z0"]), BODY["bevel"]
    )
    cabin = _rounded_box(
        np.array([CABIN["x"], 0.0, (CABIN["z0"] + height) / 2]),
        (CABIN["length"], width - CABIN["inset"], height - CABIN["z0"]),
        CABIN["bevel"],
    )
    wheels = []
    for sx, sy in itertools.product((-1, 1), repeat=2):
        for a in np.linspace(0, 2 * math.pi, 48, endpoint=False):
            for side in (-0.115, 0.115):
                wheels.append(
                    (sx * CAR["wheelbaseM"] / 2 + WHEEL_R * math.cos(a), sy * CAR["trackM"] / 2 + side, WHEEL_R + WHEEL_R * math.sin(a))
                )
    local = np.concatenate([body, cabin, np.array(wheels)])
    return _place(local, anchor, heading_deg)


def _place(local: np.ndarray, anchor, heading_deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg))
    x = anchor[0] + c * local[:, 0] - s * local[:, 1]
    y = anchor[1] + s * local[:, 0] + c * local[:, 1]
    return np.stack([x, y, local[:, 2]], axis=-1)


def contact_points(anchor, heading_deg: float) -> dict[str, np.ndarray]:
    labels = {
        "front_left": (CAR["wheelbaseM"] / 2, CAR["trackM"] / 2),
        "front_right": (CAR["wheelbaseM"] / 2, -CAR["trackM"] / 2),
        "rear_left": (-CAR["wheelbaseM"] / 2, CAR["trackM"] / 2),
        "rear_right": (-CAR["wheelbaseM"] / 2, -CAR["trackM"] / 2),
    }
    out = {}
    for label, (lx, ly) in labels.items():
        out[label] = _place(np.array([[lx, ly, 0.0]]), anchor, heading_deg)[0]
    return out


def footprint(anchor, heading_deg: float) -> np.ndarray:
    corners = [(sx * CAR["lengthM"] / 2, sy * CAR["widthM"] / 2, 0.0) for sx in (-1, 1) for sy in (-1, 1)]
    return _place(np.array(corners), anchor, heading_deg)


def heading_for(view: str, anchor) -> float:
    """Heading (deg, 0 = +x, CCW) of the car's front so the camera sees it from `view`."""
    cam = camera_position()
    to_cam = math.degrees(math.atan2(cam[1] - anchor[1], cam[0] - anchor[0]))
    return to_cam - VIEW_ANGLES[view]


def bbox(anchor, heading_deg: float) -> tuple[float, float, float, float]:
    u, v, _ = project(proxy_points(anchor, heading_deg))
    return float(u.min()), float(v.min()), float(u.max()), float(v.max())


def solve_shot(view: str, target: float) -> dict:
    """Proxy pose: centred horizontally, bbox width = target (fixed camera)."""
    anchor = np.array([0.0, -WALL_DISTANCE_M / 2.0])
    heading = heading_for(view, anchor)
    for _ in range(200):
        heading = heading_for(view, anchor)
        u0, _, u1, _ = bbox(anchor, heading)
        depth = anchor[1] + WALL_DISTANCE_M
        step = np.array([-((u0 + u1) / 2 - 0.5) * depth / FOCAL_NORM, ((u1 - u0) / target - 1.0) * depth])
        anchor = anchor + 0.7 * step
        if np.abs(step).max() < 1e-7:
            break
    heading = heading_for(view, anchor)
    return {"anchor": anchor, "heading": heading}


def layout() -> dict:
    shots = {}
    for key, shot in SHOTS.items():
        pose = solve_shot(shot["view"], shot["target"])
        anchor, heading = pose["anchor"], pose["heading"]
        fp = footprint(anchor, heading)
        shots[key] = {
            "view": shot["view"],
            "anchor": [round(float(anchor[0]), 5), round(float(anchor[1]), 5)],
            "headingDeg": round(float(heading), 4),
            "wallClearanceM": round(float(-fp[:, 1].max()), 3),
            "bbox": [round(x, 5) for x in bbox(anchor, heading)],
        }
    return {
        "camera": {
            "position": [round(float(c), 5) for c in camera_position()],
            "hfovDeg": HFOV_DEG,
            "focalNorm": round(FOCAL_NORM, 6),
            "horizonV": round(HORIZON_V, 6),
            "shiftY": round(SHIFT_Y, 6),
        },
        "car": CAR,
        "room": room(),
        "shots": shots,
    }


def plate_entry(key: str, shot: dict, size: tuple[int, int], room_info: dict, image: str, wall_albedo) -> dict:
    """The plates.json entry of one shot (without the shadow/reflection fields)."""
    anchor = np.array(shot["anchor"], np.float64)
    heading = float(shot["headingDeg"])
    au, av, adepth = project(np.array([anchor[0], anchor[1], 0.0]))
    contacts = {}
    for label, p in contact_points(anchor, heading).items():
        u, v, depth = project(p)
        contacts[label] = {"u": round(float(u), 5), "v": round(float(v), 5), "depthM": round(float(depth), 3), "floor": [round(float(p[0]), 4), round(float(p[1]), 4)]}
    cam = camera_position()
    width = size[0]
    distance = float(math.hypot(anchor[0] - cam[0], anchor[1] - cam[1]))
    return {
        "image": image,
        "size": list(size),
        "camera": {
            "position": [round(float(c), 4) for c in cam],
            "heightM": CAMERA_HEIGHT_M,
            "distanceToAnchorM": round(distance, 4),
            # camera azimuth around the anchor (0 = straight in front of the wall, + = camera to the right)
            "orbitDeg": round(math.degrees(math.atan2(cam[0] - anchor[0], anchor[1] - cam[1])), 3),
            "pitchDownDeg": 0.0,
            "hfovDeg": HFOV_DEG,
            "focalPx": round(FOCAL_NORM * width, 3),
            "focalNorm": round(FOCAL_NORM, 6),
            "shiftY": round(SHIFT_Y, 6),
        },
        "horizonV": round(HORIZON_V, 5),
        "floorHomography": floor_homography(),
        "wallHomography": wall_homography(),
        "anchor": {
            "floor": [round(float(anchor[0]), 5), round(float(anchor[1]), 5)],
            "u": round(float(au), 5),
            "v": round(float(av), 5),
            "depthM": round(float(adepth), 4),
            "widthPerMetre": round(float(FOCAL_NORM / adepth), 6),
        },
        "vehicle": {
            "view": shot["view"],
            "headingDeg": round(heading, 3),
            "targetWidthRatio": round(shot["bbox"][2] - shot["bbox"][0], 3),
            "proxy": dict(CAR),
            "proxyBBox": shot["bbox"],
            "proxyContacts": contacts,
            "wallClearanceM": shot["wallClearanceM"],
        },
        "wall": {"albedo": [round(float(a), 4) for a in wall_albedo], "brandArea": room_info["brandArea"]},
    }


if __name__ == "__main__":
    import json

    print(json.dumps(layout(), indent=2))
