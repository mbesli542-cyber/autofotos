"""Second-stage edge refinement (app/pipeline/matting.py) on synthetic arrays."""

import json

import cv2
import numpy as np
import pytest

from app.pipeline import matting
from app.pipeline.matting import (
    colour_guided_filter,
    model_alpha,
    refine_edges,
    side_separation,
    thin_parts,
    trust_weight,
)


def _model_like(truth: np.ndarray, model_size: int = 256) -> np.ndarray:
    """What the segmenter delivers: the mask at model resolution, up-sampled (soft edge)."""
    h, w = truth.shape
    small = cv2.resize(truth, (model_size, model_size), interpolation=cv2.INTER_AREA)
    return np.clip(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), 0.0, 1.0).astype(np.float32)


def _band(truth: np.ndarray, px: int = 12) -> np.ndarray:
    solid = (truth > 0.5).astype(np.uint8)
    kernel = np.ones((2 * px + 1, 2 * px + 1), np.uint8)
    return (cv2.dilate(solid, kernel) - cv2.erode(solid, kernel)) > 0


def _isoluminant_scene(size=(640, 960), seed=1):
    """Red 'car' on a grey floor of the SAME luminance – the grey guide cannot see this edge."""
    h, w = size
    rng = np.random.default_rng(seed)
    truth = np.zeros((h, w), np.float32)
    cv2.ellipse(truth, (w // 2, h // 2), (w // 3, h // 4), 0, 0, 360, 1.0, -1)
    truth = cv2.GaussianBlur(truth, (0, 0), 0.7)  # optics: ~1 px real edge
    red = np.array([200, 40, 40], np.float32)
    luma = 0.299 * red[0] + 0.587 * red[1] + 0.114 * red[2]
    grey = np.array([luma, luma, luma], np.float32)
    rgb = truth[..., None] * red + (1 - truth[..., None]) * grey + rng.normal(0, 2, (h, w, 3))
    return np.clip(rgb, 0, 255).astype(np.uint8), truth


def test_output_shape_dtype_range_and_rgb_untouched():
    rgb, truth = _isoluminant_scene()
    before = rgb.copy()
    coarse = _model_like(truth)
    out, info = refine_edges(rgb, coarse, model_input_size=256)
    assert out.shape == truth.shape and out.dtype == np.float32
    assert 0.0 <= out.min() and out.max() <= 1.0
    assert np.array_equal(rgb, before)  # the photo itself is never modified
    json.dumps(info)  # diagnostics go into job metadata


def test_snaps_to_isoluminant_colour_edge_better_than_the_model_alpha():
    rgb, truth = _isoluminant_scene()
    coarse = _model_like(truth)
    alpha = model_alpha(coarse)
    out, info = refine_edges(rgb, coarse, model_input_size=256)
    band = _band(truth)
    err_model = np.abs(alpha - truth)[band].mean()
    err_colour = np.abs(out - truth)[band].mean()
    assert err_colour < 0.6 * err_model
    assert info["edgeConfidence"] > 0.8  # red vs grey: the cut line is backed by the photo


def test_pixels_outside_the_uncertain_band_are_unchanged():
    rgb, truth = _isoluminant_scene()
    coarse = _model_like(truth)
    alpha = model_alpha(coarse)
    out, info = refine_edges(rgb, coarse, model_input_size=256)
    radius = info["bandRadius"]
    uncertain = ((coarse > 0.02) & (coarse < 0.98)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    outside = ~cv2.dilate(uncertain, kernel).astype(bool)
    assert np.array_equal(out[outside], alpha[outside])
    assert info["bandPx"] == int((~outside).sum())


def _choked(coarse):
    return np.clip((coarse - matting.CHOKE) / (1 - 2 * matting.CHOKE), 0, 1)


def test_dark_on_dark_edge_keeps_the_model_alpha_without_noise():
    """Black tyre on a dark ground shadow: no colour evidence – the model's own
    alpha is used (no grainy edge from fitting sensor noise, no blur)."""
    h, w = 400, 600
    rng = np.random.default_rng(7)
    truth = np.zeros((h, w), np.float32)
    cv2.circle(truth, (300, 150), 140, 1.0, -1)
    tyre, shadow = np.array([14, 14, 16], np.float32), np.array([20, 19, 18], np.float32)
    rgb = truth[..., None] * tyre + (1 - truth[..., None]) * shadow + rng.normal(0, 3, (h, w, 3))
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    coarse = _model_like(truth, 128)
    out, info = refine_edges(rgb, coarse, model_input_size=128)
    assert np.abs(out - _choked(coarse)).max() < 0.02
    # not grainy: as smooth along the edge as the model alpha
    assert np.abs(np.diff(out, axis=1)).sum() <= 1.05 * np.abs(np.diff(_choked(coarse), axis=1)).sum()
    assert info["lowContrastFraction"] > 0.9
    assert info["edgeConfidence"] < 0.1


def test_edge_inside_the_vehicle_does_not_count_as_evidence():
    """Orange bumper over a black lip over a dark shadow: the strong orange/black edge
    is INSIDE the car; the lip/shadow cut line has no contrast and must stay put."""
    h, w = 300, 400
    rng = np.random.default_rng(3)
    rgb = np.empty((h, w, 3), np.float32)
    rgb[:120] = (210, 110, 30)  # bumper paint
    rgb[120:150] = (16, 15, 15)  # black lip (vehicle)
    rgb[150:] = (19, 18, 17)  # ground shadow (background)
    rgb = np.clip(rgb + rng.normal(0, 2.5, rgb.shape), 0, 255).astype(np.uint8)
    truth = np.zeros((h, w), np.float32)
    truth[:150] = 1.0
    coarse = _model_like(truth, 100)
    coarse = np.roll(coarse, 6, axis=0)  # the model put the line into the shadow
    coarse[:6] = 1.0
    out, _ = refine_edges(rgb, coarse, model_input_size=100)
    rows = slice(130, 175)
    assert np.abs(out[rows] - _choked(coarse)[rows]).max() < 0.05


def test_tiled_evaluation_matches_the_full_frame_filter():
    rgb, truth = _isoluminant_scene(size=(500, 700), seed=4)
    coarse = _model_like(truth)
    alpha = model_alpha(coarse)
    out, info = refine_edges(rgb, coarse, model_input_size=256)
    r = info["radius"]
    guide = rgb.astype(np.float32) / 255.0
    sharp, contrast = colour_guided_filter(guide, coarse, r, matting.EPS)
    weight = trust_weight(contrast, side_separation(guide, alpha, 2 * r))
    rb = info["bandRadius"]
    uncertain = ((coarse > 0.02) & (coarse < 0.98)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rb + 1, 2 * rb + 1))
    band = cv2.dilate(uncertain, kernel).astype(bool)
    model = _choked(coarse)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rb + 1, 2 * rb + 1))
    solid = (alpha >= 0.5).astype(np.uint8)
    lower = np.where(cv2.dilate(1 - solid, kernel) > 0, 0.0, alpha)
    upper = np.where(cv2.dilate(solid, kernel) > 0, 1.0, alpha)
    blended = np.clip(model + weight * (_choked(sharp) - model), lower, upper)
    thin = thin_parts(model >= 0.5, int(round(matting.THIN_OPEN_FACTOR * r)))
    blended = np.where(thin, np.maximum(blended, model), blended)  # thin parts keep the model alpha
    blended = np.where(model < 0.5, np.minimum(blended, model), blended)  # fringe clamp
    expected = np.where(band, blended, alpha)
    assert np.abs(out - expected).max() < 1e-2  # float32 rounding of the box sums only


def test_confident_mask_without_uncertain_band_is_returned_unchanged():
    rgb = np.full((120, 160, 3), 128, np.uint8)
    alpha = np.zeros((120, 160), np.float32)
    alpha[30:90, 40:120] = 1.0
    out, info = refine_edges(rgb, alpha)
    assert np.array_equal(out, alpha)
    assert info["tiles"] == 0 and info["bandPx"] == 0


def test_size_mismatch_raises():
    with pytest.raises(ValueError):
        refine_edges(np.zeros((10, 10, 3), np.uint8), np.zeros((10, 11), np.float32))
    with pytest.raises(ValueError):
        refine_edges(np.zeros((10, 10), np.uint8), np.zeros((10, 10), np.float32))


def test_bottom_zone_confidence_is_reported_separately():
    """Bright body on a light floor but dark tyres on a dark shadow at the bottom."""
    h, w = 500, 800
    rng = np.random.default_rng(11)
    truth = np.zeros((h, w), np.float32)
    truth[120:380, 150:650] = 1.0  # body
    rgb = np.empty((h, w, 3), np.float32)
    rgb[:] = (200, 205, 210)  # light background
    rgb[truth > 0] = (40, 70, 160)  # blue body
    rgb[330:, :] = (22, 22, 22)  # dark shadow zone at the bottom ...
    rgb[330:380, 150:650] = (18, 18, 20)  # ... where the vehicle's lower part is black too
    rgb = np.clip(rgb + rng.normal(0, 2, rgb.shape), 0, 255).astype(np.uint8)
    coarse = _model_like(truth, 200)
    _, info = refine_edges(rgb, coarse, model_input_size=200)
    assert info["bottomLowContrastFraction"] > info["lowContrastFraction"] + 0.2


def test_real_size_vehicle_photo_runs_on_tiles(vehicle):
    rgb, truth = vehicle
    coarse = _model_like(truth, 512)
    alpha = model_alpha(coarse)
    out, info = refine_edges(rgb, coarse, model_input_size=512)
    band = _band(truth, 10)
    assert np.abs(out - truth)[band].mean() <= np.abs(alpha - truth)[band].mean() + 1e-3
    # only tiles along the outline are evaluated, not the whole frame
    assert 0 < info["tiles"] * matting.TILE**2 < 0.5 * truth.size


def test_no_new_transparency_inside_the_solid_body(monkeypatch):
    """Worst case – the colour model wants to open everything and is fully trusted:
    transparency may still only grow within the band radius of background the
    mask already has (no see-through hairlines along chrome trim or window frames
    inside the body), and opacity only next to the existing vehicle."""
    h, w = 300, 400
    rgb = np.full((h, w, 3), 120, np.uint8)
    truth = np.zeros((h, w), np.float32)
    truth[60:240, 80:320] = 1.0
    coarse = _model_like(truth, 100)
    coarse[100:200, 120:280] = 0.6  # model unsure inside the body (glass, trim)
    alpha = model_alpha(coarse)
    monkeypatch.setattr(matting, "colour_guided_filter", lambda g, p, r, e: (np.zeros_like(p), np.ones_like(p)))
    monkeypatch.setattr(matting, "trust_weight", lambda c, s: np.ones_like(c))
    out, info = refine_edges(rgb, coarse, model_input_size=100)
    rb = info["bandRadius"]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rb + 1, 2 * rb + 1))
    near_bg = cv2.dilate((alpha < 0.5).astype(np.uint8), kernel) > 0
    assert np.all(out[~near_bg] >= alpha[~near_bg])
    assert (out < alpha - 0.5).any()  # the edge itself was still allowed to move


def _dark_busy_scene(seed=5):
    """Dark car edge next to dark, high-frequency background (bare twigs/hedge in
    shade): the colour model 'sees' edges in the background texture."""
    h, w = 360, 480
    rng = np.random.default_rng(seed)
    truth = np.zeros((h, w), np.float32)
    truth[:, :240] = 1.0
    truth = cv2.GaussianBlur(truth, (0, 0), 0.7)
    car = np.array([28, 30, 34], np.float32)
    twigs = rng.integers(0, 2, (h // 3, w // 3)).astype(np.float32)
    twigs = cv2.resize(twigs, (w, h), interpolation=cv2.INTER_NEAREST)[..., None]
    background = twigs * np.array([12, 14, 10], np.float32) + (1 - twigs) * np.array([70, 74, 60], np.float32)
    rgb = truth[..., None] * car + (1 - truth[..., None]) * background + rng.normal(0, 2, (h, w, 3))
    return np.clip(rgb, 0, 255).astype(np.uint8), truth


def test_fringe_clamp_never_adds_alpha_above_the_model_outside_the_contour():
    """Outside the 0.5 contour the colour model may only remove alpha: dark twigs
    next to a dark edge must not gain alpha above the model's own (no loose
    fringe of old background)."""
    rgb, truth = _dark_busy_scene()
    coarse = _model_like(truth, 96)
    out, _ = refine_edges(rgb, coarse, model_input_size=96)
    model = _choked(coarse)
    outside = model < 0.5  # every pixel outside the model's own contour, in and outside the band
    assert outside.any()
    assert np.all(out[outside] <= model[outside] + 1e-6)


def test_fringe_clamp_is_active_on_a_trusted_colour_model(monkeypatch):
    """Worst case – a fully trusted colour model that wants to make the whole
    background half-opaque: outside the contour nothing exceeds the model."""
    rgb, truth = _dark_busy_scene(seed=9)
    coarse = _model_like(truth, 96)
    monkeypatch.setattr(matting, "colour_guided_filter", lambda g, p, r, e: (np.full_like(p, 0.45), np.ones_like(p)))
    monkeypatch.setattr(matting, "trust_weight", lambda c, s: np.ones_like(c))
    out, _ = refine_edges(rgb, coarse, model_input_size=96)
    model = _choked(coarse)
    outside = model < 0.5
    assert np.all(out[outside] <= model[outside] + 1e-6)
    assert (model > 0).any() and (out[(model == 0)] == 0).all()


def test_dark_ground_is_not_pulled_into_a_dark_tyre():
    """Black tyre over dark, grainy ground (gravel in the shadow): the colour model
    must not grow the cut-out beyond the model's own 0.5 contour (that was a
    ragged dark rim under the tyres on real photos) – it may only tighten it."""
    h, w = 300, 400
    rng = np.random.default_rng(13)
    truth = np.zeros((h, w), np.float32)
    cv2.circle(truth, (200, 40), 180, 1.0, -1)  # lower part of a tyre
    truth = cv2.GaussianBlur(truth, (0, 0), 0.7)
    tyre = np.array([22, 22, 24], np.float32)
    gravel = rng.choice([18.0, 30.0, 95.0], size=(h // 2, w // 2), p=[0.5, 0.3, 0.2]).astype(np.float32)
    gravel = cv2.resize(gravel, (w, h), interpolation=cv2.INTER_NEAREST)[..., None] * np.ones(3, np.float32)
    rgb = truth[..., None] * tyre + (1 - truth[..., None]) * gravel + rng.normal(0, 2, (h, w, 3))
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    coarse = _model_like(truth, 80)
    model = model_alpha(coarse)
    out, _ = refine_edges(rgb, coarse, model_input_size=80)
    outside_model = model < 0.5
    assert np.all(out[outside_model] <= model[outside_model] + 1e-6)
    assert ((out >= 0.5) & outside_model).sum() == 0  # no new opaque pixels, no specks


def test_model_alpha_is_the_choked_segmenter_output():
    coarse = np.array([[0.0, 0.04, 0.5, 0.96, 1.0]], np.float32)
    out = model_alpha(coarse)
    assert out.dtype == np.float32
    assert np.allclose(out, [[0.0, 0.0, 0.5, 1.0, 1.0]])


def _antenna_scene(rod_width: int, rod_alpha: float, seed: int = 3):
    """Dark antenna mast (narrower than the filter window) standing on a dark roof
    in front of a light, textured background (sky, house wall); the model is
    unsure on the mast (alpha 0.5–0.85)."""
    h, w, cell = 300, 400, 3
    rng = np.random.default_rng(seed)
    texture = rng.random((h // cell + 1, w // cell + 1)).astype(np.float32)
    texture = cv2.resize(texture, ((w // cell + 1) * cell, (h // cell + 1) * cell), interpolation=cv2.INTER_NEAREST)
    texture = texture[:h, :w, None]
    background = texture * np.array([235, 236, 240], np.float32) + (1 - texture) * np.array([150, 158, 170], np.float32)
    truth = np.zeros((h, w), np.float32)
    truth[220:, 40:360] = 1.0  # roof
    x0 = 200 - rod_width // 2
    truth[70:220, x0 : x0 + rod_width] = 1.0  # mast
    car = np.array([28, 30, 36], np.float32)
    rgb = truth[..., None] * car + (1 - truth[..., None]) * background + rng.normal(0, 2, (h, w, 3))
    coarse = cv2.GaussianBlur(truth, (0, 0), 1.2)
    coarse[70:216, x0 - 3 : x0 + rod_width + 3] *= rod_alpha
    mast = (slice(70, 216), slice(x0, x0 + rod_width))
    return np.clip(rgb, 0, 255).astype(np.uint8), np.clip(coarse, 0, 1).astype(np.float32), mast


@pytest.mark.parametrize("rod_width, rod_alpha", [(5, 0.85), (6, 0.75), (4, 0.9)])
def test_thin_vehicle_parts_keep_at_least_the_model_alpha(rod_width, rod_alpha):
    """An antenna mast narrower than the colour filter's window: the box-averaged
    coefficients come mostly from background-only windows and attenuate it (on a
    real photo the mast was eroded to a stub). Thin parts of the model's 0.5 mask
    keep at least the model alpha."""
    rgb, coarse, mast = _antenna_scene(rod_width, rod_alpha)
    out, info = refine_edges(rgb, coarse, model_input_size=100)
    assert info["radius"] == 6 and rod_width < 2 * info["radius"] + 1
    model = _choked(coarse)
    on_mast = model[mast] >= 0.5
    assert on_mast.sum() > 100 and 0.5 <= model[mast][on_mast].min() and model[mast][on_mast].max() <= 0.9
    assert np.all(out[mast][on_mast] >= model[mast][on_mast] - 1e-6)
    assert info["thinProtectedPx"] > 0  # the colour model alone would have lowered it


def test_thin_vehicle_parts_are_not_protected_without_the_rule(monkeypatch):
    """Pins the failure the protection prevents (the test above is not vacuous)."""
    monkeypatch.setattr(matting, "THIN_OPEN_FACTOR", 0.0)
    rgb, coarse, mast = _antenna_scene(5, 0.85)
    out, _ = refine_edges(rgb, coarse, model_input_size=100)
    model = _choked(coarse)
    on_mast = model[mast] >= 0.5
    assert (out[mast][on_mast] < model[mast][on_mast] - 0.05).any()


def test_thin_parts_marks_masts_and_arms_not_the_body():
    solid = np.zeros((200, 300), bool)
    solid[120:, 20:280] = True  # body
    solid[30:120, 148:153] = True  # 5 px mast
    solid[100:106, 0:20] = True  # 6 px mirror arm reaching the image border
    thin = thin_parts(solid, 6)
    assert thin[40:115, 148:153].all()  # the mast (above the 1 px dilation of the body)
    assert thin[101:105, 2:18].all()  # the arm – the image border does not count as background
    assert not thin[130:, 30:270].any()  # the body interior
    region = np.zeros_like(solid)
    region[0:80, 140:160] = True
    limited = thin_parts(solid, 6, region)
    assert np.array_equal(limited[0:80, 140:160], thin[0:80, 140:160])  # same result inside the region box
    assert not limited[101:105, 2:18].any()  # nothing far outside it

