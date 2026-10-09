import cv2
import numpy as np
import pytest

from app.pipeline.mask import BBox, assess_mask, clean_mask, mask_bbox
from app.pipeline.segmentation import SegmentationError


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


# --------------------------------------------------------------- foreign objects


def _rear_view(cone: bool = True) -> np.ndarray:
    """Rear view silhouette (1000 x 1400): body, cabin, roof antenna rod, shark
    fin, two mirrors on thin arms – and a traffic cone standing behind the right
    shoulder (its lower part hidden by the car)."""
    alpha = np.zeros((1000, 1400), np.float32)
    cv2.rectangle(alpha, (200, 450), (1000, 800), 1.0, -1)  # body (shoulder at y=450)
    cabin = np.array([[300, 451], [900, 451], [820, 250], [380, 250]], np.int32)
    cv2.fillPoly(alpha, [cabin], 1.0)  # cabin, roof at y=250
    cv2.rectangle(alpha, (520, 200), (523, 250), 1.0, -1)  # antenna rod
    cv2.fillPoly(alpha, [np.array([[640, 251], [700, 251], [662, 215]], np.int32)], 1.0)  # shark fin
    cv2.rectangle(alpha, (115, 380), (170, 445), 1.0, -1)  # left mirror head …
    cv2.rectangle(alpha, (170, 438), (200, 452), 1.0, -1)  # … on a short arm at the door
    cv2.rectangle(alpha, (1030, 380), (1085, 445), 1.0, -1)  # right mirror head
    cv2.rectangle(alpha, (1000, 438), (1030, 452), 1.0, -1)
    cv2.rectangle(alpha, (240, 330), (243, 450), 1.0, -1)  # whip antenna standing on the shoulder
    if cone:
        cv2.fillPoly(alpha, [np.array([[900, 470], [960, 470], [935, 300], [925, 300]], np.int32)], 1.0)
    return alpha


def test_cone_standing_behind_the_car_is_cut_from_the_outline():
    alpha = _rear_view()
    result, info = clean_mask(alpha)
    assert len(info.foreign_removed) == 1
    x0, y0, x1, y1 = info.foreign_removed[0]
    assert 880 <= x0 and x1 <= 980 and 290 <= y0 and y1 <= 460
    for x, y in [(930, 310), (930, 400), (906, 445), (954, 445)]:  # cone, also its wide base
        assert result[y, x] == 0.0
    kept = [(930, 456), (980, 455), (850, 452), (600, 300), (521, 210), (662, 230), (140, 400), (1060, 400), (241, 340)]
    for x, y in kept:
        assert result[y, x] > 0.5  # car, antennas, fin and both mirrors untouched
    # nothing of the car below the shoulder line is lost
    assert (result[455:800, 200:1000] > 0.5).all()
    codes = [w.code for w in assess_mask(result, mask_bbox(result), info)]
    assert "background_object_removed" in codes


def test_vehicle_parts_are_never_cut_rear_view():
    alpha = _rear_view(cone=False)
    result, info = clean_mask(alpha)
    assert info.foreign_removed == [] and info.foreign_suspected == []
    assert np.array_equal(result >= 0.5, alpha >= 0.5)


def test_vehicle_parts_are_never_cut_side_and_front_views():
    side = np.zeros((900, 1600), np.float32)
    cv2.rectangle(side, (150, 450), (1450, 700), 1.0, -1)  # body
    cv2.fillPoly(side, [np.array([[400, 451], [1200, 451], [1050, 280], [520, 280]], np.int32)], 1.0)  # cabin
    for x in (560, 760, 960):  # roof rail feet + rail
        cv2.rectangle(side, (x, 262), (x + 12, 280), 1.0, -1)
    cv2.rectangle(side, (540, 256), (1010, 264), 1.0, -1)
    cv2.rectangle(side, (650, 200), (900, 255), 1.0, -1)  # roof box on the rails
    cv2.rectangle(side, (1350, 410), (1356, 450), 1.0, -1)  # wing struts
    cv2.rectangle(side, (1410, 410), (1416, 450), 1.0, -1)
    cv2.rectangle(side, (1320, 398), (1440, 412), 1.0, -1)  # rear wing
    cv2.rectangle(side, (250, 230), (253, 450), 1.0, -1)  # whip antenna on the front fender
    cv2.rectangle(side, (1230, 380), (1420, 452), 1.0, -1)  # tall rear wing block on the trunk
    cv2.fillPoly(side, [np.array([[950, 281], [1000, 281], [975, 262]], np.int32)], 1.0)  # shark fin
    front = np.zeros((1000, 1400), np.float32)
    cv2.rectangle(front, (250, 450), (1150, 800), 1.0, -1)
    cv2.fillPoly(front, [np.array([[340, 451], [1060, 451], [960, 230], [440, 230]], np.int32)], 1.0)
    cv2.rectangle(front, (150, 300), (210, 440), 1.0, -1)  # tall van mirror head …
    cv2.rectangle(front, (210, 430), (330, 438), 1.0, -1)  # … on a thin arm
    cv2.rectangle(front, (1190, 300), (1250, 440), 1.0, -1)
    cv2.rectangle(front, (1150, 430), (1190, 438), 1.0, -1)
    cv2.rectangle(front, (1095, 232), (1135, 452), 1.0, -1)  # snorkel up to the roof level
    van = np.zeros((1000, 1400), np.float32)
    cv2.rectangle(van, (250, 450), (1150, 800), 1.0, -1)
    cv2.fillPoly(van, [np.array([[340, 451], [1060, 451], [980, 200], [420, 200]], np.int32)], 1.0)
    cv2.rectangle(van, (196, 260), (230, 470), 1.0, -1)  # tall narrow truck mirror beside the body …
    cv2.rectangle(van, (230, 455), (250, 466), 1.0, -1)  # … on a short arm from the door
    cv2.rectangle(van, (1170, 260), (1204, 470), 1.0, -1)
    cv2.rectangle(van, (1150, 455), (1170, 466), 1.0, -1)
    for alpha in (side, front, van):
        result, info = clean_mask(alpha)
        assert info.foreign_removed == [] and info.foreign_suspected == []
        assert np.array_equal(result >= 0.5, alpha >= 0.5)


def _flush_post(rgb_too: bool):
    """Orange post behind the right rear corner, its right side flush with the
    car's flank – the car's outline runs ALONG the post's edge."""
    alpha = np.zeros((900, 1300), np.float32)
    cv2.rectangle(alpha, (150, 420), (1050, 750), 1.0, -1)
    cv2.fillPoly(alpha, [np.array([[260, 421], [940, 421], [860, 220], [340, 220]], np.int32)], 1.0)
    cv2.rectangle(alpha, (1010, 270), (1050, 420), 1.0, -1)  # the post above the shoulder
    rgb = np.full((900, 1300, 3), 150, np.uint8)
    rgb[alpha > 0] = (70, 75, 80)  # grey car
    rgb[270:421, 1010:1051] = (240, 120, 30)  # orange post
    return alpha, (rgb if rgb_too else None)


def test_post_flush_with_the_car_side_is_cut_at_the_colour_edge():
    alpha, rgb = _flush_post(True)
    result, info = clean_mask(alpha, rgb=rgb)
    assert len(info.foreign_removed) == 1
    assert result[300, 1030] == 0.0 and result[415, 1030] == 0.0  # post incl. its foot
    assert (result[425:750, 150:1050] > 0.5).all()  # the car below the shoulder stays


def test_post_is_left_and_the_job_rejected_when_the_cut_line_is_not_certain():
    from app.pipeline.quality import GateReport, check_mask
    from app.presets import QualityConfig

    alpha, _ = _flush_post(False)  # no photo: the right junction cannot be found
    result, info = clean_mask(alpha)
    assert info.foreign_removed == [] and len(info.foreign_suspected) == 1
    assert np.array_equal(result >= 0.5, alpha >= 0.5)  # nothing is guessed
    codes = [w.code for w in assess_mask(result, mask_bbox(result), info)]
    assert "background_object_suspected" in codes
    report = GateReport()  # … and the result is never accepted with the post in it
    check_mask(report, result, mask_bbox(result), info, QualityConfig())
    assert report.failed_code == "mask_low_confidence" and "foreign_object" in report.checks["mask"]["reasons"]


def test_post_attached_by_a_thin_bridge_is_removed():
    side = np.zeros((900, 1600), np.float32)
    cv2.rectangle(side, (150, 450), (1450, 700), 1.0, -1)
    cv2.fillPoly(side, [np.array([[400, 451], [1200, 451], [1050, 280], [520, 280]], np.int32)], 1.0)
    cv2.rectangle(side, (1300, 345), (1330, 430), 1.0, -1)  # post behind the trunk …
    cv2.rectangle(side, (1313, 430), (1317, 450), 1.0, -1)  # … merged by a thin bridge
    result, info = clean_mask(side)
    assert len(info.foreign_removed) == 1
    assert result[360, 1315] == 0.0 and result[440, 1315] == 0.0
    assert (result[452:700, 150:1450] > 0.5).all()


def test_detached_blob_far_from_the_body_is_dropped_but_a_close_mirror_head_is_kept():
    alpha = np.zeros((800, 1400), np.float32)
    cv2.rectangle(alpha, (150, 400), (1250, 650), 1.0, -1)
    cv2.fillPoly(alpha, [np.array([[500, 401], [1100, 401], [1000, 230], [600, 230]], np.int32)], 1.0)
    cv2.circle(alpha, (250, 300), 25, 1.0, -1)  # blob above the hood, inside the box, 75 px away
    cv2.rectangle(alpha, (520, 355), (560, 392), 1.0, -1)  # mirror head, its arm lost (gap ~3 px)
    result, info = clean_mask(alpha)
    assert result[300, 250] == 0.0 and info.detached_dropped == 1
    assert result[370, 540] > 0.5


def test_busy_background_counts_model_fragments_not_refinement_speckle():
    alpha = np.zeros((600, 900), np.float32)
    alpha[200:450, 150:750] = 1.0
    speckled = alpha.copy()
    for k in range(40):  # 40 single-pixel specks along the top edge
        speckled[196, 160 + 14 * k] = 1.0
    _, info = clean_mask(speckled, model=alpha)  # speckle only in the refined alpha
    assert info.model_fragments == 1
    assert "busy_background" not in [w.code for w in assess_mask(speckled, mask_bbox(speckled), info)]
    _, info = clean_mask(speckled, model=speckled)  # the model itself is fragmented
    assert info.model_fragments == 41
    assert "busy_background" in [w.code for w in assess_mask(speckled, mask_bbox(speckled), info)]


def test_the_cut_edge_against_the_car_is_anti_aliased():
    """A post behind a sloping tail: the chord where the cut meets the car is a
    straight, anti-aliased edge (signed-distance ramp) – per column the removed
    alpha follows a line within a fraction of a pixel (a row-wise chord showed
    1 px stair-steps), and the car below the chord is untouched."""
    side = np.zeros((900, 1600), np.float32)
    cv2.fillPoly(side, [np.array([[150, 450], [1150, 450], [1450, 560], [1450, 700], [150, 700]], np.int32)], 1.0)
    cv2.fillPoly(side, [np.array([[400, 451], [1100, 451], [980, 280], [520, 280]], np.int32)], 1.0)
    cv2.rectangle(side, (1270, 330), (1310, 560), 1.0, -1)  # post behind the sloping tail
    result, info = clean_mask(side)
    assert len(info.foreign_removed) == 1
    x0, y0, x1, _ = info.foreign_removed[0]
    columns = np.arange(x0 + 6, x1 - 6)
    removed = np.array([(1.0 - result[y0 - 2 : 640, x]).sum() for x in columns])
    fit = np.polyfit(columns, removed, 1)
    assert 0.25 < fit[0] < 0.5  # follows the slope of the tail
    assert np.abs(removed - np.polyval(fit, columns)).max() < 0.15
    edge = result[y0:640, x0 + 6 : x1 - 6]
    assert ((edge > 0.05) & (edge < 0.95)).any(axis=0).all()  # a soft transition in every column
    assert result[400, 1290] == 0.0 and (result[600:700, 150:1450] > 0.5).all()
