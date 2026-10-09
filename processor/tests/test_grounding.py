"""Grounding v3: tyre contact patches, image-space floor line, underbody, calibrated ambient – floor only."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from app.pipeline.color import srgb_to_linear
from app.pipeline.grounding import (
    SHADOW_TINT,
    GroundContact,
    apply_grounding,
    build_ground_model,
    clean_ground_fringe,
    draw_ground_model,
    fit_pose,
    floor_line_model,
    ground_line_without_contacts,
    reference_scale,
)
from app.pipeline.mask import BBox
from app.pipeline.placement import compute_placement
from app.pipeline.shadow import FloorFrame, Window, floor_weight, tyre_gap, tyre_patch_shadow
from app.presets import BackgroundProvider, load_preset

W, H = 1200, 900


def _box_car(placement, *, wheels=(0.2, 0.8), radius=40, clearance=40, overhang=0.0):
    """Box body with round wheels at the placement's ground line (alpha, contacts).
    `overhang`: the body's bottom rises by this many px towards both ends (bumpers)."""
    alpha = np.zeros((H, W), np.float32)
    left, top = int(round(placement.left)), int(round(placement.top))
    right, bottom = int(round(placement.left + placement.width)), int(round(placement.bottom))
    alpha[top : bottom - clearance, left:right] = 1.0
    if overhang:
        for x in range(left, right):
            f = abs((x - left) / max(right - left, 1) - 0.5) * 2.0  # 0 centre … 1 ends
            lift = int(round(overhang * max(0.0, (f - 0.6) / 0.4)))
            if lift:
                alpha[bottom - clearance - lift : bottom - clearance, x] = 0.0
    yy, xx = np.ogrid[:H, :W]
    contacts = []
    for f in wheels:
        cx = left + f * (right - left)
        alpha[(xx - cx) ** 2 + (yy - (bottom - radius)) ** 2 <= radius * radius] = 1.0
        contacts.append(GroundContact(x=cx, y=float(bottom), width=2.0 * radius, side="near"))
    return alpha, contacts


@pytest.fixture
def scene(settings):
    """A box car with two wheels placed on the left_side plate like the pipeline does."""
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
    alpha, contacts = _box_car(placement)
    background = srgb_to_linear(np.asarray(showroom.rgb))
    return preset, showroom, placement, alpha, contacts, background


def _darkening(grounded, background):
    light = background.mean(axis=-1)
    return np.where(light > 1e-4, 1.0 - grounded.mean(axis=-1) / np.maximum(light, 1e-4), 0.0)


def _only_contacts(preset):
    """The preset with only the tyre contact shadows (no underbody, ambient, crease)."""
    shadow = dataclasses.replace(
        preset.shadow, underbody_opacity=0.0, edge_opacity=0.0, ambient_opacity=0.0, crease_opacity=0.0
    )
    return dataclasses.replace(preset, shadow=shadow)


def test_each_tyre_gets_a_compact_near_black_contact_shadow(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    info: dict = {}
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset, info=info)
    assert grounded.shape == background.shape and grounded.dtype == np.float32
    dark = _darkening(grounded, background)
    for c in contacts:
        x, y = int(c.x), int(c.y)
        right_below = dark[y + 1 : y + 3, x - 5 : x + 5].mean()
        assert right_below > 0.75  # near-black right under the tyre (result/plate < 0.25)
        # compact: clearly lighter a little further in front of the tyre
        assert dark[y + 40 : y + 44, x - 5 : x + 5].mean() < right_below - 0.3
    # never pure black: the floor keeps its (warm) colour at the floor-light minimum
    assert np.all(grounded >= background * preset.shadow.min_floor_light - 1e-6)
    assert info["version"] == 3 and len(info["tyres"]) == 2
    assert info["pose"]["matched"] == 2 and info["poseClamped"] is False


def test_contact_core_stays_within_the_tyre_and_has_no_hard_edge(scene):
    """No 'blades': the near-black core never protrudes beyond the tyre's silhouette
    (+3 cm); outside it falls off smoothly (no horizontal edge)."""
    preset, showroom, placement, alpha, contacts, background = scene
    only = _only_contacts(preset)
    info: dict = {}
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, only, info=info)
    dark = _darkening(grounded, background)
    frame = FloorFrame(showroom.plate.floor_h, W, H, showroom.plate.camera.position[:2])
    for c, tyre in zip(contacts, info["tyres"]):
        x0, x1 = tyre["extentPx"]
        y = int(c.y) + 1
        margin = 0.03 * frame.px_per_metre(c.x, c.y)[0]
        row = dark[y, :]
        assert row[int(c.x)] > 0.55  # the patch itself is hidden behind the tyre in a side view
        near = slice(int(x0) - 150, int(x1) + 150)
        local = row[near]
        cols = np.arange(W)[near]
        outside = local[(cols < x0 - margin - 4) | (cols > x1 + margin + 4)]
        assert outside.max() < 0.5  # only the soft halo out there
        # smooth sideways (no blade edge); towards the camera the sidewall falloff (3 cm of floor
        # depth = only ~4 px in this 1200 px test frame) is the steepest part
        patch = dark[y - 2 : y + 60, int(x0) - 80 : int(x1) + 80]
        assert np.abs(np.diff(patch, axis=1)).max() < 0.12
        assert np.abs(np.diff(patch[2:], axis=0)).max() < 0.3


def test_tyre_patch_follows_the_gap_height_along_the_rolling_direction():
    """Along the rolling direction the occlusion follows R − sqrt(R² − s²): a bigger tyre
    keeps the floor dark further out; across the axle it ends at the tread width."""
    floor_h = np.array([[0.25, 0.0, 0.5], [0.0, 0.25, 0.5], [0.0, 0.0, 1.0]])  # 100 px per metre
    frame = FloorFrame(floor_h, 400, 400, (0.0, 10.0))
    assert tyre_gap(np.array([0.0, 0.07]), 0.33, 0.07).max() == 0.0  # flat contact patch
    assert tyre_gap(np.array([0.2]), 0.33, 0.07)[0] == pytest.approx(0.33 - math.sqrt(0.33**2 - 0.13**2))
    window = Window(0, 0, 400, 400)
    small, _ = tyre_patch_shadow(frame, window, (0.0, 0.0), (1.0, 0.0), 0.12, 0.07, 0.28, 0.012, 0.03)
    big, _ = tyre_patch_shadow(frame, window, (0.0, 0.0), (1.0, 0.0), 0.12, 0.07, 0.40, 0.012, 0.03)
    gx, gy = window.grid()
    fx, fy = frame.to_floor(gx, gy)
    along = (np.abs(fy) < 0.01) & (fx > 0.12) & (fx < 0.3)
    assert big[along].sum() > small[along].sum()
    across_in = (np.abs(fx) < 0.01) & (np.abs(fy) < 0.1)
    across_out = (np.abs(fx) < 0.01) & (np.abs(fy) > 0.2) & (np.abs(fy) < 0.3)
    assert small[across_in].min() > 0.9 and small[across_out].max() < 0.01


def test_contact_shadow_depth_follows_the_floor_perspective(scene):
    """A tyre further away (higher in the image) gets a shallower shadow in px."""
    preset, showroom, placement, _, _, background = scene
    only = _only_contacts(preset)
    extents = []
    for ground in (placement.ground_y, placement.ground_y - 90):
        moved = dataclasses.replace(placement, top=placement.top - (placement.ground_y - ground), ground_y=ground)
        alpha, contacts = _box_car(moved)
        grounded = apply_grounding(background, alpha, moved, contacts[:1], showroom, only)
        dark = _darkening(grounded, background)
        x, y = int(contacts[0].x), int(contacts[0].y)
        column = dark[y:, x]
        extents.append(int(np.argmax(column < 0.25)))
    assert extents[1] < extents[0]


def test_nothing_above_the_floor_is_ever_darkened(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset)
    wall = np.arange(H)[:, None] < (showroom.floor_top[None, :] - 1)
    assert np.array_equal(grounded[wall], background[wall])
    # and the darkening is gated by the floor weight everywhere
    dark = _darkening(grounded, background)
    assert np.all(dark[floor_weight(showroom.floor_top, H, W) == 0.0] <= 1e-6)


def test_lower_foreground_is_not_darkened_broadly(scene):
    """The reviewer measured 30–45 % on the whole lower foreground with v1."""
    preset, showroom, placement, alpha, contacts, background = scene
    info: dict = {}
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset, info=info)
    dark = _darkening(grounded, background)
    ground = int(placement.ground_y)
    foreground = dark[min(H - 1, ground + 60) :, int(placement.left) : int(placement.left + placement.width)]
    assert foreground.mean() < 0.05
    assert info["farFloorDarkening"] < 0.03
    assert np.allclose(grounded[-3:, :20], background[-3:, :20], atol=1e-4)  # far from the car


def test_the_floor_under_the_body_is_dark_and_lightens_towards_the_floor_line(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset)
    dark = _darkening(grounded, background)
    x = int((contacts[0].x + contacts[1].x) / 2)
    edge = int(placement.bottom) - 40  # the sill (clearance 40 px)
    assert dark[edge + 2, x] > 0.75  # deep under the car
    assert dark[edge + 2, x] > dark[edge + 30, x] > 0.3  # lighter towards the floor line, still dark


def test_plain_xy_contacts_are_still_accepted(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    plain = [(c.x, c.y) for c in contacts]
    grounded = apply_grounding(background, alpha, placement, plain, showroom, preset)
    dark = _darkening(grounded, background)
    x, y = int(plain[0][0]), int(plain[0][1])
    assert dark[y + 1 : y + 3, x - 3 : x + 3].mean() > 0.6


def test_side_view_overhangs_keep_the_side_line_as_floor_line(scene):
    """Beyond the outermost tyres of a side view the floor line continues the line through
    the contacts – a high bumper hangs over a shadowed floor, it does not float."""
    preset, showroom, placement, _, _, background = scene
    alpha, contacts = _box_car(placement, overhang=60)
    model = build_ground_model(alpha, placement, contacts, showroom)
    end = int(placement.left) + 6 - model.window.x0
    assert model.line[end] == pytest.approx(contacts[0].y, abs=3.0)
    assert model.line[end] - model.edge[end] > 80  # bumper 100 px above the floor line
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset, model=model)
    dark = _darkening(grounded, background)
    x, e, f = int(placement.left) + 6, int(model.edge[end]), int(model.line[end])
    rows = dark[e + 1 : e + 1 + (f - e) // 2, x]
    assert rows.min() > 0.4  # result/plate <= 0.6 for at least half the gap


def test_the_end_facing_the_camera_of_a_three_quarter_view_uses_the_bumper_clearance():
    cols = np.arange(0, 400, dtype=np.float64) + 0.5
    edge = np.full(400, 300.0)
    pxm = np.full(400, 200.0)
    near, far = GroundContact(150.0, 360.0, 40.0), GroundContact(300.0, 330.0, 40.0)  # steep side line
    line = floor_line_model(edge, cols, pxm, [], [(near, far)], 400.0, 0.15)
    assert line[200] == pytest.approx(360.0 - 30.0 * 50.5 / 150.0, abs=1.0)  # between the tyres: the side line
    assert line[20] == pytest.approx(300.0 + 0.15 * 200.0, abs=1.0)  # facing end: outline + clearance
    assert line[380] == pytest.approx(330.0 - 0.2 * 80.5, abs=1.5)  # far end: the side line continues
    flat = floor_line_model(edge, cols, pxm, [], [(GroundContact(150.0, 360.0), GroundContact(300.0, 358.0))], 400.0, 0.15)
    assert flat[20] == pytest.approx(360.0 + 2.0 / 150.0 * 130.0, abs=1.0)  # side view: both ends continue


def test_without_visible_tyres_the_floor_under_the_bumper_is_shadowed(scene):
    """Front/rear views whose bumper hides the tyres: a dark band over the lip clearance,
    then a soft falloff; occlusion pools at the hidden tyres beside the lower corners."""
    preset, showroom, placement, _, _, background = scene
    alpha = np.zeros((H, W), np.float32)
    left, right = int(placement.left), int(placement.left + placement.width)
    bottom = int(placement.ground_y)
    alpha[int(placement.top) : bottom, left:right] = 1.0
    info: dict = {}
    grounded = apply_grounding(background, alpha, placement, [], showroom, preset, info=info)
    dark = _darkening(grounded, background)
    cx = (left + right) // 2
    frame = FloorFrame(showroom.plate.floor_h, W, H, showroom.plate.camera.position[:2])
    lip = int(0.14 * frame.px_per_metre(cx, bottom)[0])
    assert dark[bottom + 1 : bottom + 1 + lip // 2, cx - 20 : cx + 20].min() > 0.4
    assert dark[bottom + lip + 60 :, cx].max() < 0.05
    assert info["pose"]["matched"] == 0 and len(info["hiddenTyresM"]) == 2
    # soft occlusion pools at the hidden tyres (inside the lower corners, behind the lip)
    for hx, hy in info["hiddenTyresM"]:
        px, py = (int(round(float(v))) for v in frame.to_px(hx, hy))
        assert left < px < right and py < bottom + lip
        assert dark[py, px] > 0.3


def test_pose_fit_recovers_the_proxy_pose(scene):
    """Contacts exactly at the proxy's projected contacts: no rotation; the wheelbase gives the
    scale along the car, the scale across stays at the reference."""
    preset, showroom, placement, _, _, _ = scene
    plate = showroom.plate
    frame = FloorFrame(plate.floor_h, W, H, plate.camera.position[:2])
    contacts = []
    for pc in plate.visible_contacts():
        x, y = frame.to_px(*pc.floor)
        contacts.append(GroundContact(x=float(x), y=float(y), width=60.0))
    pose = fit_pose(plate, frame, placement, contacts)
    s0 = reference_scale(plate, placement)
    assert pose.matched == len(contacts)
    assert abs(pose.angle) < 1e-3
    assert pose.scale_along == pytest.approx(min(max(1.0, 0.75 * s0), 1.6 * s0), rel=1e-3)
    assert pose.scale_across == pytest.approx(s0, rel=1e-6)
    qx, qy = pose.apply(*plate.visible_contacts()[0].floor)
    assert (float(qx), float(qy)) == pytest.approx(plate.visible_contacts()[0].floor, abs=1e-3)


def test_implausible_contacts_hit_the_pose_limits_and_are_reported(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    skewed = [contacts[0], dataclasses.replace(contacts[1], y=contacts[1].y - 260)]
    info: dict = {}
    apply_grounding(background, alpha, placement, skewed, showroom, preset, info=info)
    assert info["poseClamped"] is True and "rotation" in info["pose"]["clamped"]


def test_front_rear_ground_line_without_contacts_is_calibrated(settings):
    preset = load_preset(settings, "autoexperten_standard")
    plates = BackgroundProvider(settings).plate_set(preset)
    for key in ("front", "rear"):
        plate = plates.plate(key)
        v = ground_line_without_contacts(plate)
        # above the proxy's lowest point (cars sat ~5 % low), not above the tyre line
        assert plate.ground_v <= v < plate.proxy_bbox[3]


def test_deep_shadows_are_slightly_cooler_like_the_rendered_proxy_shadow(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    grounded = apply_grounding(background, alpha, placement, contacts, showroom, preset)
    x, y = int((contacts[0].x + contacts[1].x) / 2), int(placement.bottom) - 38
    ratio = grounded[y, x] / np.maximum(background[y, x], 1e-6)
    assert SHADOW_TINT[2] < 1.0 and ratio[2] > ratio[0]


# --------------------------------------------------------------------------- matte clean-up


def test_a_light_fringe_under_a_tyre_is_removed_but_the_rubber_stays(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    rgb = np.full((H, W, 3), 0.5, np.float32)
    rgb[alpha > 0] = 0.4  # light grey body …
    c = contacts[0]
    yy, xx = np.ogrid[:H, :W]
    tyre = (xx - c.x) ** 2 + (yy - (c.y - 40)) ** 2 <= 40 * 40
    rgb[tyre] = 0.02  # … dark rubber …
    fringe = tyre & (yy >= c.y - 4)
    rgb[fringe] = 0.8  # … with snow/ground hanging at its bottom
    model = build_ground_model(alpha, placement, contacts, showroom)
    info: dict = {}
    cleaned = clean_ground_fringe(rgb, alpha, model, info=info)
    assert info["tyreAlphaRemoved"] > 0
    assert cleaned[fringe].mean() < 0.35  # the light fringe is (mostly) gone
    rubber = tyre & (yy < c.y - 8)
    assert np.array_equal(cleaned[rubber], alpha[rubber])  # the tyre itself is untouched
    assert np.array_equal(cleaned[: int(c.y) - 60], alpha[: int(c.y) - 60])


def test_a_light_body_is_never_eroded(scene):
    """A white car's sill is as light as the body above it – nothing is removed."""
    preset, showroom, placement, alpha, contacts, background = scene
    rgb = np.full((H, W, 3), 0.85, np.float32)
    model = build_ground_model(alpha, placement, contacts, showroom)
    info: dict = {}
    cleaned = clean_ground_fringe(rgb, alpha, model, info=info)
    assert info["outlineAlphaRemoved"] == 0 and np.array_equal(cleaned, alpha)


def test_the_debug_overlay_shows_the_ground_model(scene):
    preset, showroom, placement, alpha, contacts, background = scene
    model = build_ground_model(alpha, placement, contacts, showroom)
    plate = np.asarray(showroom.rgb)
    overlay = draw_ground_model(plate, model)
    assert overlay.shape == plate.shape and overlay.dtype == np.uint8
    assert np.abs(overlay.astype(int) - plate.astype(int)).sum() > 0
    small = draw_ground_model(plate[::2, ::2].copy(), model, transform=lambda x, y: (x / 2, y / 2))
    assert small.shape == plate[::2, ::2].shape
