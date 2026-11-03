"""EXPERIMENTAL detail backdrop (app/pipeline/detail_backdrop.py): config, gate, composite, pipeline.

Synthetic close-up: a navy car flank from the left edge to the bottom (a tyre at the bottom
standing over open floor, with the photo's own soft contact shadow on the parquet) with an
enclosed side window through which the old background is seen; the fake detector's car
instance covers the window too (like RF-DETR, glass included).
"""

from __future__ import annotations

import io
import json
from dataclasses import replace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import REPO_ROOT
from app.pipeline import detail_backdrop as db
from app.pipeline.debug import DebugSink
from app.pipeline.pipeline import process_photo
from app.presets import (
    BackgroundProvider,
    DetailBackdropConfig,
    PresetConfigError,
    ShowroomUnavailableError,
    detail_backdrop_applies,
    load_preset,
    parse_preset,
)
from tests.conftest import NAVY, FakeSegmenter, encode_jpeg
from tests.semantic_fixtures import FakeDetector

W, H = 1600, 1200


def make_detail_photo(seed: int = 7, *, shadow: bool = True):
    """(rgb, part mask float [0,1] without the window, detector car mask with it, window mask).
    ``shadow``: the parquet next to the part's lower outline is darkened as in a real photo."""
    rng = np.random.default_rng(seed)
    rgb = np.zeros((H, W, 3), np.float32)
    rgb[: int(0.4 * H)] = (205, 208, 212)  # hall wall
    rgb[int(0.4 * H) :] = (150, 110, 70)  # parquet
    rgb += rng.normal(0, 4, rgb.shape)
    car = np.zeros((H, W), np.uint8)
    body = np.array([[0, int(0.15 * H)], [int(0.45 * W), int(0.25 * H)], [int(0.62 * W), int(0.55 * H)],
                     [int(0.62 * W), int(0.8 * H)], [0, int(0.8 * H)]], np.int32)  # fmt: skip
    cv2.fillPoly(car, [body], 255)
    cv2.circle(car, (int(0.35 * W), int(0.8 * H)), int(0.13 * H), 255, -1)  # tyre, bottom at 0.93
    window = np.zeros_like(car)
    cv2.rectangle(window, (int(0.12 * W), int(0.26 * H)), (int(0.34 * W), int(0.4 * H)), 255, -1)
    rgb[car > 0] = NAVY
    tyre = np.zeros_like(car)
    cv2.circle(tyre, (int(0.35 * W), int(0.8 * H)), int(0.13 * H), 255, -1)
    rgb[tyre > 0] = (24, 24, 26)
    rgb[window > 0] = (200, 205, 210)  # the hall seen through the glass – stays original
    if shadow:
        distance = cv2.distanceTransform((car == 0).astype(np.uint8), cv2.DIST_L2, 5)
        below = np.arange(H)[:, None] > int(0.55 * H)
        rgb *= np.where((car == 0) & below, 1.0 - 0.55 * np.clip(1.0 - distance / 90.0, 0.0, 1.0), 1.0)[..., None]
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    part = ((car > 0) & (window == 0)).astype(np.float32)
    return rgb, part, car > 0, window > 0


@pytest.fixture(scope="module")
def detail():
    return make_detail_photo()


def _cfg(**changes) -> DetailBackdropConfig:
    return replace(DetailBackdropConfig(), enabled=True, **changes)


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _preset_data() -> dict:
    return json.loads((REPO_ROOT / "public/presets/autoexperten-standard.json").read_text())


# --------------------------------------------------------------------------- config


def test_detail_backdrop_is_off_by_default():
    preset = parse_preset(_preset_data())
    assert preset.detail_backdrop.enabled is False
    assert not detail_backdrop_applies(preset.detail_backdrop, "wheel_detail")


def test_detail_backdrop_config_is_validated():
    data = _preset_data()
    data["detailBackdrop"] = {
        "enabled": True,
        "shots": ["wheel_detail", "extra"],
        "blur": 0.01,
        "centerX": 0.3,
        "maxHorizonDrop": 0.2,
        "minContactShadow": 0.3,
        "maxShadowClipped": 0.1,
    }
    cfg = parse_preset(data).detail_backdrop
    assert cfg.shots == ("wheel_detail", "extra") and cfg.blur == 0.01 and cfg.center_x == 0.3
    assert (cfg.max_horizon_drop, cfg.min_contact_shadow, cfg.max_shadow_clipped) == (0.2, 0.3, 0.1)
    assert detail_backdrop_applies(cfg, "extra_03") and detail_backdrop_applies(cfg, "wheel_detail")
    assert not detail_backdrop_applies(cfg, "special_detail")
    assert not detail_backdrop_applies(cfg, "cockpit")
    for bad in (
        {"shots": ["cockpit"]},  # interior shots never
        {"shots": ["front"]},
        {"shots": "wheel_detail"},
        {"shots": ["wheel_detail", "wheel_detail"]},
        {"plate": "cockpit"},
        {"blur": 0.2},
        {"minVehicleIou": 0.2},
        {"cropHeight": 0.1},
        {"centerX": 1.5},
        {"enabled": "yes"},
        {"maxHorizonDrop": -0.1},
        {"minContactShadow": 1.5},
        {"maxShadowClipped": 2},
        {"maxUndecided": 0.5},
        {"unknownField": 1},
    ):
        data["detailBackdrop"] = bad
        with pytest.raises(PresetConfigError):
            parse_preset(data)
    data["detailBackdrop"] = []
    with pytest.raises(PresetConfigError):
        parse_preset(data)


# --------------------------------------------------------------------------- mask + gate


def test_part_alpha_keeps_see_through_glass_original(detail):
    rgb, part, _, window = detail
    alpha, info = db.part_alpha(rgb, cv2.GaussianBlur(part, (0, 0), 1.2))
    inner = cv2.erode(window.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    assert alpha[window].min() == 1.0  # the window (and its soft rim) is car: never the plate
    assert alpha[inner].min() == 1.0
    assert alpha[0, -1] == 0.0  # the real background stays background
    assert info["filledHoleFraction"] > 0


def _instances(rgb, *objects):
    detector = FakeDetector()
    for label, mask in objects:
        detector.add(label, mask)
    return detector.detect(rgb)


def test_gate_passes_a_clean_detail(detail):
    rgb, part, car, _ = detail
    coarse = cv2.GaussianBlur(part, (0, 0), 1.2)
    alpha, _ = db.part_alpha(rgb, coarse)
    gate = db.assess(alpha, coarse, _instances(rgb, ("car", car)), _cfg())
    assert gate.passed, gate.as_dict()
    assert gate.metrics["vehicleIou"] > 0.95


def test_gate_rejects_unclear_masks(detail):
    rgb, part, car, _ = detail
    coarse = cv2.GaussianBlur(part, (0, 0), 1.2)
    alpha, _ = db.part_alpha(rgb, coarse)
    cfg = _cfg()

    # no detector / no vehicle
    assert db.assess(alpha, coarse, None, cfg).reason == "vehicle_not_detected"
    person = np.zeros_like(car)
    person[50:300, 1400:1500] = True
    assert db.assess(alpha, coarse, _instances(rgb, ("person", person)), cfg).reason == "vehicle_not_detected"

    # another object against the part (a person standing at the car)
    touching = np.zeros_like(car)
    touching[500:900, int(0.62 * W) - 5 : int(0.62 * W) + 120] = True
    gate = db.assess(alpha, coarse, _instances(rgb, ("car", car), ("person", touching)), cfg)
    assert gate.reason == "object_touching" and gate.metrics["touching"][0]["label"] == "person"
    # … one far away is just background
    far = np.zeros_like(car)
    far[30:200, 1450:1590] = True
    assert db.assess(alpha, coarse, _instances(rgb, ("car", car), ("person", far)), cfg).passed

    # the detector sees much more vehicle than the mask (lost part / merged background)
    bigger = car.copy()
    bigger[:, : int(0.8 * W)] |= np.arange(H)[:, None] > int(0.3 * H)
    assert db.assess(alpha, coarse, _instances(rgb, ("car", bigger)), cfg).reason == "mask_mismatch"

    # glass open to the photo border: detector vehicle where the mask leaves background open
    open_glass = part.copy()
    open_glass[int(0.26 * H) : int(0.4 * H), 0 : int(0.34 * W)] = 0.0
    alpha_open, _ = db.part_alpha(rgb, open_glass)
    gate = db.assess(alpha_open, open_glass, _instances(rgb, ("car", car)), replace(cfg, min_vehicle_iou=0.5))
    assert gate.reason == "edge_undecided", gate.as_dict()

    # a wide soft band along the outline
    soft = cv2.GaussianBlur(part, (0, 0), 25)
    alpha_soft, _ = db.part_alpha(rgb, soft)
    assert db.assess(alpha_soft, soft, _instances(rgb, ("car", car)), cfg).reason == "edge_soft"

    # hardly any background left
    full = np.ones_like(part)
    full[:40, -40:] = 0.0
    assert db.assess(full, full, _instances(rgb, ("car", full > 0)), cfg).reason == "no_background"


def test_gate_rejects_semi_transparent_glass_at_the_border(detail):
    """BiRefNet gives a window at the photo border a soft alpha (0.2–0.9): not "background" by
    the 0.5 threshold, yet the plate would be blended into it – car pixels changed."""
    rgb, part, car, _ = detail
    glass = part.copy()
    region = (slice(int(0.26 * H), int(0.4 * H)), slice(0, int(0.12 * W)))  # a window open to the left edge
    glass[region] = np.linspace(0.2, 0.9, int(0.12 * W), dtype=np.float32)[None, :]
    alpha, _ = db.part_alpha(rgb, glass)
    assert alpha[region].min() < 0.99  # not filled: it reaches the border
    gate = db.assess(alpha, glass, _instances(rgb, ("car", car)), _cfg(max_edge_softness=0.05))
    assert gate.reason == "edge_undecided", gate.as_dict()
    assert gate.metrics["undecidedFraction"] > _cfg().max_undecided
    # the clean detail (enclosed window) has nothing undecided
    coarse = cv2.GaussianBlur(part, (0, 0), 1.2)
    clean, _ = db.part_alpha(rgb, coarse)
    assert db.assess(clean, coarse, _instances(rgb, ("car", car)), _cfg()).metrics["undecidedFraction"] == 0.0


def test_scene_gate_rejects_steep_views_and_unclear_ground_contact(detail, plate):
    rgb, part, _, _ = detail
    alpha, _ = db.part_alpha(rgb, cv2.GaussianBlur(part, (0, 0), 1.2))
    size = (plate.rgb.shape[1], plate.rgb.shape[0])
    plan = db.plan_backdrop(rgb, alpha, size, plate.floor_top, _cfg())
    assert db.assess_backdrop(plan, _cfg()).passed, db.assess_backdrop(plan, _cfg()).metrics

    # the part meets the floor high in the frame (steep top-down view): wrong floor perspective
    high = np.zeros_like(part)
    high[: int(0.3 * H), : int(0.6 * W)] = 1.0
    steep = db.plan_backdrop(rgb, high, size, plate.floor_top, _cfg())
    gate = db.assess_backdrop(steep, _cfg())
    assert gate.reason == "perspective" and gate.metrics["horizonDrop"] > 0.1

    # no contact shadow in the photo: the part would float on the plate floor
    flat_rgb, _, _, _ = make_detail_photo(shadow=False)
    floating = db.plan_backdrop(flat_rgb, alpha, size, plate.floor_top, _cfg())
    assert db.assess_backdrop(floating, _cfg()).reason == "ground_contact"

    # a floor far darker under the part than the plate floor can carry (would get a light halo)
    dark = flat_rgb.copy()
    under = (np.arange(H)[:, None] > int(0.8 * H)) & (part < 0.5)
    dark[under] = (dark[under] * 0.06).astype(np.uint8)
    deep = db.plan_backdrop(dark, alpha, size, plate.floor_top, _cfg())
    gate = db.assess_backdrop(deep, _cfg())
    assert gate.reason == "ground_contact" and gate.metrics["shadowClipped"] > 0.2, gate.metrics


def test_every_message_is_german_and_has_the_skip_code():
    for reason in db.MESSAGES:
        warning = db.skip_warning(reason)
        assert warning.code == "detail_backdrop_skipped"
        assert warning.message.startswith("Detailfoto:")


# --------------------------------------------------------------------------- composite


@pytest.fixture
def plate(settings):
    preset = load_preset(settings, "autoexperten_standard")
    backgrounds = BackgroundProvider(settings)
    key = preset.detail_backdrop.plate
    size = backgrounds.plate_set(preset).plate(key).size
    return backgrounds.get(preset, key, size[0], size[1])


def test_composite_keeps_every_opaque_car_pixel_and_replaces_the_background(detail, plate):
    rgb, part, _, window = detail
    alpha, _ = db.part_alpha(rgb, cv2.GaussianBlur(part, (0, 0), 1.2))
    result = db.compose(rgb, alpha, plate.rgb, plate.floor_top, _cfg())
    out = result.rgb
    assert out.shape == rgb.shape and out.dtype == np.uint8
    core = cv2.erode((alpha >= 0.999).astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    assert core.sum() > 0.3 * core.size
    assert np.array_equal(out[core], rgb[core])  # bit for bit, incl. the hall seen through the glass
    assert np.array_equal(out[window & core], rgb[window & core])
    background = alpha == 0
    assert np.abs(out[background].astype(int) - rgb[background].astype(int)).mean() > 10
    # deterministic (seeded grain)
    again = db.compose(rgb, alpha, plate.rgb, plate.floor_top, _cfg())
    assert np.array_equal(again.rgb, out)


def test_floor_line_stays_above_where_the_part_meets_the_floor(detail, plate):
    rgb, part, _, _ = detail
    alpha, _ = db.part_alpha(rgb, cv2.GaussianBlur(part, (0, 0), 1.2))
    # tyre bottom at 0.93, the flank's lower edge at 0.8 → the configured 0.45 stays
    assert db.compose(rgb, alpha, plate.rgb, plate.floor_top, _cfg()).crop.horizon == pytest.approx(0.45, abs=0.01)
    # a part whose lower edge is high in the frame: the plate's floor line moves above it
    high = np.zeros_like(part)
    high[: int(0.3 * H), : int(0.6 * W)] = 1.0
    result = db.compose(rgb, high, plate.rgb, plate.floor_top, _cfg())
    assert result.crop.horizon <= 0.3 - db.GROUND_MARGIN + 0.01
    assert result.info["crop"]["groundY"] == pytest.approx(0.3, abs=0.01)


def test_crop_follows_the_side_of_the_background(detail, plate):
    rgb, part, _, _ = detail
    alpha, _ = db.part_alpha(rgb, cv2.GaussianBlur(part, (0, 0), 1.2))
    right = db.compose(rgb, alpha, plate.rgb, plate.floor_top, _cfg())
    left = db.compose(rgb[:, ::-1].copy(), alpha[:, ::-1].copy(), plate.rgb, plate.floor_top, _cfg())
    assert not right.info["crop"]["mirrored"] and left.info["crop"]["mirrored"]
    assert left.crop.x0 < right.crop.x0


def test_contact_shadow_is_measured_never_drawn():
    rgb, part, _, _ = make_detail_photo(shadow=False)
    part_bool = part > 0.5
    flat, info = db.contact_shadow(db.srgb_to_linear(rgb), part_bool, 1.0)
    assert info["maxDarkening"] == 0.0 and flat.min() == 1.0  # no shadow in the photo → none added
    assert info["contactDarkening"] < 0.05
    shaded = rgb.copy()
    band = cv2.dilate(part_bool.astype(np.uint8), np.ones((41, 41), np.uint8)) > 0
    floor = band & ~part_bool & (np.arange(H)[:, None] > int(0.5 * H))
    shaded[floor] = (shaded[floor] * 0.4).astype(np.uint8)
    multiplier, info = db.contact_shadow(db.srgb_to_linear(shaded), part_bool, 1.0)
    assert info["maxDarkening"] > 0.2
    assert multiplier[floor].mean() < 0.95
    assert multiplier[50:150, 1450:1550].min() == pytest.approx(1.0)  # far from the part: untouched
    assert info["contactDarkening"] > 0.4


def test_contact_shadow_wide_along_the_row_is_not_cancelled():
    """A cast shadow that covers most of a row (under a sill) is measured against the unshadowed
    floor, not against its own row mean (which would leave only a narrow rim = a drop shadow)."""
    rgb, part, _, _ = make_detail_photo(shadow=False)
    part_bool = part > 0.5
    shaded = rgb.copy()
    rows = slice(int(0.8 * H) + 2, int(0.9 * H))
    cast = np.zeros((H, W), bool)
    cast[rows, : int(0.9 * W)] = True
    cast &= ~part_bool
    shaded[cast] = (shaded[cast] * 0.45).astype(np.uint8)
    multiplier, _ = db.contact_shadow(db.srgb_to_linear(shaded), part_bool, 1.0)
    # under the flank, well away from the tyre and from the outline itself: still the cast shadow
    probe = (slice(int(0.83 * H), int(0.86 * H)), slice(int(0.52 * W), int(0.58 * W)))
    assert multiplier[probe].mean() < 0.7

# --------------------------------------------------------------------------- pipeline


@pytest.fixture
def run_detail(settings, detail):
    rgb, part, car, _ = detail

    def _run(
        *, enabled=True, shot_key="wheel_detail", detector_objects=(("car", None),), segmenter=None, debug=None, progress=None
    ):
        preset = load_preset(settings, "autoexperten_standard")
        if enabled:
            preset = replace(preset, detail_backdrop=replace(preset.detail_backdrop, enabled=True))
        detector = FakeDetector()
        for label, mask in detector_objects:
            detector.add(label, car if mask is None else mask)
        seg = segmenter or FakeSegmenter({part.shape: part})
        seg.detector = detector
        result = process_photo(
            encode_jpeg(rgb, 97),
            preset=preset,
            segmenter=seg,
            backgrounds=BackgroundProvider(settings),
            shot_key=shot_key,
            debug=debug or DebugSink(None),
            **({"progress": progress} if progress else {}),
        )
        return result, seg

    return _run


def _smooth(rgb):
    return cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), 3)


def test_pipeline_default_keeps_detail_shots_unchanged(run_detail, detail):
    result, seg = run_detail(enabled=False)
    assert result.metadata["shotKind"] == "interior_passthrough"
    assert "detailBackdrop" not in result.metadata
    assert seg.calls == 0 and result.warnings == []
    assert np.abs(_smooth(_decode(result.jpeg)) - _smooth(detail[0])).mean() < 2


def test_pipeline_interior_shots_never_get_the_backdrop(run_detail):
    result, seg = run_detail(shot_key="cockpit")
    assert result.metadata["shotKind"] == "interior_passthrough" and seg.calls == 0


def test_pipeline_composites_a_clean_detail(run_detail, detail, tmp_path):
    rgb, part, _, _ = detail
    result, _ = run_detail(debug=DebugSink(tmp_path / "dbg"))
    meta = result.metadata
    assert meta["shotKind"] == "detail_backdrop" and result.warnings == []
    assert meta["detailBackdrop"]["applied"] and meta["detailBackdrop"]["gate"]["passed"]
    out = _decode(result.jpeg)
    assert out.shape == rgb.shape
    core = cv2.erode((part > 0.5).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    background = cv2.erode((part < 0.5).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    diff = np.abs(_smooth(out) - _smooth(rgb)).mean(axis=-1)
    assert diff[core].mean() < 2  # the part is the original (up to JPEG)
    assert diff[background].mean() > 10  # the hall is gone
    for name in ("mask.png", "background.jpg", "final.jpg", "metadata.json"):
        assert (tmp_path / "dbg" / name).is_file()


def test_pipeline_progress_never_steps_back(run_detail):
    for objects in ((("car", None),), ()):  # composited, skipped early
        steps: list[float] = []
        run_detail(detector_objects=objects, progress=lambda p, _s, steps=steps: steps.append(p))
        assert steps == sorted(steps) and steps[-1] == 1.0, steps


def test_pipeline_leaves_unclear_details_original_with_a_hint(run_detail, detail):
    rgb, _, car, _ = detail
    touching = np.zeros_like(car)
    touching[500:900, int(0.62 * W) - 5 : int(0.62 * W) + 120] = True
    result, _ = run_detail(detector_objects=(("car", None), ("person", touching)))
    assert result.metadata["shotKind"] == "detail_passthrough"
    assert result.metadata["detailBackdrop"]["reason"] == "object_touching"
    assert [w.code for w in result.warnings] == ["detail_backdrop_skipped"]
    assert "anderes Objekt" in result.warnings[0].message
    assert np.abs(_smooth(_decode(result.jpeg)) - _smooth(rgb)).mean() < 2  # nothing drawn in

    # no vehicle detected: skipped before the (expensive) segmentation
    result, seg = run_detail(detector_objects=())
    assert result.metadata["detailBackdrop"]["reason"] == "vehicle_not_detected" and seg.calls == 0

    # segmentation without a confident object
    result, _ = run_detail(segmenter=FakeSegmenter({}))
    assert result.metadata["detailBackdrop"]["reason"] == "segmentation_failed"
    assert result.warnings[0].code == "detail_backdrop_skipped"


def test_pipeline_leaves_a_floating_part_original(settings):
    """No contact shadow in the photo: the part would float on the plate floor → the original
    with a hint; nothing (no shadow) is drawn in to make up for it."""
    rgb, part, car, _ = make_detail_photo(shadow=False)
    preset = load_preset(settings, "autoexperten_standard")
    preset = replace(preset, detail_backdrop=replace(preset.detail_backdrop, enabled=True))
    seg = FakeSegmenter({part.shape: part})
    seg.detector = FakeDetector().add("car", car)
    result = process_photo(
        encode_jpeg(rgb, 97), preset=preset, segmenter=seg, backgrounds=BackgroundProvider(settings), shot_key="wheel_detail"
    )
    detail = result.metadata["detailBackdrop"]
    assert result.metadata["shotKind"] == "detail_passthrough" and detail["reason"] == "ground_contact"
    assert detail["gate"]["contactDarkening"] < 0.15 and "crop" in detail
    assert "Bodenkontakt" in result.warnings[0].message
    json.dumps(result.metadata)  # serialisable
    assert np.abs(_smooth(_decode(result.jpeg)) - _smooth(rgb)).mean() < 2


def test_pipeline_without_showroom_plates_keeps_the_original(settings, detail, monkeypatch):
    rgb, part, car, _ = detail

    def broken(self, preset):
        raise ShowroomUnavailableError("missing")

    monkeypatch.setattr(BackgroundProvider, "plate_set", broken)
    preset = load_preset(settings, "autoexperten_standard")
    preset = replace(preset, detail_backdrop=replace(preset.detail_backdrop, enabled=True))
    seg = FakeSegmenter({part.shape: part})
    seg.detector = FakeDetector().add("car", car)
    result = process_photo(
        encode_jpeg(rgb, 97), preset=preset, segmenter=seg, backgrounds=BackgroundProvider(settings), shot_key="wheel_detail"
    )
    assert result.metadata["detailBackdrop"]["reason"] == "showroom_unavailable"
    assert result.warnings[0].code == "detail_backdrop_skipped" and seg.calls == 0
