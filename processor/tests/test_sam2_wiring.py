"""One SAM 2.1 per process (reconciliation of the contested cut and the glass masks).

``create_segmenter`` attaches the ONE ``Sam2Segmenter`` (``segmenter.sam2``, nothing loaded); the
glass-mask provider of interior shots reads that same instance on every call (``shared_glass_masks``)
– so PROCESSOR_SAM2_ENABLED, a missing graph and a warm-up refusal switch BOTH off, ``/health``
reports ``sam2`` and ``glassMasks`` consistently, and the regression runner's ``LockedSam2`` covers
the provider's ``decode`` calls under the same lock.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import PROCESSOR_ROOT, Settings
from app.pipeline import glass_mask as gm
from app.pipeline import sam2 as sam2_module
from app.pipeline.glass_backdrop import GlassMasksUnavailable, run_glass
from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter, create_segmenter
from app.presets import GlassBackdropConfig, parse_preset
from tests.test_glass_backdrop import _enabled_data


def _fake_graphs(settings: Settings) -> None:
    spec = sam2_module.SAM2_REGISTRY[settings.sam2_model]
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    for name in spec.filenames:
        (settings.models_dir / name).write_bytes(b"not a graph")


def _capture_find_glass(monkeypatch) -> list:
    seen: list = []

    def fake_find_glass(segmenter, rgb, shot_key, cfg, detector=None):
        seen.append((segmenter, detector))
        return gm.GlassMaskResult(None, rgb.shape[1], rgb.shape[0], {})

    monkeypatch.setattr(gm, "find_glass", fake_find_glass)
    return seen


RGB = np.full((48, 64, 3), 120, np.uint8)


# --------------------------------------------------------------------------- one instance


def test_the_glass_masks_use_the_segmenters_sam_instance_and_load_nothing(settings, monkeypatch):
    from app.main import create_glass_masks

    _fake_graphs(settings)
    monkeypatch.setattr(
        "onnxruntime.InferenceSession", lambda *a, **k: (_ for _ in ()).throw(AssertionError("loaded a graph"))
    )
    segmenter = create_segmenter(settings)
    assert isinstance(segmenter.sam2, sam2_module.Sam2Segmenter) and not segmenter.sam2.loaded
    provider = create_glass_masks(settings, segmenter)
    assert provider is not None and not segmenter.sam2.loaded  # created, nothing loaded
    seen = _capture_find_glass(monkeypatch)
    assert provider(RGB, "cockpit") is None
    assert seen == [(segmenter.sam2, segmenter.detector)]  # the SAME object the contested cut uses


def test_create_sam2_loads_the_sessions_on_first_use_only(settings, monkeypatch):
    _fake_graphs(settings)
    sam2 = sam2_module.create_sam2(settings)
    assert not sam2.loaded
    monkeypatch.setattr("onnxruntime.InferenceSession", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    with pytest.raises(Exception, match="Checksum mismatch|no"):
        sam2.encode(RGB)  # the first use touches the files (checksum) – not before


# --------------------------------------------------------------------------- one switch


def test_sam2_off_or_missing_switches_the_glass_masks_off_too(settings, caplog):
    from app.main import create_glass_masks

    for variant in (replace(settings, sam2_enabled=False), settings):  # off / graphs not built
        with caplog.at_level(logging.INFO, logger="app.pipeline.sam2"):
            segmenter = create_segmenter(variant)
        assert segmenter.sam2 is None
        provider = create_glass_masks(variant, segmenter)
        assert provider is not None  # the provider exists – it answers "unavailable" per call
        with pytest.raises(GlassMasksUnavailable):
            provider(RGB, "cockpit")
        preset = parse_preset(_enabled_data())
        outcome = run_glass(RGB, preset=preset, backgrounds=None, glass_masks=provider, shot_key="cockpit")
        assert not outcome.applied and outcome.meta["reason"] == "no_mask_provider"
        assert outcome.warnings[0].code == "glass_backdrop_skipped" and "nicht verfügbar" in outcome.warnings[0].message
    messages = [r.message for r in caplog.records]
    assert any("PROCESSOR_SAM2_ENABLED=false" in m and "glass masks" in m for m in messages)
    assert any("graphs" in m and "glass masks" in m for m in messages)
    assert create_glass_masks(replace(settings, glass_masks="none"), create_segmenter(settings)) is None
    with pytest.raises(RuntimeError, match="PROCESSOR_GLASS_MASKS"):
        create_glass_masks(replace(settings, glass_masks="magic"), create_segmenter(settings))


def test_a_warm_up_refusal_after_creation_switches_the_glass_masks_off(settings, monkeypatch):
    from app.main import create_glass_masks

    _fake_graphs(settings)  # present, but not the registered export
    segmenter = OnnxSegmenter(
        MODEL_REGISTRY["isnet-general-use"], settings.models_dir, auto_download=False, sam2=sam2_module.create_sam2(settings)
    )
    provider = create_glass_masks(settings, segmenter)  # wired before the warm-up thread ran
    monkeypatch.setattr(segmenter, "_session", object())
    segmenter.warm_up()
    assert segmenter.sam2 is None and "Checksum mismatch" in (segmenter.sam2_error or "")
    with pytest.raises(GlassMasksUnavailable, match="refused"):
        provider(RGB, "cockpit")


# --------------------------------------------------------------------------- /health


def test_health_reports_sam2_and_glass_masks_consistently(settings, fake_segmenter):
    from app.main import create_app

    class Sam:
        spec = type("Spec", (), {"name": "sam2.1-hiera-small"})()
        loaded = False

    fake_segmenter.sam2 = Sam()
    fake_segmenter.sam2_error = None
    with TestClient(create_app(settings, segmenter=fake_segmenter, store=None, warm_up=False)) as client:
        body = client.get("/health").json()
    assert body["sam2"] == "sam2.1-hiera-small" and body["glassMasks"] == "sam2"
    # SAM 2.1 off / not built / refused: the provider waits, interior shots stay the original with a hint
    fake_segmenter.sam2 = None
    fake_segmenter.sam2_error = "Checksum mismatch …"
    with TestClient(create_app(settings, segmenter=fake_segmenter, store=None, warm_up=False)) as client:
        body = client.get("/health").json()
    assert body["sam2"] is None and body["sam2Error"].startswith("Checksum") and body["glassMasks"] is None
    # switched off by configuration
    fake_segmenter.sam2 = Sam()
    fake_segmenter.sam2_error = None
    app = create_app(replace(settings, glass_masks="none"), segmenter=fake_segmenter, store=None, warm_up=False)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["sam2"] == "sam2.1-hiera-small" and body["glassMasks"] == "none"


# --------------------------------------------------------------------------- regression runner


def _runner_module():
    path = PROCESSOR_ROOT / "scripts" / "visual_regression.py"
    spec = importlib.util.spec_from_file_location("visual_regression_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_regression_runner_locks_every_sam_call_of_the_glass_provider(monkeypatch, tmp_path):
    runner = _runner_module()
    locks: list[Path] = []

    class _Lock:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            locks.append(self.path)
            return 0.0

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(runner, "exclusive_lock", _Lock)

    class Real:
        name = "onnx:fake"
        spec = type("Spec", (), {"name": "fake"})()
        loaded = False

        def encode(self, rgb):
            return "embedding"

        def predict(self, embedding, points, box=None, multimask=True):
            return "predicted"

        def decode(self, embedding, points, box, *, multimask):
            return "decoded"

    lock_path = tmp_path / "lock"
    locked = runner.LockedSam2(Real(), lock_path)
    assert locked.encode(RGB) == "embedding"
    assert locked.decode("embedding", [(1.0, 2.0, 1)], None, multimask=True) == "decoded"
    assert locked.predict("embedding", [(1.0, 2.0, 1)]) == "predicted"
    assert locks == [lock_path] * 3 and locked.reset()["predicts"] == 2

    # the provider the runner wires reads the locked instance – never the real one
    class Segmenter:
        sam2 = locked
        detector = None

    seen = []
    monkeypatch.setattr(
        gm,
        "find_glass",
        lambda segmenter, rgb, shot_key, cfg, detector=None: (
            seen.append(segmenter),
            gm.GlassMaskResult(None, rgb.shape[1], rgb.shape[0], {}),
        )[1],
    )
    assert gm.shared_glass_masks(Segmenter())(RGB, "cockpit") is None
    assert seen == [locked]
    assert "decode" in runner.LockedSam2.__dict__  # the provider's call (glass_mask._Work.predict)


def test_the_provider_config_is_the_glass_mask_config():
    class Segmenter:
        sam2 = None
        detector = None

    provider = gm.shared_glass_masks(Segmenter(), gm.GlassMaskConfig())
    with pytest.raises(GlassMasksUnavailable):
        provider(RGB, "cockpit")
    assert GlassBackdropConfig().enabled is False  # the module default stays off; the shipped preset decides
