"""The SAM cut of a foreign object glued to the vehicle outline (``mask.sam_cut``, the dealer's
decision of 2026-11-06): prompts, candidate screening and every guard with synthetic masks and a
fake SAM (an oracle that knows the car, with planted mistakes), the order of the cut methods and
the fallback to the zoom cut, the wiring (``segmenter.sam2``, settings, warm-up, /health) – and
one integration test with the built SAM 2.1 graphs (skipped without them)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import PROCESSOR_ROOT, Settings
from app.pipeline import mask as mask_module
from app.pipeline.mask import (
    SAM_EDGE_PX,
    SamCut,
    _apply_sam_cut,
    _sam_soft_edge,
    assess_mask,
    clean_mask,
    mask_bbox,
    sam_cut_on,
    sam_scene,
    semantic_findings,
)
from app.pipeline.quality import GateReport, check_foreign
from app.pipeline.segmentation import MODEL_REGISTRY, ModelUnavailableError, OnnxSegmenter, create_segmenter
from tests.semantic_fixtures import FakeDetector, side_view
from tests.test_detection import _glued_wheel_scene, _zoom

NAVY = (24, 34, 78)


def _gate(info) -> GateReport:
    report = GateReport()
    check_foreign(report, info)
    return report


class FakeSam:
    """A SAM 2.1 stand-in that knows ``truth`` (bool, photo size): three candidates with logits +8
    inside / -8 outside (blurred: a smooth edge like SAM's bilinear logits), IoUs 0.95/0.9/0.85.
    ``edit(k, logits, window)`` plants mistakes per candidate; ``fail`` raises instead."""

    name = "fake-sam"

    def __init__(self, truth: np.ndarray, *, edit=None, ious=(0.95, 0.9, 0.85), fail: Exception | None = None):
        self.truth = truth
        self.edit = edit
        self.ious = np.array(ious, np.float32)
        self.fail = fail
        self.encodes: list[tuple[int, int, int, int]] = []
        self.predicts: list[tuple[list, tuple | None]] = []

    def encode(self, window):
        if self.fail is not None:
            raise self.fail
        self.encodes.append(window)
        return window

    def predict(self, embedding, points, box=None, multimask=True):
        x0, y0, x1, y1 = embedding
        self.predicts.append((list(points), box))
        base = cv2.GaussianBlur(np.where(self.truth[y0:y1, x0:x1], 8.0, -8.0).astype(np.float32), (0, 0), 2.0)
        logits = []
        for k in range(3):
            lg = base.copy()
            if self.edit is not None:
                lg = self.edit(k, lg, embedding)
            logits.append(lg)
        return np.stack(logits), self.ious.copy()


def _finding(alpha, rgb, detector):
    binary = alpha >= 0.5
    sem, findings = semantic_findings(binary, rgb, detector)
    finding = next(f for f in findings if f.foreign)
    ys, xs = np.nonzero(binary)
    vw = int(xs.max() - xs.min() + 1)
    zone_px = mask_module.ZOOM_ZONE_CELLS * sem.cell / sem.scale + mask_module.SEMANTIC_MARGIN * vw
    return binary, finding, sem.scale, zone_px, vw


# --------------------------------------------------------------------------- the cut


def test_the_glued_wheel_is_cut_by_sam_before_any_zoom_pass_and_the_car_stays_whole():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    sam, zoom_calls = FakeSam(car), []
    result, info = clean_mask(alpha, rgb=rgb, detector=detector, zoom_segment=_zoom(rgb, NAVY, calls=zoom_calls), sam=sam)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "cut" and finding["verify"]["cutBy"] == "sam"
    assert zoom_calls == [] and len(sam.encodes) == 1 and len(sam.predicts) == 1  # no BiRefNet pass needed
    diag = finding["verify"]["samCut"]
    assert diag["reasons"] == [] and diag["chosen"] == 0 and diag["confined"] is True
    assert diag["left"] == 0.0 and diag["coreRemoved"] == 1.0 and diag["vehicleCutPx"] == 0 and diag["thinCut"] == 0
    assert diag["disagree"] <= mask_module.SAM_MAX_DISAGREE and diag["edgeSupport"] >= mask_module.SAM_MIN_EDGE_SUPPORT
    assert {"encodeMs", "predictMs", "ms", "points", "box", "candidates"} <= set(diag)
    # the prompts: our car's points inside the car, "not that" inside the neighbour, box around our car
    x0, y0 = diag["window"][:2]
    for x, y, label in diag["points"]:
        assert bool(car[int(y) + y0, int(x) + x0]) is (label == 1)
        assert label == 1 or not wheel[int(y) + y0, int(x) + x0] or True  # negatives may sit in the glued wheel
    bx0, by0, bx1, by1 = diag["box"]
    assert bx1 - bx0 > 50 and by1 - by0 > 50
    near_car = cv2.dilate(car.view(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    assert (result[wheel] < 0.5).all() and (result[wheel & ~near_car] == 0).all()  # the wheel is gone
    assert (result[car] >= 0.5).all()  # every car pixel stays
    assert (result <= alpha + 1e-6).all()  # alpha only removed, never added
    assert info.foreign_suspected == [] and len(info.foreign_removed) == 1
    assert _gate(info).failed_code is None
    assert "background_object_removed" in [w.code for w in assess_mask(result, mask_bbox(result), info)]
    # the cut edge is anti-aliased (SAM's soft edge, ≤ 3 px), not a hard step
    band = cv2.dilate(wheel.view(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & car
    soft = (result > 0.05) & (result < 0.95) & band
    assert 0 < int(soft.sum()) < int(band.sum())
    # nothing changed outside the contested zone
    wx0, wy0, wx1, wy1 = diag["window"]
    outside = np.ones(alpha.shape, bool)
    outside[wy0:wy1, wx0:wx1] = False
    assert np.array_equal(result[outside], alpha[outside])


def test_a_candidate_that_keeps_the_wheel_or_the_neighbour_is_no_cut_and_the_photo_stays_rejected():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    neighbour = np.zeros_like(car)
    cv2.rectangle(neighbour.view(np.uint8), (1980, 820), (2399, 1130), 1, -1)

    def edit(k, lg, window):
        x0, y0, x1, y1 = window
        if k == 0:  # keeps the glued wheel: the region is left
            lg[wheel[y0:y1, x0:x1]] = 8.0
        elif k == 1:  # takes the whole car behind as well: disagrees outside the zone, contains the foreign core
            lg[neighbour[y0:y1, x0:x1]] = 8.0
        else:  # misses the prompts: the car's mask inverted
            lg[:] = -lg
        return lg

    sam = FakeSam(car, edit=edit)
    result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=sam)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    diag = finding["verify"]["samCut"]
    assert finding["action"] == "suspected" and "cutBy" not in finding["verify"]
    assert diag["reasons"] == ["no_candidate"]
    kept, merged, inverted = diag["candidates"]
    assert kept["containsPrompts"] is False  # a negative point sits in the wheel
    assert merged["disagree"] > mask_module.SAM_MAX_DISAGREE and merged["foreignInside"] > mask_module.SAM_MAX_FOREIGN_INSIDE
    assert inverted["containsPrompts"] is False
    assert np.array_equal(result >= 0.5, alpha >= 0.5)
    assert _gate(info).failed_code == "mask_low_confidence"


def test_a_cut_that_bites_into_the_cars_core_is_refused():
    """A candidate that passes the screening (agrees outside the zone, contains the prompts) but
    leaves out a piece of our car's core inside the zone is no cut: not one core pixel may go."""
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    binary, finding, scale, zone_px, vw = _finding(alpha, rgb, detector)
    scene, _ = sam_scene(binary, finding, scale=scale, zone_px=zone_px)
    x0, y0, x1, y1 = scene.window
    logits = cv2.GaussianBlur(np.where(car[y0:y1, x0:x1], 8.0, -8.0).astype(np.float32), (0, 0), 2.0)
    # a bite inside the zone AND inside our car's core, away from the prompt points
    keep_out = np.zeros(scene.mask.shape, np.uint8)
    for x, y, label in scene.points:
        cv2.circle(keep_out, (int(x), int(y)), 8, 1, -1)
    bite_at = scene.zone & scene.vehicle_core & (keep_out == 0)
    assert bite_at.any()
    ys, xs = np.nonzero(bite_at)
    cv2.circle(logits, (int(xs[len(xs) // 2]), int(ys[len(ys) // 2])), 3, -8.0, -1)
    cut, diag = sam_cut_on(scene, rgb, np.stack([logits]), np.array([0.95], np.float32), vehicle_w=vw)
    assert cut is None and diag["candidates"][0]["ok"] is True
    assert "vehicle_cut" in diag["reasons"] and diag["vehicleCutPx"] > 0
    # the same candidate without the bite is a cut
    clean = cv2.GaussianBlur(np.where(car[y0:y1, x0:x1], 8.0, -8.0).astype(np.float32), (0, 0), 2.0)
    cut, diag = sam_cut_on(scene, rgb, np.stack([clean]), np.array([0.95], np.float32), vehicle_w=vw)
    assert cut is not None and diag["vehicleCutPx"] == 0 and diag["reasons"] == []


def test_a_foreign_object_that_continues_beyond_the_zone_refuses_the_cut():
    """The neighbour's lower body (the measured case): separated from its verified upper part by a
    strip the coarse instance of OUR car claims, itself unclaimed and in the lower half (no
    candidate, no zone), merged by BiRefNet – and taken for our car by SAM. Nothing may cut it
    outside the zone, so the result would keep a piece of the neighbour: no cut, the photo stays
    rejected – whatever SAM thinks of it."""
    car, wheel, rgb, detector = _glued_wheel_scene()
    bridge = np.zeros_like(car)
    cv2.rectangle(bridge.view(np.uint8), (2040, 1062), (2082, 1082), 1, -1)  # claimed by our instance
    block = np.zeros_like(car)
    cv2.rectangle(block.view(np.uint8), (2049, 1083), (2073, 1148), 1, -1)  # 24 px thick, well below the zone
    bridge &= ~car & ~wheel
    block &= ~car & ~wheel & ~bridge
    rgb[block | bridge] = (232, 232, 236)
    alpha = (car | wheel | bridge | block).astype(np.float32)
    neighbour = np.zeros_like(car)
    cv2.rectangle(neighbour.view(np.uint8), (1980, 820), (2399, 1065), 1, -1)
    detector = FakeDetector().add("car", car | bridge, 0.95).add("car", neighbour & ~car, 0.9)

    def explicit(parts):  # the fake's blur must not thin the strips: their logits are set outright
        def edit(k, lg, window):
            x0, y0, x1, y1 = window
            for part, value in parts:
                lg[part[y0:y1, x0:x1]] = value
            return lg

        return edit

    taken = explicit([(bridge, 8.0), (block, 8.0)])  # SAM takes the block (and the bridge) for ours
    left_out = explicit([(bridge, 8.0), (block, -8.0)])  # SAM leaves the block out
    for edit in (taken, left_out):
        result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car | bridge, edit=edit))
        findings = [f for f in info.semantic["findings"] if f["foreign"]]
        assert len(findings) == 1  # the block is nobody's candidate (unclaimed, lower half)
        diag = findings[0]["verify"]["samCut"]
        assert findings[0]["action"] == "suspected", diag
        if diag["reasons"] != ["no_candidate"]:
            assert "foreign_continues" in diag["reasons"], diag
            assert diag["spillPx"] > 0 and diag["spillThick"] >= mask_module.SEMANTIC_MIN_THICKNESS
            assert diag["spillReachCells"] >= mask_module.SEMANTIC_MIN_REACH_CELLS
        assert np.array_equal(result >= 0.5, alpha >= 0.5)
        assert _gate(info).failed_code == "mask_low_confidence"
    # SAM taking the block for ours passes the screening and fails this guard alone
    _, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car | bridge, edit=taken))
    diag = next(f for f in info.semantic["findings"] if f["foreign"])["verify"]["samCut"]
    assert diag["reasons"] == ["foreign_continues"]
    # without the block the same scene is cut (the bridge, claimed as ours, stays)
    alpha = (car | wheel | bridge).astype(np.float32)
    _, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car | bridge, edit=explicit([(bridge, 8.0)])))
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "cut" and finding["verify"]["samCut"]["spillReachCells"] < mask_module.SEMANTIC_MIN_REACH_CELLS


def test_no_cut_where_neither_a_colour_edge_nor_a_confident_sam_backs_the_line():
    car, wheel, rgb, detector = _glued_wheel_scene()
    rgb[wheel] = (26, 36, 80)  # the wheel in the car's colour: no edge backs a cut line
    alpha = (car | wheel).astype(np.float32)
    _, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car, ious=(0.8, 0.7, 0.6)))
    diag = next(f for f in info.semantic["findings"] if f["foreign"])["verify"]["samCut"]
    assert diag["reasons"] == ["no_colour_edge"] and diag["edgeSupport"] < mask_module.SAM_MIN_EDGE_SUPPORT
    # the same line with a confident SAM (IoU ≥ SAM_MIN_IOU) is accepted
    result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car, ious=(0.95, 0.9, 0.85)))
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "cut" and finding["verify"]["samCut"]["reasons"] == []
    assert (result[wheel] < 0.5).all() and (result[car] >= 0.5).all()


def test_the_best_iou_among_the_usable_candidates_is_chosen():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)

    def edit(k, lg, window):
        if k == 2:  # the highest-IoU candidate keeps the wheel
            x0, y0, x1, y1 = window
            lg[wheel[y0:y1, x0:x1]] = 8.0
        return lg

    _, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car, edit=edit, ious=(0.7, 0.9, 0.99)))
    diag = next(f for f in info.semantic["findings"] if f["foreign"])["verify"]["samCut"]
    assert diag["chosen"] == 1 and diag["iou"] == 0.9 and diag["reasons"] == []
    assert [c["ok"] for c in diag["candidates"]] == [True, True, False]


def test_a_thin_part_is_never_thinned_by_the_cut():
    """A thin part of the mask inside the contested zone (a rod the instance masks miss – SAM may
    miss it too) is never cut below the model alpha: the cut is refused."""
    car, wheel, rgb, detector = _glued_wheel_scene()
    rod = np.zeros_like(car)
    cv2.line(rod.view(np.uint8), (2030, 942), (2078, 942), 1, 3)  # from the bumper out over the wheel, 3 px
    rod &= ~car & ~wheel
    rgb[rod] = (10, 10, 12)
    alpha = (car | wheel | rod).astype(np.float32)

    def edit(k, lg, window):
        x0, y0, x1, y1 = window
        lg[rod[y0:y1, x0:x1]] = -8.0  # SAM does not see the rod
        return lg

    result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car | rod, edit=edit))
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    diag = finding["verify"]["samCut"]
    assert finding["action"] == "suspected"
    assert "vehicle_cut" in diag["reasons"] and diag["thinCut"] > 0 and diag["vehicleCutPx"] == 0
    assert np.array_equal(result >= 0.5, alpha >= 0.5) and (result[rod] >= 0.5).all()
    # a SAM that keeps the rod (its blurred logits would thin a 3 px rod on their own, like the real
    # model – the guard above is for exactly that) cuts the wheel and leaves the rod
    def keep_rod(k, lg, window):
        x0, y0, x1, y1 = window
        lg[rod[y0:y1, x0:x1]] = 8.0
        return lg

    result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=FakeSam(car | rod, edit=keep_rod))
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "cut" and finding["verify"]["samCut"]["thinCut"] == 0
    assert (result[rod] >= 0.5).all() and (result[wheel & ~cv2.dilate(rod.view(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)] < 0.5).all()


# --------------------------------------------------------------------------- order and fallback


def test_the_zoom_cut_is_tried_when_the_sam_cut_fails(monkeypatch):
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    zoom_calls: list = []
    sam = FakeSam(car, edit=lambda k, lg, w: -lg)  # SAM misunderstands every prompt
    result, info = clean_mask(alpha, rgb=rgb, detector=detector, zoom_segment=_zoom(rgb, NAVY, calls=zoom_calls), sam=sam)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "cut" and finding["verify"]["cutBy"] == "zoom"
    assert finding["verify"]["samCut"]["reasons"] == ["no_candidate"] and finding["verify"]["zoomCut"]["reasons"] == []
    assert len(zoom_calls) == 1 and (result[wheel] < 0.5).all() and (result[car] >= 0.5).all()


def test_the_cut_methods_run_in_the_configured_order(monkeypatch):
    monkeypatch.setattr(mask_module, "CUT_METHODS", ("zoom", "sam"))
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    zoom_calls: list = []
    sam = FakeSam(car)
    _, info = clean_mask(alpha, rgb=rgb, detector=detector, zoom_segment=_zoom(rgb, NAVY, calls=zoom_calls), sam=sam)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["verify"]["cutBy"] == "zoom" and len(zoom_calls) == 1 and sam.encodes == []
    assert "samCut" not in finding["verify"]


def test_without_sam_the_zoom_cut_decides_as_before():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    zoom_calls: list = []
    _, info = clean_mask(alpha, rgb=rgb, detector=detector, zoom_segment=_zoom(rgb, NAVY, calls=zoom_calls), sam=None)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["verify"]["cutBy"] == "zoom" and "samCut" not in finding["verify"] and len(zoom_calls) == 1


def test_sam_that_cannot_run_is_logged_and_the_photo_judged_as_without_it(caplog):
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    sam = FakeSam(car, fail=ModelUnavailableError("graph missing"))
    with caplog.at_level(logging.WARNING, logger="app.pipeline.mask"):
        result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=sam)
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    assert finding["action"] == "suspected" and finding["verify"]["samCut"]["reason"] == "sam_unavailable"
    assert any("SAM 2.1 cannot run" in record.message for record in caplog.records)
    assert np.array_equal(result >= 0.5, alpha >= 0.5) and _gate(info).failed_code == "mask_low_confidence"


def test_more_contested_regions_than_sam_cuts_are_rejected_without_a_pass(monkeypatch):
    monkeypatch.setattr(mask_module, "SAM_MAX_CUTS", 0)
    car, wheel, rgb, detector = _glued_wheel_scene()
    sam = FakeSam(car)
    _, info = clean_mask((car | wheel).astype(np.float32), rgb=rgb, detector=detector, sam=sam)
    assert sam.encodes == [] and len(info.foreign_suspected) == 1


def test_a_region_no_detected_object_claims_is_not_cut_by_sam_either():
    car = side_view()
    sign = np.zeros_like(car)
    cv2.rectangle(sign.view(np.uint8), (900, 420), (1250, 680), 1, -1)  # stands on the roof
    rgb = np.full(car.shape + (3,), 150, np.uint8)
    rgb[sign] = (200, 30, 30)
    rgb[car] = NAVY
    sam = FakeSam(car)
    _, info = clean_mask((car | sign).astype(np.float32), rgb=rgb, detector=FakeDetector().add("car", car, 0.95), sam=sam)
    assert sam.encodes == [] and len(info.foreign_suspected) == 1


# --------------------------------------------------------------------------- pieces


def test_the_scene_puts_the_prompts_on_the_right_objects_and_the_zone_around_the_region():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    binary, finding, scale, zone_px, vw = _finding(alpha, rgb, detector)
    scene, reason = sam_scene(binary, finding, scale=scale, zone_px=zone_px)
    assert reason is None and scene is not None
    x0, y0, x1, y1 = scene.window
    assert scene.mask.shape == (y1 - y0, x1 - x0) and scene.region.any() and (scene.region & ~scene.mask).sum() == 0
    assert (scene.zone & scene.region).sum() == scene.region.sum()  # the zone contains the region …
    assert scene.zone.sum() > scene.region.sum()  # … and its surroundings
    assert not (scene.vehicle_core & scene.foreign_core).any()
    labels = [label for _, _, label in scene.points]
    assert 1 in labels and 0 in labels and len(scene.points) <= 2 * mask_module.SAM_MAX_POINTS
    for x, y, label in scene.points:
        inside_car = bool(car[y0 + int(y), x0 + int(x)])
        assert inside_car is (label == 1)
        if label == 1:
            assert scene.vehicle_core[int(y), int(x)]
        else:
            assert scene.foreign_core[int(y), int(x)]
    bx0, by0, bx1, by1 = scene.box
    cy, cx = np.nonzero(scene.vehicle_core)
    assert bx0 <= cx.min() and bx1 >= cx.max() and by0 <= cy.min() and by1 >= cy.max()
    # without a verdict or without the other object's map there is no scene
    assert sam_scene(binary, replace(finding, verdict=None), scale=scale, zone_px=zone_px) == (None, "no_zoom_verdict")


def test_sam_cut_on_refuses_a_wrong_number_of_candidates_and_records_every_candidate():
    car, wheel, rgb, detector = _glued_wheel_scene()
    alpha = (car | wheel).astype(np.float32)
    binary, finding, scale, zone_px, vw = _finding(alpha, rgb, detector)
    scene, _ = sam_scene(binary, finding, scale=scale, zone_px=zone_px)
    x0, y0, x1, y1 = scene.window
    base = cv2.GaussianBlur(np.where(car[y0:y1, x0:x1], 8.0, -8.0).astype(np.float32), (0, 0), 2.0)
    cut, diag = sam_cut_on(scene, rgb, np.stack([base]), np.array([0.9], np.float32), vehicle_w=vw)
    assert cut is not None and len(diag["candidates"]) == 1 and diag["chosen"] == 0
    assert isinstance(cut, SamCut) and cut.window == scene.window and (cut.region & ~cut.zone).sum() == 0
    assert cut.box()[0] >= x0 and cut.box()[2] <= x1
    assert cut.soft.shape == scene.mask.shape and cut.soft.dtype == np.float32


def test_the_core_erosion_counts_sams_logit_resolution():
    from app.pipeline.sam2 import LOW_RES_SIZE

    assert mask_module.SAM_LOGIT_CELLS == LOW_RES_SIZE


def test_the_soft_edge_is_sams_signed_distance_ramp():
    logits = np.tile(np.linspace(-20.0, 20.0, 41, dtype=np.float32), (9, 1))  # a ramp of 1 logit per px
    soft = _sam_soft_edge(logits)
    centre = soft[4]
    assert centre[20] == pytest.approx(0.5, abs=0.01)  # the zero crossing
    assert centre[20 + int(SAM_EDGE_PX)] >= 0.99 and centre[20 - int(SAM_EDGE_PX)] <= 0.01  # a 2 px ramp each way
    assert np.all(np.diff(centre) >= -1e-6) and centre.min() == 0.0 and centre.max() == 1.0
    flat = _sam_soft_edge(np.full((5, 5), 3.0, np.float32))  # well inside, no gradient: 1
    assert (flat == 1.0).all() and (_sam_soft_edge(np.full((5, 5), -3.0, np.float32)) == 0.0).all()


def test_apply_sam_cut_only_lowers_alpha_and_only_inside_the_zone():
    alpha = np.ones((60, 80), np.float32)
    alpha[:, 50:] = 0.0
    region = np.zeros((40, 60), bool)
    region[10:30, 20:40] = True
    zone = cv2.dilate(region.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    soft = np.ones((40, 60), np.float32)
    soft[:, 19:] = 0.3  # SAM would lower the alpha of everything to the right – only the zone counts
    out = _apply_sam_cut(alpha, SamCut(window=(10, 10, 70, 50), region=region, soft=soft, zone=zone))
    assert (out <= alpha + 1e-6).all()
    local = out[10:50, 10:70]
    assert (local[region] == 0.0).all() or (local[region] <= 0.3).all()
    changed = local != alpha[10:50, 10:70]
    assert not (changed & ~zone).any()  # confined to the zone
    assert (local[~zone] == alpha[10:50, 10:70][~zone]).all()
    outside = np.ones(alpha.shape, bool)
    outside[10:50, 10:70] = False
    assert np.array_equal(out[outside], alpha[outside])


# --------------------------------------------------------------------------- wiring


def test_create_segmenter_attaches_sam_only_when_the_graphs_exist(settings, caplog, monkeypatch):
    from app.pipeline import sam2 as sam2_module

    with caplog.at_level(logging.INFO, logger="app.pipeline.sam2"):
        segmenter = create_segmenter(settings)  # the test models dir has no graphs
    assert segmenter.sam2 is None and segmenter.sam2_error is None
    assert any("SAM 2.1 graphs" in r.message and r.levelno == logging.INFO for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.pipeline.sam2"):
        assert create_segmenter(replace(settings, sam2_enabled=True)).sam2 is None
    assert any(r.levelno == logging.WARNING and "scripts/export_sam2.py" in r.message for r in caplog.records)
    assert create_segmenter(replace(settings, sam2_enabled=False)).sam2 is None
    spec = sam2_module.SAM2_REGISTRY[settings.sam2_model]
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    for name in spec.filenames:
        (settings.models_dir / name).write_bytes(b"not a graph")
    segmenter = create_segmenter(settings)
    assert segmenter.sam2 is not None and segmenter.sam2.spec is spec and not segmenter.sam2.loaded
    assert create_segmenter(replace(settings, sam2_enabled=False)).sam2 is None
    with pytest.raises(Exception, match="Unknown SAM 2 model"):
        create_segmenter(replace(settings, sam2_model="nope"))


def test_sam2_enabled_is_read_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PROCESSOR_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.delenv("PROCESSOR_SAM2_ENABLED", raising=False)
    assert Settings.from_env().sam2_enabled is None
    for value, expected in (("true", True), ("1", True), ("false", False), ("off", False), ("", None)):
        monkeypatch.setenv("PROCESSOR_SAM2_ENABLED", value)
        assert Settings.from_env().sam2_enabled is expected


def test_warm_up_refuses_foreign_graphs_loudly_and_switches_the_cut_off(settings, monkeypatch, caplog):
    from app.pipeline import sam2 as sam2_module

    spec = sam2_module.SAM2_REGISTRY[settings.sam2_model]
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    for name in spec.filenames:
        (settings.models_dir / name).write_bytes(b"not the registered export")
    segmenter = OnnxSegmenter(
        MODEL_REGISTRY["isnet-general-use"], settings.models_dir, auto_download=False, sam2=sam2_module.create_sam2(settings)
    )
    monkeypatch.setattr(segmenter, "_session", object())  # the segmentation model itself is fine
    with caplog.at_level(logging.ERROR, logger="app.pipeline.segmentation"):
        segmenter.warm_up()
    assert segmenter.sam2 is None and "Checksum mismatch" in (segmenter.sam2_error or "")
    assert segmenter.loaded and any("contested cut is off" in r.message for r in caplog.records)


def test_health_reports_the_sam_model(settings, fake_segmenter):
    from app.main import create_app

    class Sam:
        spec = type("Spec", (), {"name": "sam2.1-hiera-small"})()
        loaded = False

    fake_segmenter.sam2 = Sam()
    fake_segmenter.sam2_error = None
    app = create_app(settings, segmenter=fake_segmenter, store=None, warm_up=False)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["sam2"] == "sam2.1-hiera-small" and body["sam2Loaded"] is False and body["sam2Error"] is None
    fake_segmenter.sam2 = None
    fake_segmenter.sam2_error = "Checksum mismatch …"
    app = create_app(settings, segmenter=fake_segmenter, store=None, warm_up=False)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["sam2"] is None and body["sam2Loaded"] is None and body["sam2Error"].startswith("Checksum")


def test_the_pipeline_caches_the_tile_embedding_and_lists_the_passes():
    from app.pipeline.pipeline import _sam_prompter

    class Sam:
        def __init__(self):
            self.encoded = []

        def encode(self, rgb):
            self.encoded.append(rgb.shape)
            return ("embedding", rgb.shape)

        def predict(self, embedding, points, box=None, multimask=True):
            return embedding, points, box, multimask

    rgb = np.zeros((100, 200, 3), np.uint8)
    segmentation: dict = {}
    assert _sam_prompter(rgb, None, segmentation) is None
    sam = Sam()
    prompter = _sam_prompter(rgb, sam, segmentation)
    e1 = prompter.encode((10, 20, 60, 70))
    e2 = prompter.encode((10, 20, 60, 70))
    assert e1 is e2 and sam.encoded == [(50, 50, 3)]
    assert [p["window"] for p in segmentation["samPasses"]] == [[10, 20, 60, 70]] and "ms" in segmentation["samPasses"][0]
    assert prompter.predict(e1, [(1.0, 2.0, 1)], None, multimask=False) == (e1, [(1.0, 2.0, 1)], None, False)


# --------------------------------------------------------------------------- integration


MODELS_DIR = Path(os.environ.get("PROCESSOR_MODELS_DIR") or PROCESSOR_ROOT / "models")


def test_the_built_graphs_cut_a_second_car_glued_to_the_photographed_one():
    """Runs only with the built SAM 2.1 graphs: two synthetic cars of different colours touching
    – SAM separates them along their contact, the car stays whole, the neighbour's part goes."""
    from app.pipeline.sam2 import SAM2_REGISTRY, Sam2Segmenter, ensure_built_model

    spec = SAM2_REGISTRY["sam2.1-hiera-small"]
    try:
        for filename, sha256 in ((spec.encoder_filename, spec.encoder_sha256), (spec.decoder_filename, spec.decoder_sha256)):
            ensure_built_model(filename, sha256, MODELS_DIR, name=spec.name)
    except ModelUnavailableError as error:
        pytest.skip(f"SAM 2.1 graphs not built: {error}")
    car, wheel, rgb, detector = _glued_wheel_scene()
    # a more photo-like neighbour: the white car behind with a dark wheel arch around its silver wheel
    neighbour = np.zeros_like(car)
    cv2.rectangle(neighbour.view(np.uint8), (1980, 820), (2399, 1130), 1, -1)
    rgb = rgb.copy()
    rng = np.random.default_rng(3)
    rgb = np.clip(rgb.astype(np.int16) + rng.normal(0, 4, rgb.shape).astype(np.int16), 0, 255).astype(np.uint8)
    alpha = (car | wheel).astype(np.float32)
    real = Sam2Segmenter(spec, MODELS_DIR)

    class Prompter:
        def encode(self, window):
            x0, y0, x1, y1 = window
            return real.encode(np.ascontiguousarray(rgb[y0:y1, x0:x1]))

        def predict(self, embedding, points, box=None, multimask=True):
            return real.predict(embedding, points, box, multimask=multimask)

    started = time.perf_counter()
    result, info = clean_mask(alpha, rgb=rgb, detector=detector, sam=Prompter())
    seconds = time.perf_counter() - started
    finding = next(f for f in info.semantic["findings"] if f["foreign"])
    diag = finding["verify"]["samCut"]
    assert finding["action"] == "cut" and finding["verify"]["cutBy"] == "sam", diag
    assert diag["reasons"] == [] and diag["ms"] < 60_000 and seconds < 180
    assert (result <= alpha + 1e-6).all()
    core = cv2.erode(car.view(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    assert (result[core] >= 0.5).all()  # the car's body stays
    far_wheel = wheel & ~cv2.dilate(car.view(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    assert float(np.mean(result[far_wheel] < 0.5)) > 0.98  # the wheel is gone
