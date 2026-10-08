import pytest

from app.pipeline.mask import BBox
from app.pipeline.placement import compute_placement, output_size
from app.presets import OutputConfig, Placement

STANDARD = Placement(center_x=0.5, ground_line=0.84, width_ratio=0.78, max_height_ratio=0.62, min_margin=0.04, max_upscale=2.5)


def test_output_is_4_3_within_long_edge_limits():
    out = OutputConfig()
    for bbox_w in (300, 1800, 2600, 3900, 8000):
        w, h = output_size(BBox(0, 0, bbox_w, bbox_w // 3), out, STANDARD)
        assert 2400 <= w <= 3200
        assert abs(w / h - 4 / 3) < 0.002
        assert w % 2 == 0 and h % 2 == 0


def test_output_size_keeps_vehicle_at_native_resolution_when_possible():
    w, _ = output_size(BBox(0, 0, 2106, 800), OutputConfig(), STANDARD)
    assert w == 2700  # 2106 / 0.78 → scale 1.0


def test_typical_vehicle_is_centred_on_ground_line_with_target_width():
    bbox = BBox(400, 600, 3400, 1800)  # 3000 x 1200
    p = compute_placement(bbox, 3200, 2400, STANDARD)
    assert p.limited_by == "width"
    assert p.width / 3200 == pytest.approx(0.78, abs=1e-6)
    assert p.center_x / 3200 == pytest.approx(0.5, abs=1e-6)
    assert p.bottom / 2400 == pytest.approx(0.84, abs=1e-6)


@pytest.mark.parametrize(
    "bbox",
    [BBox(0, 0, 3000, 1200), BBox(10, 20, 1210, 1220), BBox(0, 0, 400, 100), BBox(0, 0, 5000, 600)],
)
def test_aspect_ratio_is_preserved(bbox):
    p = compute_placement(bbox, 3200, 2400, STANDARD)
    assert p.width / p.height == pytest.approx(bbox.width / bbox.height, rel=1e-9)


def test_tall_vehicle_is_limited_by_height_and_not_cropped():
    bbox = BBox(0, 0, 1500, 1500)  # square-ish (front view of a van)
    p = compute_placement(bbox, 3200, 2400, STANDARD)
    assert p.limited_by in {"height", "margin"}
    assert p.height <= 0.62 * 2400 + 1e-6
    assert p.top >= 0.04 * 2400 - 1e-6


def test_small_source_is_not_upscaled_beyond_limit():
    p = compute_placement(BBox(0, 0, 400, 150), 2400, 1800, STANDARD)
    assert p.limited_by == "upscale"
    assert p.scale == pytest.approx(2.5)


def test_very_wide_vehicle_stays_inside_margins():
    wide = Placement(width_ratio=0.99, min_margin=0.04, max_upscale=10)
    p = compute_placement(BBox(0, 0, 4000, 800), 3200, 2400, wide)
    assert p.left >= 0.04 * 3200 - 1e-6
    assert p.left + p.width <= 3200 * 0.96 + 1e-6


def test_empty_bbox_rejected():
    with pytest.raises(ValueError):
        compute_placement(BBox(5, 5, 5, 10), 3200, 2400, STANDARD)


def test_far_wheels_of_a_steep_three_quarter_view_stay_on_the_floor():
    bbox = BBox(0, 0, 1000, 500)
    free = compute_placement(bbox, 3200, 2400, STANDARD)
    steep = compute_placement(bbox, 3200, 2400, STANDARD, floor_horizon=0.62, contact_rise=300)
    assert steep.limited_by == "horizon"
    assert steep.scale < free.scale
    highest_contact = steep.bottom - 300 * steep.scale
    assert highest_contact >= (0.62 + 0.02) * 2400 - 1e-6  # on the floor, not in front of the wall
    assert steep.bottom == pytest.approx(0.84 * 2400)  # near wheels still on the ground line


def test_flat_contacts_do_not_change_the_placement():
    bbox = BBox(0, 0, 1000, 500)
    assert compute_placement(bbox, 3200, 2400, STANDARD, floor_horizon=0.62, contact_rise=10) == compute_placement(
        bbox, 3200, 2400, STANDARD
    )


def test_tall_vehicles_stay_below_the_branding():
    tall = BBox(0, 0, 1000, 900)  # front view of an SUV
    p = compute_placement(tall, 3200, 2400, STANDARD, min_top=0.27)
    assert p.limited_by == "headroom"
    assert p.top >= 0.27 * 2400 - 1e-6  # roof below the logo and texts
    assert p.bottom == pytest.approx(0.84 * 2400)  # still standing on the ground line


def test_no_room_for_the_vehicle_is_an_error_not_a_one_pixel_car():
    from app.pipeline.placement import PlacementError

    with pytest.raises(PlacementError):
        compute_placement(BBox(0, 0, 900, 400), 2400, 1800, STANDARD, min_top=0.80)
    with pytest.raises(PlacementError):
        compute_placement(BBox(0, 0, 900, 400), 2400, 1800, STANDARD, floor_horizon=0.84, contact_rise=10)


def test_far_tyre_hidden_behind_a_lower_sill_is_found():
    """Photo from above: the sill descends towards the middle and sits lower in the
    image than the far rear tyre at the right end (car7-like profile)."""
    import numpy as np

    from app.pipeline.shadow import contact_rise

    x = np.arange(1000)
    bottom = np.full(1000, 640.0)  # near wheels / body at the bottom
    bottom[700:880] = 640 - (x[700:880] - 700) * (262 / 180)  # sill rising towards the rear
    bottom[880:905] = 378  # end of the sill below the wheel arch
    bottom[905:916] = [379, 380, 381, 382, 382, 382, 381, 380, 379, 376, 372]  # far rear tyre
    bottom[916:] = 370 - (x[916:] - 916) * 1.5  # rear corner rising steeply
    rise = contact_rise(bottom, np.ones(1000, bool), vehicle_height=571)
    assert rise == pytest.approx(640 - 382, abs=1)
