"""Quality gate: every code, exact German messages, priorities, generous thresholds."""

from __future__ import annotations

import numpy as np
import pytest

from app.pipeline.mask import BBox, MaskInfo
from app.pipeline.placement import PlacementResult
from app.pipeline.quality import (
    MESSAGES,
    GateReport,
    QualityGateError,
    check_contacts,
    check_cropped,
    check_mask,
    check_perspective,
    check_source_resolution,
    check_upscale,
    touching_borders,
)
from app.pipeline.vehicle_geometry import TyreContact, VehicleGeometry
from app.presets import QualityConfig
from app.showroom.plates import load_plate_set

CFG = QualityConfig()

#: Pinned – the app shows exactly these texts (src/lib/processing/quality-gate.ts).
EXPECTED = {
    "source_resolution_too_low": "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen.",
    "vehicle_too_small": "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
    "vehicle_cropped": "Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren.",
    "mask_low_confidence": "Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen.",
    "ground_contact_uncertain": "Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein.",
    "perspective_mismatch": "Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren.",
}


@pytest.fixture(scope="module")
def plates(synthetic_plates):
    return load_plate_set(synthetic_plates / "plates.json")


def test_messages_are_exactly_the_agreed_german_texts():
    assert MESSAGES == EXPECTED
    for code, text in EXPECTED.items():
        error = QualityGateError(code, {"x": 1})
        assert error.code == code and error.user_message == text and error.details == {"x": 1}
    with pytest.raises(ValueError):
        QualityGateError("something_else")


def test_source_resolution_gate():
    check_source_resolution(1600, 1200, CFG)  # the minimum passes
    check_source_resolution(4032, 3024, CFG)
    with pytest.raises(QualityGateError) as info:
        check_source_resolution(1024, 768, CFG)
    assert info.value.code == "source_resolution_too_low"
    assert info.value.details["checks"]["resolution"]["longEdge"] == 1024


def _alpha(box, size=(1500, 2000)):
    alpha = np.zeros(size, np.float32)
    x0, y0, x1, y1 = box
    alpha[y0:y1, x0:x1] = 1.0
    return alpha, BBox(x0, y0, x1, y1)


@pytest.mark.parametrize(
    "box, sides",
    [
        ((300, 400, 1700, 1100), []),
        ((20, 400, 1980, 1100), []),  # tight framing (98 % of the width) is fine
        ((0, 400, 1700, 1100), ["left"]),
        ((5, 400, 1700, 1100), ["left"]),  # segmenters fade out a few px before the border
        ((300, 400, 2000, 1100), ["right"]),
        ((300, 400, 1700, 1500), ["bottom"]),
        ((300, 0, 1700, 1100), ["top"]),  # roof cut over its full width
    ],
)
def test_cropped_vehicles_are_detected(box, sides):
    alpha, bbox = _alpha(box)
    assert touching_borders(alpha, bbox, CFG) == sides
    report = GateReport()
    check_cropped(report, alpha, bbox, CFG)
    assert report.failed_code == ("vehicle_cropped" if sides else None)


def test_a_bumper_tip_reaching_the_border_band_is_not_a_cropped_car():
    alpha, bbox = _alpha((20, 400, 1700, 1100))
    alpha[700:730, 2:20] = 1.0  # rounded front tip, 30 rows (4 % of the car height)
    assert touching_borders(alpha, bbox, CFG) == []


def test_an_antenna_touching_the_top_border_is_not_a_cropped_car():
    alpha, bbox = _alpha((300, 60, 1700, 1100))
    alpha[0:60, 1000:1004] = 1.0  # thin antenna up to the border
    assert touching_borders(alpha, bbox, CFG) == []


def _placement(scale: float) -> PlacementResult:
    return PlacementResult(scale, 0, 0, 100, 50, 2400, 1800, "width", 0.81, 1500, scale)


@pytest.mark.parametrize(
    "scale, width_ratio, code",
    [
        (1.2, 0.3, None),
        (1.5, 0.3, None),  # the limit itself is allowed
        (1.8, 0.3, "vehicle_too_small"),  # small in a good photo → "zu klein"
        (1.8, 0.7, "source_resolution_too_low"),  # big in a tiny photo → resolution
    ],
)
def test_upscale_gate(scale, width_ratio, code):
    report = GateReport()
    check_upscale(report, _placement(scale), BBox(0, 0, int(width_ratio * 4000), 500), 4000, CFG)
    assert report.failed_code == code


def test_mask_confidence_gate():
    alpha, bbox = _alpha((300, 400, 1700, 1100))
    good = MaskInfo(coverage=0.3, uncertain_fraction=0.04, split_parts=0)
    report = GateReport()
    check_mask(report, alpha, bbox, good, CFG)
    assert report.failed_code is None
    for info, reason in [
        (MaskInfo(coverage=0.3, uncertain_fraction=0.45), "uncertain_edges"),
        (MaskInfo(coverage=0.3, split_parts=5), "split"),
        (MaskInfo(coverage=0.95), "coverage"),
    ]:
        report = GateReport()
        check_mask(report, alpha, bbox, info, CFG)
        assert report.failed_code == "mask_low_confidence"
        assert reason in report.checks["mask"]["reasons"]
    sparse = np.zeros_like(alpha)
    sparse[400:1100:10, 300:1700] = 1.0  # stripes: 10 % of the bbox
    report = GateReport()
    check_mask(report, sparse, bbox, good, CFG)
    assert report.checks["mask"]["reasons"] == ["fill"]


def test_an_uncut_foreign_object_rejects_the_job():
    """A background object detected in the outline whose cut line was not certain
    (``foreign_suspected``) must not end in an accepted result; one that was cut
    (``foreign_removed``, warning only) passes."""
    alpha, bbox = _alpha((300, 400, 1700, 1100))
    report = GateReport()
    check_mask(report, alpha, bbox, MaskInfo(coverage=0.3, foreign_suspected=[[1500, 250, 1560, 420]]), CFG)
    assert report.failed_code == "mask_low_confidence"
    assert report.checks["mask"]["reasons"] == ["foreign_object"]
    assert report.checks["mask"]["foreignSuspected"] == 1
    report = GateReport()
    check_mask(report, alpha, bbox, MaskInfo(coverage=0.3, foreign_removed=[[1500, 250, 1560, 420]]), CFG)
    assert report.failed_code is None and report.checks["mask"]["foreignSuspected"] == 0


def _geometry(bbox: BBox, contacts: list[tuple[float, float]], outer_rise: float | None = None) -> VehicleGeometry:
    tyres = tuple(TyreContact(x, y, 0.9) for x, y in sorted(contacts))
    rise = max(c[1] for c in contacts) - min(c[1] for c in contacts) if len(contacts) > 1 else 0.0
    outer_rise = rise if outer_rise is None else outer_rise
    return VehicleGeometry(
        bbox=bbox,
        candidates=tyres,
        contacts=tyres,
        contact_confidence=0.9 if len(tyres) > 1 else 0.4,
        near_end=None,
        near_end_confidence=0.0,
        contact_rise=rise,
        outer_rise=outer_rise,
    )


def _three_quarter(plate, rise_factor: float, aspect_factor: float = 1.0) -> VehicleGeometry:
    width = 1400
    height = int(round(width / (plate.proxy_aspect() * aspect_factor)))
    bbox = BBox(300, 400, 300 + width, 400 + height)
    rise = rise_factor * plate.proxy_rise_ratio() * width
    low = bbox.y1
    return _geometry(bbox, [(bbox.x0 + 0.45 * width, low), (bbox.x0 + 0.88 * width, low - rise)])


@pytest.mark.parametrize(
    "rise_factor, aspect_factor, passed",
    [
        (1.0, 1.0, True),  # like the plate camera
        (0.5, 1.0, True),  # camera lower / farther away (0.8 m)
        (2.0, 0.85, True),  # eye height and close (wide angle)
        (1.6, 0.62, True),  # close photo of a tall estate/SUV
        (1.8, 0.73, False),  # from an upper floor: steep floor and roof/bonnet visible
        (1.0, 0.6, True),  # tall vehicle (van) at normal height
        (4.0, 0.9, False),  # from a parking deck
        (0.05, 1.3, True),  # 3/4 photo taken closer to the side: tyres nearly level – fine
        (1.0, 0.4, False),  # nearly top-down
    ],
)
def test_perspective_gate_is_generous_for_normal_photos(plates, rise_factor, aspect_factor, passed):
    plate = plates.plate("front_left_45")
    report = GateReport()
    check_perspective(report, _three_quarter(plate, rise_factor, aspect_factor), plate, CFG)
    assert report.checks["perspective"]["passed"] is passed
    assert report.failed_code == (None if passed else "perspective_mismatch")


def test_a_low_rise_limit_can_be_switched_on(plates):
    plate = plates.plate("front_left_45")
    report = GateReport()
    check_perspective(report, _three_quarter(plate, 0.05), plate, QualityConfig(min_rise_factor=0.2))
    assert report.checks["perspective"]["reasons"] == ["rise_low"]


def test_photo_from_above_with_hidden_far_tyres_is_a_perspective_mismatch(plates):
    plate = plates.plate("rear_right_45")
    bbox = BBox(300, 300, 1700, 1300)
    geometry = _geometry(bbox, [(900, 1300)], outer_rise=0.6 * bbox.width)  # one visible contact
    report = GateReport()
    check_perspective(report, geometry, plate, CFG)
    assert report.failed_code == "perspective_mismatch"
    assert {"outer_rise_high", "high_view"} & set(report.checks["perspective"]["reasons"])


def test_side_views_only_check_the_aspect(plates):
    plate = plates.plate("left_side")
    bbox = BBox(200, 600, 1900, 1120)
    report = GateReport()
    check_perspective(report, _geometry(bbox, [(500, 1120), (1600, 1118)]), plate, CFG)
    assert report.failed_code is None and "riseFactor" not in report.checks["perspective"]


@pytest.mark.parametrize(
    "shot, contacts, code",
    [
        ("front_left_45", [(500, 1100), (1500, 950)], None),
        ("front_left_45", [(500, 1100)], "ground_contact_uncertain"),
        ("left_side", [(500, 1100), (600, 1100)], "ground_contact_uncertain"),  # one tyre, two vertices
        ("left_side", [], "ground_contact_uncertain"),
        ("front", [], None),  # front/rear views: bumpers hide the tyre contacts – not required
        ("rear", [(800, 1100)], None),
    ],
)
def test_ground_contact_gate(shot, contacts, code):
    bbox = BBox(300, 500, 1700, 1100)
    geometry = _geometry(bbox, contacts)
    report = GateReport()
    check_contacts(report, geometry, shot, CFG)
    assert report.failed_code == code


def test_the_first_failing_check_in_priority_order_is_reported():
    report = GateReport()
    report.add("contacts", "ground_contact_uncertain")
    report.add("perspective", "perspective_mismatch")
    report.add("upscale", "vehicle_too_small")
    report.add("cropped", None)
    with pytest.raises(QualityGateError) as info:
        report.raise_if_failed(plateUsed="front")
    assert info.value.code == "vehicle_too_small"
    assert info.value.user_message == EXPECTED["vehicle_too_small"]
    assert info.value.details["plateUsed"] == "front"
    assert info.value.details["checks"]["perspective"]["passed"] is False
    report.add("cropped", "vehicle_cropped")
    assert report.failed_code == "vehicle_cropped"
