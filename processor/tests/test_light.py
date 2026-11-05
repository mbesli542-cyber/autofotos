import json
from dataclasses import fields, replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.pipeline.color import lab_from_linear, luminance, srgb_to_linear
from app.pipeline.light import (
    HARD_LIMITS,
    STUDIO_HARD_LIMITS,
    StudioLight,
    StudioPlan,
    VehicleCorrection,
    apply_correction,
    apply_studio_light,
    effective_limits,
    estimate_correction,
    find_paint,
    guarded_correction,
    analyse_paint,
    cct_duv,
    highlight_slope,
    lift_allowance,
    linear_from_cct_duv,
    measure_studio,
    paint_guard,
    paint_stats,
    studio_light_correction,
    tone_curve,
)
from app.presets import STUDIO_LIGHT_RANGES, PresetConfigError, StudioLightConfig, VehicleAdjustments, parse_preset


def test_configuration_cannot_exceed_hard_limits():
    aggressive = VehicleAdjustments(exposure_ev_limit=3.0, contrast_limit=1.0, white_balance_limit=0.5, light_wrap=0.9)
    limits = effective_limits(aggressive)
    assert limits["exposure_ev"] == HARD_LIMITS["exposure_ev"]
    assert limits["contrast"] == HARD_LIMITS["contrast"]
    assert limits["white_balance"] == HARD_LIMITS["white_balance"]
    assert limits["light_wrap"] == HARD_LIMITS["light_wrap"]


def test_dark_photo_gets_at_most_the_configured_exposure_boost(vehicle):
    rgb, mask = vehicle
    dark = (rgb.astype(np.float32) * 0.25).astype(np.uint8)
    plan = estimate_correction(dark, mask, VehicleAdjustments(exposure_ev_limit=0.25))
    assert 0 < plan.exposure_ev <= 0.25 + 1e-9


def test_exposure_does_not_change_navy_hue_or_saturation():
    navy = srgb_to_linear(np.full((50, 50, 3), (24, 34, 78), np.uint8))
    alpha = np.ones((50, 50), np.float32)
    out, applied = guarded_correction(navy, alpha, VehicleCorrection(exposure_ev=0.25))
    assert applied.guard_passed and applied.reverted is None
    assert applied.hue_shift_p95_deg < 0.5
    assert abs(applied.saturation_change) < 1e-3
    assert out.mean() > navy.mean()  # slightly brighter, still navy


def test_colour_guard_reverts_a_white_balance_that_would_change_the_paint():
    red = srgb_to_linear(np.full((60, 60, 3), (150, 40, 40), np.uint8))
    alpha = np.ones((60, 60), np.float32)
    # absurd gains (beyond any limit) – the guard must refuse them
    bad = VehicleCorrection(exposure_ev=0.0, wb_gains=(0.7, 1.2, 1.4))
    out, applied = guarded_correction(red, alpha, bad)
    assert applied.reverted in {"white_balance", "all"}
    assert np.allclose(applied.wb_gains, (1.0, 1.0, 1.0))
    assert np.allclose(out, red, atol=1e-6)


def test_exposure_keeps_every_pixel_ratio_and_does_not_clip_white_paint():
    rng = np.random.default_rng(3)
    white_paint = np.clip(0.75 + 0.2 * rng.random((80, 80, 1)), 0, 1).repeat(3, axis=2).astype(np.float32)
    white_paint[..., 2] *= 0.97  # slightly warm white
    out = apply_correction(white_paint, VehicleCorrection(exposure_ev=0.25))
    ratio = out / white_paint
    assert np.allclose(ratio[..., 0], ratio[..., 1], atol=1e-5)  # same factor per channel
    assert np.allclose(ratio[..., 0], ratio[..., 2], atol=1e-5)
    assert (out.max(axis=-1) >= 0.995).mean() < 0.002  # highlights keep their shading
    # brighter input stays brighter (no flattening of the gradient)
    order_in = np.argsort(white_paint[..., 1].ravel())
    assert np.all(np.diff(out[..., 1].ravel()[order_in]) >= -1e-6)


def test_hue_guard_looks_at_the_tail_not_the_median():
    from app.pipeline.light import colour_shift

    before = srgb_to_linear(np.full((1000, 3), (120, 190, 250), np.uint8)[:, None, :]).reshape(-1, 3)
    after = before.copy()
    after[:300] = srgb_to_linear(np.full((300, 3), (130, 205, 255), np.uint8)[:, None, :]).reshape(-1, 3)
    hue_p95, _, _ = colour_shift(before, after)
    assert hue_p95 > 1.5  # 30 % of the paint changed hue – must be caught


def test_white_balance_gains_respect_the_limit_after_normalisation():
    rng = np.random.default_rng(5)
    photo = np.clip(rng.normal(128, 30, (300, 400, 3)), 0, 255).astype(np.uint8)
    photo[..., 1] = np.clip(photo[..., 1].astype(int) + 25, 0, 255)  # strong green cast
    gray = rng.integers(60, 200, (300, 400, 1)).astype(np.uint8)
    photo[100:250, 100:300] = np.concatenate([gray, np.clip(gray.astype(int) + 12, 0, 255).astype(np.uint8), gray], -1)[100:250, 100:300]
    alpha = np.zeros((300, 400), np.float32)
    alpha[100:250, 100:300] = 1.0
    for limit in (0.02, 0.05, 0.5):
        plan = estimate_correction(photo, alpha, VehicleAdjustments(white_balance_limit=limit))
        effective = min(limit, HARD_LIMITS["white_balance"])
        gains = np.asarray(plan.wb_gains)
        assert np.max(np.abs(gains - 1.0)) <= effective + 1e-6
        assert float(np.dot(gains, [0.2126, 0.7152, 0.0722])) == pytest.approx(1.0, abs=1e-6)


# ── studio light match (vehicleAdjustments.studioLight, the dealer's decision) ──────────────

PRESETS = Path(__file__).resolve().parents[2] / "public/presets"
STANDARD = json.loads((PRESETS / "autoexperten-standard.json").read_text(encoding="utf-8"))
HAENDLER = json.loads((PRESETS / "variants/autoexperten-haendler.json").read_text(encoding="utf-8"))
#: The bright studio: key 0.49, white 0.69, warm neutral (like the dealer plate near the car).
STUDIO = StudioLight(white=0.69, key=0.49, neutral=(1.06, 0.99, 0.97))


def _cfg(**changes) -> StudioLightConfig:
    base = parse_preset(HAENDLER).adjustments.studio_light
    return replace(base, **changes)


def _car(paint_srgb, *, size=(240, 360), seed=1, reflections=True):
    """A 'car' layer: uniform paint panels (with soft shading), black tyres, grey glass and a few
    bright reflections / chrome. Returns (linear rgb, alpha)."""
    rng = np.random.default_rng(seed)
    h, w = size
    srgb = np.zeros((h, w, 3), np.float32)
    shade = np.linspace(0.9, 1.1, w, dtype=np.float32)[None, :, None]
    srgb[:] = np.asarray(paint_srgb, np.float32) * shade
    srgb[: h // 4, w // 4 : 3 * w // 4] = (60, 66, 72)  # glass
    for cx in (w // 4, 3 * w // 4):  # tyres
        cv2.circle(srgb, (cx, h - 25), 24, (20, 20, 21), -1)
    if reflections:
        srgb[h // 2 : h // 2 + 6, :] = (235, 238, 240)  # chrome strip
        srgb[h // 3 : h // 3 + 10, w // 3 : w // 3 + 40] = (250, 250, 250)  # sky reflection
    srgb += rng.normal(0, 1.0, srgb.shape).astype(np.float32)
    rgb = srgb_to_linear(np.clip(srgb, 0, 255).astype(np.uint8))
    alpha = np.ones((h, w), np.float32)
    alpha[:3], alpha[-3:], alpha[:, :3], alpha[:, -3:] = 0, 0, 0, 0
    return rgb, alpha


def test_the_shipped_preset_carries_the_studio_light_match():
    """The dealer's decision: the shipped look (his studio, "Schatten mittel") lights the car with the
    guarded studio light match – the same block as the dealer variants; without the block a preset
    keeps the conservative correction."""
    assert STANDARD["vehicleAdjustments"] == HAENDLER["vehicleAdjustments"]
    assert parse_preset(STANDARD).adjustments.studio_light.enabled is True
    conservative = {**STANDARD, "vehicleAdjustments": {
        k: v for k, v in STANDARD["vehicleAdjustments"].items() if k != "studioLight"
    }}  # fmt: skip
    assert parse_preset(conservative).adjustments.studio_light.enabled is False


def test_the_dealer_variant_writes_out_every_studio_light_field():
    block = HAENDLER["vehicleAdjustments"]["studioLight"]
    names = {f.name for f in fields(StudioLightConfig)}
    assert len(block) == len(names) and block["enabled"] is True
    config = parse_preset(HAENDLER).adjustments.studio_light
    for name, (low, high) in STUDIO_LIGHT_RANGES.items():
        assert low <= getattr(config, name) <= high, name


@pytest.mark.parametrize(
    "change",
    [
        {"maxExposureEv": 1.5},  # beyond the dealer's limit
        {"maxWhiteBalance": 0.2},
        {"contrast": 0.5},
        {"maxHueShiftDeg": 5.0},  # the paint guard can only be made stricter
        {"maxSaturationChange": 0.3},
        {"maxBlackLift": 8.0},
        {"maxNewClipped": 0.05},
        {"whiteLimit": 0.8},
        {"highlightSlope": 0.3},
        {"enabled": "yes"},
        {"exposureStrenght": 0.5},  # typo
    ],
)
def test_invalid_studio_light_values_are_configuration_errors(change):
    data = json.loads(json.dumps(HAENDLER))
    data["vehicleAdjustments"]["studioLight"].update(change)
    with pytest.raises(PresetConfigError):
        parse_preset(data)


def test_a_studio_light_block_with_missing_fields_is_refused():
    data = json.loads(json.dumps(HAENDLER))
    del data["vehicleAdjustments"]["studioLight"]["maxBlackLift"]
    with pytest.raises(PresetConfigError, match="maxBlackLift"):
        parse_preset(data)


def test_the_tone_mapping_is_global_monotone_and_keeps_every_pixel_ratio():
    plan = StudioPlan(exposure_ev=0.6, contrast=0.3, shadows=0.4, pivot=0.05, highlight_slope=0.75, white_limit=0.985)
    y = np.geomspace(1e-5, 1.0, 4000).astype(np.float32)
    out = tone_curve(y, plan)
    assert np.all(np.diff(out) > 0)  # monotone: no tonal inversions, no posterisation
    grey = apply_studio_light(y[:, None].repeat(3, axis=1), plan)[:, 1]
    assert np.all(np.diff(grey) >= -1e-6)  # still monotone with the highlight ceiling
    rng = np.random.default_rng(2)
    pixels = rng.random((5000, 3)).astype(np.float32) ** 2.2
    mapped = apply_studio_light(pixels, plan, scale_wb=0.0)
    ratio = mapped / np.maximum(pixels, 1e-6)
    keep = pixels.min(axis=1) > 1e-3
    assert np.allclose(ratio[keep, 0], ratio[keep, 1], rtol=1e-4) and np.allclose(ratio[keep, 0], ratio[keep, 2], rtol=1e-4)
    # global: the same value gives the same result wherever it is (no spatial operation)
    shuffled = rng.permutation(len(pixels))
    assert np.array_equal(apply_studio_light(pixels[shuffled], plan), apply_studio_light(pixels, plan)[shuffled])


def test_highlights_roll_off_instead_of_clipping_and_are_never_darkened():
    plan = StudioPlan(exposure_ev=1.0, contrast=0.3, pivot=0.2, highlight_knee=0.85, highlight_slope=0.75, white_limit=0.985)
    white_paint = np.linspace(0.5, 0.97, 500, dtype=np.float32)[:, None].repeat(3, axis=1)
    out = apply_studio_light(white_paint, plan)
    assert out.max() < 0.99  # nothing new reaches full white
    assert np.all(out[:, 1] >= white_paint[:, 1] - 1e-6)  # brightened highlights never end darker
    assert np.all(np.diff(out[:, 1]) >= -1e-6)  # monotone: shading on white paint keeps its order
    # highlight detail: the shading keeps at least highlight_slope of its contrast (no flat white)
    assert np.ptp(out[:, 1]) >= 0.7 * (0.97 - 0.5)
    already_white = np.ones((10, 3), np.float32)
    assert np.allclose(apply_studio_light(already_white, plan), 1.0)  # stays as photographed


def test_below_the_knee_the_curve_is_untouched_and_above_it_detail_is_kept():
    grey = np.geomspace(1e-3, 0.97, 3000).astype(np.float32)
    gentle = StudioPlan(exposure_ev=0.1, pivot=0.05, highlight_knee=0.85, highlight_slope=0.65, white_limit=0.985)
    assert highlight_slope(gentle) > gentle.highlight_slope  # the knee stays at 0.85
    curve = tone_curve(grey, gentle)
    out = apply_studio_light(grey[:, None].repeat(3, axis=1), gentle)[:, 1]
    low = curve < 0.7  # below the knee and its rounded corner: exactly the tone curve
    assert np.allclose(out[low], curve[low], rtol=1e-5)
    plan = StudioPlan(exposure_ev=0.3, contrast=0.2, pivot=0.05, highlight_knee=0.85, highlight_slope=0.65, white_limit=0.985)
    curve = tone_curve(grey, plan)
    out = apply_studio_light(grey[:, None].repeat(3, axis=1), plan)[:, 1]
    # wheels / chrome in the upper mid tones get the full lift (no early compression)
    mid = (grey > 0.2) & (grey < 0.4)
    assert np.all(out[mid] > grey[mid] * 2 ** 0.3 * 0.999)
    # the top segment keeps at least highlight_slope of the tonal detail and never clips
    s = highlight_slope(plan)
    assert plan.highlight_slope <= s <= 1.0
    top = grey > 0.9
    slope = np.diff(out[top]) / np.diff(grey[top])
    assert slope.min() >= plan.highlight_slope - 0.02 and out.max() < 0.985 + 1e-6
    # a strong lift moves the knee down instead of flattening the highlights below highlight_slope
    strong = StudioPlan(exposure_ev=1.0, contrast=0.3, pivot=0.05, highlight_knee=0.85, highlight_slope=0.65, white_limit=0.985)
    assert highlight_slope(strong) == pytest.approx(0.65)


def test_nothing_ends_darker_than_photographed():
    """Contrast acts only above the car's median; exposure, shadow lift and shoulder never darken
    (the white balance keeps every pixel's luminance)."""
    rng = np.random.default_rng(5)
    pixels = (rng.random((20000, 3)) ** 2.4).astype(np.float32)
    for plan in (
        StudioPlan(exposure_ev=0.0, contrast=0.3, pivot=0.3, highlight_slope=0.65),  # contrast only (A5 case)
        StudioPlan(exposure_ev=0.5, contrast=0.3, shadows=0.3, pivot=0.04, highlight_slope=0.65),  # backlit S63 case
        StudioPlan(exposure_ev=1.0, contrast=0.2, pivot=0.1, highlight_slope=0.4),
    ):
        out = apply_studio_light(pixels, plan)
        y_before, y_after = luminance(pixels), luminance(out)
        assert np.all(y_after >= y_before * (1 - 2e-3) - 1e-6)
        assert np.all(lab_from_linear(out.reshape(-1, 1, 3))[..., 0] >= lab_from_linear(pixels.reshape(-1, 1, 3))[..., 0] - 0.15)
    # the white balance keeps the luminance of neutral pixels; a coloured pixel's luminance moves at
    # most by the largest per-channel gain change
    plan = StudioPlan(wb_gains=(1.05, 0.99, 0.95))
    grey = np.linspace(0.02, 0.85, 200, dtype=np.float32)[:, None].repeat(3, axis=1)
    assert np.allclose(luminance(apply_studio_light(grey, plan)), luminance(grey), rtol=2e-3)
    ratio = luminance(apply_studio_light(pixels, plan)) / np.maximum(luminance(pixels), 1e-6)
    assert np.all(ratio[luminance(pixels) > 1e-3] >= 0.95 - 1e-3)


@pytest.mark.parametrize("paint", [(16, 16, 18), (40, 40, 44)])  # deep black / black as photographed
def test_a_black_car_stays_black(paint):
    rgb, alpha = _car(paint)
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg(max_exposure_ev=1.0, exposure_strength=1.0))
    guard = info["guard"]
    assert guard["passed"] and info["planned"]["exposureEv"] == 1.0  # the plan wants a full stop …
    if paint[0] > 30:
        assert "exposure" in guard["limitedBy"]  # … the paint guard stops it
    before, after = info["paint"]["before"], info["paint"]["after"]
    assert after["L50"] - before["L50"] <= _cfg().max_black_lift + 0.2
    assert after["L10"] - before["L10"] <= _cfg().max_black_lift + 0.2
    assert after["L50"] < 25  # still black, not grey
    assert guard["vehicleBlackShift"] <= 2.0  # tyres and the deepest blacks keep their level


def test_navy_keeps_its_hue_and_saturation_under_the_studio_light():
    rgb, alpha = _car((24, 34, 78))
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg())
    guard = info["guard"]
    assert guard["hueShiftDeg"] <= _cfg().max_hue_shift_deg + 1e-6
    assert abs(guard["saturationChange"]) <= _cfg().max_saturation_change + 1e-6
    lab_b = lab_from_linear(rgb[100:110, 150:200]).reshape(-1, 3).mean(axis=0)
    lab_a = lab_from_linear(out[100:110, 150:200]).reshape(-1, 3).mean(axis=0)
    hue_b, hue_a = np.degrees(np.arctan2(lab_b[2], lab_b[1])), np.degrees(np.arctan2(lab_a[2], lab_a[1]))
    assert abs(hue_a - hue_b) < 3.0 and lab_a[2] < -20  # clearly navy
    assert lab_a[0] >= lab_b[0]  # not darker


def test_a_white_balance_that_would_change_the_paint_hue_is_scaled_back():
    rgb, alpha = _car((20, 95, 105))  # teal: its hue reacts strongly to a warm white balance
    studio = StudioLight(white=0.69, key=0.49, neutral=(1.3, 1.0, 0.7))  # absurdly warm light
    out, info = studio_light_correction(rgb, alpha, studio, _cfg(white_balance_strength=1.0, max_white_balance=0.08))
    assert info["applied"]["wbScale"] < 1.0 and "whiteBalance" in info["guard"]["limitedBy"]
    assert info["guard"]["hueShiftDeg"] <= _cfg().max_hue_shift_deg + 1e-6
    gains = np.asarray(info["applied"]["wbGains"])
    assert np.max(np.abs(gains - 1.0)) <= STUDIO_HARD_LIMITS["max_white_balance"][1] + 1e-6
    assert float(np.dot(gains, [0.2126, 0.7152, 0.0722])) == pytest.approx(1.0, abs=1e-3)


def test_a_white_car_stays_white_without_clipping():
    rgb, alpha = _car((228, 230, 233))
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg(exposure_strength=1.0, max_exposure_ev=1.0, contrast=0.3))
    assert info["guard"]["newClippedFraction"] <= _cfg().max_new_clipped
    newly = (out.max(axis=-1) >= 0.995) & (rgb.max(axis=-1) < 0.995)
    assert newly.mean() <= _cfg().max_new_clipped
    lab = lab_from_linear(out[100:110, 150:200]).reshape(-1, 3).mean(axis=0)
    assert lab[0] > 85 and np.hypot(lab[1], lab[2]) < 6  # white, no colour cast added


def test_a_bright_car_is_never_darkened():
    rgb, alpha = _car((200, 200, 205))
    dark_studio = StudioLight(white=0.3, key=0.05, neutral=(1.0, 1.0, 1.0))
    _, info = studio_light_correction(rgb, alpha, dark_studio, _cfg())
    assert info["planned"]["exposureEv"] == 0.0


def test_lift_allowance_ramps_with_the_paint_lightness():
    cfg = _cfg(max_black_lift=4.0, max_paint_lightness_shift=6.0)
    assert lift_allowance(10.0, cfg) == 4.0 and lift_allowance(60.0, cfg) == 6.0
    assert 4.0 < lift_allowance(35.0, cfg) < 6.0  # no step at a class boundary


def test_the_paint_finder_picks_the_body_not_glass_or_tyres():
    rgb, alpha = _car((150, 30, 30), reflections=False)
    pixels, info = find_paint(rgb, alpha)
    assert info["kind"] == "chromatic" and info["share"] > 0.4
    lab = lab_from_linear(pixels.reshape(-1, 1, 3)).reshape(-1, 3)
    assert np.median(lab[:, 1]) > 30  # red


def test_measure_studio_reads_the_wall_above_the_car():
    plate = np.full((300, 400, 3), 120, np.uint8)
    plate[:150] = (228, 220, 216)  # warm white wall
    plate[150:] = (180, 120, 70)  # wood floor
    studio = measure_studio(plate, (100.0, 120.0, 200.0, 120.0))
    assert studio.white == pytest.approx(float(luminance(srgb_to_linear(np.array([[[228, 220, 216]]], np.uint8)))[0, 0]), rel=0.02)
    assert studio.neutral[0] > studio.neutral[2]  # warm light
    assert 0.0 < studio.key < studio.white


def test_hard_limits_cap_the_plan_even_for_a_hand_built_config():
    rgb, alpha = _car((60, 60, 62))
    wild = StudioLightConfig(enabled=True, exposure_strength=5.0, key_target=9.0, max_exposure_ev=9.0, contrast=2.0,
                             shadows=3.0, white_balance_strength=4.0, max_white_balance=1.0, max_black_lift=50.0,
                             max_paint_lightness_shift=50.0, max_hue_shift_deg=90.0, max_saturation_change=5.0,
                             max_neutral_colour_shift=50.0, max_new_clipped=1.0)
    _, info = studio_light_correction(rgb, alpha, StudioLight(white=0.9, key=0.9, neutral=(1.5, 1.0, 0.5)), wild)
    planned = info["planned"]
    assert planned["exposureEv"] <= STUDIO_HARD_LIMITS["max_exposure_ev"][1]
    assert planned["contrast"] <= STUDIO_HARD_LIMITS["contrast"][1]
    assert planned["shadows"] <= STUDIO_HARD_LIMITS["shadows"][1]
    assert np.max(np.abs(np.asarray(planned["wbGains"]) - 1.0)) <= STUDIO_HARD_LIMITS["max_white_balance"][1] + 1e-6
    assert info["guard"]["lightnessShift"] <= STUDIO_HARD_LIMITS["max_paint_lightness_shift"][1] + 0.2


# ── review fixes: near-neutral tinted paint, white balance axis and reference ────────────────

#: Shade light: the car's neutral parts (glass, trim) photographed bluish, like under an overcast sky.
SHADE_TRIM = (112, 118, 126)
#: The dealer's plate: a slightly warm, slightly magenta wall (G below R and B).
MAGENTA_STUDIO = StudioLight(white=0.69, key=0.49, neutral=(1.031, 0.989, 1.016))


def _tinted_car(paint_srgb, trim=SHADE_TRIM, size=(240, 360), seed=1):
    """A car of near-neutral tinted paint with grey glass and trim in shade (the white-balance
    reference, distinctly different from the paint) and black tyres."""
    rng = np.random.default_rng(seed)
    h, w = size
    srgb = np.zeros((h, w, 3), np.float32)
    srgb[:] = np.asarray(paint_srgb, np.float32) * np.linspace(0.92, 1.08, w, dtype=np.float32)[None, :, None]
    srgb[: h // 5, w // 4 : 3 * w // 4] = trim  # glass / trim
    srgb[h - 48 : h - 36, :] = trim  # bumper trim
    for cx in (w // 4, 3 * w // 4):
        cv2.circle(srgb, (cx, h - 25), 24, (20, 21, 24), -1)
    srgb += rng.normal(0, 1.0, srgb.shape).astype(np.float32)
    rgb = srgb_to_linear(np.clip(srgb, 0, 255).astype(np.uint8))
    alpha = np.ones((h, w), np.float32)
    alpha[:3], alpha[-3:], alpha[:, :3], alpha[:, -3:] = 0, 0, 0, 0
    return rgb, alpha


def _panel_lch(rgb):
    lab = lab_from_linear(rgb[90:120, 120:240]).reshape(-1, 3).mean(axis=0)
    return lab[0], float(np.hypot(lab[1], lab[2])), float(np.degrees(np.arctan2(lab[2], lab[1]))), lab


@pytest.mark.parametrize(
    "paint",
    [(148, 160, 146), (110, 100, 112), (96, 104, 118), (30, 36, 52), (34, 44, 38), (192, 184, 164)],
    ids=["sage-grey", "plum-grey", "blue-grey", "dark-navy", "dark-green", "champagne"],
)
def test_near_neutral_tinted_paint_keeps_its_hue_and_saturation(paint):
    """C* about 5 … 11: the hue / saturation guard measures every paint pixel with a measurable
    hue (weighted by chroma) – the white balance can no longer rotate a grey-blue or sage paint."""
    rgb, alpha = _tinted_car(paint)
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg(white_balance_strength=1.0))
    guard = info["guard"]
    assert guard["passed"]
    l0, c0, h0, _ = _panel_lch(rgb)
    l1, c1, h1, _ = _panel_lch(out)
    assert abs((h1 - h0 + 180.0) % 360.0 - 180.0) <= 3.0  # the dealer's limit, measured on the panel itself
    # saturation (exposure-invariant): C* / (L* + 16) within ±10 %
    assert abs((c1 / (l1 + 16.0)) / (c0 / (l0 + 16.0)) - 1.0) <= 0.10
    if guard["hueShiftDeg"] is not None:
        assert guard["hueShiftDeg"] <= _cfg().max_hue_shift_deg + 1e-6
    else:  # not measured – then the a*b* colour guard of near-neutral paint holds
        assert guard["colourShift"] is not None and guard["colourShift"] <= _cfg().max_neutral_colour_shift + 1e-6


def test_hue_of_an_almost_colourless_paint_is_reported_as_not_measured():
    rgb, alpha = _tinted_car((205, 206, 208))  # white, C* < 2
    _, info = studio_light_correction(rgb, alpha, STUDIO, _cfg())
    guard = info["guard"]
    assert info["paint"]["kind"] == "neutral"
    assert guard["hueShiftDeg"] is None and guard["saturationChange"] is None  # not 0.0
    assert guard["colourShift"] is not None


def test_the_colour_guard_of_neutral_paint_sees_a_hue_rotation_without_a_chroma_change():
    """A rotation of a near-neutral paint's a*b* vector keeps |C*| – the guard measures the vector."""
    rng = np.random.default_rng(3)
    lab = np.column_stack([np.full(500, 60.0), 3.0 + rng.normal(0, 0.2, 500), -2.0 + rng.normal(0, 0.2, 500)])
    rotated = lab.copy()
    rotated[:, 1], rotated[:, 2] = -lab[:, 2], lab[:, 1]  # 90° rotation, same chroma
    before = {"lab": lab, "C": 3.6, "L50": 60.0, "L10": 59.0, "L90": 61.0, "L95": 61.5}
    after = {**before, "lab": rotated}
    vehicle = np.full((300, 3), 0.3, np.float32)
    failed, m = paint_guard(before, after, vehicle, vehicle, _cfg(), kind="neutral")
    assert "neutralColour" in failed and m["colourShift"] > 3.0
    # an exposure change alone (L* and a*, b* scaled together) is no colour shift
    brighter = lab.copy()
    brighter[:, 0] = (lab[:, 0] + 16.0) * 1.1 - 16.0
    brighter[:, 1:] *= 1.1
    failed, m = paint_guard(before, {**before, "lab": brighter}, vehicle, vehicle, _cfg(), kind="neutral")
    assert "neutralColour" not in failed and m["colourShift"] < 1e-6


def test_cct_and_duv_round_trip():
    kelvin, duv = cct_duv(np.ones(3))  # sRGB white = D65
    assert kelvin == pytest.approx(6504, abs=15) and duv == pytest.approx(0.0032, abs=0.0005)
    for t, d in ((3000.0, 0.0), (5000.0, -0.006), (8000.0, 0.008)):
        back = cct_duv(linear_from_cct_duv(t, d))
        assert back[0] == pytest.approx(t, rel=2e-3) and back[1] == pytest.approx(d, abs=2e-4)


def test_the_white_balance_changes_only_the_colour_temperature_not_the_tint():
    """A magenta-tinted studio wall must not tint a white car pink: only the blue–yellow axis moves."""
    rgb, alpha = _tinted_car((226, 229, 236))  # white with a slight blue shade cast
    out, info = studio_light_correction(rgb, alpha, MAGENTA_STUDIO, _cfg(white_balance_strength=1.0))
    wb = info["measured"]["whiteBalance"]
    assert wb["studioCct"] < wb["carCct"] and wb["targetCct"] < wb["carCct"]  # warmer, as the studio
    _, _, _, lab_b = _panel_lch(rgb)
    _, _, _, lab_a = _panel_lch(out)
    assert abs(lab_a[1] - lab_b[1]) <= 0.5  # a* (green–magenta) unchanged
    assert lab_a[2] > lab_b[2]  # b*: less blue
    # the car's own tint is kept: the corrected neutral has the car's Duv
    gains = np.asarray(info["applied"]["wbGains"])
    if info["applied"]["wbScale"] == 1.0:
        corrected = np.asarray(wb["carNeutral"]) * gains
        assert cct_duv(corrected)[1] == pytest.approx(wb["carDuv"], abs=1e-3)


def test_dark_paint_is_never_its_own_white_reference():
    """Navy / brown-black paint photographed at C* < 6 is excluded from the neutral estimate: with no
    other neutral part the white balance stays off; with glass and trim the paint keeps its colour."""
    navy = (26, 30, 40)
    rgb, alpha = _tinted_car(navy, trim=navy)  # no neutral part besides the paint
    _, info = studio_light_correction(rgb, alpha, MAGENTA_STUDIO, _cfg(white_balance_strength=1.0))
    assert info["measured"]["whiteBalance"].get("skipped") == "noNeutralReference"
    assert info["applied"]["wbGains"] == [1.0, 1.0, 1.0]
    region = analyse_paint(*_tinted_car(navy, trim=(118, 118, 120)))
    reference = lab_from_linear(region.neutral_reference.reshape(-1, 1, 3)).reshape(-1, 3)
    assert len(reference) > 1000 and np.median(reference[:, 0]) > 40  # the grey trim, not the navy (L* ≈ 12)


def test_a_uniform_highlight_does_not_drop_the_whole_correction():
    rgb, alpha = _car((60, 60, 62))
    rgb = rgb.copy()
    rgb[120:126, :] = srgb_to_linear(np.array([[[236, 236, 236]]], np.uint8))  # noiseless chrome strip
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg())
    assert info["guard"]["passed"] and info["applied"]["exposureEv"] > 0.0


def test_no_paint_found_means_no_correction():
    rng = np.random.default_rng(4)
    rgb = (rng.random((120, 160, 3)) ** 2.2).astype(np.float32)  # no uniform area anywhere
    alpha = np.ones((120, 160), np.float32)
    out, info = studio_light_correction(rgb, alpha, STUDIO, _cfg())
    assert info["guard"]["limitedBy"] == {"all": ["paintNotFound"]}
    assert np.array_equal(out, rgb)  # the original pixels
