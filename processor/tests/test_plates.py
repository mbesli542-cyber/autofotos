"""The 3D-showroom plate set: strict loading, geometry helpers, real plate smoke test."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.config import REPO_ROOT, Settings
from app.showroom.plates import (
    MIRROR_PLATES,
    PLATE_SHOTS,
    PlateSetError,
    load_plate_set,
)

REAL_PLATES = REPO_ROOT / "public/presets/autoexperten-standard/plates.json"


@pytest.fixture
def plate_dir(tmp_path: Path, synthetic_plates: Path) -> Path:
    target = tmp_path / "plates"
    shutil.copytree(synthetic_plates, target)
    return target


def _rewrite(directory: Path, change) -> Path:
    path = directory / "plates.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    return path


def test_synthetic_set_loads_with_all_eight_plates(plate_dir):
    plates = load_plate_set(plate_dir / "plates.json")
    assert tuple(plates.plates) == PLATE_SHOTS
    for plate in plates.plates.values():
        assert plate.aspect == pytest.approx(4 / 3)
        assert 0.8 <= plate.ground_v <= 0.9
        assert plate.floor_h.shape == (3, 3) and np.isfinite(plate.floor_h).all()
        u0, _, u1, _ = plate.proxy_bbox
        assert (u0 + u1) / 2 == pytest.approx(0.5, abs=0.01)  # the typical car is centred


def test_expected_near_end_comes_from_the_proxy_depths(plate_dir):
    plates = load_plate_set(plate_dir / "plates.json")
    assert plates.plate("front_left_45").expected_near_end() == "left"  # nose towards the left
    assert plates.plate("front_right_45").expected_near_end() == "right"
    assert plates.plate("rear_left_45").expected_near_end() == "right"  # rear (near) right, nose left
    assert plates.plate("rear_right_45").expected_near_end() == "left"
    for key in ("front", "rear", "left_side", "right_side"):
        assert plates.plate(key).expected_near_end() is None
    for key, partner in MIRROR_PLATES.items():
        assert plates.plate(key).expected_near_end() != plates.plate(partner).expected_near_end()


def test_visible_contacts_are_the_lower_outline_of_the_proxy(plate_dir):
    plate = load_plate_set(plate_dir / "plates.json").plate("front_left_45")
    labels = [c.label for c in plate.visible_contacts()]
    assert "rear_right" not in labels  # far rear tyre hides behind the car
    assert max(plate.visible_contacts(), key=lambda c: c.v).label == "front_left"
    assert plate.proxy_rise_ratio() > 0.08
    assert load_plate_set(plate_dir / "plates.json").plate("left_side").proxy_rise_ratio() < 0.02


def test_floor_top_is_the_wall_floor_junction_of_the_floor_homography(plate_dir):
    plate = load_plate_set(plate_dir / "plates.json").plate("front_left_45")
    width, height = 800, 600
    top = plate.floor_top(width, height)
    inv = np.linalg.inv(plate.floor_h)
    for x in range(0, width, 37):
        u = (x + 0.5) / width
        for dy, on_floor in ((2.0, True), (-2.0, False)):
            v = (top[x] + dy) / height
            px, py, pw = inv @ np.array([u, v, 1.0])
            assert ((pw > 0) and (py / pw < 0)) == on_floor
    # the junction is the image of the wall/floor line y = 0 = the wall's z = 0
    u, v = plate.wall_to_uv(0.0, 0.0)
    assert top[int(u * width)] == pytest.approx(v * height, abs=1.5)
    mask = plate.floor_mask(width, height)
    assert mask[-1].all() and not mask[0].any()


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["plates"].pop("rear"), "plates missing for rear"),
        (lambda d: d.update(version=2), "version"),
        (lambda d: d["plates"]["front"].update(floorHomography=[[1, 0, 0], [0, 1, 0]]), "3x3"),
        (lambda d: d["plates"]["front"]["floorHomography"][0].__setitem__(0, float("nan")), "finite"),
        (lambda d: d["plates"]["front"].update(floorHomography=d["plates"]["front"]["wallHomography"]), "anchor"),
        (lambda d: d["plates"]["front"].update(image="../front.jpg"), "plain file name"),
        (lambda d: d["plates"]["front"].update(image="nope.jpg"), "file missing"),
        (lambda d: d["plates"]["front"].update(size=[800, 600]), "is not 800x600"),
        (lambda d: d["plates"]["front"]["vehicle"]["proxyContacts"].pop("rear_left"), "rear_left"),
        (lambda d: d["plates"]["front"]["vehicle"].update(targetWidthRatio=1.4), "targetWidthRatio"),
        (lambda d: d["plates"]["front"]["wall"]["brandArea"].update(xMin=4.0), "brandArea"),
        (lambda d: d["plates"]["front"]["wall"].update(albedo=[0.6, 0.6]), "albedo"),
    ],
)
def test_invalid_plate_sets_are_rejected_with_a_clear_error(plate_dir, change, message):
    path = _rewrite(plate_dir, change)
    with pytest.raises(PlateSetError, match=message.replace("(", r"\(")):
        load_plate_set(path)


def test_a_missing_plates_json_or_shadow_file_is_an_error(plate_dir):
    (plate_dir / "rear-shadow.png").unlink()
    with pytest.raises(PlateSetError, match="rear-shadow.png"):
        load_plate_set(plate_dir / "plates.json")
    with pytest.raises(PlateSetError, match="missing"):
        load_plate_set(plate_dir / "nothing.json")


def test_plates_without_a_shadow_pass_are_allowed(plate_dir):
    path = _rewrite(plate_dir, lambda d: d["plates"]["front"].pop("shadow"))
    assert load_plate_set(path).plate("front").shadow is None


def test_a_corrupt_plate_image_is_detected_on_load(plate_dir):
    (plate_dir / "front.jpg").write_bytes(b"\x00" * 100)
    with pytest.raises(PlateSetError, match="not a readable image"):
        load_plate_set(plate_dir / "plates.json")


def test_plate_images_with_another_aspect_ratio_are_rejected(plate_dir):
    def change(d):
        d["plates"]["front"]["size"] = [640, 360]

    Image.new("RGB", (640, 360)).save(plate_dir / "front.jpg")
    with pytest.raises(PlateSetError):
        load_plate_set(_rewrite(plate_dir, change))


@pytest.mark.skipif(not REAL_PLATES.is_file(), reason="real plate set not rendered yet")
def test_real_plate_set_smoke():
    """The rendered plate set in public/presets/autoexperten-standard/ is complete and brandable."""
    from app.presets import BackgroundProvider, load_preset

    plates = load_plate_set(REAL_PLATES)
    for plate in plates.plates.values():
        assert 0.75 <= plate.ground_v <= 0.95
        assert 0.4 <= plate.target_width_ratio <= 0.95
    settings = Settings(assets_dir=REPO_ROOT / "public")
    preset = load_preset(settings, "autoexperten_standard")
    showroom = BackgroundProvider(settings).get(preset, "front_left_45", 1200, 900)
    logo = showroom.branding.box("logo")
    assert logo is not None
    assert showroom.branding.bottom < showroom.plate.proxy_bbox[1] * 900  # above the typical roof
    assert float(np.min(showroom.shadow)) < 0.9  # the proxy shadow pass is there


# --------------------------------------------------------------------------- reflection pass


def test_the_floor_reflection_pass_is_loaded_with_its_reflectance(plate_dir):
    plate = load_plate_set(plate_dir / "plates.json").plate("left_side")
    assert plate.reflection is not None and plate.reflection.name == "left_side-reflection.png"
    assert plate.reflection_offset == pytest.approx(0.5)
    assert plate.reflectance_at(0.5) == pytest.approx(0.16) and plate.reflectance_at(1.2) == pytest.approx(0.13)
    assert plate.reflection in load_plate_set(plate_dir / "plates.json").files()


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["plates"]["front"].update(reflection="nope.png"), "file missing"),
        (lambda d: d["plates"]["front"].pop("reflectance"), "reflectance"),
        (lambda d: d["plates"]["front"].update(reflectance=[[0.5, 0.1]]), "at least two"),
        (lambda d: d["plates"]["front"].update(reflectance=[[0.9, 0.1], [0.5, 0.1]]), "increase"),
        (lambda d: d["plates"]["front"].update(reflectance=[[0.5, 0.1], [0.9, 0.8]]), "<= 0.5"),
        (lambda d: d["plates"]["front"].pop("reflectionOffset"), "reflectionOffset"),
        (lambda d: d["plates"]["front"].update(reflectionSize=[100, 75]), "reflectionSize"),
        (lambda d: d["plates"]["front"].pop("reflection"), "needs a reflection pass"),
    ],
)
def test_an_invalid_reflection_pass_is_rejected(plate_dir, change, message):
    path = _rewrite(plate_dir, change)
    with pytest.raises(PlateSetError, match=message):
        load_plate_set(path)


def test_a_reflection_pass_with_another_aspect_ratio_is_rejected(plate_dir):
    Image.new("RGB", (320, 120)).save(plate_dir / "front-reflection.png")
    path = _rewrite(plate_dir, lambda d: d["plates"]["front"].pop("reflectionSize"))
    with pytest.raises(PlateSetError, match="aspect ratio"):
        load_plate_set(path)


def test_plates_without_a_reflection_pass_are_allowed(plate_dir):
    def drop(d):
        for key in ("reflection", "reflectionSize", "reflectionOffset", "reflectance"):
            d["plates"]["front"].pop(key)

    plate = load_plate_set(_rewrite(plate_dir, drop)).plate("front")
    assert plate.reflection is None and plate.reflectance is None
    assert float(plate.reflectance_at(0.8)) == 0.0


def test_the_reflection_pass_is_decoded_signed_at_the_output_size(plate_dir):
    from app.presets import _load_reflection_map

    plate = load_plate_set(plate_dir / "plates.json").plate("left_side")
    refl = _load_reflection_map(plate, 960, 720)
    assert refl.shape == (720, 960, 3) and refl.dtype == np.float32
    assert float(refl[:100].max()) == pytest.approx(0.0, abs=2e-3)  # wall: no floor reflection
    floor = refl[-50:]
    assert float(np.median(floor)) == pytest.approx(0.04, abs=0.01)  # the sheen
    assert float(floor[..., 2].max()) > 0.2  # the LED streak (bluish)
