"""Vehicle detector (RF-DETR-Seg ONNX wrapper) and the semantic check of the mask.

The real model is never loaded: the ONNX session is faked, the semantic check
runs against ``FakeDetector`` (tests/semantic_fixtures.py), whose instance masks
have the coarse resolution of the real model."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.pipeline.detection import (
    DETECTOR_REGISTRY,
    MAX_TILES,
    OnnxDetector,
    _split_outputs,
    create_detector,
    decode_instances,
    tile_windows,
    vehicle_box,
)
from app.pipeline.mask import assess_mask, clean_mask, mask_bbox
from app.pipeline.quality import (
    FOREIGN_OBJECT_MESSAGE,
    GateReport,
    QualityGateError,
    check_foreign,
)
from app.pipeline.segmentation import ModelUnavailableError, SegmentationError
from tests.semantic_fixtures import FakeDetector, side_view

# --------------------------------------------------------------------------- model wrapper


def _outputs(queries: list[tuple[int, float, tuple[float, float, float, float]]], q: int = 8, cells: int = 16):
    """RF-DETR-like raw outputs: (class id, probability, cxcywh) per query, rest empty."""
    boxes = np.zeros((1, q, 4), np.float32)
    logits = np.full((1, q, 91), -9.0, np.float32)
    masks = np.full((1, q, cells, cells), -5.0, np.float32)
    for i, (cls, prob, box) in enumerate(queries):
        boxes[0, i] = box
        logits[0, i, cls] = np.log(prob / (1 - prob))
        cx, cy, w, h = box
        masks[0, i, int((cy - h / 2) * cells) : int((cy + h / 2) * cells), int((cx - w / 2) * cells) : int((cx + w / 2) * cells)] = 5.0
    return boxes, logits, masks


def test_outputs_are_found_by_shape_not_by_their_names():
    boxes, logits, masks = _outputs([(3, 0.9, (0.5, 0.5, 0.4, 0.3))])
    # the export's names are mislabeled and the order is not guaranteed
    found = _split_outputs([masks, logits, boxes])
    assert found[0] is not None and found[0].shape == (8, 4)
    assert found[1].shape == (8, 91) and found[2].shape == (8, 16, 16)
    with pytest.raises(ModelUnavailableError):  # another export: a service problem, no retake
        _split_outputs([boxes, logits])


def test_instances_are_scored_labelled_and_scaled_to_the_window():
    boxes, logits, masks = _outputs(
        [(3, 0.95, (0.5, 0.5, 0.5, 0.4)), (1, 0.6, (0.2, 0.4, 0.1, 0.4)), (62, 0.2, (0.8, 0.8, 0.1, 0.1)), (12, 0.9, (0.1, 0.1, 0.1, 0.1))]
    )  # fmt: skip
    instances = decode_instances(boxes[0], logits[0], masks[0], width=1000, height=800)
    assert [i.label for i in instances] == ["car", "person"]  # below 0.3 and unused COCO id 12 dropped
    car = instances[0]
    assert car.is_vehicle and car.score == pytest.approx(0.95)
    assert car.box == pytest.approx((250.0, 240.0, 750.0, 560.0))
    logits_px = car.logits(1000, 800)
    assert logits_px.shape == (800, 1000) and logits_px[400, 500] > 0 and logits_px[50, 50] < 0


class _Session:
    def __init__(self, outputs):
        self.outputs = outputs
        self.feeds = []

    def get_inputs(self):
        return [type("Input", (), {"name": "input"})()]

    def run(self, _names, feeds):
        self.feeds.append(feeds)
        return list(self.outputs)


def test_detector_runs_on_the_window_with_imagenet_normalisation(monkeypatch, tmp_path):
    detector = OnnxDetector(DETECTOR_REGISTRY["rf-detr-seg-medium"], tmp_path, auto_download=False)
    session = _Session(_outputs([(8, 0.8, (0.5, 0.5, 1.0, 1.0))]))
    monkeypatch.setattr(detector, "_get_session", lambda: session)
    rgb = np.full((600, 900, 3), 255, np.uint8)
    instances = detector.detect(rgb, (100, 50, 500, 450))
    tensor = session.feeds[0]["input"]
    assert tensor.shape == (1, 3, 432, 432)
    assert tensor[0, 0, 0, 0] == pytest.approx((1.0 - 0.485) / 0.229, rel=1e-4)
    assert len(instances) == 1 and instances[0].label == "truck"
    assert instances[0].box == pytest.approx((0.0, 0.0, 400.0, 400.0))  # relative to the window


def test_a_missing_detector_model_is_a_service_problem(tmp_path):
    detector = OnnxDetector(DETECTOR_REGISTRY["rf-detr-seg-medium"], tmp_path / "none", auto_download=False)
    assert not detector.loaded
    with pytest.raises(ModelUnavailableError):
        detector.detect(np.zeros((100, 100, 3), np.uint8))


def test_a_detector_file_with_another_output_layout_fails_at_warm_up(monkeypatch, tmp_path):
    """Checked with one blank inference when the model is loaded: /health reports a wrong
    export (503) instead of every exterior job failing later."""
    import onnxruntime

    from app.pipeline import detection

    boxes, logits, _ = _outputs([])
    monkeypatch.setattr(detection, "ensure_model", lambda *args, **kwargs: tmp_path / "model.onnx")
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *args, **kwargs: _Session([boxes, logits]))
    detector = OnnxDetector(DETECTOR_REGISTRY["rf-detr-seg-medium"], tmp_path, auto_download=False)
    with pytest.raises(ModelUnavailableError):
        detector.warm_up()
    assert not detector.loaded
    good = _Session(_outputs([(3, 0.9, (0.5, 0.5, 0.4, 0.3))]))
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *args, **kwargs: good)
    detector.warm_up()  # the registered layout: loaded, validated with one blank input
    assert detector.loaded and good.feeds[0]["input"].shape == (1, 3, 432, 432) and not good.feeds[0]["input"].any()


def test_an_existing_model_file_that_is_not_the_registered_one_is_refused(tmp_path):
    """A damaged file or another export under the registered name: a service problem."""
    from app.pipeline.segmentation import ensure_model

    spec = DETECTOR_REGISTRY["rf-detr-seg-medium"]
    (tmp_path / spec.filename).write_bytes(b"not the pinned export")
    with pytest.raises(ModelUnavailableError, match="Checksum mismatch"):
        ensure_model(spec, tmp_path, auto_download=False)
    detector = OnnxDetector(spec, tmp_path, auto_download=False)
    with pytest.raises(ModelUnavailableError):
        detector.warm_up()
    assert not detector.loaded


def test_detector_is_configured_by_name(settings):
    from dataclasses import replace

    assert create_detector(settings).spec.name == "rf-detr-seg-medium"
    with pytest.raises(SegmentationError):
        create_detector(replace(settings, detector_model="nope"))


def test_registered_models_are_pinned_to_a_revision_and_a_checksum():
    for spec in DETECTOR_REGISTRY.values():
        assert spec.url.startswith("https://huggingface.co/") and "/resolve/" in spec.url
        revision = spec.url.split("/resolve/")[1].split("/")[0]
        assert len(revision) == 40  # a commit sha, never a branch
        assert len(spec.sha256) == 64 and spec.license == "Apache-2.0"


def test_the_segmenter_carries_the_detector_and_reports_both_models(settings):
    from app.pipeline.segmentation import create_segmenter

    segmenter = create_segmenter(settings)
    assert segmenter.detector is not None and segmenter.detector.spec.name == settings.detector_model
    assert not segmenter.loaded


def test_vehicle_box_prefers_the_large_central_vehicle():
    from app.pipeline.detection import Instance

    mask = np.zeros((4, 4), np.float32)
    parked = Instance("car", 0.95, (0, 100, 600, 400), mask)  # big, at the edge
    subject = Instance("car", 0.9, (800, 700, 2400, 1500), mask)  # bigger and central
    person = Instance("person", 0.99, (0, 0, 3000, 2000), mask)
    assert vehicle_box([parked, person, subject], 3200, 2400) == subject.box
    assert vehicle_box([person], 3200, 2400) is None


def test_tiles_cover_the_vehicle_with_at_most_max_tiles():
    for bbox, size in [((300, 900, 3500, 2300), (3840, 2880)), ((900, 600, 2900, 2500), (3840, 2880)), ((100, 1500, 2000, 2300), (2160, 3840))]:  # fmt: skip
        tiles = tile_windows(bbox, *size)
        assert 1 <= len(tiles) <= MAX_TILES
        covered = np.zeros(size[::-1], bool)
        for x0, y0, x1, y1 in tiles:
            assert x1 - x0 == y1 - y0 and 0 <= x0 and x1 <= size[0] and 0 <= y0 and y1 <= size[1]
            covered[y0:y1, x0:x1] = True
        assert covered[bbox[1] : bbox[3], bbox[0] : bbox[2]].all()


# --------------------------------------------------------------------------- semantic check


def _photo(mask: np.ndarray) -> np.ndarray:
    rgb = np.full(mask.shape + (3,), 150, np.uint8)
    rgb[mask] = (24, 34, 78)
    return rgb


def _gate(info) -> GateReport:
    report = GateReport()
    check_foreign(report, info)
    return report


def test_a_neighbour_cars_wheel_glued_to_the_front_is_found_and_the_photo_rejected():
    car = side_view()
    wheel = np.zeros_like(car)
    cv2.circle(wheel.view(np.uint8), (2060, 1010), 55, 1, -1)  # beside the front bumper, merged
    neighbour = np.zeros_like(car)
    cv2.rectangle(neighbour.view(np.uint8), (1980, 820), (2399, 1130), 1, -1)
    neighbour &= ~car  # visible part of the car parked behind (includes its wheel)
    alpha = (car | wheel).astype(np.float32)
    detector = FakeDetector().add("car", car, 0.95).add("car", neighbour, 0.9)
    result, info = clean_mask(alpha, rgb=_photo(car | wheel), detector=detector)
    assert info.vehicle_detected is True
    assert len(info.foreign_suspected) == 1
    x0, y0, x1, y1 = info.foreign_suspected[0]
    assert 1990 <= x0 and x1 <= 2125 and 940 <= y0 and y1 <= 1075
    assert np.array_equal(result >= 0.5, alpha >= 0.5)  # nothing is guessed – the photo is rejected
    finding = info.semantic["findings"][0]
    assert finding["evidence"] == "other_object" and finding["foreign"] and finding["labels"] == ["car"]
    with pytest.raises(QualityGateError) as raised:
        _gate(info).raise_if_failed()
    assert raised.value.code == "mask_low_confidence" and raised.value.user_message == FOREIGN_OBJECT_MESSAGE
    codes = [w.code for w in assess_mask(result, mask_bbox(result), info)]
    assert "background_object_suspected" in codes


def test_thin_vehicle_parts_never_trigger_even_where_another_object_claims_the_background():
    car = side_view()
    parts = np.zeros_like(car, dtype=np.uint8)
    cv2.rectangle(parts, (1000, 520), (1007, 662), 1, -1)  # antenna mast on the roof (roof y=660)
    cv2.rectangle(parts, (900, 650), (1500, 662), 1, -1)  # roof rail
    cv2.rectangle(parts, (1780, 835), (1840, 880), 1, -1)  # mirror head …
    cv2.rectangle(parts, (1735, 866), (1782, 874), 1, -1)  # … on a thin arm from the A-pillar
    cv2.rectangle(parts, (370, 886), (470, 901), 1, -1)  # spoiler lip on the tail
    full = car | parts.astype(bool)
    head = np.zeros_like(car)
    head[835:881, 1780:1841] = True
    behind = np.zeros_like(car)
    cv2.rectangle(behind.view(np.uint8), (300, 450), (2300, 900), 1, -1)
    behind &= ~(car | head)  # a bus behind the whole upper half
    # the vehicle instance has the mirror head (a blob) but none of the thin parts
    detector = FakeDetector().add("car", car | head, 0.95).add("bus", behind, 0.9)
    result, info = clean_mask(full.astype(np.float32), rgb=_photo(full), detector=detector)
    assert info.foreign_suspected == [] and info.foreign_removed == []
    assert not any(f["foreign"] for f in info.semantic["findings"])
    expected, _ = clean_mask(full.astype(np.float32))
    assert np.array_equal(result, expected)  # the semantic check changed nothing
    for y, x in [(600, 1003), (655, 1200), (850, 1810), (870, 1760), (890, 420)]:
        assert result[y, x] > 0.5  # antenna, rail, mirror head and arm, spoiler lip
    assert _gate(info).failed_code is None


def test_a_sign_on_the_wall_behind_the_roof_is_found_without_a_class_for_it():
    car = side_view()
    sign = np.zeros_like(car)
    cv2.rectangle(sign.view(np.uint8), (900, 420), (1250, 680), 1, -1)  # stands on the roof (roof at y=660)
    alpha = (car | sign).astype(np.float32)
    detector = FakeDetector().add("car", car, 0.95)  # nothing claims the sign
    _, info = clean_mask(alpha, rgb=_photo(car | sign), detector=detector)
    assert len(info.foreign_suspected) == 1
    x0, y0, x1, y1 = info.foreign_suspected[0]
    assert x0 >= 880 and x1 <= 1270 and y0 >= 400 and y1 <= 700
    assert _gate(info).failed_code == "mask_low_confidence"


def test_a_roof_box_the_detector_sees_as_part_of_the_car_is_kept():
    car = side_view()
    box = np.zeros_like(car)
    cv2.rectangle(box.view(np.uint8), (900, 560), (1450, 668), 1, -1)  # roof box on the roof
    vehicle = car | box
    detector = FakeDetector().add("car", vehicle, 0.95)
    result, info = clean_mask(vehicle.astype(np.float32), rgb=_photo(vehicle), detector=detector)
    assert info.foreign_suspected == [] and info.foreign_removed == []
    assert np.array_equal(result >= 0.5, vehicle)
    assert _gate(info).failed_code is None


def test_a_separate_part_that_is_another_object_is_dropped_without_touching_the_car():
    car = side_view()
    top = np.zeros_like(car)
    cv2.rectangle(top.view(np.uint8), (1700, 720), (1790, 790), 1, -1)  # sign top above the hood, 10 px gap
    alpha = (car | top).astype(np.float32)
    sign = np.zeros_like(car)
    cv2.rectangle(sign.view(np.uint8), (1700, 600), (1790, 1100), 1, -1)
    detector = FakeDetector().add("car", car, 0.95).add("stop sign", sign & ~car, 0.8)
    _, plain = clean_mask(alpha)  # without the detector the blob is kept as a part of the car
    assert plain.components_kept == 2
    result, info = clean_mask(alpha, rgb=_photo(car | top), detector=detector)
    assert info.foreign_suspected == [] and len(info.foreign_removed) == 1
    assert (result[720:791, 1700:1791] == 0).all()
    assert np.array_equal(result >= 0.5, car)  # every car pixel stays
    assert _gate(info).failed_code is None
    assert "background_object_removed" in [w.code for w in assess_mask(result, mask_bbox(result), info)]


def test_without_a_detected_vehicle_the_mask_is_kept_and_a_warning_given():
    car = side_view()
    wheel = np.zeros_like(car)
    cv2.circle(wheel.view(np.uint8), (2060, 1010), 55, 1, -1)
    alpha = (car | wheel).astype(np.float32)
    detector = FakeDetector().add("person", np.zeros_like(car) | wheel, 0.9)
    result, info = clean_mask(alpha, rgb=_photo(car | wheel), detector=detector)
    expected, _ = clean_mask(alpha)
    assert info.vehicle_detected is False and info.foreign_suspected == []
    assert np.array_equal(result, expected)
    assert _gate(info).failed_code is None
    assert "vehicle_not_detected" in [w.code for w in assess_mask(result, mask_bbox(result), info)]


def test_ground_the_detector_does_not_claim_low_on_the_car_is_ignored():
    car = side_view()
    shadow = np.zeros_like(car)
    cv2.ellipse(shadow.view(np.uint8), (1200, 1150), (300, 40), 0, 0, 360, 1, -1)  # dark ground under the sill
    alpha = (car | shadow).astype(np.float32)
    detector = FakeDetector().add("car", car, 0.95)
    _, info = clean_mask(alpha, rgb=_photo(car | shadow), detector=detector)
    assert info.foreign_suspected == [] and info.semantic["findings"] == []
    assert _gate(info).failed_code is None


def test_the_vehicles_own_part_given_to_an_object_by_a_coarse_tile_is_kept_after_the_zoom():
    """A tail light corner that a coarse grid tile hands to the scooter behind it: the zoom
    tile centred on it sees it as part of the car."""
    car = side_view()
    corner = np.zeros_like(car)
    cv2.rectangle(corner.view(np.uint8), (2040, 905), (2070, 960), 1, -1)  # the car's own rear light corner
    vehicle = car | corner
    scooter = np.zeros_like(car)
    cv2.rectangle(scooter.view(np.uint8), (2030, 700), (2300, 1150), 1, -1)
    detector = FakeDetector().add("car", vehicle, 0.95).add("motorcycle", scooter & ~vehicle, 0.9)
    result, info = clean_mask(vehicle.astype(np.float32), rgb=_photo(vehicle), detector=detector)
    assert info.foreign_suspected == []
    assert np.array_equal(result >= 0.5, vehicle)


# --------------------------------------------------------------------------- missing parts


def _mirror(car: np.ndarray) -> np.ndarray:
    """A side mirror at the A-pillar (80 × 60 px, about 4.8 % × 3.6 % of the car length)."""
    mirror = np.zeros_like(car)
    cv2.rectangle(mirror.view(np.uint8), (1775, 842), (1855, 902), 1, -1)
    return mirror


def test_a_side_mirror_the_mask_lost_is_found_and_the_photo_rejected():
    """BiRefNet dropped a dark mirror in front of a dark car; the detector's vehicle sees it."""
    from app.pipeline.quality import VEHICLE_PART_MESSAGE, check_parts

    car = side_view()
    mirror = _mirror(car)
    gap = np.zeros_like(car)
    cv2.rectangle(gap.view(np.uint8), (700, 1140), (1700, 1195), 1, -1)  # under the sill, between the wheels
    detector = FakeDetector().add("car", car | mirror | gap, 0.95)  # coarse instance: mirror and gap
    result, info = clean_mask(car.astype(np.float32), rgb=_photo(car | mirror), detector=detector)
    assert len(info.parts_missing) == 1 and len(info.part_regions) == 1
    x0, y0, x1, y1 = info.parts_missing[0]
    assert 1755 <= x0 and x1 <= 1875 and 825 <= y0 and y1 <= 915  # the mirror – not the gap under the car
    assert np.array_equal(result >= 0.5, car)  # nothing is drawn in
    finding = next(f for f in info.semantic["findings"] if f["evidence"] == "missing_part")
    assert finding["missing"] and finding["action"] == "missing" and finding["verify"]["vehicleCover"] >= 0.6
    report = GateReport()
    check_foreign(report, info)
    check_parts(report, info)
    with pytest.raises(QualityGateError) as raised:
        report.raise_if_failed()
    assert raised.value.code == "mask_low_confidence" and raised.value.user_message == VEHICLE_PART_MESSAGE
    assert raised.value.details["checks"]["parts"]["reasons"] == ["vehicle_part_missing"]


def test_a_complete_mask_and_the_instance_masks_coarse_band_report_no_missing_part():
    car = side_view()
    vehicle = car | _mirror(car)
    detector = FakeDetector().add("car", vehicle, 0.95)
    _, info = clean_mask(vehicle.astype(np.float32), rgb=_photo(vehicle), detector=detector)
    assert info.parts_missing == []
    assert not [f for f in info.semantic["findings"] if f["evidence"] == "missing_part"]
    # a part nobody sees is never reported (the detector is no second segmenter)
    _, info = clean_mask(car.astype(np.float32), rgb=_photo(car), detector=FakeDetector().add("car", car, 0.95))
    assert info.parts_missing == []


def test_a_small_clipped_edge_is_no_missing_part():
    """The outline cut through an indicator lens (≈ 1.5 % of the car length thick) is below
    the part size – a limitation, not a rejection."""
    car = side_view()
    lens = np.zeros_like(car)
    cv2.rectangle(lens.view(np.uint8), (2040, 920), (2063, 975), 1, -1)  # 23 px beyond the front end
    detector = FakeDetector().add("car", car | lens, 0.95)
    _, info = clean_mask(car.astype(np.float32), rgb=_photo(car | lens), detector=detector)
    assert info.parts_missing == []


# --------------------------------------------------------------------------- reach beyond the vehicle


def test_the_reach_beyond_the_vehicle_instance_is_measured_in_mask_cells():
    from app.pipeline.detection import vehicle_semantics, verify_region

    car = side_view()
    rgb = _photo(car)
    detector = FakeDetector().add("car", car, 0.95)
    sem = vehicle_semantics(rgb, car, detector)

    def region(x0: int, x1: int) -> np.ndarray:
        photo = np.zeros_like(car)
        photo[950:1050, x0:x1] = True
        return cv2.resize(photo.astype(np.uint8), (sem.shape[1], sem.shape[0]), interpolation=cv2.INTER_NEAREST) > 0

    sliver = verify_region(rgb, detector, sem, region(2040, 2046), 1680)  # 6 px beyond the front end
    block = verify_region(rgb, detector, sem, region(2040, 2110), 1680)  # 70 px beyond it
    assert sliver.conclusive and block.conclusive
    assert sliver.reach_cells < 3.0 and block.reach_cells > 10.0
    assert sliver.as_dict()["reachCells"] == round(sliver.reach_cells, 2)


@pytest.mark.parametrize(
    "cover, reach, rejected",
    [
        (0.0, 11.0, True),  # a neighbour car's wheel (side_right's hubcap: 0.0 / 11.4 cells)
        (0.12, 11.0, True),  # suv_fr45's neighbour wheel
        (0.137, 1.52, False),  # side_right_3x4: the car's own bonnet edge, claimed by the snowy car behind
        (0.98, 0.4, False),  # the car's own tail light
    ],
)
def test_a_region_is_foreign_only_when_it_reaches_beyond_the_coarse_vehicle_outline(monkeypatch, cover, reach, rejected):
    """Within about two mask cells of the zoom tile's vehicle instance the coarse outline
    cannot tell the car's own edge from the object behind it – such a region is kept."""
    from app.pipeline import detection
    from app.pipeline.detection import RegionCheck

    def stub(rgb, detector, sem, region, vehicle_size):
        sem.runs += 1
        return RegionCheck((0, 0, 10, 10), cover, 1.0 - cover, ("car",), True, reach)

    monkeypatch.setattr(detection, "verify_region", stub)
    car = side_view()
    wheel = np.zeros_like(car)
    cv2.circle(wheel.view(np.uint8), (2060, 1010), 55, 1, -1)
    neighbour = np.zeros_like(car)
    cv2.rectangle(neighbour.view(np.uint8), (1980, 820), (2399, 1130), 1, -1)
    detector = FakeDetector().add("car", car, 0.95).add("car", neighbour & ~car, 0.9)
    _, info = clean_mask((car | wheel).astype(np.float32), rgb=_photo(car | wheel), detector=detector)
    findings = [f for f in info.semantic["findings"] if f["evidence"] == "other_object"]
    assert findings and findings[0]["verify"]["reachCells"] == reach
    assert (_gate(info).failed_code == "mask_low_confidence") is rejected
