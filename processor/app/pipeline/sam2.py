"""Prompted segmentation with Segment Anything 2.1 (SAM 2.1) through ONNX Runtime (CPU).

Used by the contested cut (``mask.sam_cut``, the dealer's decision of 2026-11-06):
inside the contested zone around a detector-verified foreign object glued to the
outline, SAM 2.1 – prompted with "our car" and "not that" – draws the cut line that
BiRefNet and the detector cannot. A second planned use is the window glass of
cockpit photos. Like the detector (``detection.py``) SAM 2.1 only READS the photo:
it answers "which pixels belong to the object at this box / these points"; it never
generates, moves or changes a pixel. BiRefNet stays the matting model everywhere
else, and the cut only ever lowers alpha. The segmenter is created by
``create_sam2_if_available`` (``segmenter.sam2``) and loads its sessions on first
use – a job without a contested region never loads them.

**Model.** Meta's SAM 2.1 ``facebook/sam2.1-hiera-small`` (code and weights
Apache-2.0, see ``SAM2_REGISTRY``), exported to two ONNX graphs by
``scripts/export_sam2.py`` (torch + transformers in a separate build venv – the
runtime needs only onnxruntime):

- ``<name>-encoder.onnx``: ``image`` [1, 3, 1024, 1024] float32 (RGB, resized to
  1024 × 1024 without keeping the aspect ratio, /255, ImageNet mean/std – SAM 2.1's
  own preprocessing) → ``image_embed`` [1, 256, 64, 64], ``high_res_feats_0``
  [1, 32, 256, 256], ``high_res_feats_1`` [1, 64, 128, 128].
- ``<name>-decoder.onnx`` (prompt encoder + mask decoder): the three features +
  ``point_coords`` [1, N, 2] float32 (x, y in model px, 0 … 1024) + ``point_labels``
  [1, N] int64 (1 = foreground, 0 = background, 2 = box top-left, 3 = box
  bottom-right) → ``masks`` [1, 4, 256, 256] low-res mask LOGITS (index 0 = the
  single-mask token, 1 … 3 = the three multimask outputs), ``iou_predictions``
  [1, 4], ``object_score_logits`` [1, 1]. A box is given as its two corners with
  labels 2 and 3 – what the official ``SAM2ImagePredictor`` feeds the prompt
  encoder, which appends SAM's padding point (label -1) itself inside the graph.

**Runtime.** ``Sam2Segmenter.encode(rgb)`` runs the encoder once per photo
(≈ seconds) and returns a ``Sam2Embedding``; ``predict(embedding, points, box,
multimask)`` runs the light decoder per prompt (≈ tens of ms) and returns float32
mask LOGITS (> 0 = inside; sigmoid gives probabilities) resampled bilinearly from
256 × 256 to the photo size, in photo pixel coordinates, plus the IoU predictions.
``multimask=True`` gives the three multimask outputs, ``False`` the single-mask
output – replaced by the best multimask output when it is not stable, as SAM 2.1
does at inference (``dynamic_multimask_via_stability``; ported to NumPy here).

Model files are verified against the pinned SHA-256 when loaded (``ensure_built_model``)
– they cannot be downloaded (no official ONNX release exists), they are BUILT by the
export script from the pinned checkpoint; a missing, different or broken file is a
``ModelUnavailableError``, a service problem like a missing BiRefNet.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..config import Settings
from .segmentation import ModelUnavailableError, SegmentationError, _sha256

log = logging.getLogger(__name__)

#: Side of the square model input (SAM 2.1 image size).
INPUT_SIZE = 1024
#: Side of the low-resolution mask logits the decoder returns.
LOW_RES_SIZE = 256
#: ImageNet normalisation (SAM 2.1 ``preprocessor_config.json``).
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
#: Prompt labels (SAM convention).
LABEL_BACKGROUND = 0
LABEL_FOREGROUND = 1
LABEL_BOX_TOP_LEFT = 2
LABEL_BOX_BOTTOM_RIGHT = 3

#: Expected graph layout (names and static shapes; None = dynamic).
ENCODER_INPUTS = {"image": (1, 3, INPUT_SIZE, INPUT_SIZE)}
ENCODER_OUTPUTS = {
    "image_embed": (1, 256, 64, 64),
    "high_res_feats_0": (1, 32, 256, 256),
    "high_res_feats_1": (1, 64, 128, 128),
}
DECODER_INPUTS = {**ENCODER_OUTPUTS, "point_coords": (1, None, 2), "point_labels": (1, None)}
DECODER_OUTPUTS = {
    "masks": (1, 4, LOW_RES_SIZE, LOW_RES_SIZE),
    "iou_predictions": (1, 4),
    "object_score_logits": (1, 1),
}


@dataclass(frozen=True)
class Sam2ModelSpec:
    name: str
    encoder_filename: str
    decoder_filename: str
    #: SHA-256 of the two ONNX files as ``scripts/export_sam2.py`` builds them with the
    #: pinned export stack (byte-deterministic – verified by two independent exports).
    encoder_sha256: str
    decoder_sha256: str
    #: Hugging Face checkpoint the graphs are built from: repo, commit, SHA-256 of
    #: ``model.safetensors`` (checked by the export script before it loads the weights).
    hf_repo: str
    hf_revision: str
    checkpoint_sha256: str
    #: ``dynamic_multimask_via_stability`` parameters of the checkpoint's config.
    stability_delta: float
    stability_thresh: float
    license: str
    source: str

    @property
    def filenames(self) -> tuple[str, str]:
        return self.encoder_filename, self.decoder_filename


SAM2_REGISTRY: dict[str, Sam2ModelSpec] = {
    # SAM 2.1 Hiera-Small (Meta, 46 M parameters). Code (github.com/facebookresearch/sam2)
    # and weights (Hugging Face model card: license apache-2.0) Apache-2.0, checked 2026-11-06.
    "sam2.1-hiera-small": Sam2ModelSpec(
        name="sam2.1-hiera-small",
        encoder_filename="sam2.1-hiera-small-encoder.onnx",
        decoder_filename="sam2.1-hiera-small-decoder.onnx",
        encoder_sha256="bb4440eda4e18e65c953ad926a42a4bb814aeb73dcbed4b7aedfaef0fb4d6ac5",
        decoder_sha256="18c46fb4425a1dc60c36a6b17845485ac37d33b91bb4df869406930dbf1413f1",
        hf_repo="facebook/sam2.1-hiera-small",
        hf_revision="ee5bba1d82bb8749febdf90f45e84b687142ba03",
        checkpoint_sha256="0a4067b11ce1e23d5229203f11c718a823060d15a4b23fa2372a7d4b77cbbc60",
        stability_delta=0.05,
        stability_thresh=0.98,
        license="Apache-2.0",
        source="https://huggingface.co/facebook/sam2.1-hiera-small (transformers weights), "
        "https://github.com/facebookresearch/sam2; ONNX built by scripts/export_sam2.py",
    ),
    # The lighter alternative (Hiera-Tiny, 28 MB smaller, encode ≈ 20 % faster, same decoder):
    # PROCESSOR_SAM2_MODEL=sam2.1-hiera-tiny. Same licence, same export, same verification.
    "sam2.1-hiera-tiny": Sam2ModelSpec(
        name="sam2.1-hiera-tiny",
        encoder_filename="sam2.1-hiera-tiny-encoder.onnx",
        decoder_filename="sam2.1-hiera-tiny-decoder.onnx",
        encoder_sha256="0cbccc0beb933344b169935bc2708bfc6ef94d2b6cd9e12b0873861c03664c4c",
        decoder_sha256="cc9662f5100490398b900783e2c88965c05ec7f3abde22f251cfaf73f454cdef",
        hf_repo="facebook/sam2.1-hiera-tiny",
        hf_revision="de431c4043854a71d8101e17995dfe596bf101a5",
        checkpoint_sha256="48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7",
        stability_delta=0.05,
        stability_thresh=0.98,
        license="Apache-2.0",
        source="https://huggingface.co/facebook/sam2.1-hiera-tiny (transformers weights), "
        "https://github.com/facebookresearch/sam2; ONNX built by scripts/export_sam2.py",
    ),
}


def ensure_built_model(filename: str, sha256: str, models_dir: Path, *, name: str) -> Path:
    """Return the verified local path of a BUILT model file (no download source exists):
    a missing file names the export script, another checksum is refused like the other
    models (``segmentation.ensure_model``)."""
    path = models_dir / filename
    if not path.is_file():
        raise ModelUnavailableError(
            f"Model file {path} missing – SAM 2.1 graphs are built from the pinned checkpoint by "
            "scripts/export_sam2.py (torch export venv), they cannot be downloaded"
        )
    actual = _sha256(path)
    if actual != sha256:
        raise ModelUnavailableError(
            f"Checksum mismatch for {name}: {path} is not the registered export ({actual}) – "
            "delete it and rebuild it with scripts/export_sam2.py"
        )
    return path


# --------------------------------------------------------------------------- pre/post-processing


def preprocess(rgb: np.ndarray) -> np.ndarray:
    """SAM 2.1 image preprocessing: resize to 1024 × 1024 (bilinear, antialiased – the
    resize of the official image processor), /255, ImageNet mean/std → [1, 3, 1024, 1024]."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise SegmentationError("expected an RGB image")
    if rgb.dtype != np.uint8:
        raise SegmentationError("expected an 8-bit RGB image")
    if rgb.shape[:2] != (INPUT_SIZE, INPUT_SIZE):
        resized = np.asarray(Image.fromarray(rgb).resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR))
    else:
        resized = rgb
    tensor = (resized.astype(np.float32) / 255.0 - MEAN) / STD
    return np.ascontiguousarray(tensor.transpose(2, 0, 1)[None])


def to_model_coords(xy: np.ndarray, width: int, height: int) -> np.ndarray:
    """Photo px (x, y) → model px of the 1024 × 1024 input (SAM 2.1 scales each axis
    on its own, no padding)."""
    xy = np.asarray(xy, np.float32).reshape(-1, 2)
    return xy * np.array([INPUT_SIZE / width, INPUT_SIZE / height], np.float32)


def prompt_arrays(
    points: list[tuple[float, float, int]], box: tuple[float, float, float, float] | None, width: int, height: int
) -> tuple[np.ndarray, np.ndarray]:
    """Decoder inputs for the prompt: ``points`` as (x, y, label) in photo px, ``box`` as
    (x0, y0, x1, y1) in photo px → corners with labels 2/3 first; ``point_coords``
    [1, N, 2] float32 (model px), ``point_labels`` [1, N] int64. The padding point is
    appended by the graph's prompt encoder, never here."""
    coords: list[tuple[float, float]] = []
    labels: list[int] = []
    if box is not None:
        x0, y0, x1, y1 = (float(v) for v in box)
        coords += [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))]
        labels += [LABEL_BOX_TOP_LEFT, LABEL_BOX_BOTTOM_RIGHT]
    for x, y, label in points:
        if int(label) not in (LABEL_BACKGROUND, LABEL_FOREGROUND):
            raise ValueError("point labels are 0 (background) or 1 (foreground)")
        coords.append((float(x), float(y)))
        labels.append(int(label))
    if not coords:
        raise ValueError("a prompt needs a box or at least one point")
    model_xy = to_model_coords(np.array(coords, np.float32), width, height)
    return model_xy[None].astype(np.float32), np.array(labels, np.int64)[None]


def stability_scores(mask_logits: np.ndarray, delta: float) -> np.ndarray:
    """Per mask [K, h, w]: area above +delta / area above -delta (1 when both are empty)."""
    flat = mask_logits.reshape(mask_logits.shape[0], -1)
    area_i = np.count_nonzero(flat > delta, axis=-1).astype(np.float32)
    area_u = np.count_nonzero(flat > -delta, axis=-1).astype(np.float32)
    return np.where(area_u > 0, area_i / np.maximum(area_u, 1.0), 1.0).astype(np.float32)


def select_masks(
    masks: np.ndarray, ious: np.ndarray, *, multimask: bool, delta: float, thresh: float
) -> tuple[np.ndarray, np.ndarray]:
    """SAM 2.1's output selection on the raw decoder outputs (``masks`` [4, h, w], ``ious``
    [4]): the three multimask outputs, or the single-mask output – replaced by the best
    (by IoU) multimask output when its stability score is below ``thresh``
    (``dynamic_multimask_via_stability``, on at inference in every SAM 2.1 config)."""
    if multimask:
        return masks[1:4], ious[1:4]
    stable = float(stability_scores(masks[0:1], delta)[0]) >= thresh
    if stable:
        return masks[0:1], ious[0:1]
    best = int(np.argmax(ious[1:4])) + 1
    return masks[best : best + 1], ious[best : best + 1]


def resample_logits(low_res: np.ndarray, width: int, height: int) -> np.ndarray:
    """Low-res logits [K, 256, 256] → [K, height, width] float32, bilinear (what the
    official predictor does with ``F.interpolate(..., mode="bilinear")``)."""
    out = np.empty((low_res.shape[0], height, width), np.float32)
    for k in range(low_res.shape[0]):
        out[k] = cv2.resize(np.asarray(low_res[k], np.float32), (width, height), interpolation=cv2.INTER_LINEAR)
    return out


# --------------------------------------------------------------------------- runtime


@dataclass(frozen=True)
class Sam2Embedding:
    """The encoder's view of one photo (``width`` × ``height`` photo px)."""

    image_embed: np.ndarray
    high_res_feats_0: np.ndarray
    high_res_feats_1: np.ndarray
    width: int
    height: int

    @property
    def feeds(self) -> dict[str, np.ndarray]:
        return {
            "image_embed": self.image_embed,
            "high_res_feats_0": self.high_res_feats_0,
            "high_res_feats_1": self.high_res_feats_1,
        }


def _check_layout(session, expected_inputs: dict, expected_outputs: dict, name: str) -> None:
    """The graph must be the registered export: same names, same static shapes."""
    for kind, nodes, expected in (("input", session.get_inputs(), expected_inputs), ("output", session.get_outputs(), expected_outputs)):
        actual = {node.name: tuple(node.shape) for node in nodes}
        if set(actual) != set(expected):
            raise ModelUnavailableError(f"unexpected {kind}s of {name}: {sorted(actual)} (expected {sorted(expected)})")
        for node_name, shape in expected.items():
            got = actual[node_name]
            if len(got) != len(shape) or any(e is not None and g != e for g, e in zip(got, shape)):
                raise ModelUnavailableError(f"unexpected shape of {name} {kind} {node_name}: {got} (expected {shape})")


class Sam2Segmenter:
    """SAM 2.1 image encoder + prompt decoder through ONNX Runtime (CPU), loaded on first use."""

    def __init__(self, spec: Sam2ModelSpec, models_dir: Path, *, threads: int = 0):
        self.spec = spec
        self.name = f"onnx:{spec.name}"
        self._models_dir = models_dir
        self._threads = threads
        self._encoder = None
        self._decoder = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._encoder is not None and self._decoder is not None

    def _load(self, filename: str, sha256: str, inputs: dict, outputs: dict):
        import onnxruntime as ort

        path = ensure_built_model(filename, sha256, self._models_dir, name=self.spec.name)
        options = ort.SessionOptions()
        if self._threads:
            options.intra_op_num_threads = self._threads
        options.log_severity_level = 3
        options.enable_cpu_mem_arena = False  # activations are returned after each run
        try:
            session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        except Exception as error:  # unreadable/corrupt file, out of memory, …
            raise ModelUnavailableError(f"cannot load model {self.spec.name} ({filename}): {error}") from error
        _check_layout(session, inputs, outputs, f"{self.spec.name} {filename}")
        return session

    def _get_sessions(self):
        with self._lock:
            if self._encoder is None or self._decoder is None:
                encoder = self._load(self.spec.encoder_filename, self.spec.encoder_sha256, ENCODER_INPUTS, ENCODER_OUTPUTS)
                decoder = self._load(self.spec.decoder_filename, self.spec.decoder_sha256, DECODER_INPUTS, DECODER_OUTPUTS)
                self._encoder, self._decoder = encoder, decoder
            return self._encoder, self._decoder

    def warm_up(self) -> None:
        self._get_sessions()

    def verify(self) -> None:
        """Check both graph files against the pinned SHA-256 without loading them (≈ 0.5 s at the
        service warm-up; a missing or foreign file is a ``ModelUnavailableError``)."""
        for filename, sha256 in (
            (self.spec.encoder_filename, self.spec.encoder_sha256),
            (self.spec.decoder_filename, self.spec.decoder_sha256),
        ):
            ensure_built_model(filename, sha256, self._models_dir, name=self.spec.name)

    def release(self) -> None:
        """Drop both sessions (their weights and arenas); the next call loads them again."""
        with self._lock:
            self._encoder = None
            self._decoder = None

    def encode(self, rgb: np.ndarray) -> Sam2Embedding:
        """Image features of ``rgb`` (uint8 RGB, any size) – once per photo."""
        tensor = preprocess(rgb)
        encoder, _ = self._get_sessions()
        embed, hr0, hr1 = encoder.run(["image_embed", "high_res_feats_0", "high_res_feats_1"], {"image": tensor})
        return Sam2Embedding(
            np.asarray(embed, np.float32),
            np.asarray(hr0, np.float32),
            np.asarray(hr1, np.float32),
            width=int(rgb.shape[1]),
            height=int(rgb.shape[0]),
        )

    def decode(
        self,
        embedding: Sam2Embedding,
        points: list[tuple[float, float, int]],
        box: tuple[float, float, float, float] | None,
        *,
        multimask: bool,
    ) -> tuple[np.ndarray, np.ndarray, float]:
        """Low-res mask logits [K, 256, 256], IoU predictions [K] and the object score
        logit for the prompt (``points`` (x, y, label 0/1), ``box`` (x0, y0, x1, y1), photo
        px); K = 3 with ``multimask`` else 1."""
        coords, labels = prompt_arrays(points, box, embedding.width, embedding.height)
        _, decoder = self._get_sessions()
        masks, ious, score = decoder.run(
            ["masks", "iou_predictions", "object_score_logits"],
            {**embedding.feeds, "point_coords": coords, "point_labels": labels},
        )
        masks, ious = select_masks(
            np.asarray(masks, np.float32)[0],
            np.asarray(ious, np.float32)[0],
            multimask=multimask,
            delta=self.spec.stability_delta,
            thresh=self.spec.stability_thresh,
        )
        return masks, ious, float(np.asarray(score).reshape(-1)[0])

    def predict(
        self,
        embedding: Sam2Embedding,
        points: list[tuple[float, float, int]],
        box: tuple[float, float, float, float] | None = None,
        multimask: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Mask LOGITS [K, height, width] float32 in photo px (> 0 = inside the object;
        ``1 / (1 + exp(-logits))`` = probabilities) and IoU predictions [K] for the prompt;
        K = 3 with ``multimask`` (the three SAM candidates – pick by IoU or by area),
        else 1 (see ``select_masks``)."""
        masks, ious, _ = self.decode(embedding, points, box, multimask=multimask)
        return resample_logits(masks, embedding.width, embedding.height), ious


def create_sam2(settings: Settings) -> Sam2Segmenter:
    spec = SAM2_REGISTRY.get(settings.sam2_model)
    if spec is None:
        raise SegmentationError(
            f"Unknown SAM 2 model '{settings.sam2_model}'. Available: {', '.join(sorted(SAM2_REGISTRY))}"
        )
    return Sam2Segmenter(spec, settings.models_dir, threads=settings.threads)


def graphs_present(settings: Settings) -> bool:
    """Both built graphs of the configured model exist in ``models_dir`` (not yet verified)."""
    spec = SAM2_REGISTRY.get(settings.sam2_model)
    return spec is not None and all((settings.models_dir / name).is_file() for name in spec.filenames)


def create_sam2_if_available(settings: Settings) -> Sam2Segmenter | None:
    """The process's ONE SAM 2.1 segmenter (``segmenter.sam2``: the contested cut and the automatic
    glass masks of interior shots share it), or None when it is off or its graphs are not there
    (``Settings.sam2_enabled``): the pipeline then behaves as without it – a glued foreign object
    whose cut line no other signal gives keeps rejecting the photo, interior shots stay the
    original with a hint. Nothing is loaded here (the sessions load on first use). A missing graph
    is logged (a WARNING when PROCESSOR_SAM2_ENABLED=true asked for it); an unknown model name is
    a configuration error like an unknown detector."""
    if settings.sam2_enabled is False:
        log.info("SAM 2.1 is off (PROCESSOR_SAM2_ENABLED=false): no contested cut, no automatic glass masks")
        return None
    if not graphs_present(settings):
        spec = SAM2_REGISTRY.get(settings.sam2_model)
        if spec is None:
            raise SegmentationError(
                f"Unknown SAM 2 model '{settings.sam2_model}'. Available: {', '.join(sorted(SAM2_REGISTRY))}"
            )
        message = (
            "SAM 2.1 graphs %s/%s not found in %s – the contested cut and the automatic glass masks are off: a "
            "foreign object glued to the vehicle outline rejects the photo as before, interior shots stay the "
            "original with a hint (build them with scripts/export_sam2.py; the Docker build does, unless SAM2=off)"
        )
        args = (spec.encoder_filename, spec.decoder_filename, settings.models_dir)
        if settings.sam2_enabled:
            log.warning(message, *args)
        else:
            log.info(message, *args)
        return None
    return create_sam2(settings)
