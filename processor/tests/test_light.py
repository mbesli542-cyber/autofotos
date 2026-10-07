import numpy as np
import pytest

from app.pipeline.color import srgb_to_linear
from app.pipeline.light import (
    HARD_LIMITS,
    VehicleCorrection,
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
    assert applied.median_hue_shift_deg < 0.5
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
