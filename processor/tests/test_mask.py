import cv2
import numpy as np
import pytest

from app.pipeline.mask import BBox, assess_mask, clean_mask, mask_bbox, refine_alpha
from app.pipeline.segmentation import SegmentationError


def test_refined_mask_has_image_dimensions(vehicle):
    rgb, truth = vehicle
    coarse = cv2.resize(cv2.resize(truth, (256, 192)), (rgb.shape[1], rgb.shape[0]))
    alpha = refine_alpha(rgb, coarse)
    assert alpha.shape == rgb.shape[:2]
    assert alpha.dtype == np.float32
    assert 0.0 <= alpha.min() and alpha.max() <= 1.0


def test_refinement_sharpens_edges_towards_ground_truth(vehicle):
    rgb, truth = vehicle
    coarse = cv2.resize(cv2.resize(truth, (200, 150), interpolation=cv2.INTER_AREA), (rgb.shape[1], rgb.shape[0]))
    refined = refine_alpha(rgb, coarse)
    band = cv2.dilate((truth > 0.5).astype(np.uint8), np.ones((21, 21))) - cv2.erode((truth > 0.5).astype(np.uint8), np.ones((21, 21)))
    err_coarse = np.abs(coarse - truth)[band > 0].mean()
    err_refined = np.abs(refined - truth)[band > 0].mean()
    assert err_refined < err_coarse


def test_clean_mask_drops_far_blobs_keeps_attached_parts_and_fills_windows():
    alpha = np.zeros((600, 800), np.float32)
    cv2.rectangle(alpha, (200, 250), (600, 420), 1.0, -1)  # body
    cv2.rectangle(alpha, (280, 160), (520, 250), 1.0, -1)  # cabin
    cv2.rectangle(alpha, (320, 180), (480, 235), 0.0, -1)  # see-through window (hole, upper part)
    cv2.rectangle(alpha, (300, 400), (500, 420), 0.0, -1)  # gap under the car, open towards the floor
    cv2.circle(alpha, (188, 260), 10, 1.0, -1)  # mirror just outside the body
    cv2.circle(alpha, (60, 60), 20, 1.0, -1)  # unrelated blob far away
    cleaned, info = clean_mask(alpha)
    assert cleaned.shape == alpha.shape
    assert cleaned[60, 60] == 0.0  # far blob removed
    assert cleaned[260, 188] > 0.5  # mirror kept
    assert cleaned[200, 400] == 1.0  # window hole filled (upper part)
    assert info.holes_filled == 1
    assert cleaned[410, 400] == 0.0  # low gap near the ground stays open


def test_empty_mask_raises():
    with pytest.raises(SegmentationError):
        clean_mask(np.zeros((100, 100), np.float32))


def test_bbox_and_cropped_warning():
    alpha = np.zeros((400, 600), np.float32)
    alpha[100:300, 0:500] = 1.0  # touches the left border
    bbox = mask_bbox(alpha)
    assert bbox == BBox(0, 100, 500, 300)
    _, info = clean_mask(alpha)
    codes = [w.code for w in assess_mask(alpha, bbox, info)]
    assert "vehicle_cropped" in codes


def test_thin_gaps_under_roof_rails_are_not_filled_but_windows_are():
    from app.pipeline.mask import clean_mask

    alpha = np.zeros((600, 900), np.float32)
    alpha[150:500, 100:800] = 1.0  # body + cabin block (vehicle height 350)
    alpha[250:330, 250:420] = 0.0  # window the model missed (80 px = 23 %)
    alpha[160:170, 450:650] = 0.0  # thin gap under a roof rail (10 px = 3 %)
    result, info = clean_mask(alpha)
    assert result[290, 330] == 1.0  # window filled
    assert result[165, 550] == 0.0  # rail gap stays background
    assert info.holes_filled == 1


class _FakeSession:
    def __init__(self, output):
        self.output = output

    def get_inputs(self):
        return [type("Input", (), {"name": "input_image"})()]

    def run(self, _outputs, _feeds):
        return [self.output]


@pytest.mark.parametrize("peak, area", [(0.09, 0.3), (0.98, 0.0005)])
def test_photos_without_a_confident_object_are_rejected(monkeypatch, tmp_path, peak, area):
    from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter, SegmentationError

    out = np.full((1, 1, 1024, 1024), 0.001, np.float32)
    rows = int(1024 * area) or 1
    out[0, 0, :rows, :] = peak
    segmenter = OnnxSegmenter(MODEL_REGISTRY["isnet-general-use"], tmp_path, auto_download=False)
    monkeypatch.setattr(segmenter, "_get_session", lambda: _FakeSession(out))
    with pytest.raises(SegmentationError):
        segmenter.segment(np.full((300, 400, 3), 128, np.uint8))


def test_a_confident_object_is_returned_at_photo_size(monkeypatch, tmp_path):
    from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter

    out = np.zeros((1, 1, 1024, 1024), np.float32)
    out[0, 0, 400:700, 200:800] = 0.97
    segmenter = OnnxSegmenter(MODEL_REGISTRY["isnet-general-use"], tmp_path, auto_download=False)
    monkeypatch.setattr(segmenter, "_get_session", lambda: _FakeSession(out))
    alpha = segmenter.segment(np.full((300, 400, 3), 128, np.uint8))
    assert alpha.shape == (300, 400) and alpha.max() == pytest.approx(1.0)


def test_a_car_split_by_a_pole_is_kept_whole_and_flagged():
    from app.pipeline.mask import assess_mask, clean_mask, mask_bbox

    alpha = np.zeros((600, 1000), np.float32)
    alpha[200:480, 100:900] = 1.0
    alpha[150:520, 520:545] = 0.0  # pole in front of the car
    result, info = clean_mask(alpha)
    bbox = mask_bbox(result)
    assert (bbox.x0, bbox.x1) == (100, 900)  # both halves kept
    assert info.split_parts == 1
    assert any(w.code == "vehicle_occluded" for w in assess_mask(result, bbox, info))


def test_a_separate_car_further_away_is_not_merged():
    from app.pipeline.mask import clean_mask, mask_bbox

    alpha = np.zeros((600, 1400), np.float32)
    alpha[200:480, 50:750] = 1.0  # the photographed car
    alpha[220:470, 950:1350] = 1.0  # another car with a clear gap
    result, info = clean_mask(alpha)
    assert mask_bbox(result).x1 == 750
