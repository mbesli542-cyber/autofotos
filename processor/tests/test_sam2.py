"""SAM 2.1 prompted segmentation (app/pipeline/sam2.py): preprocessing, prompt and mask
coordinate mapping, output selection, registry pins, missing/wrong graphs – without the
model; one integration test runs when the built ONNX graphs exist in the models folder."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.config import PROCESSOR_ROOT, Settings
from app.pipeline import sam2
from app.pipeline.sam2 import (
    DECODER_INPUTS,
    DECODER_OUTPUTS,
    ENCODER_INPUTS,
    ENCODER_OUTPUTS,
    INPUT_SIZE,
    LOW_RES_SIZE,
    SAM2_REGISTRY,
    Sam2Embedding,
    Sam2Segmenter,
    create_sam2,
    ensure_built_model,
    preprocess,
    prompt_arrays,
    resample_logits,
    select_masks,
    stability_scores,
    to_model_coords,
)
from app.pipeline.segmentation import ModelUnavailableError, SegmentationError
from tests.conftest import make_vehicle_photo

SPEC = SAM2_REGISTRY["sam2.1-hiera-small"]
MODELS_DIR = Path(os.environ.get("PROCESSOR_MODELS_DIR") or PROCESSOR_ROOT / "models")


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, PROCESSOR_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- preprocessing


def test_preprocess_resizes_to_1024_and_normalises_like_imagenet():
    rgb = np.zeros((300, 500, 3), np.uint8)
    rgb[:, :250] = 255  # left half white
    tensor = preprocess(rgb)
    assert tensor.shape == (1, 3, INPUT_SIZE, INPUT_SIZE) and tensor.dtype == np.float32
    assert tensor.flags["C_CONTIGUOUS"]
    for channel, (mean, std) in enumerate(zip((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))):
        assert tensor[0, channel, 100, 100] == pytest.approx((1.0 - mean) / std, rel=1e-4)  # white
        assert tensor[0, channel, 100, 900] == pytest.approx(-mean / std, rel=1e-4)  # black
    # the aspect ratio is NOT kept: the white half still ends at the middle of the model input
    assert tensor[0, 0, 500, 530] < tensor[0, 0, 500, 500] and tensor[0, 0, 500, 530] < 0
    with pytest.raises(SegmentationError):
        preprocess(np.zeros((300, 500), np.uint8))
    with pytest.raises(SegmentationError):
        preprocess(np.zeros((300, 500, 3), np.float32))


def test_preprocess_is_the_export_scripts_preprocessing():
    """The verification in scripts/export_sam2.py (--verify) runs the graphs on exactly the
    tensor the service builds."""
    export = _script("export_sam2")
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    assert np.allclose(preprocess(rgb), export.preprocess(rgb), atol=1e-6)
    assert export.INPUT_SIZE == INPUT_SIZE and export.LOW_RES_SIZE == LOW_RES_SIZE
    assert list(export.ENCODER_INPUTS) == list(ENCODER_INPUTS) and list(export.ENCODER_OUTPUTS) == list(ENCODER_OUTPUTS)
    assert list(export.DECODER_INPUTS) == list(DECODER_INPUTS) and list(export.DECODER_OUTPUTS) == list(DECODER_OUTPUTS)


# --------------------------------------------------------------------------- coordinates


def test_points_map_to_model_pixels_per_axis():
    xy = to_model_coords(np.array([[2000.0, 1500.0], [4000.0, 3000.0], [0.0, 0.0]]), width=4000, height=3000)
    assert xy.dtype == np.float32
    assert xy.tolist() == [[512.0, 512.0], [1024.0, 1024.0], [0.0, 0.0]]
    # a portrait photo: x and y are scaled on their own (no padding, no letterbox)
    assert to_model_coords(np.array([[1080.0, 960.0]]), width=2160, height=3840).tolist() == [[512.0, 256.0]]


def test_prompt_arrays_put_the_box_corners_first_and_pad_nothing():
    coords, labels = prompt_arrays([(1000.0, 750.0, 1), (10.0, 10.0, 0)], (1500.0, 1100.0, 300.0, 400.0), width=2000, height=1500)
    assert coords.shape == (1, 4, 2) and coords.dtype == np.float32
    assert labels.shape == (1, 4) and labels.dtype == np.int64
    # corners normalised to top-left / bottom-right, labels 2 / 3, then the points, no padding point
    assert np.allclose(coords[0], [[153.6, 273.0667], [768.0, 750.9333], [512.0, 512.0], [5.12, 6.8267]], atol=1e-3)
    assert labels[0].tolist() == [2, 3, 1, 0]
    coords, labels = prompt_arrays([(5.0, 5.0, 1)], None, width=100, height=50)
    assert coords.shape == (1, 1, 2) and labels[0].tolist() == [1]
    with pytest.raises(ValueError):
        prompt_arrays([], None, width=100, height=50)
    with pytest.raises(ValueError):
        prompt_arrays([(5.0, 5.0, 2)], None, width=100, height=50)


def test_low_res_logits_are_resampled_to_photo_pixels():
    low = np.full((2, LOW_RES_SIZE, LOW_RES_SIZE), -8.0, np.float32)
    low[0, 64:128, 32:96] = 8.0  # cells (x 32 … 96, y 64 … 128) of 256 → photo x 125 … 375, y 200 … 400
    logits = resample_logits(low, width=1000, height=800)
    assert logits.shape == (2, 800, 1000) and logits.dtype == np.float32
    ys, xs = np.nonzero(logits[0] > 0)
    assert abs(xs.min() - 125) <= 4 and abs(xs.max() + 1 - 375) <= 4
    assert abs(ys.min() - 200) <= 4 and abs(ys.max() + 1 - 400) <= 4
    assert not (logits[1] > 0).any()
    # bilinear: the edge of the block is a ramp, not a stair-step of 4 px cells
    row = logits[0, 300, 120:132]
    assert np.all(np.diff(row) >= 0) and 0 < np.count_nonzero((row > -8.0) & (row < 8.0)) < 12


# --------------------------------------------------------------------------- output selection


def test_stability_score_is_the_area_ratio_between_the_two_thresholds():
    masks = np.full((2, 4, 4), -3.0, np.float32)
    masks[0, :2] = 3.0  # 8 cells well inside, the rest well outside: stable
    masks[1, :2] = 0.03  # 8 cells in the band: above -delta but not above +delta
    masks[1, 2] = 3.0
    scores = stability_scores(masks, delta=0.05)
    assert scores.tolist() == pytest.approx([1.0, 4 / 12])
    assert stability_scores(np.full((1, 4, 4), -3.0, np.float32), 0.05).tolist() == [1.0]  # empty: 1


def test_single_mask_falls_back_to_the_best_multimask_output_when_unstable():
    masks = np.full((4, 8, 8), -5.0, np.float32)
    masks[0, :4] = 0.02  # the single-mask token: everything in the uncertain band
    masks[1, :, :2] = 5.0
    masks[2, :, :4] = 5.0
    masks[3, :, :6] = 5.0
    ious = np.array([0.9, 0.5, 0.8, 0.6], np.float32)
    chosen, chosen_iou = select_masks(masks, ious, multimask=False, delta=0.05, thresh=0.98)
    assert chosen.shape == (1, 8, 8) and np.array_equal(chosen[0], masks[2]) and chosen_iou.tolist() == [pytest.approx(0.8)]
    masks[0, :4] = 5.0  # now stable: the single-mask token itself
    chosen, chosen_iou = select_masks(masks, ious, multimask=False, delta=0.05, thresh=0.98)
    assert np.array_equal(chosen[0], masks[0]) and chosen_iou.tolist() == [pytest.approx(0.9)]
    multi, multi_iou = select_masks(masks, ious, multimask=True, delta=0.05, thresh=0.98)
    assert multi.shape == (3, 8, 8) and np.array_equal(multi, masks[1:4]) and multi_iou.tolist() == pytest.approx([0.5, 0.8, 0.6])


# --------------------------------------------------------------------------- registry


def test_registered_graphs_are_pinned_to_the_checkpoint_commit_and_checksums():
    export = _script("export_sam2")
    for name, spec in SAM2_REGISTRY.items():
        assert spec.name == name and spec.license == "Apache-2.0"
        assert spec.encoder_filename == f"{name}-encoder.onnx" and spec.decoder_filename == f"{name}-decoder.onnx"
        assert len(spec.hf_revision) == 40 and all(c in "0123456789abcdef" for c in spec.hf_revision)
        for digest in (spec.encoder_sha256, spec.decoder_sha256, spec.checkpoint_sha256):
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        assert (spec.stability_delta, spec.stability_thresh) == (0.05, 0.98)  # the SAM 2.1 config
        # the export script builds exactly this checkpoint
        checkpoint = export.CHECKPOINTS[name]
        assert checkpoint["repo"] == spec.hf_repo and checkpoint["revision"] == spec.hf_revision
        assert checkpoint["safetensors_sha256"] == spec.checkpoint_sha256 and checkpoint["license"] == spec.license
    assert SPEC.hf_repo == "facebook/sam2.1-hiera-small"


def test_download_script_knows_the_built_graphs_and_only_verifies_them(tmp_path, monkeypatch, capsys):
    download = _script("download_models")
    assert set(SAM2_REGISTRY) <= set(download.REGISTRY)
    monkeypatch.setenv("PROCESSOR_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("PROCESSOR_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setattr("sys.argv", ["download_models.py", "sam2.1-hiera-small"])
    assert download.main() == 1  # missing: not downloaded, the build hint is printed
    assert "export_sam2.py" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_download_script_without_names_tolerates_unbuilt_graphs_but_never_foreign_ones(tmp_path, monkeypatch, capsys):
    """`download_models.py` / `--all` (the Docker build, a developer's first setup): the two
    downloadable models plus the CONFIGURED SAM 2.1 graphs – not built = reported, exit 0 (the
    service runs without SAM 2.1); built but not the registered export = exit 1. The other
    registered SAM 2.1 model (tiny) is never checked by --all."""
    download = _script("download_models")
    monkeypatch.setenv("PROCESSOR_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("PROCESSOR_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setenv("PROCESSOR_SAM2_MODEL", "sam2.1-hiera-small")
    downloaded: list[str] = []

    def fake_ensure_model(spec, models_dir, *, auto_download):
        downloaded.append(spec.name)
        path = Path(models_dir) / spec.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
        return path

    monkeypatch.setattr(download, "ensure_model", fake_ensure_model)
    for argv in (["download_models.py"], ["download_models.py", "--all"]):
        downloaded.clear()
        monkeypatch.setattr("sys.argv", argv)
        assert download.main() == 0
        out = capsys.readouterr()
        assert "sam2.1-hiera-small: not built" in out.out and "sam2.1-hiera-tiny" not in out.out
        assert "sam2.1-hiera-small" not in downloaded  # never downloaded
    assert set(downloaded) == set(download.MODEL_REGISTRY) | set(download.DETECTOR_REGISTRY)  # --all
    spec = SAM2_REGISTRY["sam2.1-hiera-small"]
    for name in spec.filenames:
        (tmp_path / name).write_bytes(b"not the registered export")
    monkeypatch.setattr("sys.argv", ["download_models.py"])
    assert download.main() == 1
    assert "Checksum mismatch" in capsys.readouterr().err


def test_sam2_model_is_configured_by_name(settings, monkeypatch):
    assert create_sam2(settings).spec.name == "sam2.1-hiera-small"
    with pytest.raises(SegmentationError):
        create_sam2(replace(settings, sam2_model="nope"))
    monkeypatch.setenv("PROCESSOR_SAM2_MODEL", "sam2.1-hiera-small")
    monkeypatch.setenv("PROCESSOR_ENV_FILE", "/nonexistent/.env")
    assert Settings.from_env().sam2_model == "sam2.1-hiera-small"


# --------------------------------------------------------------------------- loading


def test_missing_or_foreign_graphs_are_a_service_problem_naming_the_export(tmp_path):
    segmenter = Sam2Segmenter(SPEC, tmp_path / "none")
    assert not segmenter.loaded
    with pytest.raises(ModelUnavailableError, match="export_sam2.py"):
        segmenter.encode(np.zeros((100, 100, 3), np.uint8))
    (tmp_path / SPEC.encoder_filename).write_bytes(b"not the registered export")
    with pytest.raises(ModelUnavailableError, match="Checksum mismatch"):
        ensure_built_model(SPEC.encoder_filename, SPEC.encoder_sha256, tmp_path, name=SPEC.name)
    with pytest.raises(ModelUnavailableError, match="Checksum mismatch"):
        Sam2Segmenter(SPEC, tmp_path).warm_up()


class _Node:
    def __init__(self, name, shape):
        self.name, self.shape = name, list(shape)


class _Session:
    """A fake ONNX Runtime session with the registered layout."""

    def __init__(self, inputs, outputs, run):
        self._inputs = [_Node(n, s) for n, s in inputs.items()]
        self._outputs = [_Node(n, s) for n, s in outputs.items()]
        self._run = run
        self.feeds = []

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs

    def run(self, names, feeds):
        self.feeds.append(feeds)
        return self._run(names, feeds)


def _layout(shape):
    return tuple("num_points" if d is None else d for d in shape)


def _fake_sessions(monkeypatch, tmp_path, *, decoder_outputs=DECODER_OUTPUTS, run_decoder=None):
    import onnxruntime

    def run_encoder(names, feeds):
        return [np.zeros(ENCODER_OUTPUTS[n], np.float32) for n in names]

    def run_decoder_default(names, feeds):
        masks = np.full((1, 4, LOW_RES_SIZE, LOW_RES_SIZE), -6.0, np.float32)
        masks[0, 2, 64:192, 64:192] = 6.0  # multimask output 1 (index 2) covers the centre
        outputs = {"masks": masks, "iou_predictions": np.array([[0.2, 0.3, 0.9, 0.4]], np.float32), "object_score_logits": np.array([[3.0]], np.float32)}
        return [outputs[n] for n in names]

    sessions = {
        "encoder": _Session({k: _layout(v) for k, v in ENCODER_INPUTS.items()}, {k: _layout(v) for k, v in ENCODER_OUTPUTS.items()}, run_encoder),
        "decoder": _Session({k: _layout(v) for k, v in DECODER_INPUTS.items()}, {k: _layout(v) for k, v in decoder_outputs.items()}, run_decoder or run_decoder_default),
    }
    monkeypatch.setattr(sam2, "ensure_built_model", lambda filename, sha256, models_dir, name: tmp_path / filename)
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda path, *args, **kwargs: sessions["encoder" if "encoder" in str(path) else "decoder"])
    return sessions


def test_a_graph_with_another_layout_is_refused_at_load(monkeypatch, tmp_path):
    _fake_sessions(monkeypatch, tmp_path, decoder_outputs={"masks": (1, 3, 256, 256), "iou_predictions": (1, 3)})
    segmenter = Sam2Segmenter(SPEC, tmp_path)
    with pytest.raises(ModelUnavailableError, match="unexpected outputs"):
        segmenter.warm_up()
    assert not segmenter.loaded
    _fake_sessions(monkeypatch, tmp_path, decoder_outputs={**DECODER_OUTPUTS, "masks": (1, 4, 128, 128)})
    with pytest.raises(ModelUnavailableError, match="unexpected shape"):
        Sam2Segmenter(SPEC, tmp_path).warm_up()


def test_encode_and_predict_feed_the_registered_graphs_and_map_the_mask_back(monkeypatch, tmp_path):
    sessions = _fake_sessions(monkeypatch, tmp_path)
    segmenter = Sam2Segmenter(SPEC, tmp_path, threads=2)
    segmenter.warm_up()
    assert segmenter.loaded
    rgb = np.zeros((600, 800, 3), np.uint8)
    embedding = segmenter.encode(rgb)
    assert isinstance(embedding, Sam2Embedding) and (embedding.width, embedding.height) == (800, 600)
    feed = sessions["encoder"].feeds[0]
    assert list(feed) == ["image"] and feed["image"].shape == (1, 3, INPUT_SIZE, INPUT_SIZE)
    assert embedding.image_embed.shape == (1, 256, 64, 64) and embedding.high_res_feats_0.shape == (1, 32, 256, 256)

    masks, ious = segmenter.predict(embedding, [(400.0, 300.0, 1)], (200.0, 150.0, 600.0, 450.0), multimask=True)
    feed = sessions["decoder"].feeds[0]
    assert set(feed) == set(DECODER_INPUTS)
    assert feed["point_coords"].dtype == np.float32 and feed["point_coords"].shape == (1, 3, 2)
    assert feed["point_labels"].dtype == np.int64 and feed["point_labels"][0].tolist() == [2, 3, 1]
    assert feed["point_coords"][0].tolist() == [[256.0, 256.0], [768.0, 768.0], [512.0, 512.0]]
    assert masks.shape == (3, 600, 800) and masks.dtype == np.float32 and ious.tolist() == pytest.approx([0.3, 0.9, 0.4])
    # the fake's mask covers cells 64 … 192 of 256 → photo x 200 … 600, y 150 … 450
    ys, xs = np.nonzero(masks[1] > 0)
    assert abs(xs.min() - 200) <= 4 and abs(xs.max() + 1 - 600) <= 4 and abs(ys.min() - 150) <= 4 and abs(ys.max() + 1 - 450) <= 4
    assert not (masks[0] > 0).any()

    single, single_iou = segmenter.predict(embedding, [], (200.0, 150.0, 600.0, 450.0), multimask=False)
    # the single-mask token (all -6: stable, empty) is returned as is
    assert single.shape == (1, 600, 800) and not (single > 0).any() and single_iou.tolist() == [pytest.approx(0.2)]
    segmenter.release()
    assert not segmenter.loaded


# --------------------------------------------------------------------------- integration


def test_the_built_graphs_segment_the_prompted_vehicle():
    """Runs only with the built graphs in the models folder (scripts/export_sam2.py)."""
    try:
        for filename, sha256 in ((SPEC.encoder_filename, SPEC.encoder_sha256), (SPEC.decoder_filename, SPEC.decoder_sha256)):
            ensure_built_model(filename, sha256, MODELS_DIR, name=SPEC.name)
    except ModelUnavailableError as error:
        pytest.skip(f"SAM 2.1 graphs not built: {error}")
    rgb, truth = make_vehicle_photo(1600, 1200, seed=3)
    ys, xs = np.nonzero(truth > 0.5)
    box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
    segmenter = Sam2Segmenter(SPEC, MODELS_DIR)
    embedding = segmenter.encode(rgb)
    assert segmenter.loaded and (embedding.width, embedding.height) == (1600, 1200)

    def iou(logits):
        a, b = logits > 0, truth > 0.5
        return np.count_nonzero(a & b) / np.count_nonzero(a | b)

    masks, ious = segmenter.predict(embedding, [], box, multimask=True)
    assert masks.shape == (3, 1200, 1600) and ious.shape == (3,) and np.all((ious >= 0) & (ious <= 1))
    assert max(iou(m) for m in masks) > 0.85
    cx, cy = 0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3])
    single, single_iou = segmenter.predict(embedding, [(cx, cy, 1)], None, multimask=False)
    assert single.shape == (1, 1200, 1600) and single_iou.shape == (1,)
    assert iou(single[0]) > 0.5  # one click: the body or the whole car
    boxed, boxed_iou = segmenter.predict(embedding, [(cx, cy, 1)], box, multimask=False)
    assert iou(boxed[0]) > 0.85 and 0 <= float(boxed_iou[0]) <= 1
