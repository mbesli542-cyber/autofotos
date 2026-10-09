"""Vehicle geometry from synthetic masks: tyre contacts, near end, contact rise."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from app.pipeline.mask import mask_bbox
from app.pipeline.vehicle_geometry import analyse_vehicle, bottom_profile, top_profile
from tests.conftest import make_vehicle_photo, three_quarter_mask


def _soft(mask: np.ndarray) -> np.ndarray:
    mask = mask.astype(np.float32)
    if mask.max() > 1:
        mask /= 255.0
    return cv2.GaussianBlur(mask, (0, 0), 1.2)


def test_side_view_has_two_tyre_contacts_at_the_wheel_bottoms():
    _, mask = make_vehicle_photo()
    alpha = _soft(mask)
    geometry = analyse_vehicle(alpha, mask_bbox(alpha))
    assert len(geometry.contacts) == 2
    width = 2000
    body_w = int(width * 0.7)
    x0 = width // 2 - body_w // 2
    expected = [x0 + int(0.2 * body_w), x0 + body_w - int(0.2 * body_w)]
    for contact, x in zip(geometry.contacts, expected):
        assert contact.x == pytest.approx(x, abs=3)
        assert contact.y == pytest.approx(int(1500 * 0.78) + 1, abs=2)  # bottom edge of the tyre
        assert contact.confidence >= 0.8
    assert geometry.contact_rise == pytest.approx(0, abs=1.5)
    assert geometry.contact_span > 0.5
    assert geometry.near_end is None  # level tyres: no near end
    # bumper/sill corners are candidates but not plausible tyre contacts
    assert len(geometry.candidates) > len(geometry.contacts)


@pytest.mark.parametrize("near_end", ["left", "right"])
def test_three_quarter_view_contacts_rise_and_near_end(near_end):
    mask, wheels, bw = three_quarter_mask(near_end=near_end)
    alpha = _soft(mask)
    geometry = analyse_vehicle(alpha, mask_bbox(alpha))
    assert geometry.near_end == near_end
    assert geometry.near_end_confidence >= 0.3
    # the near front and the near rear tyre (the far front one is optional)
    near_front, near_rear = wheels[0], wheels[1]
    for cx, cy, r in (near_front, near_rear):
        contact = min(geometry.contacts, key=lambda c: abs(c.x - cx))
        assert contact.x == pytest.approx(cx, abs=4)
        assert contact.y == pytest.approx(cy + r, abs=3)
    lowest = geometry.lowest
    assert lowest.y == max(c.y for c in geometry.contacts)
    assert geometry.ground_y == lowest.y
    assert geometry.contact_rise_ratio == pytest.approx(0.12, abs=0.01)  # near rear tyre 0.12 × width higher
    assert geometry.aspect == pytest.approx(1.0 / 0.56, rel=0.03)


def test_a_flat_blob_under_the_car_has_no_plausible_tyre_contacts():
    """The cut-out includes the car's ground shadow: a flat bottom edge, no tyres."""
    _, mask = make_vehicle_photo()
    blob = (mask * 255).astype(np.uint8)
    cv2.rectangle(blob, (250, 1150), (1750, 1215), 255, -1)
    alpha = _soft(blob)
    geometry = analyse_vehicle(alpha, mask_bbox(alpha))
    assert geometry.contacts == ()
    assert geometry.ground_y == mask_bbox(alpha).y1  # placement falls back to the bbox bottom


def test_profiles_and_metadata_are_plain_json():
    mask, _, _ = three_quarter_mask()
    alpha = _soft(mask)
    bbox = mask_bbox(alpha)
    bottom, has = bottom_profile(alpha, bbox.x0, bbox.x1)
    top, _ = top_profile(alpha, bbox.x0, bbox.x1)
    assert has.all() and (bottom >= top).all()
    data = json.loads(json.dumps(analyse_vehicle(alpha, bbox).as_dict()))
    assert data["nearEnd"] == "left" and len(data["contacts"]) >= 2


def test_solid_roof_ignores_a_thin_antenna():
    _, mask = make_vehicle_photo()
    blob = (mask * 255).astype(np.uint8)
    roof = int(np.flatnonzero(blob.any(axis=1))[0])
    blob[roof - 120 : roof, 1000:1004] = 255  # antenna
    alpha = _soft(blob)
    geometry = analyse_vehicle(alpha, mask_bbox(alpha))
    assert geometry.bbox.y0 <= roof - 119
    assert geometry.solid_top == pytest.approx(roof, abs=3)


# --------------------------------------------------------------------------- tyre vs bumper


def _bumper_scene(width: int = 2000, height: int = 1400, *, bumper_colour=(190, 192, 196), wide_bumper: bool = True):
    """Front 3/4-like silhouette: a front bumper on the left whose lowest point sits LOWER in
    the image than the near front tyre (it is nearer to the camera), body-coloured; a dark
    tyre with a round bottom; a second (near rear) tyre higher up on the right."""
    rgb = np.full((height, width, 3), (120, 125, 118), np.uint8)
    mask = np.zeros((height, width), np.uint8)
    base = 1100
    body = np.array([[200, 700], [1800, 650], [1800, 900], [1300, 960], [700, 1000], [200, 960]], np.int32)
    cv2.fillPoly(mask, [body], 255)
    rgb[mask > 0] = bumper_colour
    # front bumper: a wide, shallow curve whose lowest point is 8 px below the tyre bottom
    bumper = np.zeros_like(mask)
    half = 260 if wide_bumper else 40
    cv2.ellipse(bumper, (480, base + 8 - 60), (half, 60), 0, 0, 180, 255, -1)
    cv2.rectangle(bumper, (480 - half, 900), (480 + half, base + 8 - 60), 255, -1)
    mask[bumper > 0] = 255
    rgb[bumper > 0] = bumper_colour
    tyres = [(1000, base, 110), (1650, base - 150, 85)]
    for cx, bottom, r in tyres:
        circle = np.zeros_like(mask)
        cv2.circle(circle, (cx, bottom - r), r, 255, -1)
        mask[circle > 0] = 255
        rgb[circle > 0] = (24, 24, 26)
        cv2.circle(rgb, (cx, bottom - r), int(0.6 * r), (170, 172, 176), -1)  # rim
    return rgb, mask, tyres


def test_a_lower_body_coloured_bumper_is_not_a_tyre_contact():
    rgb, mask, tyres = _bumper_scene()
    alpha = _soft(mask)
    bbox = mask_bbox(alpha)
    geometry = analyse_vehicle(alpha, bbox, rgb=rgb)
    assert geometry.colour_evidence
    bumper = [c for c in geometry.candidates if abs(c.x - 480) < 120]
    assert bumper and all(c.confidence < 0.5 for c in bumper)  # lowest point, but no tyre
    assert len(geometry.contacts) == 2
    for (cx, bottom, r), contact in zip(tyres, geometry.contacts):
        assert contact.x == pytest.approx(cx, abs=6)
        assert contact.y == pytest.approx(bottom, abs=3)
        assert contact.side == "near"
        # visible tyre width ≈ the wheel diameter (the chord near the floor, circle model)
        assert contact.width == pytest.approx(2 * r, rel=0.25)
    # the ground line is the tyre, not the bumper 8 px lower
    assert geometry.ground_y == pytest.approx(tyres[0][1], abs=3)
    assert geometry.near_end == "left"


def test_the_bumper_foot_is_too_wide_even_without_colours():
    rgb, mask, _ = _bumper_scene()
    alpha = _soft(mask)
    geometry = analyse_vehicle(alpha, mask_bbox(alpha))  # geometry only
    assert not geometry.colour_evidence
    assert all(abs(c.x - 480) > 120 for c in geometry.contacts)
    bumper = next(c for c in geometry.candidates if abs(c.x - 480) < 120)
    assert bumper.evidence["footFactor"] == 0.0


def test_a_narrow_body_coloured_point_is_rejected_by_its_colour():
    """A narrow bumper corner passes the foot-width test – the paint colour above it does not."""
    rgb, mask, _ = _bumper_scene(wide_bumper=False)
    alpha = _soft(mask)
    bbox = mask_bbox(alpha)
    corner = [c for c in analyse_vehicle(alpha, bbox, rgb=rgb).candidates if abs(c.x - 480) < 60]
    assert corner and corner[0].evidence["colourFactor"] < 0.5 and corner[0].confidence < 0.5
    dark = rgb.copy()
    dark[(mask > 0) & (np.abs(np.arange(mask.shape[1])[None, :] - 480) < 60)] = (25, 25, 27)  # black plastic
    corner = [c for c in analyse_vehicle(alpha, bbox, rgb=dark).candidates if abs(c.x - 480) < 60]
    assert corner[0].evidence["colourFactor"] > 0.9  # dark & neutral: colour alone cannot tell


def test_rgb_must_match_the_mask():
    rgb, mask, _ = _bumper_scene()
    alpha = _soft(mask)
    with pytest.raises(ValueError):
        analyse_vehicle(alpha, mask_bbox(alpha), rgb=rgb[:-10])


def test_contact_details_are_plain_json():
    rgb, mask, _ = _bumper_scene()
    alpha = _soft(mask)
    data = json.loads(json.dumps(analyse_vehicle(alpha, mask_bbox(alpha), rgb=rgb).as_dict()))
    details = data["contactDetails"]
    assert {"x", "y", "confidence", "width", "side", "evidence"} <= set(details[0])
    assert data["colourEvidence"] is True
