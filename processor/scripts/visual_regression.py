"""Visual regression runner for the exterior showroom pipeline (developer tool).

Runs the CURRENT pipeline on every case of ``tests/visual/regression_set.json``
the way the API job manager does (``process_photo(data, preset=load_preset(...),
segmenter=..., backgrounds=BackgroundProvider(settings), shot_key=...,
debug=DebugSink(dir))``) and writes results, debug images, metadata and a
labelled collage. Not part of pytest: it needs the cached photos and, for
uncached masks, the segmentation model.

Usage (from processor/)::

    .venv/bin/python scripts/visual_regression.py --label baseline
    .venv/bin/python scripts/visual_regression.py --label wip --cases too_far,front
    .venv/bin/python scripts/visual_regression.py --label old-photos --legacy-dir /path/to/cars
    .venv/bin/python scripts/visual_regression.py --compare baseline wip
    .venv/bin/python scripts/visual_regression.py --label wip --collage-only

Output (``--out``, default ``/opt/ae-regression/runs/<label>/``)::

    run.json                     run info + per-case summary (outcome, metrics, timings, credit)
    collage.jpg                  one tile per case: original | result, with caption
    <caseId>/result.jpg          final JPEG (accepted cases only)
    <caseId>/result.json         outcome, error (type, code, message, details), warnings, metrics, metadata
    <caseId>/original-thumb.jpg  decoded original (long edge 1024) for the collages
    <caseId>/debug/              DebugSink output (mask, geometry, cut-out, plate, shadow, final, ...)

Re-running a label with ``--cases`` replaces only those cases in run.json.

Outcomes: ``accepted``; ``rejected:<code>`` for any exception with a ``code``
attribute (QualityGateError and its successors); ``crash:<ExceptionType>`` for
everything else (traceback in result.json). A case passes when it is expected
to be accepted and was, or was rejected with exactly the expected code.

Masks: the coarse segmentation is cached as 16-bit PNG in
``/opt/ae-regression/masks/`` (key = sha256 of the decoded RGB bytes + shape +
segmenter name). On a hit the model is never loaded. A miss runs the real
segmenter in a child process under the shared lock
``/opt/ae-regression/birefnet.lock`` (one BiRefNet run at a time on the machine;
its ~7 GB are returned when the child exits). ``--no-cache`` bypasses the cache,
``--refresh-masks`` recomputes and overwrites it.

``--legacy-dir`` runs the old test photos car1.jpg … car8.jpg with fixed shot
keys instead of the manifest (expected outcome: none).

The outputs contain third-party photos (CC BY / CC BY-SA). Never commit them;
credit the authors when you share a collage (see tests/visual/README.md).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import importlib
import inspect
import io
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

PROCESSOR_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROCESSOR_ROOT.parent
sys.path.insert(0, str(PROCESSOR_ROOT))

DEFAULT_MANIFEST = PROCESSOR_ROOT / "tests" / "visual" / "regression_set.json"
DEFAULT_RUNS_ROOT = Path("/opt/ae-regression/runs")
DEFAULT_MASK_CACHE = Path("/opt/ae-regression/masks")
DEFAULT_LOCK = Path("/opt/ae-regression/birefnet.lock")
DEFAULT_PRESET = "autoexperten_standard"

#: Old test photos (low resolution) that produced the first bad collage.
LEGACY_SHOTS = {
    "car1": "front_left_45",
    "car2": "front_right_45",
    "car3": "front_right_45",
    "car4": "front_right_45",
    "car5": "front_left_45",
    "car6": "rear_right_45",
    "car7": "front_left_45",
    "car8": "front_left_45",
}

MASK_SCALE = 65535.0
THUMB_LONG_EDGE = 1024


# --------------------------------------------------------------------------- cases


@dataclass
class Case:
    id: str
    shot_key: str
    expected: str
    path: Path
    sha256: str | None = None
    credit: dict = field(default_factory=dict)


def manifest_cases(manifest_path: Path, cache_dir: Path | None) -> list[Case]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cache = cache_dir or Path(manifest.get("cacheDirDefault") or "/opt/ae-regression/images")
    cases = []
    for entry in manifest["cases"]:
        credit = {k: entry[k] for k in ("title", "author", "license", "licenseUrl", "landingUrl") if entry.get(k)}
        cases.append(
            Case(
                id=entry.get("caseId") or entry["id"],
                shot_key=entry["shotKey"],
                expected=entry.get("expected", "accept"),
                path=cache / entry["file"],
                sha256=entry.get("sha256"),
                credit=credit,
            )
        )
    return cases


def legacy_cases(directory: Path) -> list[Case]:
    return [
        Case(
            id=name,
            shot_key=shot,
            expected="none",
            path=directory / f"{name}.jpg",
            credit={"author": "legacy test photo"},
        )
        for name, shot in LEGACY_SHOTS.items()
    ]


# --------------------------------------------------------------------------- json helpers


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist() if value.size <= 64 else f"<array {value.shape} {value.dtype}>"
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def write_json(path: Path, payload) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    os.replace(tmp, path)


def json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _get(data, *path, default=None):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


# --------------------------------------------------------------------------- mask cache


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """flock on the machine-wide segmentation lock; yields the seconds spent waiting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        started = time.perf_counter()
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield time.perf_counter() - started
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def segmenter_name(settings) -> str:
    """Name of the configured segmenter without creating it (OnnxSegmenter.name = "onnx:<model>")."""
    return f"{settings.segmenter}:{settings.segmentation_model}"


def mask_key(rgb: np.ndarray, name: str) -> str:
    rgb = np.ascontiguousarray(rgb)
    digest = hashlib.sha256()
    digest.update(memoryview(rgb).cast("B"))
    digest.update(f"|{rgb.shape}|{rgb.dtype.str}|{name}".encode())
    return digest.hexdigest()


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _rebuild_error(status: dict, proc: subprocess.CompletedProcess) -> Exception:
    """Re-raise the child's exception type (e.g. SegmentationError) in the parent."""
    name, module, message = status.get("type"), status.get("module"), status.get("message", "")
    if name and module:
        try:
            cls = getattr(importlib.import_module(module), name, None)
        except ImportError:
            cls = None
        if isinstance(cls, type) and issubclass(cls, Exception):
            with contextlib.suppress(TypeError):  # needs other constructor arguments
                return cls(message)
    tail = (proc.stderr or "").strip().splitlines()[-5:]
    return RuntimeError(
        f"segmentation worker failed (exit {proc.returncode}): {name or ''} {message} {' | '.join(tail)}"
    )


def run_segmentation_child(rgb: np.ndarray, lock_path: Path) -> tuple[np.ndarray, dict]:
    """Segment in a child process (model memory is freed on exit) under the shared lock."""
    with tempfile.TemporaryDirectory(prefix="ae-seg-") as tmp:
        src, dst = Path(tmp) / "rgb.npy", Path(tmp) / "alpha.npy"
        np.save(src, np.ascontiguousarray(rgb))
        cmd = [sys.executable, str(Path(__file__).resolve()), "--segment-worker", str(src), str(dst)]
        with exclusive_lock(lock_path) as waited:
            started = time.perf_counter()
            proc = subprocess.run(cmd, cwd=PROCESSOR_ROOT, capture_output=True, text=True, check=False)
            seconds = time.perf_counter() - started
        status: dict = {}
        for line in reversed((proc.stdout or "").strip().splitlines()):
            with contextlib.suppress(json.JSONDecodeError):
                status = json.loads(line)
                break
        if proc.returncode != 0 or not status.get("ok"):
            raise _rebuild_error(status, proc)
        alpha = np.load(dst)
    info = {
        "seconds": round(seconds, 1),
        "lockWaitSeconds": round(waited, 1),
        "modelSeconds": status.get("seconds"),
        "childMaxRssMb": status.get("maxRssMb"),
    }
    return alpha, info


def segment_worker(src: str, dst: str) -> int:
    """Child process: run the configured segmenter on one RGB array."""
    import resource

    from app.config import Settings
    from app.pipeline.segmentation import create_segmenter

    rgb = np.load(src)
    started = time.perf_counter()
    try:
        alpha = create_segmenter(Settings.from_env()).segment(rgb)
    except Exception as error:  # noqa: BLE001 - reported to the parent, which re-raises it
        print(
            json.dumps(
                {"ok": False, "type": type(error).__name__, "module": type(error).__module__, "message": str(error)}
            )
        )
        return 3
    np.save(dst, np.asarray(alpha, dtype=np.float32))
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps({"ok": True, "seconds": round(time.perf_counter() - started, 1), "maxRssMb": rss_mb}))
    return 0


class CachedSegmenter:
    """VehicleSegmenter with a disk cache of the coarse mask (float → 16-bit PNG).

    Misses run the real segmenter in a child process under the shared lock and
    return the quantised mask, so a first run and every cached re-run see
    exactly the same values. Other attributes the pipeline may read (e.g.
    `spec`) come from a lazily created real segmenter (creating it does not
    load the model); unknown methods run in-process under the lock, uncached.
    """

    def __init__(self, settings, cache_dir: Path, lock_path: Path, *, read: bool = True, write: bool = True):
        self.name = segmenter_name(settings)
        self._settings = settings
        self._cache_dir = cache_dir
        self._lock_path = lock_path
        self._read = read
        self._write = write
        self._real = None
        self._seg_source = _file_digest(PROCESSOR_ROOT / "app" / "pipeline" / "segmentation.py")
        self.calls: list[dict] = []

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        key = mask_key(rgb, self.name)
        path = self._cache_dir / f"{key}.png"
        info: dict = {"key": key, "cache": "miss" if (self._read or self._write) else "off"}
        if self._read and path.is_file():
            mask = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if mask is not None and mask.dtype == np.uint16 and mask.shape == rgb.shape[:2]:
                info.update(cache="hit", seconds=0.0)
                sidecar = _get_sidecar(path)
                if sidecar.get("segmentationPySha256") not in (None, self._seg_source):
                    info["staleWarning"] = "mask cached with another app/pipeline/segmentation.py (use --refresh-masks)"
                self.calls.append(info)
                return mask.astype(np.float32) / MASK_SCALE
            info["cache"] = "corrupt"
        alpha, child = run_segmentation_child(rgb, self._lock_path)
        info.update(child)
        if self._write:
            quantised = np.round(np.clip(alpha, 0.0, 1.0) * MASK_SCALE).astype(np.uint16)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.stem + ".tmp.png")
            if not cv2.imwrite(str(tmp), quantised):
                raise OSError(f"cannot write mask cache {tmp}")
            os.replace(tmp, path)
            write_json(
                path.with_suffix(".json"),
                {
                    "segmenter": self.name,
                    "shape": list(rgb.shape),
                    "createdAt": _now_iso(),
                    "modelSeconds": child.get("modelSeconds"),
                    "segmentationPySha256": self._seg_source,
                },
            )
            alpha = quantised.astype(np.float32) / MASK_SCALE
        self.calls.append(info)
        return np.asarray(alpha, dtype=np.float32)

    def __getattr__(self, attr: str):
        if attr.startswith("_"):
            raise AttributeError(attr)
        if self._real is None:
            from app.pipeline.segmentation import create_segmenter

            self._real = create_segmenter(self._settings)
        value = getattr(self._real, attr)
        if not callable(value):
            return value

        def locked(*args, **kwargs):
            print(f"    note: pipeline called segmenter.{attr}() – not cached, runs in-process under the lock")
            with exclusive_lock(self._lock_path):
                return value(*args, **kwargs)

        return locked


def _get_sidecar(png: Path) -> dict:
    try:
        return json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- run info


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _tree_digest(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        with contextlib.suppress(OSError):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def code_digest() -> str:
    app_dir = PROCESSOR_ROOT / "app"
    return _tree_digest([p for p in app_dir.rglob("*.py") if "__pycache__" not in p.parts], app_dir)


def assets_digest(settings) -> str:
    files = []
    for directory in (settings.presets_dir, settings.brand_dir):
        if directory.is_dir():
            files += [p for p in directory.rglob("*") if p.is_file()]
    return _tree_digest(files, settings.assets_dir)


def git_info() -> dict:
    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=20
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    dirty = [line[3:] for line in git("status", "--porcelain", "--", "processor/app", "public/presets").splitlines()]
    return {"commit": git("rev-parse", "--short", "HEAD") or None, "dirtyFiles": dirty}


def apply_overrides(preset, overrides: list[str]):
    """`--preset-override quality.min_source_long_edge=1000` (JSON values; dataclass fields only)."""

    def replace(obj, parts: list[str], value):
        name = parts[0]
        if not dataclasses.is_dataclass(obj) or name not in {f.name for f in dataclasses.fields(obj)}:
            raise SystemExit(f"unknown preset field {name!r} in --preset-override")
        if len(parts) > 1:
            return dataclasses.replace(obj, **{name: replace(getattr(obj, name), parts[1:], value)})
        current = getattr(obj, name)
        if isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)
        elif isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        return dataclasses.replace(obj, **{name: value})

    for item in overrides:
        dotted, sep, raw = item.partition("=")
        if not sep:
            raise SystemExit(f"--preset-override needs FIELD=VALUE, got {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        preset = replace(preset, dotted.strip().split("."), value)
    return preset


# --------------------------------------------------------------------------- running


def pipeline_kwargs(process_photo, wanted: dict) -> tuple[dict, list[str]]:
    """Pass only the keyword arguments the current process_photo accepts (survives refactors)."""
    params = inspect.signature(process_photo).parameters
    var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs = {k: v for k, v in wanted.items() if var_kw or k in params}
    return kwargs, sorted(set(wanted) - set(kwargs))


def describe_error(error: BaseException) -> dict:
    info: dict = {"type": type(error).__name__, "module": type(error).__module__, "str": str(error)}
    for attr in ("code", "user_message", "message"):
        value = getattr(error, attr, None)
        if value is not None:
            info[attr] = value if isinstance(value, (str, int, float, bool)) else str(value)
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        info["details"] = json_safe(details)
    return info


def verdict(expected: str, status: str, code: str | None) -> bool | None:
    if expected in ("", "none", None):
        return None
    if expected == "accept":
        return status == "accepted"
    return status == "rejected" and code == expected


def extract_metrics(metadata: dict, details: dict, source_size: tuple[int, int] | None, warnings: list[dict]) -> dict:
    """Key numbers for captions and comparisons – every field optional (the metadata evolves)."""
    meta, det = metadata or {}, details or {}
    placement = meta.get("placement") or det.get("placement") or {}
    geometry = meta.get("geometry") or det.get("geometry") or {}
    checks = _get(meta, "qualityGate", "checks") or det.get("checks") or {}
    bbox = _get(meta, "mask", "bbox") or geometry.get("bbox")
    out: dict = {}
    if isinstance(bbox, list) and len(bbox) == 4 and source_size:
        out["bbox"] = bbox
        out["carWidthRatio"] = round((bbox[2] - bbox[0]) / max(source_size[0], 1), 4)
        out["carHeightRatio"] = round((bbox[3] - bbox[1]) / max(source_size[1], 1), 4)
    for key, name in (
        ("scale", "placementScale"),
        ("targetScale", "targetScale"),
        ("limitedBy", "limitedBy"),
        ("achievedWidthRatio", "outputWidthRatio"),
        ("targetWidthRatio", "targetWidthRatio"),
        ("targetFraction", "targetFraction"),
    ):
        if key in placement:
            out[name] = placement[key]
    out["plateUsed"] = meta.get("plateUsed") or det.get("plateUsed")
    out["plateSwitched"] = _get(meta, "plateSelection", "switched") or _get(det, "plateSelection", "switched")
    contacts = geometry.get("contacts")
    if isinstance(contacts, list):
        out["contacts"] = [c[:2] for c in contacts if isinstance(c, list) and len(c) >= 2]
    out["nearEnd"] = geometry.get("nearEnd")
    perspective = checks.get("perspective") if isinstance(checks, dict) else None
    if isinstance(perspective, dict):
        out["aspectFactor"] = perspective.get("aspectFactor")
        out["riseFactor"] = perspective.get("riseFactor")
    if isinstance(checks, dict):
        failed = {}
        for name, check in checks.items():
            if isinstance(check, dict) and not check.get("passed", True):
                failed[name] = check.get("reasons") or check.get("sides") or check.get("code")
        if failed:
            out["failedChecks"] = failed
    if warnings:
        out["warnings"] = [w.get("code") for w in warnings if isinstance(w, dict)]
    output = meta.get("output")
    if isinstance(output, dict) and "width" in output:
        out["output"] = [output.get("width"), output.get("height")]
    return {k: v for k, v in out.items() if v is not None}


def save_thumbnail(data: bytes, target: Path) -> tuple[int, int] | None:
    """Decoded original (as the pipeline sees it) → JPEG thumbnail; returns the decoded size."""
    try:
        try:
            from app.pipeline.decode import decode_image

            rgb = decode_image(data).rgb
        except ImportError:
            rgb = np.asarray(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))
    except Exception:  # noqa: BLE001 - the pipeline reports the decode error itself
        return None
    height, width = rgb.shape[:2]
    scale = min(1.0, THUMB_LONG_EDGE / max(height, width))
    small = cv2.resize(rgb, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    Image.fromarray(small).save(target, quality=90)
    return width, height


class LogCollector(logging.Handler):
    """Collects WARNING+ log records of one case (e.g. "plate upscaled") instead of printing them."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append({"logger": record.name, "level": record.levelname, "message": record.getMessage()})


@dataclass
class RunContext:
    out: Path
    settings: object
    preset: object
    backgrounds: object
    segmenter: CachedSegmenter
    process_photo: object
    code_digest: str


def run_case(case: Case, ctx: RunContext) -> dict:
    from app.pipeline.debug import DebugSink

    case_dir = ctx.out / case.id
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)
    record: dict = {
        "caseId": case.id,
        "shotKey": case.shot_key,
        "expected": case.expected,
        "input": str(case.path),
        "credit": case.credit,
        "runAt": _now_iso(),
        "codeDigest": ctx.code_digest,
    }
    if not case.path.is_file():
        record.update(outcome="missing", status="missing", passed=False if case.expected != "none" else None)
        write_json(case_dir / "result.json", record)
        return record
    data = case.path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    record["inputSha256"] = digest
    if case.sha256 and digest != case.sha256:
        record["inputWarning"] = "sha256 differs from the manifest – run scripts/fetch_regression_set.py"
    source_size = save_thumbnail(data, case_dir / "original-thumb.jpg")
    record["sourceSize"] = list(source_size) if source_size else None

    debug = DebugSink(case_dir / "debug")
    ctx.segmenter.calls.clear()
    wanted = {
        "preset": ctx.preset,
        "segmenter": ctx.segmenter,
        "backgrounds": ctx.backgrounds,
        "shot_key": case.shot_key,
        "debug": debug,
        "require_master_showroom": True,
    }
    kwargs, _ = pipeline_kwargs(ctx.process_photo, wanted)
    metadata: dict = {}
    details: dict = {}
    warnings: list[dict] = []
    code = None
    collector = LogCollector()
    logging.getLogger().addHandler(collector)
    started = time.perf_counter()
    try:
        result = ctx.process_photo(data, **kwargs)
    except Exception as error:  # noqa: BLE001 - every failure is a recorded outcome
        info = describe_error(error)
        code = info.get("code")
        if code is not None:
            code = str(code)
            status, outcome = "rejected", f"rejected:{code}"
        else:
            status, outcome = "crash", f"crash:{info['type']}"
            info["traceback"] = traceback.format_exc()
        details = info.get("details") or {}
        record["error"] = info
    else:
        (case_dir / "result.jpg").write_bytes(result.jpeg)
        status, outcome = "accepted", "accepted"
        metadata = json_safe(getattr(result, "metadata", {}) or {})
        warnings = [json_safe(getattr(w, "__dict__", {"message": str(w)})) for w in getattr(result, "warnings", [])]
    seconds = time.perf_counter() - started
    logging.getLogger().removeHandler(collector)
    debug_dir = case_dir / "debug"
    record.update(
        outcome=outcome,
        status=status,
        code=code,
        passed=verdict(case.expected, status, code),
        seconds=round(seconds, 2),
        # without segmentation (model run + lock wait) – the pipeline's own cost
        pipelineSeconds=round(
            seconds - sum((c.get("seconds") or 0.0) + (c.get("lockWaitSeconds") or 0.0) for c in ctx.segmenter.calls), 2
        ),
        segmentation=list(ctx.segmenter.calls),
        metrics=extract_metrics(metadata, details, source_size, warnings),
        warnings=warnings,
        logWarnings=collector.records,
        debugFiles=sorted(p.name for p in debug_dir.iterdir()) if debug_dir.is_dir() else [],
        metadata=metadata,
    )
    write_json(case_dir / "result.json", record)
    return record


def summary_entry(record: dict) -> dict:
    entry = {k: v for k, v in record.items() if k not in ("metadata", "warnings")}
    if "error" in entry:
        entry["error"] = {k: v for k, v in entry["error"].items() if k not in ("details", "traceback")}
    return entry


def run_cases(args, cases: list[Case], out: Path, label: str, set_name: str) -> int:
    from app.config import Settings
    from app.pipeline import pipeline as pipeline_module
    from app.presets import BackgroundProvider, load_preset

    settings = Settings.from_env()
    preset = apply_overrides(load_preset(settings, args.preset), args.preset_override)
    backgrounds = BackgroundProvider(settings)
    segmenter = CachedSegmenter(
        settings,
        args.mask_cache,
        args.lock_file,
        read=not (args.no_cache or args.refresh_masks),
        write=not args.no_cache,
    )
    process_photo = pipeline_module.process_photo
    _, dropped = pipeline_kwargs(process_photo, {"require_master_showroom": True})
    digest_before = assets_digest(settings)
    ctx = RunContext(out, settings, preset, backgrounds, segmenter, process_photo, code_digest())

    out.mkdir(parents=True, exist_ok=True)
    run_path = out / "run.json"
    previous = {}
    if run_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, KeyError):
            old = json.loads(run_path.read_text(encoding="utf-8"))
            if old.get("set") == set_name:
                previous = {c["caseId"]: c for c in old.get("cases", [])}
    order = [
        c.id for c in (legacy_cases(Path()) if set_name == "legacy" else manifest_cases(args.manifest, args.cache_dir))
    ]
    run_info = {
        "label": label,
        "set": set_name,
        "createdAt": _now_iso(),
        "pipelineVersion": getattr(pipeline_module, "PIPELINE_VERSION", None),
        "preset": args.preset,
        "presetOverrides": list(args.preset_override),
        # the preset as loaded once at the start (the JSON may change while the run is going on)
        "presetEffective": json_safe(dataclasses.asdict(preset)) if dataclasses.is_dataclass(preset) else None,
        "segmenter": segmenter.name,
        "maskCache": "off" if args.no_cache else str(args.mask_cache),
        "processPhotoKwargsDropped": dropped,
        "git": git_info(),
        "codeDigest": ctx.code_digest,
        "assetsDigest": digest_before,
    }
    version = run_info["pipelineVersion"]
    print(f"run '{label}' → {out}  ({len(cases)} cases, pipeline {version}, code {ctx.code_digest[:10]})")
    if dropped:
        print(f"  process_photo does not accept {', '.join(dropped)} – not passed")

    started = time.perf_counter()
    records = dict(previous)

    def save() -> None:
        ordered = sorted(
            records.values(), key=lambda c: order.index(c["caseId"]) if c["caseId"] in order else len(order)
        )
        write_json(run_path, {**run_info, "wallSeconds": round(time.perf_counter() - started, 1), "cases": ordered})

    for index, case in enumerate(cases, 1):
        print(
            f"[{index:2d}/{len(cases)}] {case.id:<16} {case.shot_key:<15} expected {case.expected:<22}",
            end=" ",
            flush=True,
        )
        record = run_case(case, ctx)
        records[case.id] = summary_entry(record)
        seg = record.get("segmentation") or []
        seg_note = ",".join(
            s.get("cache", "?") + (f" {s.get('seconds')}s" if s.get("cache") != "hit" else "") for s in seg
        )
        mark = {True: "PASS", False: "FAIL", None: "    "}[record.get("passed")]
        print(f"{mark} {record['outcome']:<36} {record.get('seconds', 0):6.1f}s  mask: {seg_note or '-'}", flush=True)
        for entry in record.get("logWarnings") or []:
            print(f"    log {entry['level'].lower()}: {entry['message']}")
        for s in seg:
            if s.get("staleWarning"):
                print(f"    warning: {s['staleWarning']}")
        save()

    run_info["assetsChangedDuringRun"] = assets_digest(settings) != digest_before
    if run_info["assetsChangedDuringRun"]:
        print("  warning: presets/plates changed while the run was going on – results may mix plate versions")
    save()
    collage = build_run_collage(out, args.max_side)
    total = time.perf_counter() - started
    misses = [s for r in records.values() for s in r.get("segmentation") or [] if s.get("cache") != "hit"]
    graded = [r for r in records.values() if r.get("passed") is not None]
    print(
        f"done in {total:.0f} s – pass {sum(1 for r in graded if r['passed'])}/{len(graded)}, "
        f"model runs {len(misses)} ({sum(s.get('seconds') or 0 for s in misses):.0f} s incl. lock waits)"
    )
    print(f"collage: {collage}")
    return 0


# --------------------------------------------------------------------------- collage

BG = (18, 19, 21)
PANEL_BG = (34, 35, 38)
TEXT = (232, 232, 232)
DIM = (160, 162, 168)
GREEN = (60, 190, 100)
RED = (235, 70, 60)
AMBER = (240, 170, 40)
MAGENTA = (220, 80, 220)
BLUE = (10, 123, 255)

FONT_DIRS = [PROCESSOR_ROOT / "app" / "showroom" / "fonts", Path("/usr/share/fonts/truetype/dejavu")]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["Inter-SemiBold.otf", "DejaVuSans-Bold.ttf"] if bold else ["Inter-Medium.otf", "DejaVuSans.ttf"]
    for directory in FONT_DIRS:
        for name in names:
            with contextlib.suppress(OSError):
                return ImageFont.truetype(str(directory / name), size)
    return ImageFont.load_default(size=size)


def open_image(path: Path, max_side: int) -> Image.Image | None:
    if not path.is_file():
        return None
    try:
        image = Image.open(path)
        if image.format == "JPEG":
            image.draft("RGB", (max_side, max_side))
        return image.convert("RGB")
    except OSError:
        return None


def fit(image: Image.Image | None, width: int, height: int) -> tuple[Image.Image, float, tuple[int, int]]:
    """Letterbox `image` into width × height; returns panel, scale and paste offset."""
    panel = Image.new("RGB", (width, height), PANEL_BG)
    if image is None:
        return panel, 0.0, (0, 0)
    scale = min(width / image.width, height / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    offset = ((width - size[0]) // 2, (height - size[1]) // 2)
    panel.paste(image.resize(size, Image.Resampling.LANCZOS), offset)
    return panel, scale, offset


def truncate(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: float, max_lines: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = truncate(draw, lines[-1] + " …", font, width)
    return lines


def tag(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, colour, font) -> None:
    x, y = xy
    box = draw.textbbox((x, y), text, font=font)
    pad = max(3, font.size // 4) if hasattr(font, "size") else 4
    draw.rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill=colour)
    draw.text((x, y), text, fill=(255, 255, 255), font=font)


def original_panel(case_dir: Path, record: dict, width: int, height: int, font) -> Image.Image:
    thumb = open_image(case_dir / "original-thumb.jpg", max(width, height))
    panel, scale, (ox, oy) = fit(thumb, width, height)
    draw = ImageDraw.Draw(panel)
    metrics = record.get("metrics") or {}
    source = record.get("sourceSize")
    if thumb is not None and source:
        factor = scale * thumb.width / max(source[0], 1)
        bbox = metrics.get("bbox")
        line = max(2, width // 300)
        if bbox:
            x0, y0, x1, y1 = (v * factor for v in bbox)
            draw.rectangle([ox + x0, oy + y0, ox + x1, oy + y1], outline=(255, 220, 0), width=line)
        for x, y in metrics.get("contacts") or []:
            r = 3 * line
            cx, cy = ox + x * factor, oy + y * factor
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GREEN, width=line)
    if thumb is None:
        draw.text((10, 10), "no input", fill=DIM, font=font)
    tag(draw, (8 + font.size // 3, 8 + font.size // 3), "original", (60, 60, 66), font)
    return panel


def result_panel(case_dir: Path, record: dict, width: int, height: int, font, small, prefix: str = "") -> Image.Image:
    status = record.get("status")
    pad = 8 + font.size // 3
    if status == "accepted":
        panel, _, _ = fit(open_image(case_dir / "result.jpg", max(width, height)), width, height)
        tag(ImageDraw.Draw(panel), (pad, pad), f"{prefix}accepted", GREEN, font)
        return panel
    source = open_image(case_dir / "debug" / "geometry.jpg", max(width, height))
    if source is None:
        source = open_image(case_dir / "original-thumb.jpg", max(width, height))
    if source is not None:
        source = Image.eval(source, lambda v: int(v * 0.45))
    panel, _, _ = fit(source, width, height)
    draw = ImageDraw.Draw(panel)
    error = record.get("error") or {}
    if status == "rejected":
        tag(draw, (pad, pad), f"{prefix}REJECTED {record.get('code')}", RED, font)
        message = error.get("user_message") or error.get("message") or ""
    elif status == "missing":
        tag(draw, (pad, pad), f"{prefix}MISSING INPUT", AMBER, font)
        message = record.get("input", "")
    elif status == "absent":
        tag(draw, (pad, pad), f"{prefix}not in this run", (90, 90, 96), font)
        message = ""
    else:
        tag(draw, (pad, pad), f"{prefix}CRASH {error.get('type', '?')}", MAGENTA, font)
        message = error.get("str", "")
    y = pad + 2 * font.size
    for line in wrap(draw, message, small, width - 2 * pad, 4):
        draw.text((pad, y), line, fill=TEXT, font=small)
        y += int(small.size * 1.3)
    return panel


def _pct(value) -> str:
    return f"{value * 100:.0f}%" if isinstance(value, (int, float)) else "–"


def _num(value, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "–"


def metrics_lines(record: dict) -> list[str]:
    m = record.get("metrics") or {}
    lines = []
    scale = f"scale {_num(m.get('placementScale'))}"
    if m.get("limitedBy"):
        scale += f" ({m['limitedBy']})"
    size = f"car {_pct(m.get('carWidthRatio'))} of photo width"
    if "outputWidthRatio" in m:
        size += f" → {_pct(m['outputWidthRatio'])} of output (target {_pct(m.get('targetWidthRatio'))})"
    lines.append(f"{scale}  ·  {size}")
    plate = f"plate {m.get('plateUsed', '–')}" + (" (mirrored)" if m.get("plateSwitched") else "")
    seg = record.get("segmentation") or []
    mask = ",".join(s.get("cache", "?") for s in seg) or "-"
    lines.append(
        f"{plate}  ·  contacts {len(m.get('contacts') or [])}  ·  aspect× {_num(m.get('aspectFactor'))}"
        f"  rise× {_num(m.get('riseFactor'))}  ·  {_num(record.get('pipelineSeconds', record.get('seconds')), 0)} s"
        f" + mask {mask}"
    )
    notes = []
    for name, reasons in (m.get("failedChecks") or {}).items():
        reason = ", ".join(reasons) if isinstance(reasons, list) else str(reasons)
        notes.append(f"{name}({reason})")
    parts = []
    if notes:
        parts.append("gate failed: " + "; ".join(notes))
    if m.get("warnings"):
        parts.append("warnings: " + ", ".join(str(w) for w in m["warnings"]))
    if record.get("status") == "crash":
        parts.append("crash: " + (record.get("error") or {}).get("str", ""))
    logs = record.get("logWarnings") or []
    if logs:
        parts.append("log: " + "; ".join(entry.get("message", "") for entry in logs))
    lines.append("  ·  ".join(parts) or "no warnings")
    return lines


def credit_line(record: dict) -> str:
    credit = record.get("credit") or {}
    parts = [credit.get("author"), credit.get("license")]
    return "Photo: " + " · ".join(p for p in parts if p) if any(parts) else ""


def verdict_badge(passed) -> tuple[str, tuple]:
    return {True: ("PASS", GREEN), False: ("FAIL", RED), None: ("n/a", (90, 90, 96))}[passed]


@dataclass
class Layout:
    cols: int
    rows: int
    tile_w: int
    panel_w: int
    panel_h: int
    caption_h: int
    tile_h: int
    gap: int
    header_h: int
    footer_h: int
    font: object
    small: object
    bold: object
    big: object

    @property
    def size(self) -> tuple[int, int]:
        return (
            self.cols * self.tile_w + (self.cols + 1) * self.gap,
            self.header_h + self.rows * (self.tile_h + self.gap) + self.gap + self.footer_h,
        )


def make_layout(count: int, max_side: int, caption_lines: int, target_aspect: float = 1.5) -> Layout:
    # proportions in units of the tile width
    gap, font, line = 0.012, 0.0185, 0.0185 * 1.32
    panel_w = (1 - 3 * gap) / 2
    panel_h = panel_w * 3 / 4
    caption = caption_lines * line + 1.5 * gap
    tile_h = gap + panel_h + caption
    header, footer = 0.075, 0.03

    def dims(cols: int) -> tuple[float, float]:
        rows = math.ceil(count / cols)
        return cols + (cols + 1) * gap, (header + footer) * 1.6 + rows * (tile_h + gap) + gap

    cols = min(range(1, max(count, 1) + 1), key=lambda c: abs(math.log(dims(c)[0] / dims(c)[1] / target_aspect)))
    width, height = dims(cols)
    unit = max_side / max(width, height)

    def px(value: float) -> int:
        return max(1, round(value * unit))

    font_px = max(11, px(font))
    return Layout(
        cols=cols,
        rows=math.ceil(count / cols),
        tile_w=px(1),
        panel_w=px(panel_w),
        panel_h=px(panel_h),
        caption_h=px(caption),
        tile_h=px(tile_h),
        gap=px(gap),
        header_h=px(header * 1.6),
        footer_h=max(px(footer * 1.6), int(font_px * 2.2)),
        font=load_font(font_px),
        small=load_font(max(10, int(font_px * 0.9))),
        bold=load_font(int(font_px * 1.15), bold=True),
        big=load_font(int(font_px * 1.6), bold=True),
    )


def render_grid(
    tiles: list[tuple[Image.Image, Image.Image, list[tuple[str, tuple]], tuple[str, tuple]]],
    layout: Layout,
    header: list[str],
    footer: str,
) -> Image.Image:
    canvas = Image.new("RGB", layout.size, BG)
    draw = ImageDraw.Draw(canvas)
    y = layout.gap
    draw.text((layout.gap, y), header[0], fill=TEXT, font=layout.big)
    y += int(layout.big.size * 1.35)
    for line in header[1:]:
        draw.text(
            (layout.gap, y),
            truncate(draw, line, layout.font, layout.size[0] - 2 * layout.gap),
            fill=DIM,
            font=layout.font,
        )
        y += int(layout.font.size * 1.3)
    for index, (left, right, lines, badge) in enumerate(tiles):
        row, col = divmod(index, layout.cols)
        x0 = layout.gap + col * (layout.tile_w + layout.gap)
        y0 = layout.header_h + row * (layout.tile_h + layout.gap)
        draw.rectangle([x0, y0, x0 + layout.tile_w, y0 + layout.tile_h], fill=(26, 27, 30), outline=badge[1], width=2)
        inner = layout.gap
        canvas.paste(left, (x0 + inner, y0 + inner))
        canvas.paste(right, (x0 + 2 * inner + layout.panel_w, y0 + inner))
        ty = y0 + inner + layout.panel_h + inner // 2
        text_w = layout.tile_w - 2 * inner
        for number, (text, colour) in enumerate(lines):
            font = layout.bold if number == 0 else (layout.small if number == len(lines) - 1 else layout.font)
            if number == 0:
                badge_w = draw.textlength(badge[0], font=layout.bold) + layout.bold.size
                tag(
                    draw,
                    (int(x0 + layout.tile_w - inner - badge_w + layout.bold.size // 2), ty),
                    badge[0],
                    badge[1],
                    layout.bold,
                )
                draw.text(
                    (x0 + inner, ty), truncate(draw, text, font, text_w - badge_w - inner), fill=colour, font=font
                )
            else:
                draw.text((x0 + inner, ty), truncate(draw, text, font, text_w), fill=colour, font=font)
            ty += int(font.size * 1.32)
    draw.text((layout.gap, layout.size[1] - layout.footer_h + layout.gap // 2), footer, fill=DIM, font=layout.small)
    return canvas


FOOTER = (
    "Photos: Wikimedia Commons, credited per tile (CC0 / CC BY / CC BY-SA). Local developer output – never commit; "
    "when sharing, credit the authors and share CC BY-SA material under CC BY-SA."
)
LEGACY_FOOTER = "Legacy test photos (source and licence not recorded). Local developer output – never commit or share."


def footer_for(*runs: dict) -> str:
    return LEGACY_FOOTER if any(run.get("set") == "legacy" for run in runs) else FOOTER


def _load_run(run: Path) -> dict:
    path = run / "run.json"
    if not path.is_file():
        raise SystemExit(f"no run.json in {run}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_run_collage(out: Path, max_side: int) -> Path:
    run = _load_run(out)
    cases = run.get("cases", [])
    layout = make_layout(len(cases), max_side, caption_lines=6)
    tiles = []
    for record in cases:
        case_dir = out / record["caseId"]
        left = original_panel(case_dir, record, layout.panel_w, layout.panel_h, layout.font)
        right = result_panel(case_dir, record, layout.panel_w, layout.panel_h, layout.font, layout.small)
        outcome_colour = {"accepted": GREEN, "rejected": RED}.get(record.get("status"), MAGENTA)
        lines = [
            (f"{record['caseId']}  ·  {record['shotKey']}", TEXT),
            (f"expected {record.get('expected')}  →  actual {record.get('outcome')}", outcome_colour),
            *[(text, TEXT) for text in metrics_lines(record)],
            (credit_line(record), DIM),
        ]
        tiles.append((left, right, lines, verdict_badge(record.get("passed"))))
    graded = [c for c in cases if c.get("passed") is not None]
    digests = {c.get("codeDigest") for c in cases if c.get("codeDigest")}
    git = run.get("git") or {}
    header = [
        f"AutoExperten visual regression – {run.get('label')} ({run.get('set')})",
        f"{run.get('createdAt')}  ·  pipeline {run.get('pipelineVersion')}  ·  git {git.get('commit')}"
        f"{' + ' + str(len(git.get('dirtyFiles') or [])) + ' dirty files' if git.get('dirtyFiles') else ''}"
        f"  ·  code {str(run.get('codeDigest'))[:10]}{' (MIXED code versions)' if len(digests) > 1 else ''}"
        f"  ·  assets {str(run.get('assetsDigest'))[:10]}"
        + (f"  ·  pass {sum(1 for c in graded if c['passed'])}/{len(graded)}" if graded else ""),
        (
            f"preset {run.get('preset')}"
            f"{' overrides ' + ', '.join(run['presetOverrides']) if run.get('presetOverrides') else ''}"
            f"  ·  segmenter {run.get('segmenter')}  ·  left: decoded original (yellow = mask bbox, green = tyre"
            " contacts)  ·  right: result / rejection (geometry debug view)"
        ),
    ]
    image = render_grid(tiles, layout, header, footer_for(run))
    target = out / "collage.jpg"
    image.save(target, quality=88, optimize=True)
    return target


def resolve_run(value: str, runs_root: Path) -> Path:
    path = Path(value)
    return path if (path / "run.json").is_file() else runs_root / value


def compare_runs(run_a: Path, run_b: Path, out: Path | None, max_side: int) -> Path:
    a, b = _load_run(run_a), _load_run(run_b)
    cases_a = {c["caseId"]: c for c in a.get("cases", [])}
    cases_b = {c["caseId"]: c for c in b.get("cases", [])}
    ids = list(cases_a) + [i for i in cases_b if i not in cases_a]
    label_a, label_b = a.get("label", run_a.name), b.get("label", run_b.name)
    layout = make_layout(len(ids), max_side, caption_lines=6)
    tiles, changes = [], []
    missing = {"status": "absent", "outcome": "not in run"}
    for case_id in ids:
        ra, rb = cases_a.get(case_id, missing), cases_b.get(case_id, missing)
        left = result_panel(run_a / case_id, ra, layout.panel_w, layout.panel_h, layout.font, layout.small, "A ")
        right = result_panel(run_b / case_id, rb, layout.panel_w, layout.panel_h, layout.font, layout.small, "B ")
        changed = ra.get("outcome") != rb.get("outcome")
        base = ra if ra is not missing else rb

        def side(name: str, record: dict) -> str:
            m = record.get("metrics") or {}
            mark = {True: "PASS", False: "FAIL", None: "–"}[record.get("passed")]
            return (
                f"{name}: {record.get('outcome')} [{mark}]  scale {_num(m.get('placementScale'))}"
                f"  car→out {_pct(m.get('outputWidthRatio'))}  plate {m.get('plateUsed', '–')}"
            )

        notes_b = metrics_lines(rb)[-1] if rb is not missing else ""
        lines = [
            (f"{case_id}  ·  {base.get('shotKey')}  ·  expected {base.get('expected')}", TEXT),
            (side(f"A {label_a}", ra), DIM),
            (side(f"B {label_b}", rb), AMBER if changed else TEXT),
            (f"B {notes_b}" if notes_b else "", TEXT),
            ("outcome changed" if changed else "same outcome", AMBER if changed else DIM),
            (credit_line(base), DIM),
        ]
        badge = verdict_badge(rb.get("passed")) if rb is not missing else ("n/a", (90, 90, 96))
        tiles.append((left, right, lines, badge))
        changes.append(
            {
                "caseId": case_id,
                "a": ra.get("outcome"),
                "b": rb.get("outcome"),
                "passedA": ra.get("passed"),
                "passedB": rb.get("passed"),
                "changed": changed,
            }
        )

    def passes(run: dict) -> str:
        graded = [c for c in run.get("cases", []) if c.get("passed") is not None]
        return f"{sum(1 for c in graded if c['passed'])}/{len(graded)}"

    header = [
        f"AutoExperten visual regression – compare A '{label_a}' | B '{label_b}'",
        (
            f"A: {a.get('createdAt')}  code {str(a.get('codeDigest'))[:10]}  pass {passes(a)}    "
            f"B: {b.get('createdAt')}  code {str(b.get('codeDigest'))[:10]}  pass {passes(b)}    "
            f"changed outcomes: {sum(1 for c in changes if c['changed'])}/{len(changes)}"
        ),
        "left: A result  ·  right: B result  ·  badge = B verdict",
    ]
    image = render_grid(tiles, layout, header, footer_for(a, b))
    target_dir = out or run_b.parent / f"compare-{label_a}-vs-{label_b}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "collage.jpg"
    image.save(target, quality=88, optimize=True)
    write_json(
        target_dir / "compare.json", {"a": str(run_a), "b": str(run_b), "createdAt": _now_iso(), "cases": changes}
    )
    return target


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", help="run label (default: UTC timestamp; '-legacy' appended in legacy mode)")
    parser.add_argument("--out", type=Path, help="output directory (default: <runs-root>/<label>)")
    parser.add_argument("--cases", default="", help="comma separated case ids (default: all)")
    parser.add_argument("--legacy-dir", type=Path, help="run the old car1..car8.jpg photos from this directory instead")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="collage of two runs (labels or dirs)")
    parser.add_argument("--collage-only", action="store_true", help="only rebuild the collage of an existing run")
    parser.add_argument("--max-side", type=int, default=4800, help="long side of the collage in px (default 4800)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, help="image cache (default: manifest cacheDirDefault)")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--mask-cache", type=Path, default=DEFAULT_MASK_CACHE)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK, help="shared segmentation lock (flock)")
    parser.add_argument("--no-cache", action="store_true", help="bypass the mask cache (neither read nor write)")
    parser.add_argument("--refresh-masks", action="store_true", help="recompute every mask and overwrite the cache")
    parser.add_argument("--preset", default=DEFAULT_PRESET)
    parser.add_argument(
        "--preset-override",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="experiment: override a preset field, e.g. quality.min_source_long_edge=1000 (repeatable, recorded)",
    )
    parser.add_argument("--segment-worker", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.segment_worker:
        return segment_worker(*args.segment_worker)
    if args.compare:
        target = compare_runs(
            resolve_run(args.compare[0], args.runs_root),
            resolve_run(args.compare[1], args.runs_root),
            args.out,
            args.max_side,
        )
        print(f"comparison collage: {target}")
        return 0

    set_name = "legacy" if args.legacy_dir else "manifest"
    label = args.label or datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ("-legacy" if args.legacy_dir else "")
    out = args.out or args.runs_root / label
    if args.collage_only:
        print(f"collage: {build_run_collage(out, args.max_side)}")
        return 0

    cases = legacy_cases(args.legacy_dir) if args.legacy_dir else manifest_cases(args.manifest, args.cache_dir)
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = sorted(set(wanted) - {c.id for c in cases})
    if unknown:
        print(f"unknown case ids: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if wanted:
        cases = [c for c in cases if c.id in wanted]
    return run_cases(args, cases, out, label, set_name)


if __name__ == "__main__":
    raise SystemExit(main())
