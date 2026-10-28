"""Window pass of portrait/square frames (segmentation.segment_vehicle) – no model."""

from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.segmentation import (
    merge_parts,
    segment_vehicle,
    vehicle_window,
    window_edges_touched,
)
from tests.semantic_fixtures import ColourSegmenter

NAVY = (24, 34, 78)


def _frame(width: int, height: int, box: tuple[int, int, int, int]) -> np.ndarray:
    rgb = np.full((height, width, 3), 170, np.uint8)
    x0, y0, x1, y1 = box
    rgb[y0:y1, x0:x1] = NAVY
    return rgb


def test_window_grows_the_vehicle_box_and_stays_between_square_and_4_3():
    # side view in a portrait frame: the full width, framed like a landscape photo (4:3)
    x0, y0, x1, y1 = vehicle_window((100, 1500, 2060, 2200), 2160, 3840)
    assert (x0, x1) == (0, 2160)
    assert y0 <= 1500 - 0.2 * 700 and y1 >= 2200 + 0.2 * 700
    assert (x1 - x0) / (y1 - y0) == pytest.approx(4 / 3, rel=0.01)
    # front view: at least square, inside the photo
    x0, y0, x1, y1 = vehicle_window((500, 1200, 1700, 2300), 2160, 2880)
    assert x1 - x0 >= y1 - y0 - 1 and 0 <= x0 and x1 <= 2160 and 0 <= y0 and y1 <= 2880
    assert x0 <= 500 - 0.2 * 1200 and x1 >= 1700 + 0.2 * 1200


def test_landscape_frames_keep_the_whole_frame_pass():
    rgb = _frame(1600, 1200, (300, 500, 1300, 900))
    segmenter = ColourSegmenter(NAVY)
    alpha, info = segment_vehicle(rgb, segmenter, (300, 500, 1300, 900))
    assert segmenter.calls == [(1200, 1600)]
    assert info["passes"] == [{"window": None, "ms": info["passes"][0]["ms"]}]
    assert alpha.shape == (1200, 1600)


def test_portrait_frames_are_segmented_in_a_window_around_the_detected_vehicle():
    box = (80, 1500, 2080, 2300)
    rgb = _frame(2160, 3840, box)
    rgb[200:400, 900:1200] = NAVY  # something navy far above the car (outside the window)
    segmenter = ColourSegmenter(NAVY)
    alpha, info = segment_vehicle(rgb, segmenter, box)
    window = tuple(info["window"])
    assert info["windowUsed"] and info["boxSource"] == "detector" and len(segmenter.calls) == 1
    assert segmenter.calls[0] == (window[3] - window[1], window[2] - window[0])
    assert alpha.shape == (3840, 2160)
    assert alpha[1900, 1000] > 0.9  # the car
    assert alpha[300, 1000] == 0.0  # zero outside the window
    assert np.all(alpha[: window[1]] == 0) and np.all(alpha[window[3] :] == 0)


def test_without_a_detected_vehicle_the_window_comes_from_a_whole_frame_pass():
    box = (80, 1500, 2080, 2300)
    rgb = _frame(2160, 3840, box)
    segmenter = ColourSegmenter(NAVY)
    alpha, info = segment_vehicle(rgb, segmenter, None)
    assert info["boxSource"] == "firstPass" and info["windowUsed"]
    assert segmenter.calls[0] == (3840, 2160) and len(segmenter.calls) == 2
    assert alpha[1900, 1000] > 0.9


def test_a_vehicle_running_into_the_window_edge_falls_back_to_the_whole_frame():
    rgb = _frame(2160, 3840, (80, 1500, 2080, 2300))
    rgb[2300:3500, 900:1300] = NAVY  # the "vehicle" continues far below the detected box
    segmenter = ColourSegmenter(NAVY)
    alpha, info = segment_vehicle(rgb, segmenter, (80, 1500, 2080, 2300))
    assert info["windowUsed"] is False and info["windowEdgesTouched"] == ["bottom"]
    assert segmenter.calls[-1] == (3840, 2160)
    assert alpha[3000, 1000] > 0.9  # whole-frame alpha


def test_window_edges_at_the_photo_border_do_not_count():
    alpha = np.ones((100, 200), np.float32)
    assert window_edges_touched(alpha, (0, 0, 200, 100), 200, 100) == []
    assert window_edges_touched(alpha, (10, 0, 210, 100), 300, 100) == ["left", "right"]


def test_a_part_the_window_pass_lost_is_taken_from_the_other_pass_inside_its_claim_only():
    """merge_parts: only the claimed region changes, only solid parts attached to the car."""
    coarse = np.zeros((400, 600), np.float32)
    coarse[200:300, 100:500] = 1.0  # the car body of the window pass, without its mirror
    other = coarse.copy()
    other[170:202, 440:480] = 1.0  # the whole-frame pass has the mirror …
    other[100:150, 200:260] = 1.0  # … and a merged background object elsewhere
    other[120:160, 455:470] = 1.0  # … and a blob inside the claim, not attached to the car
    scale = 0.5  # working resolution of the claims (half size)
    claim = np.zeros((200, 300), bool)
    claim[55:101, 215:245] = True  # the mirror's claimed region (photo 430–490 × 110–202) …
    core = np.zeros_like(claim)
    core[86:100, 221:239] = True  # … and its core (photo 442–478 × 172–200)
    merged, taken = merge_parts(coarse, other, [(core, claim)], scale)
    assert len(taken) == 1
    x0, y0, x1, _ = taken[0]
    assert 438 <= x0 and x1 <= 482 and 168 <= y0
    assert merged[185, 460] == 1.0  # the mirror
    assert merged[125, 230] == 0.0 and merged[140, 462] == 0.0  # neither the background nor the blob
    changed = merged != coarse
    assert not changed[:, :420].any() and not changed[:, 500:].any()
    # the other pass lacks the part as well: nothing is taken
    merged, taken = merge_parts(coarse, coarse, [(core, claim)], scale)
    assert taken == [] and np.array_equal(merged, coarse)
