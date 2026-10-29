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
from app.pipeline.quality import QualityGateError
from app.presets import BackgroundProvider, ShowroomUnavailableError, load_preset
from tests.conftest import NAVY, encode_jpeg, make_three_quarter_photo


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _navy_mask(rgb: np.ndarray) -> np.ndarray:
    diff = np.abs(rgb.astype(np.int16) - np.array(NAVY, np.int16)).sum(axis=-1)
    return diff < 45


@pytest.fixture
def run(settings, fake_segmenter, vehicle):
    def _run(shot_key="left_side", debug=None, photo=None):
        preset = load_preset(settings, "autoexperten_standard")
        return process_photo(
            encode_jpeg(vehicle[0] if photo is None else photo, 97),
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


def test_vehicle_is_placed_on_its_plate_without_distortion(run, vehicle, settings):
    result = run()
    out = _decode(result.jpeg)
    ys, xs = np.nonzero(_navy_mask(out))
    width, height = result.width, result.height
    # navy body pixels (excludes tyres/glass) – compare against the source body
    src_ys, src_xs = np.nonzero(_navy_mask(vehicle[0]))
    src_aspect = (src_xs.max() - src_xs.min()) / (src_ys.max() - src_ys.min())
    out_aspect = (xs.max() - xs.min()) / (ys.max() - ys.min())
    assert out_aspect == pytest.approx(src_aspect, rel=0.03)

    meta = result.metadata
    assert meta["plateUsed"] == "left_side" and meta["showroomSource"] == "plates"
    plate = BackgroundProvider(settings).plate_set(load_preset(settings, "autoexperten_standard")).plate("left_side")
    p = meta["placement"]
    assert p["limitedBy"] == "width"
    assert p["width"] / width == pytest.approx(plate.target_width_ratio, abs=0.002)  # 0.85 for side views
    assert p["achievedWidthRatio"] == pytest.approx(p["targetWidthRatio"], abs=0.002)
    assert (p["left"] + p["width"] / 2) / width == pytest.approx(0.5, abs=0.002)
    # both tyres stand on the plate's ground line (the proxy car's lowest contact)
    assert len(meta["contacts"]["output"]) == 2
    for _, y in meta["contacts"]["output"]:
        assert y / height == pytest.approx(plate.ground_v, abs=0.003)
    # nothing cropped, roof below the branding
    assert xs.min() > 0 and xs.max() < width - 1 and ys.min() > meta["branding"]["bottom"]


def test_three_quarter_photo_uses_the_shot_plate_and_mirrored_photo_the_partner_plate(run, three_quarter, fake_segmenter):
    result = run(shot_key="front_left_45", photo=three_quarter[0])
    assert result.metadata["plateUsed"] == "front_left_45"
    assert result.metadata["plateSelection"]["switched"] is False
    assert result.metadata["geometry"]["nearEnd"] == "left"
    assert not any(w.code == "plate_mirrored" for w in result.warnings)

    rgb, mask = make_three_quarter_photo(near_end="right")
    fake_segmenter.register(mask)
    mirrored = run(shot_key="front_left_45", photo=rgb)
    assert mirrored.metadata["plateUsed"] == "front_right_45"
    assert mirrored.metadata["plateSelection"]["switched"] is True
    warning = next(w for w in mirrored.warnings if w.code == "plate_mirrored")
    assert "Vorne rechts (45°)" in warning.message


def test_exterior_metadata_describes_plate_contacts_gate_and_placement(run):
    meta = run().metadata
    assert meta["plate"]["camera"]["hfovDeg"] == pytest.approx(66.0)
    assert {"heightM", "distanceToAnchorM", "orbitDeg", "pitchDownDeg"} <= set(meta["plate"]["camera"])
    assert meta["qualityGate"]["passed"] is True
    assert set(meta["qualityGate"]["checks"]) == {"cropped", "upscale", "mask", "perspective", "contacts", "foreign", "parts"}
    assert {"targetWidthRatio", "achievedWidthRatio", "scale", "limitedBy", "groundY"} <= set(meta["placement"])
    assert meta["contacts"]["source"] and meta["geometry"]["contactSpan"] > 0.5
    assert meta["showroomPlaceholder"] is False


def test_grounding_v3_and_reflection_are_reported(run):
    meta = run().metadata
    assert meta["geometry"]["colourEvidence"] is True  # the source photo was used for the tyres
    grounding = meta["grounding"]
    assert grounding["version"] == 3 and grounding["pose"]["matched"] == 2
    assert grounding["poseClamped"] is False and "warnings" not in grounding
    tyres = grounding["tyres"]
    assert len(tyres) == 2 and all(t["width"] > 0 and not t["inferred"] for t in tyres)
    assert all(t["extentPx"][1] > t["extentPx"][0] and 0.2 < t["radiusM"] < 0.5 for t in tyres)
    assert len(grounding["floorLine"]) > 3 and {"tyreAlphaRemoved", "outlineAlphaRemoved"} <= set(grounding["fringe"])
    assert grounding["farFloorDarkening"] < 0.03  # no broad darkening of the foreground
    reflection = meta["reflection"]
    assert reflection["enabled"] is True and reflection["platePass"] is True
    assert 0.0 < reflection["maxStrength"] <= 0.2 + 1e-6


def test_debug_output_shows_the_ground_model(run, tmp_path):
    from app.pipeline.debug import DebugSink

    debug_dir = tmp_path / "debug"
    run(debug=DebugSink(debug_dir))
    assert (debug_dir / "geometry.jpg").is_file() and (debug_dir / "grounding.jpg").is_file()


def test_a_rejected_photo_raises_the_quality_gate_error(run, fake_segmenter):
    from tests.conftest import make_vehicle_photo

    rgb, mask = make_vehicle_photo(2400, 1800, body_ratio=0.3)  # car covers 30 % of a good photo
    fake_segmenter.register(mask)
    with pytest.raises(QualityGateError) as info:
        run(photo=rgb)
    assert info.value.code == "vehicle_too_small"
    assert info.value.user_message == "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren."
    assert info.value.details["checks"]["upscale"]["scale"] > 1.5
    assert info.value.details["plateUsed"] == "left_side"


def test_missing_plates_fail_before_segmentation(run, settings, fake_segmenter):
    (settings.presets_dir / "autoexperten-standard/plates.json").unlink()
    with pytest.raises(ShowroomUnavailableError):
        run()
    assert fake_segmenter.calls == 0


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


def test_shadow_darkens_floor_under_the_vehicle_only(run, settings):
    result = run()
    out = _decode(result.jpeg).astype(np.float32)
    p = result.metadata["placement"]
    ground = int(round(p["groundY"]))
    cx = int(round(p["left"] + p["width"] / 2))
    below = out[ground + 3 : ground + 12, cx - 50 : cx + 50].mean()
    preset = load_preset(settings, "autoexperten_standard")
    plate = BackgroundProvider(settings).get(preset, "left_side", result.width, result.height)
    background = np.asarray(plate.rgb, np.float32)
    assert below < background[ground + 3 : ground + 12, cx - 50 : cx + 50].mean() - 10
    # the wall (above the floor) is never darkened: identical to the branded plate
    top = int(plate.floor_top.min()) - 5
    wall = slice(int(p["top"]) - 40, min(int(p["top"]) - 5, top))
    assert np.abs(out[wall, 20:200] - background[wall, 20:200]).mean() < 1.5


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


# --------------------------------------------------------------- detector, window pass


def _portrait(vehicle_rgb: np.ndarray, vehicle_mask: np.ndarray, height: int):
    """The landscape vehicle photo centred in an upright frame of the same width."""
    from tests.conftest import _outdoor

    h, w = vehicle_mask.shape
    top = (height - h) // 2
    rgb = _outdoor(w, height, 11)
    rgb[top : top + h] = vehicle_rgb
    mask = np.zeros((height, w), np.float32)
    mask[top : top + h] = vehicle_mask
    return rgb, mask, top


def test_an_upright_exterior_photo_is_segmented_in_a_window_around_the_detected_car(settings, vehicle):
    from tests.semantic_fixtures import ColourSegmenter, FakeDetector

    rgb, mask, _ = _portrait(vehicle[0], vehicle[1], 3000)
    detector = FakeDetector().add("car", mask > 0.5, 0.95)
    segmenter = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), tolerance=40, detector=detector)
    preset = load_preset(settings, "autoexperten_standard")
    result = process_photo(
        encode_jpeg(rgb, 97),
        preset=preset,
        segmenter=segmenter,
        backgrounds=BackgroundProvider(settings),
        shot_key="left_side",
    )
    meta = result.metadata
    assert meta["segmentation"]["windowUsed"] is True and meta["segmentation"]["boxSource"] == "detector"
    x0, y0, x1, y1 = meta["segmentation"]["window"]
    assert segmenter.calls == [(y1 - y0, x1 - x0)]  # one BiRefNet-like pass, on the window only
    assert y0 > 0 and y1 < 3000 and y1 - y0 < 2000
    assert meta["mask"]["vehicleDetected"] is True and meta["mask"]["foreignSuspected"] == []
    assert meta["detector"] == "fake-detector" and meta["qualityGate"]["passed"] is True


def test_an_object_glued_to_the_car_that_the_detector_assigns_elsewhere_rejects_the_photo(settings, vehicle):
    from app.pipeline.quality import FOREIGN_OBJECT_MESSAGE
    from tests.semantic_fixtures import ColourSegmenter, FakeDetector

    rgb, gt = vehicle[0].copy(), vehicle[1] > 0.5
    ys, xs = np.nonzero(gt)
    # a navy "neighbour car" rear glued to the back of the car (BiRefNet would merge it)
    other = np.zeros_like(gt)
    other[ys.min() + 60 : ys.min() + 260, xs.max() - 10 : xs.max() + 110] = True
    other &= ~gt
    rgb[other] = NAVY
    detector = FakeDetector().add("car", gt, 0.95).add("car", other, 0.85)
    segmenter = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), tolerance=40, detector=detector)
    with pytest.raises(QualityGateError) as raised:
        process_photo(
            encode_jpeg(rgb, 97),
            preset=load_preset(settings, "autoexperten_standard"),
            segmenter=segmenter,
            backgrounds=BackgroundProvider(settings),
            shot_key="left_side",
        )
    assert raised.value.code == "mask_low_confidence"
    assert raised.value.user_message == FOREIGN_OBJECT_MESSAGE
    details = raised.value.details
    assert details["checks"]["foreign"]["reasons"] == ["foreign_object"]
    assert details["mask"]["foreignSuspected"] and details["mask"]["semantic"]["findings"][0]["labels"] == ["car"]


# --------------------------------------------------------------- missing vehicle parts

MIRROR = (44, 44, 50)  # charcoal mirror housing


class _MirrorSegmenter:
    """BiRefNet on a dark mirror in front of a dark car: the whole frame keeps it (or not),
    every other pass (the window of an upright photo) loses it."""

    name = "colour"

    def __init__(self, detector, full_shape, *, whole_keeps_mirror: bool):
        from tests.semantic_fixtures import ColourSegmenter

        self.detector = detector
        self.calls: list[tuple[int, int]] = []
        self._full_shape = full_shape
        self._keeps = whole_keeps_mirror
        self._with = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), MIRROR, tolerance=40)
        self._without = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), tolerance=40)

    def segment(self, rgb):
        self.calls.append(rgb.shape[:2])
        whole = rgb.shape[:2] == self._full_shape
        return (self._with if whole and self._keeps else self._without).segment(rgb)


def _with_mirror(vehicle):
    """The side-view fixture with a large mirror at the A-pillar (90 × 60 px, ≈ 4 % of the car)."""
    rgb, mask = vehicle[0].copy(), vehicle[1] > 0.5
    mirror = np.zeros_like(mask)
    mirror[878:938, 1440:1530] = True
    rgb[mirror & ~mask] = MIRROR
    return rgb, mask | mirror, mirror & ~mask


def test_a_mirror_the_window_pass_lost_is_taken_from_the_whole_frame_pass(settings, vehicle, tmp_path):
    from tests.semantic_fixtures import FakeDetector

    rgb, gt, _ = _with_mirror(vehicle)
    rgb, gt_portrait, top = _portrait(rgb, gt.astype(np.float32), 3000)
    detector = FakeDetector().add("car", gt_portrait > 0.5, 0.95)
    segmenter = _MirrorSegmenter(detector, rgb.shape[:2], whole_keeps_mirror=True)
    result = process_photo(
        encode_jpeg(rgb, 97),
        preset=load_preset(settings, "autoexperten_standard"),
        segmenter=segmenter,
        backgrounds=BackgroundProvider(settings),
        shot_key="left_side",
        debug=DebugSink(tmp_path / "debug"),
    )
    meta = result.metadata
    window = meta["segmentation"]["window"]
    assert segmenter.calls == [(window[3] - window[1], window[2] - window[0]), rgb.shape[:2]]
    parts = meta["segmentation"]["partsFromWholeFrame"]
    assert len(parts["missing"]) == 1 and len(parts["taken"]) == 1
    x0, y0, x1, y1 = parts["taken"][0]
    assert 1420 <= x0 and x1 <= 1550 and top + 860 <= y0 and y1 <= top + 960
    assert meta["mask"]["partsMissing"] == [] and meta["qualityGate"]["passed"] is True
    mask = cv2.imread(str(tmp_path / "debug" / "mask.png"), cv2.IMREAD_GRAYSCALE)
    assert mask[top + 908, 1485] > 128  # the mirror is in the cut-out
    assert mask[top + 700, 1485] < 128  # nothing else came with it


def test_a_mirror_no_pass_has_rejects_the_photo(settings, vehicle):
    from app.pipeline.quality import VEHICLE_PART_MESSAGE
    from tests.semantic_fixtures import FakeDetector

    rgb, gt, _ = _with_mirror(vehicle)
    portrait, gt_portrait, _ = _portrait(rgb, gt.astype(np.float32), 3000)
    for photo, truth in ((portrait, gt_portrait > 0.5), (rgb, gt)):  # upright (two passes), landscape (one)
        detector = FakeDetector().add("car", truth, 0.95)
        segmenter = _MirrorSegmenter(detector, (-1, -1), whole_keeps_mirror=False)
        with pytest.raises(QualityGateError) as raised:
            process_photo(
                encode_jpeg(photo, 97),
                preset=load_preset(settings, "autoexperten_standard"),
                segmenter=segmenter,
                backgrounds=BackgroundProvider(settings),
                shot_key="left_side",
            )
        assert raised.value.code == "mask_low_confidence" and raised.value.user_message == VEHICLE_PART_MESSAGE
        details = raised.value.details
        assert details["checks"]["parts"]["reasons"] == ["vehicle_part_missing"] and len(details["mask"]["partsMissing"]) == 1
        upright = photo.shape[0] > photo.shape[1]
        assert len(segmenter.calls) == (2 if upright else 1)
        if upright:
            assert details["segmentation"]["partsFromWholeFrame"]["taken"] == []
        else:
            assert "partsFromWholeFrame" not in details["segmentation"]


BASE = (96, 70, 40)  # a mirror base in a colour neither pass confuses with the car or the ground


class _BaseSegmenter:
    """The window pass of an upright photo keeps a mirror's head but loses its base; the whole
    frame keeps the base (or not)."""

    name = "colour"

    def __init__(self, detector, full_shape, *, whole_keeps_base: bool):
        from tests.semantic_fixtures import ColourSegmenter

        self.detector = detector
        self.calls: list[tuple[int, int]] = []
        self._full_shape = full_shape
        self._keeps = whole_keeps_base
        self._with = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), MIRROR, BASE, tolerance=40)
        self._without = ColourSegmenter(NAVY, (60, 70, 80), (22, 22, 24), MIRROR, tolerance=40)

    def segment(self, rgb):
        self.calls.append(rgb.shape[:2])
        whole = rgb.shape[:2] == self._full_shape
        return (self._with if whole and self._keeps else self._without).segment(rgb)


def _with_mirror_base(vehicle):
    """The side-view fixture with a mirror at the A-pillar: a head (90 × 32 px) and below it a
    base (60 × 28 px, 2 % of the car – thinner than a whole part) down to the body."""
    rgb, mask = vehicle[0].copy(), vehicle[1] > 0.5
    head = np.zeros_like(mask)
    head[878:910, 1440:1530] = True
    base = np.zeros_like(mask)
    base[910:938, 1455:1515] = True
    rgb[head & ~mask] = MIRROR
    rgb[base & ~mask] = BASE
    return rgb, mask | head | base, base & ~mask


@pytest.mark.parametrize("whole_keeps_base", [True, False])
def test_a_mirror_base_the_window_pass_lost_is_taken_from_the_whole_frame_or_flagged(
    settings, vehicle, tmp_path, whole_keeps_base
):
    """A PARTLY lost mirror after the window pass: the whole-frame pass runs and its base is taken
    when it has it; when no pass has it the photo is accepted with a hint to check the result."""
    from tests.semantic_fixtures import FakeDetector

    rgb, gt, base = _with_mirror_base(vehicle)
    rgb, gt_portrait, top = _portrait(rgb, gt.astype(np.float32), 3000)
    detector = FakeDetector().add("car", gt_portrait > 0.5, 0.95)
    segmenter = _BaseSegmenter(detector, rgb.shape[:2], whole_keeps_base=whole_keeps_base)
    result = process_photo(
        encode_jpeg(rgb, 97),
        preset=load_preset(settings, "autoexperten_standard"),
        segmenter=segmenter,
        backgrounds=BackgroundProvider(settings),
        shot_key="left_side",
        debug=DebugSink(tmp_path / "debug"),
    )
    meta = result.metadata
    window = meta["segmentation"]["window"]
    assert segmenter.calls == [(window[3] - window[1], window[2] - window[0]), rgb.shape[:2]]
    parts = meta["segmentation"]["partsFromWholeFrame"]
    assert parts["missing"] == [] and len(parts["partial"]) == 1
    x0, y0, x1, y1 = parts["partial"][0]
    assert 1440 <= x0 and x1 <= 1530 and top + 895 <= y0 and y1 <= top + 950
    assert meta["qualityGate"]["passed"] is True and meta["mask"]["partsMissing"] == []
    mask = cv2.imread(str(tmp_path / "debug" / "mask.png"), cv2.IMREAD_GRAYSCALE)
    codes = [w.code for w in result.warnings]
    if whole_keeps_base:
        assert len(parts["taken"]) == 1 and meta["mask"]["partsPartial"] == []
        assert mask[top + 924, 1485] > 128  # the base is in the cut-out
        assert "vehicle_part_uncertain" not in codes
    else:
        assert parts["taken"] == [] and len(meta["mask"]["partsPartial"]) == 1
        assert mask[top + 924, 1485] < 128  # nothing is drawn in …
        assert "vehicle_part_uncertain" in codes  # … the employee is asked to check the result
    assert mask[top + 894, 1485] > 128  # the head
