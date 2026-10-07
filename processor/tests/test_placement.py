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
