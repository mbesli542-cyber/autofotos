import numpy as np
import pytest

from app.pipeline.color import srgb_to_linear
from app.pipeline.light import (
    HARD_LIMITS,
    VehicleCorrection,
    apply_correction,
    effective_limits,
    estimate_correction,
    guarded_correction,
)
from app.presets import VehicleAdjustments


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
