"""Floor reflection: the plate's own reflection is removed where the car blocks it, the mirrored
ORIGINAL vehicle pixels are added as a colour-neutral specular term – floor only, configurable."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.pipeline.color import srgb_to_linear
from app.pipeline.grounding import GroundContact, build_ground_model
from app.pipeline.mask import BBox
from app.pipeline.placement import compute_placement
from app.pipeline.reflection import apply_reflection
from app.presets import BackgroundProvider, ReflectionConfig, load_preset

W, H = 1200, 900
CLEARANCE = 30


def _car(placement, body):
    """Box body (colour `body`, CLEARANCE px above the floor) with two dark wheels."""
    left, top = int(round(placement.left)), int(round(placement.top))
    right, bottom = int(round(placement.left + placement.width)), int(round(placement.bottom))
    alpha = np.zeros((H, W), np.float32)
    rgb = np.zeros((H, W, 3), np.float32)
    alpha[top : bottom - CLEARANCE, left:right] = 1.0
    rgb[top : bottom - CLEARANCE, left:right] = body
    yy, xx = np.ogrid[:H, :W]
    contacts = []
    for f in (0.2, 0.8):
        cx = left + f * (right - left)
        wheel = (xx - cx) ** 2 + (yy - (bottom - 45)) ** 2 <= 45 * 45
        alpha[wheel] = 1.0
        rgb[wheel] = (0.02, 0.02, 0.02)
        contacts.append(GroundContact(x=cx, y=float(bottom), width=90.0))
    return rgb, alpha, contacts


@pytest.fixture
def scene(settings):
    preset = load_preset(settings, "autoexperten_standard")
    showroom = BackgroundProvider(settings).get(preset, "left_side", W, H)
    plate = showroom.plate
    placement = compute_placement(
        BBox(0, 0, 1000, 300),
        W,
        H,
        preset.placement,
        target_width_ratio=plate.target_width_ratio,
        ground_v=plate.ground_v,
    )
    background = srgb_to_linear(np.asarray(showroom.rgb))
    return preset, showroom, placement, background


def _run(scene, body, cfg=None, **kwargs):
    preset, showroom, placement, background = scene
    rgb, alpha, contacts = _car(placement, body)
    info: dict = {}
    out = apply_reflection(
        background, rgb, alpha, contacts, showroom, placement, cfg or preset.reflection, info=info,
        background_linear=background, **kwargs,
    )  # fmt: skip
    return out, info, rgb, alpha, contacts


def test_the_plate_reflection_is_removed_under_the_car_and_in_its_mirror_image(scene):
    """The synthetic plate has a bluish LED streak on the floor (tests/plate_fixtures.py): under a
    BLACK car (nothing to reflect) it disappears below the car, beside the car it stays."""
    preset, showroom, placement, background = scene
    out, info, _, alpha, contacts = _run(scene, (0.0, 0.0, 0.0))
    assert info["platePass"] is True and info["removedMax"] > 0.1
    own = showroom.reflection
    y = int(contacts[0].y) + 60
    left, right = int(placement.left), int(placement.left + placement.width)
    streak = left + int(np.argmax(own[y, left:right, 2]))
    assert own[y, streak, 2] > 0.15  # the streak crosses the car's columns
    before = background[y, streak, 2] - background[y, streak - 40, 2]
    after = out[y, streak, 2] - out[y, streak - 40, 2]
    assert before > 0.1 and abs(after) < 0.25 * before  # the streak ends where the car begins
    # far from the car the plate is untouched
    assert np.array_equal(out[:, : max(0, left - 200)], background[:, : max(0, left - 200)])


def test_the_car_reflection_is_additive_and_colour_neutral(scene):
    grey, info, _, _, contacts = _run(scene, (0.6, 0.6, 0.6))
    black, *_ = _run(scene, (0.0, 0.0, 0.0))
    added = grey - black  # same removal, only the specular term differs
    y0 = int(contacts[0].y) + 2 * CLEARANCE + 6  # below the mirrored under-car band
    x = int((contacts[0].x + contacts[1].x) / 2)
    term = added[y0 : y0 + 10, x - 20 : x + 20].reshape(-1, 3)
    assert term.mean() > 0.03  # a light car shows a light mirrored body on the floor
    spread = term.mean(axis=0)
    assert spread.max() - spread.min() < 0.1 * spread.mean()  # neutral: not tinted by the floor
    k = info["reflectance"]
    assert 0.1 <= k[0] <= k[1] <= 0.2  # the plate's measured reflectance (plate_fixtures.REFLECTANCE)


def test_the_band_between_the_outline_and_its_mirror_stays_dark(scene):
    """The floor under the body and its mirror image reflect the dark underside: no light there."""
    grey, _, _, _, contacts = _run(scene, (0.9, 0.9, 0.9))
    black, *_ = _run(scene, (0.0, 0.0, 0.0))
    x = int((contacts[0].x + contacts[1].x) / 2)
    edge = int(contacts[0].y) - CLEARANCE
    band = (grey - black)[edge + 2 : int(contacts[0].y) + CLEARANCE - 8, x - 20 : x + 20]
    assert np.abs(band).max() < 0.02


def test_the_reflection_is_floor_only_and_never_over_the_car(scene):
    preset, showroom, placement, background = scene
    out, _, _, alpha, contacts = _run(scene, (0.8, 0.8, 0.8))
    above_floor = np.arange(H)[:, None] < (showroom.floor_top[None, :] - 1)
    assert np.array_equal(out[above_floor], background[above_floor])
    covered = alpha >= 1.0
    assert np.array_equal(out[covered], background[covered])  # the car is composited over it unchanged


def test_the_reflection_blurs_with_the_distance_from_the_floor(scene):
    preset, showroom, placement, background = scene
    rgb, alpha, contacts = _car(placement, (0.0, 0.0, 0.0))
    x = int((contacts[0].x + contacts[1].x) / 2)
    edge = int(contacts[0].y) - CLEARANCE
    for height in (8, 60):  # thin bright vertical stripes low and high on the body
        rgb[edge - height - 4 : edge - height, x - 3 + height : x + 3 + height] = 1.0
    model = build_ground_model(alpha, placement, contacts, showroom)
    out = apply_reflection(background, rgb, alpha, contacts, showroom, placement, preset.reflection, model=model)
    black = apply_reflection(
        background, np.zeros_like(rgb), alpha, contacts, showroom, placement, preset.reflection, model=model
    )
    diff = (out - black).mean(axis=-1)
    axis = model.line[x - model.window.x0]
    widths = []
    for height in (8, 60):
        y = int(round(2 * axis - (edge - height - 2)))
        row = diff[y, x - 60 + height : x + 60 + height]
        widths.append(int((row > 0.5 * row.max()).sum()))
    assert widths[1] > widths[0]


def test_the_reflection_can_be_disabled_or_weakened(scene):
    preset, showroom, placement, background = scene
    off = dataclasses.replace(preset.reflection, enabled=False)
    rgb, alpha, contacts = _car(placement, (0.8, 0.8, 0.8))
    assert apply_reflection(background, rgb, alpha, contacts, showroom, placement, off) is background
    weak, *_ = _run(scene, (0.8, 0.8, 0.8), cfg=ReflectionConfig(strength=0.5))
    strong, *_ = _run(scene, (0.8, 0.8, 0.8), cfg=ReflectionConfig(strength=1.0))
    black, *_ = _run(scene, (0.0, 0.0, 0.0))
    assert np.abs(weak - black).sum() < np.abs(strong - black).sum()


def test_plates_without_a_reflection_pass_use_the_default_reflectance(scene):
    preset, showroom, placement, background = scene
    bare = dataclasses.replace(
        showroom, reflection=None, plate=dataclasses.replace(showroom.plate, reflection=None, reflectance=None)
    )
    rgb, alpha, contacts = _car(placement, (0.8, 0.8, 0.8))
    info: dict = {}
    out = apply_reflection(background, rgb, alpha, contacts, bare, placement, preset.reflection, info=info)
    assert info["platePass"] is False
    assert info["reflectance"] == [preset.reflection.default_reflectance] * 2
    assert np.all(out >= background - 1e-6)  # nothing removed, only the car's reflection added
