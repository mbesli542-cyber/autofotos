"""Placement v2: plate target width, centred, lowest tyre contact on the plate's ground line."""

import numpy as np
import pytest

from app.pipeline.mask import BBox
from app.pipeline.placement import PlacementError, compute_placement, output_size
from app.pipeline.vehicle_geometry import contact_rise
from app.presets import OutputConfig, Placement

LIMITS = Placement(max_height_ratio=0.62, min_margin=0.03, min_target_fraction=0.9)


def _place(bbox, width=3200, height=2400, **kwargs):
    kwargs.setdefault("target_width_ratio", 0.81)
    kwargs.setdefault("ground_v", 0.86)
    return compute_placement(bbox, width, height, LIMITS, **kwargs)


def test_output_is_4_3_within_long_edge_limits():
    out = OutputConfig()
    for bbox_w in (300, 1800, 2600, 3900, 8000):
        w, h = output_size(BBox(0, 0, bbox_w, bbox_w // 3), out, 0.81)
        assert 2400 <= w <= 3200
        assert abs(w / h - 4 / 3) < 0.002
        assert w % 2 == 0 and h % 2 == 0


def test_output_size_keeps_vehicle_at_native_resolution_when_possible():
    w, _ = output_size(BBox(0, 0, 2187, 800), OutputConfig(), 0.81)
    assert w == 2700  # 2187 / 0.81 → scale 1.0


def test_vehicle_gets_the_plate_target_width_centred_on_the_ground_line():
    bbox = BBox(400, 600, 3400, 1800)  # 3000 x 1200
    p = _place(bbox)
    assert p.limited_by == "width"
    assert p.achieved_width_ratio == pytest.approx(0.81, abs=1e-9)
    assert p.target_fraction == pytest.approx(1.0)
    assert p.center_x / 3200 == pytest.approx(0.5, abs=1e-9)
    assert p.bottom / 2400 == pytest.approx(0.86, abs=1e-9)  # contact = bbox bottom by default


def test_the_lowest_tyre_contact_not_the_bbox_bottom_stands_on_the_ground_line():
    bbox = BBox(0, 0, 3000, 1200)
    p = _place(bbox, ground_y_src=1150)  # e.g. a low front spoiler reaches 50 px below the tyres
    _, contact_y = p.to_output(bbox, 0, 1150)
    assert contact_y == pytest.approx(0.86 * 2400)
    assert p.bottom > contact_y


@pytest.mark.parametrize(
    "bbox",
    [BBox(0, 0, 3000, 1200), BBox(10, 20, 1210, 1220), BBox(0, 0, 400, 100), BBox(0, 0, 5000, 600)],
)
def test_aspect_ratio_is_preserved(bbox):
    p = _place(bbox)
    assert p.width / p.height == pytest.approx(bbox.width / bbox.height, rel=1e-9)


def test_tall_vehicle_is_limited_by_height_and_not_cropped():
    p = _place(BBox(0, 0, 1500, 1500), target_width_ratio=0.62, ground_v=0.84)  # front view of a van
    assert p.limited_by in {"height", "margin"}
    assert p.height <= 0.62 * 2400 + 1e-6
    assert p.top >= 0.03 * 2400 - 1e-6
    assert p.target_fraction < 0.9  # the pipeline warns


def test_small_sources_are_scaled_to_the_target_the_gate_decides_about_upscaling():
    p = _place(BBox(0, 0, 400, 150), width=2400, height=1800)
    assert p.limited_by == "width"
    assert p.scale == pytest.approx(0.81 * 2400 / 400)


def test_very_wide_vehicle_stays_inside_margins():
    p = _place(BBox(0, 0, 4000, 800), target_width_ratio=0.98)
    assert p.limited_by == "margin"
    assert p.left >= 0.03 * 3200 - 1e-6
    assert p.left + p.width <= 3200 * 0.97 + 1e-6


def test_empty_bbox_rejected():
    with pytest.raises(ValueError):
        _place(BBox(5, 5, 5, 10))


def test_far_tyres_stay_on_the_floor_below_the_wall_junction():
    bbox = BBox(0, 0, 1000, 500)
    free = _place(bbox)
    steep = _place(bbox, contact_rise=300, floor_y=0.62 * 2400)
    assert steep.limited_by == "floor"
    assert steep.scale < free.scale
    highest_contact = steep.ground_y - 300 * steep.scale
    assert highest_contact >= (0.62 + 0.02) * 2400 - 1e-6
    assert steep.bottom == pytest.approx(0.86 * 2400)  # near tyres still on the ground line


def test_flat_contacts_do_not_change_the_placement():
    bbox = BBox(0, 0, 1000, 500)
    assert _place(bbox, floor_y=0.55 * 2400, contact_rise=10) == _place(bbox)


def test_tall_vehicles_stay_below_the_branding():
    tall = BBox(0, 0, 1000, 900)  # front view of an SUV
    p = _place(tall, target_width_ratio=0.62, ground_v=0.84, min_top=0.27 * 2400)
    assert p.limited_by == "headroom"
    assert p.top >= 0.27 * 2400 - 1e-6  # roof below the logo and texts
    assert p.bottom == pytest.approx(0.84 * 2400)  # still standing on the ground line


def test_no_room_for_the_vehicle_is_an_error_not_a_one_pixel_car():
    with pytest.raises(PlacementError):
        _place(BBox(0, 0, 900, 400), width=2400, height=1800, min_top=0.80 * 1800)
    with pytest.raises(PlacementError):
        _place(BBox(0, 0, 900, 400), width=2400, height=1800, floor_y=0.86 * 1800, contact_rise=10)


def test_far_tyre_hidden_behind_a_lower_sill_is_found():
    """Photo from above: the sill descends towards the middle and sits lower in the
    image than the far rear tyre at the right end (car7-like profile)."""
    x = np.arange(1000)
    bottom = np.full(1000, 640.0)  # near wheels / body at the bottom
    bottom[700:880] = 640 - (x[700:880] - 700) * (262 / 180)  # sill rising towards the rear
    bottom[880:905] = 378  # end of the sill below the wheel arch
    bottom[905:916] = [379, 380, 381, 382, 382, 382, 381, 380, 379, 376, 372]  # far rear tyre
    bottom[916:] = 370 - (x[916:] - 916) * 1.5  # rear corner rising steeply
    rise = contact_rise(bottom, np.ones(1000, bool), vehicle_height=571)
    assert rise == pytest.approx(640 - 382, abs=1)


def test_a_thin_antenna_does_not_push_the_car_down_but_stays_in_the_frame():
    bbox = BBox(0, 0, 1000, 600)  # antenna tip at y=0, solid roof at y=150
    plain = _place(bbox, target_width_ratio=0.62, ground_v=0.84, min_top=0.45 * 2400)
    antenna = _place(bbox, target_width_ratio=0.62, ground_v=0.84, min_top=0.45 * 2400, roof_y_src=150)
    assert antenna.scale > plain.scale
    _, roof_out = antenna.to_output(bbox, 0, 150)
    assert roof_out >= 0.45 * 2400 - 1e-6  # the solid roof stays below the branding
    assert antenna.top >= 0.03 * 2400 - 1e-6  # the antenna tip is never cut by the frame
