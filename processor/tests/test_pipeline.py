"""End-to-end pipeline tests with a synthetic vehicle and a fake segmenter."""

import io

import cv2
import numpy as np
import pytest
from PIL import Image

from app.pipeline.color import lab_from_linear, srgb_to_linear
from app.pipeline.composite import VehicleLayer, over, place_layer
from app.pipeline.debug import DEBUG_FILE_NAMES, DebugSink
from app.pipeline.pipeline import process_photo
from app.presets import BackgroundProvider, load_preset
from tests.conftest import NAVY, encode_jpeg


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _navy_mask(rgb: np.ndarray) -> np.ndarray:
    diff = np.abs(rgb.astype(np.int16) - np.array(NAVY, np.int16)).sum(axis=-1)
    return diff < 45


@pytest.fixture
def run(settings, fake_segmenter, vehicle):
    def _run(shot_key="front_left_45", debug=None):
        preset = load_preset(settings, "autoexperten_standard")
        return process_photo(
            encode_jpeg(vehicle[0], 97),
            preset=preset,
            segmenter=fake_segmenter,
            backgrounds=BackgroundProvider(settings),
            shot_key=shot_key,
            debug=debug or DebugSink(None),
        )

    return _run


def test_output_is_a_4_3_jpeg_in_listing_resolution(run):
    result = run()
    image = Image.open(io.BytesIO(result.jpeg))
    assert image.format == "JPEG"
    assert image.size == (result.width, result.height)
    assert 2400 <= result.width <= 3200
    assert result.width / result.height == pytest.approx(4 / 3, abs=0.002)
    assert image.info.get("icc_profile")  # sRGB embedded
    assert "exif" not in image.info  # no camera/GPS metadata leaks


def test_vehicle_is_placed_consistently_without_distortion(run, vehicle):
    result = run()
    out = _decode(result.jpeg)
    ys, xs = np.nonzero(_navy_mask(out))
    width, height = result.width, result.height
    # navy body pixels (excludes tyres/glass) – compare against the source body
    src_ys, src_xs = np.nonzero(_navy_mask(vehicle[0]))
    src_aspect = (src_xs.max() - src_xs.min()) / (src_ys.max() - src_ys.min())
    out_aspect = (xs.max() - xs.min()) / (ys.max() - ys.min())
    assert out_aspect == pytest.approx(src_aspect, rel=0.03)

    p = result.metadata["placement"]
    assert p["limitedBy"] == "width"
    assert p["width"] / width == pytest.approx(0.80, abs=0.005)  # preset front_left_45
    assert (p["left"] + p["width"] / 2) / width == pytest.approx(0.5, abs=0.005)
    assert (p["top"] + p["height"]) / height == pytest.approx(0.84, abs=0.005)
    # nothing cropped
    assert xs.min() > 0 and xs.max() < width - 1 and ys.min() > 0


def test_navy_paint_stays_navy(run, vehicle):
    result = run()
    out = _decode(result.jpeg)
    src = vehicle[0]

    def mean_lab(rgb):
        sel = _navy_mask(rgb)
        lin = srgb_to_linear(rgb[sel].reshape(-1, 1, 3))
        return lab_from_linear(lin).reshape(-1, 3).mean(axis=0)

    before, after = mean_lab(src), mean_lab(out)
    hue_b = np.degrees(np.arctan2(before[2], before[1]))
    hue_a = np.degrees(np.arctan2(after[2], after[1]))
    assert abs(hue_a - hue_b) < 2.0  # same hue (blue)
    assert np.hypot(after[1], after[2]) > 0.9 * np.hypot(before[1], before[2])  # not desaturated
    assert after[0] > 0.85 * before[0]  # not turned black
    assert after[2] < -15  # clearly blue
    adj = result.metadata["adjustments"]
    assert abs(adj["exposure_ev"]) <= 0.25 + 1e-6
    assert adj["guard_passed"] is True


def test_shadow_darkens_floor_under_the_vehicle_only(run):
    result = run()
    out = _decode(result.jpeg).astype(np.float32)
    p = result.metadata["placement"]
    ground = int(round(p["top"] + p["height"]))
    cx = int(round(p["left"] + p["width"] / 2))
    below = out[ground + 3 : ground + 12, cx - 50 : cx + 50].mean()
    far = out[ground + 3 : ground + 12, 10:110].mean()  # same floor row, far left
    assert below < far - 10
    wall = out[50:150, 50:150].mean()
    assert wall > 200  # wall untouched by the shadow


def test_interior_shots_are_not_composited(run, vehicle):
    result = run(shot_key="cockpit")
    out = _decode(result.jpeg)
    assert result.metadata["shotKind"] == "interior_passthrough"
    assert out.shape[:2] == vehicle[0].shape[:2]  # original framing kept
    # the synthetic photo is noisy and gets JPEG-encoded twice, so compare the
    # image content (blurred) rather than per-pixel sensor noise
    smooth = lambda rgb: cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), 3)  # noqa: E731
    assert np.abs(smooth(out) - smooth(vehicle[0])).mean() < 2


def test_debug_files_are_written_when_enabled(run, tmp_path):
    sink = DebugSink(tmp_path / "dbg")
    run(debug=sink)
    for name in DEBUG_FILE_NAMES:
        assert (tmp_path / "dbg" / name).is_file(), name
    mask = Image.open(tmp_path / "dbg" / "mask.png")
    original = Image.open(tmp_path / "dbg" / "original.jpg")
    assert mask.size == original.size  # mask has the photo's dimensions
    assert Image.open(tmp_path / "dbg" / "vehicle-transparent.png").mode == "RGBA"


def test_opaque_vehicle_pixels_are_copied_unchanged_at_scale_one():
    rng = np.random.default_rng(1)
    vehicle = rng.random((40, 60, 3), dtype=np.float32)
    alpha = np.ones((40, 60), np.float32)
    layer = VehicleLayer(rgb=vehicle, alpha=alpha, offset_x=0, offset_y=0)
    rgb, a, _ = place_layer(layer, 10, 20, 200, 100)
    background = np.zeros((100, 200, 3), np.float32)
    composite = over(background, rgb, a)
    assert np.array_equal(composite[20:60, 10:70], vehicle)


def test_upscaling_does_not_invent_dark_rims_around_highlights():
    from app.pipeline.composite import resample_layer

    rgb = np.full((40, 40, 3), 0.2, np.float32)
    rgb[18:21, 18:21] = 1.0  # chrome highlight
    layer = VehicleLayer(rgb=rgb, alpha=np.ones((40, 40), np.float32), offset_x=0, offset_y=0)
    up = resample_layer(layer, 2.4)
    assert up.rgb.min() >= 0.2 - 1e-4  # nothing darker than the source neighbourhood
    assert up.rgb.max() <= 1.0 + 1e-6


def test_feathered_edge_pixels_get_the_vehicle_colour_not_black():
    from app.pipeline.composite import feather_layer

    rgb = np.zeros((30, 30, 3), np.float32)
    alpha = np.zeros((30, 30), np.float32)
    rgb[:, :15] = 0.9  # white car, hard edge at x=15
    alpha[:, :15] = 1.0
    layer = feather_layer(VehicleLayer(rgb=rgb, alpha=alpha, offset_x=0, offset_y=0), sigma=0.6)
    grown = (layer.alpha > 0.01) & (alpha == 0)
    assert grown.any()
    assert layer.rgb[grown].min() > 0.8
    assert np.array_equal(layer.rgb[:, :14], rgb[:, :14])  # opaque interior untouched
