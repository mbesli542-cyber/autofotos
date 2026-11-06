"""Build the SAM 2.1 ONNX graphs the processor uses (``app/pipeline/sam2.py``).

Meta's Segment Anything 2.1 (code and weights Apache-2.0) is published as PyTorch
weights only. This script downloads the pinned Hugging Face checkpoint, verifies its
SHA-256, loads it with ``transformers`` and exports two ONNX graphs with the pinned
export stack – the service itself needs only onnxruntime:

- ``<name>-encoder.onnx``: ``image`` [1, 3, 1024, 1024] → ``image_embed`` [1, 256, 64, 64],
  ``high_res_feats_0`` [1, 32, 256, 256], ``high_res_feats_1`` [1, 64, 128, 128]
  (``Sam2Model.get_image_embeddings``: Hiera backbone + FPN neck, the two high-res
  levels already projected by the decoder's ``conv_s0``/``conv_s1``);
- ``<name>-decoder.onnx``: the three features + ``point_coords`` [1, N, 2] float32
  (model px) + ``point_labels`` [1, N] int64 → ``masks`` [1, 4, 256, 256] (low-res mask
  logits of ALL four mask tokens: 0 = single-mask token, 1 … 3 = multimask),
  ``iou_predictions`` [1, 4], ``object_score_logits`` [1, 1]. The prompt encoder
  appends SAM's padding point itself (as ``Sam2Model`` does when no box tensor is
  given – a box is passed as two corner points labelled 2/3, like the official
  ``SAM2ImagePredictor``). The output selection (multimask / single mask with the
  stability fallback) is NOT in the graph: the runtime does it on the four raw masks.

Run it in the separate export venv (never in the service venv – torch is not a
runtime dependency)::

    python3 -m venv /opt/ae-v14/torch-venv
    /opt/ae-v14/torch-venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0+cpu
    /opt/ae-v14/torch-venv/bin/pip install transformers==5.22.1 onnx==1.23.2 onnxscript==0.7.2 \
        onnxruntime==1.31.1 huggingface_hub==2.4.0 pillow
    /opt/ae-v14/torch-venv/bin/python scripts/export_sam2.py --out models/              # sam2.1-hiera-small
    /opt/ae-v14/torch-venv/bin/python scripts/export_sam2.py --model sam2.1-hiera-tiny --out /tmp/tiny
    /opt/ae-v14/torch-venv/bin/python scripts/export_sam2.py --out models/ --verify cases.json   # vs PyTorch
    /opt/ae-v14/torch-venv/bin/python scripts/export_sam2.py --fingerprint models/sam2.1-hiera-small-encoder.onnx

Determinism: the versions above are PINNED (``PINNED_VERSIONS`` – the script refuses
others), the opset is pinned (``OPSET``), seeds are fixed, the graphs carry no
doc strings, stack traces or metadata (``strip_metadata`` – they would hold the
export machine's file paths) and the export runs twice in a row and must give the
same bytes (``--once`` skips that). The checksum printed at the end is what
``SAM2_REGISTRY`` pins; ``--fingerprint`` prints the structural fingerprint (opset,
inputs/outputs, sorted initializer names + dtypes + shapes, op histogram), the
fallback pin should an export on another machine differ in bytes (constant folding
of transcendental ops may differ in the last bits between CPUs).

``--verify cases.json`` compares the ONNX graphs (onnxruntime) against the PyTorch
model on photos: ``[{"image": "…jpg", "box": [x0, y0, x1, y1]}, …]`` (photo px) –
a box prompt, a foreground point at the box centre and the same point with a
background point in a corner; the mask IoU (logits > 0 at 256 × 256) and the IoU
predictions are reported for the official preprocessing (``Sam2ImageProcessor``:
antialiased bilinear resize to 1024 × 1024 of the uint8 tensor, then /255 and
ImageNet mean/std – reproduced here without torchvision) and for the service's own
(Pillow bilinear, ``app/pipeline/sam2.py`` – the same resize up to ±1 on 0.1 % of
the pixels). Both must give IoU > 0.98 and IoU predictions within 0.05.

Standalone on purpose: stdlib + numpy + Pillow at import time (the service's tests
import ``CHECKPOINTS`` and ``preprocess``); torch, transformers, onnx and
onnxruntime are imported where they are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

#: Side of the square model input and of the low-resolution masks.
INPUT_SIZE = 1024
LOW_RES_SIZE = 256
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
#: ONNX opset of both graphs.
OPSET = 18
#: The export stack. Another version = another graph (and another checksum): refused.
PINNED_VERSIONS = {"torch": "2.15.0+cpu", "transformers": "5.22.1", "onnx": "1.23.2", "onnxscript": "0.7.2"}
#: Graph names and the dynamic axis of the decoder prompt.
ENCODER_INPUTS = ["image"]
ENCODER_OUTPUTS = ["image_embed", "high_res_feats_0", "high_res_feats_1"]
DECODER_INPUTS = ["image_embed", "high_res_feats_0", "high_res_feats_1", "point_coords", "point_labels"]
DECODER_OUTPUTS = ["masks", "iou_predictions", "object_score_logits"]
MAX_POINTS = 256

#: The checkpoints (Hugging Face, commit-pinned; ``safetensors_sha256`` is checked before
#: the weights are loaded). Both repos: license apache-2.0 (model card, checked 2026-11-06).
CHECKPOINTS: dict[str, dict] = {
    "sam2.1-hiera-small": {
        "repo": "facebook/sam2.1-hiera-small",
        "revision": "ee5bba1d82bb8749febdf90f45e84b687142ba03",
        "safetensors_sha256": "0a4067b11ce1e23d5229203f11c718a823060d15a4b23fa2372a7d4b77cbbc60",
        "license": "Apache-2.0",
    },
    "sam2.1-hiera-tiny": {
        "repo": "facebook/sam2.1-hiera-tiny",
        "revision": "de431c4043854a71d8101e17995dfe596bf101a5",
        "safetensors_sha256": "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7",
        "license": "Apache-2.0",
    },
}
#: Files of the checkpoint the export needs (the original ``.pt`` is not downloaded).
CHECKPOINT_FILES = ["config.json", "model.safetensors", "preprocessor_config.json"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- preprocessing


def preprocess(rgb: np.ndarray) -> np.ndarray:
    """The service's preprocessing (``app/pipeline/sam2.py``): Pillow bilinear resize to
    1024 × 1024, /255, ImageNet mean/std → [1, 3, 1024, 1024] float32."""
    resized = np.asarray(Image.fromarray(rgb).resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR))
    tensor = (resized.astype(np.float32) / 255.0 - MEAN) / STD
    return np.ascontiguousarray(tensor.transpose(2, 0, 1)[None])


def preprocess_official(rgb: np.ndarray) -> np.ndarray:
    """What ``Sam2ImageProcessor`` (transformers 5, torchvision backend) does, without
    torchvision: the uint8 tensor is resized first (``torchvision.transforms.v2.functional.resize``
    = ``torch.nn.functional.interpolate`` bilinear with antialias on the uint8 tensor), then
    rescaled (/255) and normalised (ImageNet mean/std) in float32. Pillow's bilinear resize
    (the service's ``preprocess``) agrees with torch's uint8 kernel on 99.9 % of the pixels,
    ±1 on the rest (measured on a 3840 × 2880 photo)."""
    import torch
    import torch.nn.functional as F

    tensor = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1)[None]
    tensor = F.interpolate(tensor, size=(INPUT_SIZE, INPUT_SIZE), mode="bilinear", align_corners=False, antialias=True)
    tensor = tensor.to(torch.float32) / 255.0
    tensor = (tensor - torch.tensor(MEAN).view(1, 3, 1, 1)) / torch.tensor(STD).view(1, 3, 1, 1)
    return tensor.numpy().astype(np.float32)


# --------------------------------------------------------------------------- checkpoint


def check_versions() -> None:
    import onnx
    import onnxscript
    import torch
    import transformers

    actual = {
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "onnx": onnx.__version__,
        "onnxscript": onnxscript.__version__,
    }
    wrong = {k: v for k, v in actual.items() if v != PINNED_VERSIONS[k]}
    if wrong:
        raise SystemExit(f"export stack differs from the pinned versions {PINNED_VERSIONS}: {wrong}")


def fetch_checkpoint(name: str, cache_dir: Path) -> Path:
    """Download (or reuse) the pinned checkpoint files and verify ``model.safetensors``."""
    from huggingface_hub import snapshot_download

    spec = CHECKPOINTS[name]
    path = Path(
        snapshot_download(
            spec["repo"], revision=spec["revision"], allow_patterns=CHECKPOINT_FILES, cache_dir=str(cache_dir)
        )
    )
    actual = sha256_file(path / "model.safetensors")
    if actual != spec["safetensors_sha256"]:
        raise SystemExit(f"{spec['repo']}@{spec['revision']}: model.safetensors has SHA-256 {actual}, pinned is {spec['safetensors_sha256']}")
    return path


def load_model(checkpoint: Path):
    import torch
    from transformers import Sam2Model

    torch.manual_seed(0)
    model = Sam2Model.from_pretrained(str(checkpoint), local_files_only=True, dtype=torch.float32)
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model


# --------------------------------------------------------------------------- graph modules


def _graph_modules(model):
    import torch

    class EncoderGraph(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, image):
            feats_s0, feats_s1, image_embed = self.model.get_image_embeddings(image)
            return image_embed, feats_s0, feats_s1

    class DecoderGraph(torch.nn.Module):
        """Prompt encoder + mask decoder, returning ALL mask tokens (the official forward
        slices them by ``multimask_output``; the runtime selects on the raw outputs)."""

        def __init__(self, model):
            super().__init__()
            self.prompt_encoder = model.prompt_encoder
            self.decoder = model.mask_decoder
            # constant image-wide positional encoding (random Gaussian matrix of the checkpoint)
            self.register_buffer("image_pe", model.get_image_wide_positional_embeddings().detach().clone())

        def forward(self, image_embed, high_res_feats_0, high_res_feats_1, point_coords, point_labels):
            sparse, dense = self.prompt_encoder(
                input_points=point_coords[:, None], input_labels=point_labels[:, None], input_boxes=None, input_masks=None
            )
            masks, iou_pred, object_score = decode_all_masks(
                self.decoder, image_embed, self.image_pe, sparse, dense, [high_res_feats_0, high_res_feats_1]
            )
            return masks[:, 0], iou_pred[:, 0], object_score[:, 0]

    return EncoderGraph(model).eval(), DecoderGraph(model).eval()


def decode_all_masks(decoder, image_embeddings, image_pe, sparse, dense, high_res):
    """``Sam2MaskDecoder.forward`` (transformers 5.22.1) up to the output selection:
    masks [B, P, 4, 256, 256], IoU predictions [B, P, 4], object score logits [B, P, 1]."""
    import torch

    batch_size, num_channels, height, width = image_embeddings.shape
    point_batch_size = sparse.shape[1]
    output_tokens = torch.cat([decoder.obj_score_token.weight, decoder.iou_token.weight, decoder.mask_tokens.weight], dim=0)
    output_tokens = output_tokens.repeat(batch_size, point_batch_size, 1, 1)
    tokens = torch.cat((output_tokens, sparse), dim=2)
    point_embeddings = tokens.to(decoder.iou_token.weight.dtype)

    image_embeddings = image_embeddings + dense
    image_embeddings = image_embeddings.repeat_interleave(point_batch_size, dim=0)
    image_pe = image_pe.repeat_interleave(point_batch_size, 0)
    point_embeddings, image_embeddings = decoder.transformer(
        point_embeddings=point_embeddings,
        image_embeddings=image_embeddings,
        image_positional_embeddings=image_pe,
        attention_similarity=None,
        target_embedding=None,
    )
    iou_token_out = point_embeddings[:, :, 1, :]
    mask_tokens_out = point_embeddings[:, :, 2 : (2 + decoder.num_mask_tokens), :]

    image_embeddings = image_embeddings.transpose(2, 3).view(batch_size * point_batch_size, num_channels, height, width)
    feat_s0, feat_s1 = high_res
    feat_s0 = feat_s0.repeat_interleave(point_batch_size, dim=0)
    feat_s1 = feat_s1.repeat_interleave(point_batch_size, dim=0)
    upscaled = decoder.upscale_conv1(image_embeddings) + feat_s1
    upscaled = decoder.activation(decoder.upscale_layer_norm(upscaled))
    upscaled = decoder.activation(decoder.upscale_conv2(upscaled) + feat_s0)

    hyper_in = torch.stack(
        [decoder.output_hypernetworks_mlps[i](mask_tokens_out[:, :, i, :]) for i in range(decoder.num_mask_tokens)], dim=2
    )
    _, num_channels, height, width = upscaled.shape
    upscaled = upscaled.view(batch_size, point_batch_size, num_channels, height * width)
    masks = (hyper_in @ upscaled).view(batch_size, point_batch_size, -1, height, width)
    iou_pred = decoder.iou_prediction_head(iou_token_out)
    object_score_logits = decoder.pred_obj_score_head(point_embeddings[:, :, 0, :])
    return masks, iou_pred, object_score_logits


# --------------------------------------------------------------------------- export


def strip_metadata(model) -> None:
    """Remove everything that is not the graph: doc strings and metadata (the dynamo
    exporter records source locations – file paths of the export machine)."""
    model.doc_string = ""
    del model.metadata_props[:]
    graphs = [model.graph] + [f for f in model.functions]
    for graph in graphs:
        graph.doc_string = ""
        for node in graph.node:
            node.doc_string = ""
            del node.metadata_props[:]
            for attribute in node.attribute:
                if attribute.type == attribute.GRAPH:
                    graphs.append(attribute.g)
                elif attribute.type == attribute.GRAPHS:
                    graphs.extend(attribute.graphs)
        if hasattr(graph, "metadata_props"):
            del graph.metadata_props[:]
        for value in list(getattr(graph, "input", [])) + list(getattr(graph, "output", [])) + list(getattr(graph, "value_info", [])):
            value.doc_string = ""


def fingerprint(path: Path) -> str:
    """Structural fingerprint: SHA-256 over opset imports, inputs/outputs (names, dtypes,
    shapes), sorted initializers (name, dtype, shape) and the op-type histogram –
    independent of the weight bytes and of the last bits of folded constants."""
    import onnx

    model = onnx.load(str(path), load_external_data=False)

    def value(v):
        shape = [d.dim_param or d.dim_value for d in v.type.tensor_type.shape.dim]
        return [v.name, v.type.tensor_type.elem_type, shape]

    ops: dict[str, int] = {}
    for node in model.graph.node:
        ops[node.op_type] = ops.get(node.op_type, 0) + 1
    summary = {
        "opset": sorted((o.domain, o.version) for o in model.opset_import),
        "inputs": [value(v) for v in model.graph.input],
        "outputs": [value(v) for v in model.graph.output],
        "initializers": sorted([t.name, t.data_type, list(t.dims)] for t in model.graph.initializer),
        "ops": sorted(ops.items()),
    }
    return hashlib.sha256(json.dumps(summary, separators=(",", ":")).encode()).hexdigest()


def export_graph(module, args, path: Path, *, input_names, output_names, dynamic_shapes=None) -> None:
    import onnx
    import torch

    torch.manual_seed(0)
    program = torch.onnx.export(
        module,
        tuple(args),
        input_names=input_names,
        output_names=output_names,
        opset_version=OPSET,
        dynamo=True,
        dynamic_shapes=dynamic_shapes,
        external_data=False,
        optimize=True,
        verify=False,
    )
    model = program.model_proto
    if model.functions:
        raise SystemExit(f"{path.name}: the exporter left {len(model.functions)} local functions in the graph")
    strip_metadata(model)
    model.producer_name = "export_sam2.py"
    model.producer_version = f"torch {PINNED_VERSIONS['torch']}, transformers {PINNED_VERSIONS['transformers']}"
    onnx.checker.check_model(model, full_check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(model, str(path))


def export(name: str, out_dir: Path, cache_dir: Path, *, suffix: str = "") -> dict[str, Path]:
    import torch

    check_versions()
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    checkpoint = fetch_checkpoint(name, cache_dir)
    model = load_model(checkpoint)
    encoder, decoder = _graph_modules(model)
    paths = {"encoder": out_dir / f"{name}-encoder{suffix}.onnx", "decoder": out_dir / f"{name}-decoder{suffix}.onnx"}

    with torch.no_grad():
        image = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE)
        started = time.perf_counter()
        export_graph(encoder, (image,), paths["encoder"], input_names=ENCODER_INPUTS, output_names=ENCODER_OUTPUTS)
        print(f"encoder exported in {time.perf_counter() - started:.0f} s → {paths['encoder']}")

        embed, hr0, hr1 = encoder(image)
        num_points = torch.export.Dim("num_points", min=1, max=MAX_POINTS)
        coords = torch.tensor([[[300.0, 200.0], [700.0, 800.0], [512.0, 512.0]]])
        labels = torch.tensor([[2, 3, 1]], dtype=torch.int64)
        started = time.perf_counter()
        export_graph(
            decoder,
            (embed, hr0, hr1, coords, labels),
            paths["decoder"],
            input_names=DECODER_INPUTS,
            output_names=DECODER_OUTPUTS,
            dynamic_shapes=(None, None, None, {1: num_points}, {1: num_points}),
        )
        print(f"decoder exported in {time.perf_counter() - started:.0f} s → {paths['decoder']}")
    return paths


def write_manifest(name: str, paths: dict[str, Path], out_dir: Path) -> Path:
    spec = CHECKPOINTS[name]
    manifest = {
        "model": name,
        "checkpoint": {"repo": spec["repo"], "revision": spec["revision"], "safetensors_sha256": spec["safetensors_sha256"], "license": spec["license"]},
        "export": {**PINNED_VERSIONS, "opset": OPSET, "script": "processor/scripts/export_sam2.py"},
        "files": {
            kind: {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path), "fingerprint": fingerprint(path)}
            for kind, path in paths.items()
        },
    }
    target = out_dir / f"{name}.export.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


# --------------------------------------------------------------------------- verification


def _torch_reference(model, tensor: np.ndarray, coords: np.ndarray, labels: np.ndarray):
    """PyTorch: raw multimask outputs and the official single-mask output for one prompt."""
    import torch

    with torch.no_grad():
        feats = model.get_image_embeddings(torch.from_numpy(tensor))
        points = torch.from_numpy(coords)[:, None]
        point_labels = torch.from_numpy(labels)[:, None]
        multi = model(image_embeddings=feats, input_points=points, input_labels=point_labels, multimask_output=True)
        single = model(image_embeddings=feats, input_points=points, input_labels=point_labels, multimask_output=False)
    return (
        multi.pred_masks[0, 0].numpy(),
        multi.iou_scores[0, 0].numpy(),
        single.pred_masks[0, 0].numpy(),
        single.iou_scores[0, 0].numpy(),
    )


def _onnx_outputs(sessions, tensor: np.ndarray, coords: np.ndarray, labels: np.ndarray):
    encoder, decoder = sessions
    embed, hr0, hr1 = encoder.run(None, {"image": tensor})
    masks, ious, score = decoder.run(
        None, {"image_embed": embed, "high_res_feats_0": hr0, "high_res_feats_1": hr1, "point_coords": coords, "point_labels": labels}
    )
    return masks[0], ious[0], float(score.reshape(-1)[0])


def _select_single(masks: np.ndarray, ious: np.ndarray, delta: float, thresh: float):
    """The runtime's single-mask selection (``app/pipeline/sam2.select_masks``)."""
    flat = masks[0].reshape(-1)
    area_i = float(np.count_nonzero(flat > delta))
    area_u = float(np.count_nonzero(flat > -delta))
    stability = area_i / area_u if area_u > 0 else 1.0
    if stability >= thresh:
        return masks[0], ious[0]
    best = int(np.argmax(ious[1:4])) + 1
    return masks[best], ious[best]


def _mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a > 0, b > 0
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b)) / union if union else 1.0


def verify(name: str, paths: dict[str, Path], cases_file: Path, cache_dir: Path) -> bool:
    import onnxruntime as ort

    model = load_model(fetch_checkpoint(name, cache_dir))
    delta, thresh = model.mask_decoder.dynamic_multimask_stability_delta, model.mask_decoder.dynamic_multimask_stability_thresh
    options = ort.SessionOptions()
    options.log_severity_level = 3
    sessions = tuple(ort.InferenceSession(str(paths[k]), options, providers=["CPUExecutionProvider"]) for k in ("encoder", "decoder"))
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    ok = True
    print(f"{'photo':28s} {'prompt':14s} {'pre':8s} {'mask IoU (3)':22s} {'single IoU':10s} {'max |ΔIoU pred|':15s} {'max |Δlogit|':12s}")
    for case in cases:
        rgb = np.asarray(Image.open(case["image"]).convert("RGB"))
        height, width = rgb.shape[:2]
        x0, y0, x1, y1 = (float(v) for v in case["box"])
        scale = np.array([INPUT_SIZE / width, INPUT_SIZE / height], np.float32)
        prompts = {
            "box": ([[x0, y0], [x1, y1]], [2, 3]),
            "point": ([[0.5 * (x0 + x1), 0.5 * (y0 + y1)]], [1]),
            "point+neg": ([[0.5 * (x0 + x1), 0.5 * (y0 + y1)], [0.02 * width, 0.02 * height]], [1, 0]),
            "box+point": ([[x0, y0], [x1, y1], [0.5 * (x0 + x1), 0.5 * (y0 + y1)]], [2, 3, 1]),
        }
        tensors = {"official": preprocess_official(rgb), "service": preprocess(rgb)}
        print(f"  preprocessing |official - service| mean {np.abs(tensors['official'] - tensors['service']).mean():.4f}, max {np.abs(tensors['official'] - tensors['service']).max():.3f}")
        for label, (xy, lab) in prompts.items():
            coords = (np.array(xy, np.float32) * scale)[None].astype(np.float32)
            labels = np.array(lab, np.int64)[None]
            ref_multi, ref_multi_iou, ref_single, ref_single_iou = _torch_reference(model, tensors["official"], coords, labels)
            for pre, tensor in tensors.items():
                masks, ious, _ = _onnx_outputs(sessions, tensor, coords, labels)
                multi_ious = [_mask_iou(masks[1 + k], ref_multi[k]) for k in range(3)]
                single_mask, single_iou = _select_single(masks, ious, delta, thresh)
                single = _mask_iou(single_mask, ref_single[0])
                d_iou = max(float(np.abs(ious[1:4] - ref_multi_iou).max()), float(abs(single_iou - ref_single_iou[0])))
                d_logit = float(np.abs(masks[1:4] - ref_multi).max())
                good = min(multi_ious) > 0.98 and single > 0.98 and d_iou < 0.05
                ok &= good
                print(
                    f"{Path(case['image']).name:28s} {label:14s} {pre:8s} {' '.join(f'{v:.4f}' for v in multi_ious):22s} "
                    f"{single:<10.4f} {d_iou:<15.4f} {d_logit:<12.4f} {'ok' if good else 'FAIL'}"
                )
    print("verification", "passed" if ok else "FAILED")
    return ok


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="sam2.1-hiera-small", choices=sorted(CHECKPOINTS))
    parser.add_argument("--out", type=Path, default=Path("models"), help="output directory (default: models/)")
    parser.add_argument("--hf-cache", type=Path, default=Path(os.environ.get("HF_HUB_CACHE") or "/opt/ae-v14/hf-cache"))
    parser.add_argument("--once", action="store_true", help="export once (skip the determinism check)")
    parser.add_argument("--verify", type=Path, metavar="CASES_JSON", help="compare the graphs against PyTorch on photos")
    parser.add_argument("--fingerprint", type=Path, nargs="+", metavar="ONNX", help="print SHA-256 + structural fingerprint of files")
    args = parser.parse_args()

    if args.fingerprint:
        for path in args.fingerprint:
            print(f"{path.name}: sha256 {sha256_file(path)}  fingerprint {fingerprint(path)}  ({path.stat().st_size} bytes)")
        return 0

    paths = export(args.model, args.out, args.hf_cache)
    if not args.once:
        again = export(args.model, args.out, args.hf_cache, suffix=".again")
        for kind in paths:
            first, second = sha256_file(paths[kind]), sha256_file(again[kind])
            again[kind].unlink()
            if first != second:
                print(f"{paths[kind].name}: NOT byte-deterministic ({first} vs {second}) – pin the fingerprint instead", file=sys.stderr)
                return 1
        print("two exports gave identical bytes")
    manifest = write_manifest(args.model, paths, args.out)
    print(f"manifest → {manifest}")
    for kind, path in paths.items():
        print(f"{kind}: {path.name}  {path.stat().st_size} bytes  sha256 {sha256_file(path)}  fingerprint {fingerprint(path)}")
    if args.verify:
        return 0 if verify(args.model, paths, args.verify, args.hf_cache) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
