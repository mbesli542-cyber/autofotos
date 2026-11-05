"""Showroom look variants (public/presets/variants/) and the shipped look: the dealer's own
studio backdrop in three shadow strengths – "Schatten mittel" is the shipped preset
(public/presets/autoexperten-standard.json) – plus the renderer features the variants use
(side lines, icons, car further back in the room, per-photo room fit)."""

from __future__ import annotations

import copy
import io
import json
import re
import math
from dataclasses import fields, replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.pipeline.pipeline import process_photo
from app.presets import BackgroundProvider, Placement, PresetConfigError, load_preset, parse_preset
from app.showroom.branding import BrandingConfig, TextConfig, apply_branding
from app.showroom.icons import ICON_NAMES, icon_mask, path_polylines
from app.pipeline import room_depth
from app.showroom import plates as plates_module
from app.showroom.plates import WALL_CLEARANCE_M, load_plate_set, move_proxy_back
from tests.conftest import encode_jpeg

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESETS = REPO_ROOT / "public/presets"
BRAND_DIR = REPO_ROOT / "public/brand"
STANDARD = json.loads((PRESETS / "autoexperten-standard.json").read_text(encoding="utf-8"))
#: The shipped plate set: the dealer's studio (one plate, eight per-shot proxy poses).
REAL_PLATES = PRESETS / STANDARD["background"]["plates"]
NEW_TEXT_FIELDS = ("align", "icon", "rule_length", "rule_gap", "rule_thickness")


def _with(data: dict, path: list, value) -> dict:
    data = copy.deepcopy(data)
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return data


# --------------------------------------------------------------------------- configuration


def test_the_shipped_preset_is_the_dealer_studio_with_medium_shadows():
    """The dealer's decision: his own studio is the shipped look – the "Schatten mittel" variant
    copied over the standard file (same id, so the app needs no change), only name and
    description are the standard preset's."""
    haendler = json.loads((PRESETS / "variants" / "autoexperten-haendler.json").read_text(encoding="utf-8"))
    assert STANDARD["id"] == "autoexperten_standard" and STANDARD["name"] == "AutoExperten Standard"
    assert {k: v for k, v in STANDARD.items() if k not in ("name", "description")} == {
        k: v for k, v in haendler.items() if k not in ("name", "description")
    }
    assert STANDARD["background"]["plates"] == "autoexperten-haendler/plates.json"
    preset = parse_preset(STANDARD)
    assert preset.adjustments.studio_light.enabled
    assert preset.shadow.tint == (1.0, 1.0, 1.0) and preset.shadow.footprint_reach == 0.3
    assert preset.reflection.footprint_reach == 0.3
    # the car stays on the plate's spot at the plate's size (no room fit runs)
    assert preset.placement.width_scale == 1.0 and preset.placement.depth_offset_m == 0.0
    assert not preset.shot_placement
    # every field is written out explicitly (nothing hidden behind a default)
    assert {"widthScale", "depthOffsetM"} <= set(STANDARD["placement"])
    for name in ("city", "website", "phone"):
        assert {"align", "icon", "ruleLength", "ruleGap", "ruleThickness"} <= set(STANDARD["branding"][name])
    for name in ("website", "phone"):
        text = getattr(preset.branding, name)
        default = TextConfig()
        assert all(getattr(text, f) == getattr(default, f) for f in ("align", "icon")), name


def test_the_old_3d_room_plates_are_not_shipped():
    """The eight Blender plates of the earlier 3D room (processor/showroom3d/render_plates.py) are
    no longer in the shipped preset folder (Docker image); no preset references them."""
    assert not (PRESETS / "autoexperten-standard").exists()
    for path in [PRESETS / "autoexperten-standard.json", *sorted((PRESETS / "variants").glob("*.json"))]:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert (PRESETS / data["background"]["plates"]).is_file(), path.name


def test_new_placement_defaults_change_nothing():
    names = {f.name for f in fields(Placement)}
    assert {"width_scale", "depth_offset_m"} <= names
    assert Placement().width_scale == 1.0 and Placement().depth_offset_m == 0.0


#: Branding keeps this share of the brand wall (between the slat panels) free on each side.
BRAND_EDGE_MARGIN = 0.04


def test_branding_keeps_clear_of_the_brand_wall_edges():
    preset = parse_preset(STANDARD)
    width, height = 2680, 1680  # brand area 6.7 m × 4.2 m
    _, layout = apply_branding(_wall(width, height), _flat(preset.branding), BRAND_DIR)
    assert {b[0] for b in layout.boxes} == {"logo", "city"}
    for label, x0, y0, x1, y1 in layout.boxes:
        assert x0 >= BRAND_EDGE_MARGIN * width and x1 <= (1 - BRAND_EDGE_MARGIN) * width, label
    # no element runs into another one
    for i, a in enumerate(layout.boxes):
        for b in layout.boxes[i + 1 :]:
            overlap_x = min(a[3], b[3]) - max(a[1], b[1])
            overlap_y = min(a[4], b[4]) - max(a[2], b[2])
            assert overlap_x <= 0 or overlap_y <= 0, (a[0], b[0])


@pytest.mark.parametrize(
    "path, value",
    [
        (["placement", "widthScale"], 0.5),
        (["placement", "widthScale"], 1.5),
        (["placement", "depthOffsetM"], -0.2),
        (["placement", "depthOffsetM"], 2.5),
        (["shotPlacement", "front", "widthScale"], 2.0),
        (["branding", "website", "align"], "right"),
        (["branding", "website", "icon"], "star"),
        (["branding", "website", "icon"], 1),
        (["branding", "city", "ruleLength"], 0.6),
        (["branding", "city", "ruleGap"], 0.3),
        (["branding", "city", "ruleThickness"], 0.05),
        (["branding", "city", "ruleLenght"], 0.1),
    ],
)
def test_invalid_variant_values_are_configuration_errors(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(STANDARD, path, value))


def test_side_lines_need_a_thickness():
    data = _with(STANDARD, ["branding", "city", "ruleLength"], 0.1)
    data = _with(data, ["branding", "city", "ruleThickness"], 0.0)
    with pytest.raises(PresetConfigError, match="ruleThickness"):
        parse_preset(data)


# --------------------------------------------------------------------------- icons and text decoration


def test_lucide_icons_are_rasterised_as_line_drawings():
    for name in ICON_NAMES:
        mask = icon_mask(name, 96)
        assert mask.shape == (96, 96) and 0.1 < mask.mean() < 0.5  # 2/24 strokes
        assert mask.max() > 0.95 and mask[:2].max() < 0.5  # inside the 24 px view box margin
    globe = icon_mask("globe", 96)
    assert np.abs(globe - globe[:, ::-1]).mean() < 0.03  # symmetric (up to anti-aliasing)
    assert globe[48, 48] > 0.9  # equator and meridian cross in the centre


def test_svg_arcs_end_exactly_at_their_end_points():
    (line,) = path_polylines("M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20")
    assert line[0] == (12.0, 2.0)
    assert any(abs(x - 12) < 1e-9 and abs(y - 22) < 1e-9 for x, y in line)
    assert line[-1] == pytest.approx((12.0, 2.0))
    xs = [p[0] for p in line]
    assert min(xs) > 6 and max(xs) < 18  # a lens, not a circle


def _flat(cfg: BrandingConfig) -> BrandingConfig:
    return replace(cfg, light_match=0.0, mount=replace(cfg.mount, shadow_opacity=0.0))


def _wall(width=2400, height=1800, value=232):
    return np.full((height, width, 3), value, np.uint8)


def test_explicit_default_text_fields_draw_the_same_pixels():
    cfg = BrandingConfig()
    explicit = replace(
        cfg, city=replace(cfg.city, align="center", icon="", rule_length=0.0, rule_gap=0.0, rule_thickness=0.0)
    )
    a, la = apply_branding(_wall(), cfg, BRAND_DIR)
    b, lb = apply_branding(_wall(), explicit, BRAND_DIR)
    assert np.array_equal(a, b) and la == lb


def test_side_lines_frame_the_city_name():
    cfg = _flat(BrandingConfig())
    plain = replace(cfg, website=replace(cfg.website, text=""), phone=replace(cfg.phone, text=""))
    lined = replace(plain, city=replace(plain.city, rule_length=0.1, rule_gap=0.02, rule_thickness=0.002))
    _, base = apply_branding(_wall(), plain, BRAND_DIR)
    out, layout = apply_branding(_wall(), lined, BRAND_DIR)
    bx0, by0, bx1, by1 = base.box("city")
    x0, y0, x1, y1 = layout.box("city")
    width = 2400
    assert x0 == pytest.approx(bx0 - 0.12 * width, abs=3) and x1 == pytest.approx(bx1 + 0.12 * width, abs=3)
    assert (x0 + x1) / 2 == pytest.approx((bx0 + bx1) / 2, abs=2)  # the name stays centred
    dark = out.astype(np.int16).sum(axis=-1) < 3 * 200
    left_line = dark[y0:y1, x0 + 5 : x0 + int(0.1 * width) - 5]
    rows = np.nonzero(left_line.all(axis=1))[0]
    assert 1 <= len(rows) <= 6  # a thin continuous line …
    assert y0 + rows.mean() > (by0 + by1) / 2 - 0.4 * (by1 - by0)  # … at the lowercase letters
    assert not dark[y0:y1, bx0 - int(0.02 * width) + 3 : bx0 - 3].any()  # gap to the text


def test_left_aligned_texts_with_icons_line_up():
    cfg = _flat(BrandingConfig())
    website = replace(cfg.website, align="left", icon="globe", center_x=0.7)
    phone = replace(cfg.phone, align="left", icon="phone", center_x=0.7)
    out, layout = apply_branding(_wall(), replace(cfg, website=website, phone=phone), BRAND_DIR)
    w, p = layout.box("website"), layout.box("phone")
    assert w[0] == p[0] == round(0.7 * 2400)
    _, plain = apply_branding(_wall(), cfg, BRAND_DIR)
    pw = plain.box("website")
    assert (w[2] - w[0]) > (pw[2] - pw[0])  # icon + gap before the text
    icon_cols = out[w[1] : w[3], w[0] : w[0] + 10].astype(np.int16).sum(axis=-1) < 3 * 200
    assert icon_cols.any()


# --------------------------------------------------------------------------- car further back in the room


@pytest.fixture(scope="module")
def plates():
    return load_plate_set(REAL_PLATES)


def test_no_offset_returns_the_plate_itself(plates):
    plate = plates.plate("front_left_45")
    assert move_proxy_back(plate, 0.0) == (plate, 0.0)


@pytest.mark.parametrize("key", ["front_left_45", "rear_left_45", "left_side", "front"])
def test_moving_back_is_perspective_consistent(plates, key):
    plate = plates.plate(key)
    moved, applied = move_proxy_back(plate, 0.3)
    assert 0 < applied <= 0.3
    # further back: higher in the frame, smaller, still centred; contacts on the floor homography
    assert moved.ground_v < plate.ground_v
    assert moved.target_width_ratio < plate.target_width_ratio
    for label, contact in moved.proxy_contacts.items():
        u, v = plate.floor_to_uv(*contact.floor)
        assert (contact.u, contact.v) == pytest.approx((float(u), float(v)), abs=1e-9)
        old = plate.proxy_contacts[label].floor
        assert math.hypot(contact.floor[0] - old[0], contact.floor[1] - old[1]) == pytest.approx(applied)
    shift = np.asarray(moved.proxy_shift)
    assert np.linalg.norm(shift) == pytest.approx(applied)
    # width shrinks like 1 / distance from the camera
    inv = np.linalg.inv(plate.floor_h)
    ref = inv @ np.array([0.5, plate.ground_v, 1.0])
    ref = ref[:2] / ref[2]
    cam = np.asarray(plate.camera.position, np.float64)

    def dist(p):
        return math.dist((p[0], p[1], 0.0), cam)

    expected = dist(ref) / dist(ref + shift)
    assert moved.target_width_ratio / plate.target_width_ratio == pytest.approx(expected, rel=0.03)
    u, _ = plate.floor_to_uv(*(ref + shift))
    assert float(u) == pytest.approx(0.5, abs=1e-6)  # moved along the viewing ray of the centre column
    # bbox shrinks about the ground line, aspect kept
    assert moved.proxy_aspect() == pytest.approx(plate.proxy_aspect(), rel=1e-6)


@pytest.mark.parametrize("key", ["front_left_45", "front", "rear_right_45"])
def test_the_car_never_goes_into_the_wall(plates, key):
    plate = plates.plate(key)
    moved, applied = move_proxy_back(plate, 5.0)
    assert applied < 5.0
    c = moved.proxy_contacts
    centre = np.mean([np.asarray(p.floor) for p in c.values()], axis=0)
    front = 0.5 * (np.asarray(c["front_left"].floor) + np.asarray(c["front_right"].floor))
    rear = 0.5 * (np.asarray(c["rear_left"].floor) + np.asarray(c["rear_right"].floor))
    axis = (front - rear) / np.linalg.norm(front - rear)
    side = np.array([-axis[1], axis[0]])
    corners = [
        centre + i * 0.5 * plate.proxy["lengthM"] * axis + j * 0.5 * plate.proxy["widthM"] * side
        for i in (-1, 1)
        for j in (-1, 1)
    ]
    assert max(p[1] for p in corners) == pytest.approx(-WALL_CLEARANCE_M, abs=1e-6)


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def test_pipeline_places_the_car_further_back_with_its_shadow(settings, fake_segmenter, vehicle):
    base = load_preset(settings, "autoexperten_standard")
    back = replace(base, placement=replace(base.placement, depth_offset_m=0.45))
    backgrounds = BackgroundProvider(settings)
    data = encode_jpeg(vehicle[0], 97)
    near = process_photo(data, preset=base, segmenter=fake_segmenter, backgrounds=backgrounds, shot_key="left_side")
    far = process_photo(data, preset=back, segmenter=fake_segmenter, backgrounds=backgrounds, shot_key="left_side")
    pn, pf = near.metadata["placement"], far.metadata["placement"]
    assert "depthOffsetM" not in pn and pf["depthOffsetM"] > 0.3
    assert pf["achievedWidthRatio"] < pn["achievedWidthRatio"]
    assert pf["groundY"] < pn["groundY"]
    # the contact shadow follows the car: darkest floor row under the car moves up with it
    for result, placement in ((near, pn), (far, pf)):
        out = _decode(result.jpeg).astype(np.float32).mean(axis=-1)
        cx = int(placement["left"] + placement["width"] / 2)
        bottom = int(placement["top"] + placement["height"])
        below = out[bottom + 2 : bottom + 40, cx - 50 : cx + 50].mean()
        floor = out[min(bottom + 200, out.shape[0] - 5) :, cx - 50 : cx + 50].mean()
        assert below < floor  # shadow right under the car, not where the car was before


# --------------------------------------------------------------------------- per-photo room fit


def _wall_clearance(metadata: dict) -> float:
    """Floor between the car's fitted footprint (the final ground model) and the wall (m)."""
    fp = metadata["grounding"]["footprint"]
    (cx, cy), (ux, uy) = fp["centreM"], fp["axis"]
    vx, vy = -uy, ux
    corners = [
        cy + a * uy + b * vy for a in (fp["frontM"], -fp["rearM"]) for b in (fp["leftM"], -fp["rightM"])
    ]
    return -max(corners)


def _run(settings, segmenter, photo, shot, preset):
    return process_photo(
        encode_jpeg(photo[0], 97), preset=preset, segmenter=segmenter, backgrounds=BackgroundProvider(settings),
        shot_key=shot,
    )  # fmt: skip


def _variant(preset, depth=0.0, scale=1.0):
    return replace(preset, placement=replace(preset.placement, depth_offset_m=depth, width_scale=scale))


def _without_branding(preset):
    """The room fit on its own: the shipped dealer lettering sits low on the (synthetic) wall, so a
    car moved back and up in the frame would also be limited by the headroom below it."""
    return replace(preset, branding=replace(preset.branding, enabled=False))


def test_the_default_placement_runs_no_room_fit(settings, fake_segmenter, three_quarter, monkeypatch):
    calls = []
    original = room_depth.footprint_wall_y
    monkeypatch.setattr(room_depth, "footprint_wall_y", lambda *a: calls.append(1) or original(*a))
    base = load_preset(settings, "autoexperten_standard")
    result = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    assert not calls
    assert "roomFit" not in result.metadata["placement"] and "depthOffsetM" not in result.metadata["placement"]


def test_moving_back_never_binds_the_floor_harder(settings, fake_segmenter, three_quarter, monkeypatch):
    # a deep room (no wall limits): moving back far enough brings the far tyres to the wall
    # line – there the car would only be shrunk on its spot (toy car); the fit stops before
    monkeypatch.setattr(plates_module, "WALL_CLEARANCE_M", -2.0)
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", -2.0)
    base = _without_branding(load_preset(settings, "autoexperten_standard"))
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base).metadata["placement"]
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 2.0)).metadata["placement"]
    fit = far["roomFit"]
    assert fit["reducedBy"] == ["floor"] and 0.5 < fit["depthOffsetM"] < 2.0
    base_floor = near["targetFraction"] if near["limitedBy"] == "floor" else 1.0
    assert far["limitedBy"] != "floor" or far["targetFraction"] >= base_floor - room_depth.FLOOR_TOLERANCE
    assert far["targetFraction"] >= 0.99  # the car has the size perspective gives it there …
    assert far["achievedWidthRatio"] < near["achievedWidthRatio"]  # … smaller because further back
    assert far["groundY"] < near["groundY"]


def test_the_fitted_footprint_keeps_the_wall_clearance(settings, fake_segmenter, three_quarter, monkeypatch):
    # a long car / short room: the real footprint (not the plate's proxy) needs 1.3 m to the wall
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", 1.3)
    base = load_preset(settings, "autoexperten_standard")
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    assert _wall_clearance(near.metadata) > 1.4
    _, proxy_limit = move_proxy_back(
        load_plate_set(settings.assets_dir / "presets" / STANDARD["background"]["plates"]).plate("front_left_45"), 2.0
    )
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 2.0))
    fit = far.metadata["placement"]["roomFit"]
    assert fit["reducedBy"] == ["wall"]
    assert 0.0 < fit["depthOffsetM"] < proxy_limit - 0.1  # the proxy alone would go further
    assert _wall_clearance(far.metadata) >= 1.3 - 0.03  # the final ground model agrees
    assert far.metadata["placement"]["achievedWidthRatio"] < near.metadata["placement"]["achievedWidthRatio"]


def test_a_variant_never_brings_the_car_closer_to_the_wall_than_the_plate_spot(
    settings, fake_segmenter, three_quarter, monkeypatch
):
    # the plate's own spot leaves less than the clearance: depth offset and enlargement may not
    # bring the car any closer (it stays on the spot, at the plate's size)
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", 3.0)
    base = _without_branding(load_preset(settings, "autoexperten_standard"))
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 0.45, 1.06))
    fit = far.metadata["placement"]["roomFit"]
    assert "wall" in fit["reducedBy"]
    assert fit["depthOffsetM"] < 0.02 and fit["widthScale"] < 1.005
    assert _wall_clearance(far.metadata) >= _wall_clearance(near.metadata) - 0.03


def test_listing_photos_carry_no_dealer_contact_data():
    """mobile.de (AGB-HB, Inserate no. 9) forbids the dealer's contact data (phone, e-mail) in
    listing photos and web addresses in a listing; the brand wall shows logo + city only
    (every variant: test_no_variant_carries_dealer_contact_data)."""
    branding = parse_preset(STANDARD).branding
    assert branding.website.text == "" and branding.phone.text == ""
    texts = " ".join(t.text for t in (branding.city, branding.website, branding.phone))
    assert not re.search(r"\d{4,}|www\.|https?:|@|\.de\b", texts), texts


# --------------------------------------------------------------------------- the dealer's own backdrop

HAENDLER_FILE = PRESETS / "variants" / "autoexperten-haendler.json"
HAENDLER = json.loads(HAENDLER_FILE.read_text(encoding="utf-8"))
HAENDLER_PLATES = PRESETS / "autoexperten-haendler" / "plates.json"
#: Where the AI lettering was in the dealer's 1448 × 1086 image (processor/showroom3d/dealer_clean.py).
DEALER_TEXT_BOX = (336, 52, 1062, 226)
DEALER_WORDMARK = (349, 63, 1048)  # left, cap top, right
DEALER_RULES = (359, 1038)  # outer ends of the two rules
ALL_VARIANTS = sorted(p.name for p in (PRESETS / "variants").glob("*.json"))


@pytest.fixture(scope="module")
def dealer_plates():
    return load_plate_set(HAENDLER_PLATES)


def test_the_dealer_variant_is_a_complete_valid_preset():
    preset = parse_preset(HAENDLER)
    assert HAENDLER["id"] == STANDARD["id"]  # copied over the standard file, it replaces it
    assert HAENDLER["background"]["plates"] == "autoexperten-haendler/plates.json"
    assert HAENDLER["output"] == STANDARD["output"]
    # the quality gate as shipped, the "photo from above" rule re-scaled to this plate's camera
    # (test_the_dealer_gate_keeps_the_earlier_rooms_high_view_thresholds)
    assert HAENDLER["quality"] == {**STANDARD["quality"], "highViewRiseFactor": 1.42, "highViewAspectFactor": 0.88}
    # "Schatten mittel" (the recommendation, see test_the_dealer_variant_comes_in_three_shadow_strengths)
    assert HAENDLER["shadow"] == {**STANDARD["shadow"], **dict(DEALER_STRENGTHS["mittel"][0])}
    assert HAENDLER["reflection"] == {**STANDARD["reflection"], **dict(DEALER_STRENGTHS["mittel"][1])}
    assert preset.shadow.tint == (1.0, 1.0, 1.0) and preset.shadow.footprint_reach == 0.3
    # the conservative limits + the studio light match (the dealer's decision: the car gets a
    # guarded global light correction towards his bright studio; tests/test_light.py) – shipped
    assert HAENDLER["vehicleAdjustments"] == STANDARD["vehicleAdjustments"]
    assert preset.adjustments.studio_light.enabled
    branding = preset.branding
    assert branding.enabled and branding.logo.file == "official/AutoExperten_Logo.png"
    assert branding.city.text == "Schwetzingen" and branding.city.rule_length > 0 and branding.city.rule_thickness > 0
    assert branding.website.text == "" and branding.phone.text == ""
    # every field is written out (copying the variant over the standard file hides nothing)
    for name in ("city", "website", "phone"):
        assert {"align", "icon", "ruleLength", "ruleGap", "ruleThickness"} <= set(HAENDLER["branding"][name])
    assert {"widthScale", "depthOffsetM"} <= set(HAENDLER["placement"])


#: The dealer variant's three shadow strengths (shadow, reflection fields; dealer_backdrop.STRENGTHS).
DEALER_STRENGTHS = {
    "leicht": (
        {"contactOpacity": 0.85, "creaseOpacity": 0.7, "underbodyOpacity": 0.7, "edgeOpacity": 0.3,
         "underbodyFalloff": 0.08, "ambientOpacity": 0.2, "ambientReach": 0.3, "tint": [1.0, 1.0, 1.0], "footprintReach": 0.3},
        {"strength": 1.2, "maxReflectance": 0.25, "fade": 0.4, "footprintReach": 0.3},
    ),
    "mittel": (
        {"contactOpacity": 0.9, "creaseOpacity": 0.8, "underbodyOpacity": 0.8, "edgeOpacity": 0.4,
         "underbodyFalloff": 0.1, "ambientOpacity": 0.3, "ambientReach": 0.4, "tint": [1.0, 1.0, 1.0], "footprintReach": 0.3},
        {"strength": 1.5, "maxReflectance": 0.3, "fade": 0.5, "footprintReach": 0.3},
    ),
    "kraeftig": (
        {"contactOpacity": 0.92, "creaseOpacity": 0.85, "underbodyOpacity": 0.85, "edgeOpacity": 0.5,
         "underbodyFalloff": 0.14, "ambientOpacity": 0.4, "ambientReach": 0.5, "tint": [1.0, 1.0, 1.0], "footprintReach": 0.3},
        {"strength": 1.5, "maxReflectance": 0.3, "fade": 0.6, "footprintReach": 0.3},
    ),
}  # fmt: skip
DEALER_STRENGTH_FILES = {
    "leicht": PRESETS / "variants" / "autoexperten-haendler-leicht.json",
    "mittel": HAENDLER_FILE,
    "kraeftig": PRESETS / "variants" / "autoexperten-haendler-kraeftig.json",
}


def test_the_dealer_variant_comes_in_three_shadow_strengths():
    """The dealer: "the shadows underneath stay far too black". Three strengths of one look – they
    differ only in shadow/reflection (and the name); all keep the wood's hue (tint 1, 1, 1) and limit
    the under-car terms to the car's footprint; leicht < mittel < kräftig everywhere."""
    data = {k: json.loads(f.read_text(encoding="utf-8")) for k, f in DEALER_STRENGTH_FILES.items()}
    presets = {k: parse_preset(d) for k, d in data.items()}
    for key, item in data.items():
        shadow, reflection = DEALER_STRENGTHS[key]
        assert item["shadow"] == {**STANDARD["shadow"], **shadow}, key
        assert item["reflection"] == {**STANDARD["reflection"], **reflection}, key
        # everything else identical – also the studio light match (vehicleAdjustments.studioLight)
        others = {k: v for k, v in item.items() if k not in ("shadow", "reflection", "name")}
        assert others == {k: v for k, v in HAENDLER.items() if k not in ("shadow", "reflection", "name")}, key
        assert presets[key].adjustments.studio_light.enabled, key
    assert "leicht" in data["leicht"]["name"] and "kräftig" in data["kraeftig"]["name"]
    s = {k: p.shadow for k, p in presets.items()}
    for name in ("contact_opacity", "crease_opacity", "underbody_opacity", "edge_opacity", "underbody_falloff",
                 "ambient_opacity", "ambient_reach"):  # fmt: skip
        assert getattr(s["leicht"], name) < getattr(s["mittel"], name) < getattr(s["kraeftig"], name), name
    # even "kräftig" stays well below the earlier, far too black look (edge 0.7, falloff 0.25 m, ambient 0.6)
    assert s["kraeftig"].edge_opacity < 0.7 and s["kraeftig"].underbody_falloff < 0.25 and s["kraeftig"].ambient_opacity < 0.6
    r = {k: p.reflection for k, p in presets.items()}
    assert r["leicht"].fade < r["mittel"].fade < r["kraeftig"].fade <= 1.0  # the car's mirror image stays near the car


#: Proxy contact rise and bbox aspect of the earlier 3D room's 3/4 plates (front, rear pairs).
EARLIER_ROOM_3Q = {"front": (0.1546, 2.1436), "rear": (0.1591, 2.1355)}
EARLIER_HIGH_VIEW = (1.7, 0.8)  # highViewRiseFactor, highViewAspectFactor there


def test_the_dealer_gate_keeps_the_earlier_rooms_high_view_thresholds(dealer_plates):
    """The "photo from above" rule is relative to the plate's proxy car. The studio camera sees it
    with more contact rise and a lower aspect than the earlier room, so the dealer files scale the
    two factors back to the earlier ABSOLUTE thresholds – never stricter on any 3/4 plate (normal
    photos stay accepted), as strict as before on the rear ones (the photo from a window above,
    regression case too_high, is rejected again)."""
    earlier = Path(REPO_ROOT / "public/presets/autoexperten-standard/plates.json")
    if earlier.is_file():  # the recorded values are the earlier room's plates
        old = load_plate_set(earlier)
        for side, (rise, aspect) in EARLIER_ROOM_3Q.items():
            plate = old.plate(f"{side}_left_45")
            assert (plate.proxy_rise_ratio(), plate.proxy_aspect()) == pytest.approx((rise, aspect), abs=5e-5)
    quality = parse_preset(HAENDLER).quality
    for side, (rise, aspect) in EARLIER_ROOM_3Q.items():
        for key in (f"{side}_left_45", f"{side}_right_45"):
            plate = dealer_plates.plate(key)
            new_rise = quality.high_view_rise_factor * plate.proxy_rise_ratio()
            new_aspect = quality.high_view_aspect_factor * plate.proxy_aspect()
            assert new_rise >= EARLIER_HIGH_VIEW[0] * rise - 1e-3, key  # never stricter …
            assert new_aspect <= EARLIER_HIGH_VIEW[1] * aspect + 1e-3, key
            if side == "rear":  # … and as strict as before on the rear 3/4 plates
                assert new_rise == pytest.approx(EARLIER_HIGH_VIEW[0] * rise, rel=0.01), key
                assert new_aspect == pytest.approx(EARLIER_HIGH_VIEW[1] * aspect, rel=0.01), key


@pytest.mark.parametrize("name", ALL_VARIANTS)
def test_no_variant_carries_dealer_contact_data(name):
    """Every look variant (also future ones): logo + city only on the wall (mobile.de AGB-HB no. 9/14)."""
    data = json.loads((PRESETS / "variants" / name).read_text(encoding="utf-8"))
    branding = parse_preset(data).branding
    assert branding.website.text == "" and branding.phone.text == "", name
    texts = " ".join(t.text for t in (branding.city, branding.website, branding.phone))
    assert not re.search(r"\d{4,}|www\.|https?:|@|\.de\b|\+49", texts), texts


def test_the_dealer_plate_set_loads_with_one_fixed_studio_camera(dealer_plates):
    plates = dealer_plates.plates
    assert set(plates) == set(plates_module.PLATE_SHOTS)
    first = plates["front_left_45"]
    for key, plate in plates.items():
        # ONE plate image and ONE reflection pass for all shots – the camera never moves
        assert plate.image == first.image and plate.reflection == first.reflection, key
        assert plate.size == (2400, 1800)
        assert np.allclose(plate.floor_h, first.floor_h) and np.allclose(plate.wall_h, first.wall_h), key
        assert plate.camera.position == first.camera.position and plate.camera.pitch_down_deg == 0.0
        assert plate.horizon_v == first.horizon_v
        assert plate.brand_area == first.brand_area
        assert plate.shadow is not None and plate.shadow != first.shadow or key == "front_left_45"
        assert plate.reflectance and max(k for _, k in plate.reflectance) <= 0.5
    # level camera: the wall/floor junction is one horizontal row – his skirting (row 492 of 1086)
    top = first.floor_top(1448, 1086)
    assert np.allclose(top, top[0], atol=1e-3) and top[0] == pytest.approx(492.0, abs=0.6)
    # the car is turned in front of the same camera: 3/4 views at his reference size, with floor to the wall
    for key in ("front_left_45", "front_right_45", "rear_left_45", "rear_right_45"):
        plate = plates[key]
        assert 0.80 <= plate.target_width_ratio <= 0.86, key
        assert 0.78 <= plate.ground_v <= 0.9, key
        assert plate.expected_near_end() == ("left" if key in ("front_left_45", "rear_right_45") else "right"), key
    # side views as large and low as the frame allows (one level camera: they stand higher than the
    # 3/4 views); front/rear well in front of the wall, large enough for a tall front
    for key in ("left_side", "right_side"):
        assert 0.9 <= plates[key].target_width_ratio <= 0.94 and plates[key].ground_v >= 0.65, key
    for key in ("front", "rear"):
        assert 0.66 <= plates[key].target_width_ratio <= 0.72 and plates[key].ground_v >= 0.78, key
    for plate in plates.values():
        corners = []
        c = plate.proxy_contacts
        centre = np.mean([np.asarray(p.floor) for p in c.values()], axis=0)
        front = 0.5 * (np.asarray(c["front_left"].floor) + np.asarray(c["front_right"].floor))
        rear = 0.5 * (np.asarray(c["rear_left"].floor) + np.asarray(c["rear_right"].floor))
        axis = (front - rear) / np.linalg.norm(front - rear)
        side = np.array([-axis[1], axis[0]])
        for i in (-1, 1):
            for j in (-1, 1):
                corners.append(centre + i * 0.5 * plate.proxy["lengthM"] * axis + j * 0.5 * plate.proxy["widthM"] * side)
        assert -max(p[1] for p in corners) >= 1.0, plate.key  # at least 1 m of floor to the wall
    # mirrored partners see the car from the other side at the same size
    for a, b in plates_module.MIRROR_PLATES.items():
        assert plates[a].target_width_ratio == pytest.approx(plates[b].target_width_ratio, abs=1e-3)


def test_the_dealer_plate_has_no_lettering(dealer_plates):
    """The cleaned, upscaled plate itself contains no text or logo (official branding is composited)."""
    rgb = np.asarray(Image.open(dealer_plates.plate("front").image).convert("RGB")).astype(np.float32)
    sx, sy = rgb.shape[1] / 1448, rgb.shape[0] / 1086
    x0, y0, x1, y1 = DEALER_TEXT_BOX
    wall = rgb[int(y0 * sy) : int(y1 * sy), int(x0 * sx) : int(x1 * sx)]
    luma = wall.mean(axis=-1)
    assert luma.min() > 170  # no dark letters (his were ≈ 40–120)
    assert (wall[..., 2] - wall[..., 0]).max() < 25  # no blue letters (his: B − R ≈ 200)
    # smooth wall: no letter edges left (high-pass of the luminance)
    high = luma - cv2.GaussianBlur(luma, (0, 0), 4.0)
    assert np.abs(high).max() < 6.0


def test_the_official_branding_sits_where_his_lettering_was():
    settings = Settings(assets_dir=REPO_ROOT / "public")
    preset = parse_preset(HAENDLER)
    showroom = BackgroundProvider(settings).get(preset, "front_left_45", 1448, 1086)
    lx0, ly0, lx1, _ = showroom.branding.box("logo")
    left, top, right = DEALER_WORDMARK
    assert lx0 == pytest.approx(left, abs=4) and lx1 == pytest.approx(right, abs=4)
    assert ly0 == pytest.approx(top, abs=4)
    cx0, cy0, cx1, cy1 = showroom.branding.box("city")
    assert cx0 == pytest.approx(DEALER_RULES[0], abs=5) and cx1 == pytest.approx(DEALER_RULES[1], abs=5)
    assert 178 <= cy0 <= 190 and cy1 <= 222
    # the roof of a typical car stays below the branding, and the same branding is on every plate
    assert showroom.branding.bottom < showroom.plate.proxy_bbox[1] * 1086
    other = BackgroundProvider(settings).get(preset, "left_side", 1448, 1086)
    assert other.branding.boxes == showroom.branding.boxes


def test_the_dealer_plates_brand_and_shadow_like_the_standard_set(dealer_plates):
    settings = Settings(assets_dir=REPO_ROOT / "public")
    preset = parse_preset(HAENDLER)
    provider = BackgroundProvider(settings)
    for key in plates_module.PLATE_SHOTS:
        showroom = provider.get(preset, key, 1200, 900)
        assert float(showroom.shadow.min()) < 0.85, key  # the proxy shadow pass is there …
        # … on the floor only: every pixel that lies entirely above the wall/floor junction is unshadowed
        top = np.asarray(showroom.floor_top, np.float64)
        wall = (np.arange(900, dtype=np.float64)[:, None] + 1.0) <= top[None, :]
        assert wall.sum() > 0.4 * 900 * 1200, key
        assert float(showroom.shadow[wall].min()) >= 0.99, key
        assert showroom.reflection is not None and showroom.reflection.max() > 0.2
        assert np.abs(showroom.reflection[wall]).max() < 1e-4, key  # the wall has no floor reflection


def test_the_pipeline_composites_a_car_onto_the_dealer_plate(fake_segmenter, three_quarter):
    settings = Settings(assets_dir=REPO_ROOT / "public")
    preset = parse_preset(HAENDLER)
    result = process_photo(
        encode_jpeg(three_quarter[0], 97), preset=preset, segmenter=fake_segmenter,
        backgrounds=BackgroundProvider(settings), shot_key="front_left_45",
    )  # fmt: skip
    meta = result.metadata
    assert meta["plateUsed"] == "front_left_45"
    # the car got the studio light match, measured on this plate and checked by the paint guard
    adjustments = meta["adjustments"]
    assert adjustments["mode"] == "studioLight" and adjustments["guard"]["passed"]
    assert 0.3 < adjustments["studio"]["key"] < 0.8 and adjustments["applied"]["exposureEv"] >= 0.0
    out = _decode(result.jpeg)
    assert out.shape == (1800, 2400, 3)
    assert meta["reflection"]["platePass"] is True
    placement = meta["placement"]
    assert placement["achievedWidthRatio"] == pytest.approx(0.83, abs=0.05)
