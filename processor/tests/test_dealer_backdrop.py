"""showroom3d/dealer_*.py: the dealer's backdrop as one fixed studio plate (geometry, reflection split)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

SHOWROOM3D = Path(__file__).resolve().parents[1] / "showroom3d"


def _module(name: str):
    if str(SHOWROOM3D) not in sys.path:
        sys.path.insert(0, str(SHOWROOM3D))  # dealer_backdrop imports its siblings
    spec = importlib.util.spec_from_file_location(name, SHOWROOM3D / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def dl():
    return _module("dealer_layout")


@pytest.fixture(scope="module")
def layout(dl):
    return dl.layout()


def _apply(h, x, y):
    p = np.asarray(h) @ np.array([x, y, 1.0])
    return p[0] / p[2], p[1] / p[2]


def test_the_camera_is_level_with_the_measured_junction(dl):
    # the main wall's floor line is one horizontal image row: the bottom of his skirting
    for x in (-4.0, 0.0, 3.0):
        u, v, _ = dl.project(np.array([x, 0.0, 0.0]))
        assert v == pytest.approx(492 / 1086, abs=1e-9)
    # vertical edges stay vertical (no pitch): a wall edge projects to one column
    u0, _, _ = dl.project(np.array([2.0, 0.0, 0.0]))
    u1, _, _ = dl.project(np.array([2.0, 0.0, 3.5]))
    assert u0 == pytest.approx(u1, abs=1e-12)
    # the horizon is at the camera height
    _, v, _ = dl.project(np.array([0.0, 0.0, dl.CAMERA_HEIGHT_M]))
    assert v == pytest.approx(dl.HORIZON_V, abs=1e-12)
    assert dl.SHIFT_Y == pytest.approx((dl.HORIZON_V - 0.5) / dl.ASPECT)


def test_the_homographies_agree_with_the_camera(dl):
    hf, hw = dl.floor_homography(), dl.wall_homography()
    for x, y in ((-2.0, -1.5), (1.3, -4.0), (0.0, -6.5)):
        u, v, _ = dl.project(np.array([x, y, 0.0]))
        assert _apply(hf, x, y) == pytest.approx((u, v), abs=1e-9)
    for x, z in ((-3.0, 0.5), (2.5, 3.0)):
        u, v, _ = dl.project(np.array([x, 0.0, z]))
        assert _apply(hw, x, z) == pytest.approx((u, v), abs=1e-9)
    assert dl.floor_point(*dl.project(np.array([1.0, -3.0, 0.0]))[:2]) == pytest.approx([1.0, -3.0])


def test_the_right_wall_runs_through_his_junction_points(dl, layout):
    room = layout["room"]
    corner, direction = np.array(room["rightWall"]["corner"]), np.array(room["rightWall"]["direction"])
    for px, py in dl.FEATURES_PX["right_junction"]:
        p = dl._solve_column(corner, direction, px / 1448)
        _, v, _ = dl.project(np.array([p[0], p[1], 0.0]))
        assert float(v) * 1086 == pytest.approx(py, abs=0.5)
    assert 20 < room["rightWall"]["angleDeg"] < 45  # turned towards the camera
    # plausible metric scale of what his image shows
    assert 6.5 < room["panelX"][1] - room["panelX"][0] < 8.0  # white brand panel
    assert 0.1 < room["skirtingHeightM"] < 0.25
    assert 0.7 < room["planter"]["topZ"] < 1.1


@pytest.mark.parametrize("key", ["front_left_45", "front_right_45", "rear_left_45", "rear_right_45", "left_side", "right_side", "front", "rear"])
def test_every_shot_turns_the_car_in_front_of_the_same_camera(dl, layout, key):
    shot = layout["shots"][key]
    u0, _, u1, v1 = shot["bbox"]
    assert (u0 + u1) / 2 == pytest.approx(0.5, abs=1e-4)  # centred
    assert u1 - u0 == pytest.approx(dl.SHOTS[key]["target"], abs=1e-4)
    assert shot["wallClearanceM"] >= 1.0  # floor between the car and the wall
    assert v1 <= 0.95
    # the camera sees the car from the shot's view angle
    cam = dl.camera_position()
    anchor = np.array(shot["anchor"])
    to_cam = np.degrees(np.arctan2(cam[1] - anchor[1], cam[0] - anchor[0]))
    angle = (to_cam - shot["headingDeg"] + 180.0) % 360.0 - 180.0
    expected = dl.VIEW_ANGLES[shot["view"]]
    assert angle == pytest.approx(expected, abs=1e-3) or abs(angle) == pytest.approx(abs(expected), abs=1e-3)


def test_the_plates_json_entry_is_consistent(dl, layout, tmp_path):
    from PIL import Image

    from app.showroom.plates import parse_plate

    Image.new("RGB", (240, 180), (200, 200, 200)).save(tmp_path / "plate.jpg")
    for key, shot in layout["shots"].items():
        entry = dl.plate_entry(key, shot, (240, 180), layout["room"], "plate.jpg", (0.7, 0.67, 0.65))
        assert entry["camera"]["pitchDownDeg"] == 0.0
        # the processor's own validation (homographies vs anchor/contacts, junction, ground line) accepts it
        plate = parse_plate(key, entry, tmp_path)
        assert plate.target_width_ratio == pytest.approx(dl.SHOTS[key]["target"], abs=1e-3)
        # the lowest tyre contact is the ground line; only the near bumper corner may hang below it
        assert shot["bbox"][3] - 0.12 <= plate.ground_v <= shot["bbox"][3] + 1e-6
        assert plate.floor_top(240, 180)[0] == pytest.approx(492 / 1086 * 180, abs=0.01)


def test_the_reflection_split_recovers_a_streak_and_keeps_the_wood(dl, layout):
    db = _module("dealer_backdrop")
    width, height = 2400, 1800
    rng = np.random.default_rng(3)
    v = (np.arange(height) + 0.5)[:, None] / height
    u = (np.arange(width) + 0.5)[None, :] / width
    wood = np.array([0.30, 0.15, 0.07])
    light = np.array([0.99, 0.93, 0.81])
    grain = 1.0 + 0.15 * rng.standard_normal((height, width))
    lin = np.broadcast_to(np.array([0.62, 0.6, 0.63]), (height, width, 3)).copy()  # wall
    floor = np.broadcast_to(v > dl.JUNCTION_V + 0.01, (height, width))
    streak = 0.25 * np.exp(-(((u - 0.5) / 0.012) ** 2)) * (v > dl.JUNCTION_V)
    floor_lin = grain[..., None] * wood + streak[..., None] * light
    lin = np.where(floor[..., None], floor_lin, lin)
    # the wall-washer pools (light colour reference) and the corner LED (blue reference)
    lin[: int(0.05 * height), int(0.3 * width) : int(0.6 * width)] = light * 0.95
    lin[int(0.1 * height) : int(0.4 * height), int(0.792 * width) : int(0.8 * width) + 1] = np.array([0.05, 0.3, 0.85])
    srgb = np.clip(db._to_srgb(np.clip(lin, 0, 1)) * 255 + 0.5, 0, 255).astype(np.uint8)
    refl, info = db.split_reflection(srgb, layout["room"])
    assert refl.min() >= 0.0
    h, w = refl.shape[:2]
    row = int(0.75 * h)
    centre = refl[row, w // 2] @ np.array([0.2126, 0.7152, 0.0722])
    beside = refl[row, int(0.3 * w)] @ np.array([0.2126, 0.7152, 0.0722])
    expected = 0.25 * light @ np.array([0.2126, 0.7152, 0.0722])
    assert centre == pytest.approx(expected, rel=0.25)  # the streak is the reflection …
    assert beside < 0.1 * expected  # … the wood is not
    assert refl[: int(dl.JUNCTION_V * h) - 2].max() == 0.0  # nothing on the wall
    assert info["wood"][2] / info["wood"][0] < 0.35  # the wood colour is the saturated floor colour
