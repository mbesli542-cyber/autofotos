"""showroom3d/postprocess_plates.py: the floor reflection pass of the plate set."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.showroom.plates import load_plate_set
from tests.plate_fixtures import SHOTS, _linear, _srgb, plate_metadata, render_plate, solve_camera

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location("postprocess_plates", ROOT / "showroom3d" / "postprocess_plates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def plate():
    cam, heading, bbox = solve_camera(SHOTS["left_side"], 800, 600)
    meta = plate_metadata("left_side", SHOTS["left_side"], cam, heading, bbox, (400, 300))
    glossfree = render_plate(cam, 800, 600).astype(np.float32) / 255.0
    return meta, glossfree


def _with_reflection(meta, glossfree, k: float) -> tuple[np.ndarray, np.ndarray]:
    """Plate = gloss-free + k × the mirrored wall (mirror axis: the wall/floor junction),
    with a slightly NEGATIVE red channel on the floor (view-transform channel mixing)."""
    lin = _linear(glossfree)
    h, w = lin.shape[:2]
    pp = _module()
    _, _, valid = pp._floor_points(np.asarray(meta["floorHomography"]), w, h)
    top = np.where(valid.any(axis=0), np.argmax(valid, axis=0), h)
    refl = np.zeros_like(lin)
    for x in range(w):
        for y in range(int(top[x]), h):
            ym = 2 * int(top[x]) - y
            if 0 <= ym < top[x]:
                refl[y, x] = k * lin[ym, x]
    refl[:, 100:160, 0] -= 0.12 * valid[:, 100:160]  # like a blue LED streak through AgX
    return _srgb(lin + refl).astype(np.float32), refl


def test_the_reflection_pass_is_the_signed_difference_on_the_floor_and_calibrated(plate):
    meta, glossfree = plate
    rgb, refl = _with_reflection(meta, glossfree, 0.15)
    gloss_small = cv2.resize(glossfree, (400, 300), interpolation=cv2.INTER_AREA)
    out, samples = _module().reflection_pass(rgb, gloss_small, meta)
    assert out.shape == (300, 400, 3)
    expected = cv2.resize(refl, (400, 300), interpolation=cv2.INTER_AREA)
    floor = expected.any(axis=-1)
    assert float(np.median(np.abs(out - expected)[floor])) < 0.01
    assert float(out[..., 0].min()) < 0.0  # signed: kept negative, never clipped (no colour trace)
    assert np.abs(out[:60]).max() < 1e-3  # nothing on the wall
    assert len(samples) >= 2 and all(0.0 <= k <= 0.5 for _, k in samples)
    assert float(np.median([k for _, k in samples])) == pytest.approx(0.15, abs=0.05)


def test_reflection_only_mode_adds_the_pass_to_an_existing_plate_set(plate, tmp_path):
    meta, glossfree = plate
    rgb, _ = _with_reflection(meta, glossfree, 0.12)
    src, dst = tmp_path / "render", tmp_path / "plates"
    src.mkdir()
    dst.mkdir()
    gloss16 = (cv2.resize(glossfree, (400, 300), interpolation=cv2.INTER_AREA) * 65535 + 0.5).astype(np.uint16)
    cv2.imwrite(str(src / "left_side-glossfree.png"), cv2.cvtColor(gloss16, cv2.COLOR_RGB2BGR))
    Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(dst / "left_side.jpg", quality=95)
    meta = {**meta, "size": [800, 600]}
    meta.pop("shadow")
    meta.pop("shadowSize")
    (dst / "plates.json").write_text(json.dumps({"version": 1, "plates": {"left_side": meta}}))
    pp = _module()
    import sys

    argv = sys.argv
    sys.argv = ["postprocess_plates.py", str(src), str(dst), "--reflection-only"]
    try:
        assert pp.main() == 0
    finally:
        sys.argv = argv
    data = json.loads((dst / "plates.json").read_text())["plates"]["left_side"]
    assert data["reflection"] == "left_side-reflection.png" and data["reflectionOffset"] == 0.5
    loaded = load_plate_set(dst / "plates.json", shots=("left_side",)).plate("left_side")
    assert loaded.reflection_size == (400, 300) and len(loaded.reflectance) >= 2
