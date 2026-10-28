"""Processing jobs: queued → processing → complete | failed.

Kept in memory (prototype – one process, not optimised for scale). Results and
optional debug files are written below `<data_dir>/jobs/<jobId>/` and removed
after PROCESSOR_JOB_TTL_HOURS. A queued upload job keeps its photo there too
(`upload.bin`, deleted when the job starts), not in memory.
"""

from __future__ import annotations

import errno
import logging
import shutil
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
from ..pipeline.debug import DEBUG_FILE_NAMES, NULL_DEBUG, DebugSink
from ..pipeline.decode import DecodeError
from ..pipeline.pipeline import process_photo
from ..pipeline.placement import PlacementError
from ..pipeline.quality import MESSAGES as QUALITY_MESSAGES
from ..pipeline.quality import QualityGateError
from ..pipeline.segmentation import (
    ModelUnavailableError,
    SegmentationError,
    VehicleSegmenter,
)
from ..presets import (
    BackgroundProvider,
    PresetConfigError,
    PresetError,
    ShowroomUnavailableError,
    load_preset,
)
from ..storage.base import PhotoNotFoundError, PhotoStore, StorageError

log = logging.getLogger(__name__)

STATUSES = ("queued", "processing", "complete", "failed")

ERROR_MESSAGES = {
    "decode": "Das Foto konnte nicht gelesen werden. Bitte ein JPEG-, PNG- oder HEIC-Foto verwenden.",
    # app/pipeline/decode.py: above MAX_PIXELS (80 MP) and not decodable reduced (JPEGs are) – a retake fixes it
    "image_too_large": (
        "Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler Kamera-Auflösung neu aufnehmen."
    ),
    # ... and a side below 64 px: reported like the quality gate's resolution check
    "source_resolution_too_low": QUALITY_MESSAGES["source_resolution_too_low"],
    "segmentation": "Das Fahrzeug konnte im Foto nicht erkannt werden.",
    "preset": "Dieser Bearbeitungsstil ist noch nicht verfügbar.",
    "not_found": "Das Originalfoto wurde nicht gefunden.",
    "storage": "Das Foto konnte nicht geladen oder gespeichert werden. Bitte später erneut versuchen.",
    "service": "Die Bildbearbeitung ist derzeit nicht verfügbar. Bitte später erneut versuchen.",
    "configuration": "Die Bildbearbeitung ist auf dem Server nicht richtig eingerichtet (Showroom/Logo). Bitte den Administrator informieren.",
    "showroom": "AutoExperten Showroom-Master fehlt.",
    "unknown": "Die Bearbeitung ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
}


class StoreUnavailableError(Exception):
    """JSON contract jobs need a configured photo store (Supabase)."""


class QueueFullError(Exception):
    """Too many jobs are waiting."""


class JobStorageError(Exception):
    """A job's folder or a queued upload's photo could not be written (nothing was queued)."""

    def __init__(self, error: OSError):
        super().__init__(str(error))
        #: The disk (or the quota) is full – any other OSError is an I/O or permission problem.
        self.full = error.errno in (errno.ENOSPC, errno.EDQUOT)


#: Upload jobs (demo mode: several employees, each app run sends 3 photos at a time) wait
#: with their photo on disk – worst case per worker 12 × PROCESSOR_MAX_UPLOAD_MB (40 MB)
#: = 480 MB in `<data_dir>/jobs`; in memory only the running job's photo.
MAX_PENDING_UPLOADS_PER_WORKER = 12
#: File of a queued upload job's photo inside its job folder.
UPLOAD_FILE_NAME = "upload.bin"
#: Contract jobs fetch the original only when they start; this only bounds abuse.
MAX_PENDING_JOBS = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass
class Job:
    id: str
    kind: str  # "upload" | "contract"
    vehicle_id: str
    photo_id: str
    preset: str
    shot_key: str | None
    user_id: str | None
    directory: Path
    status: str = "queued"
    progress: float = 0.0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    result: dict | None = None
    error: str | None = None
    warnings: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_response(self) -> dict:
        return {
            "jobId": self.id,
            "vehicleId": self.vehicle_id,
            "photoId": self.photo_id,
            "preset": self.preset,
            "status": self.status,
            "progress": round(self.progress, 3),
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
            "result": self.result,
            "error": self.error,
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


class JobManager:
    def __init__(
        self,
        settings: Settings,
        segmenter: VehicleSegmenter,
        backgrounds: BackgroundProvider,
        store: PhotoStore | None = None,
    ):
        self.settings = settings
        self.segmenter = segmenter
        self.backgrounds = backgrounds
        self.store = store
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.concurrency, thread_name_prefix="ae-job"
        )
        self.jobs_dir = settings.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.max_pending_uploads = MAX_PENDING_UPLOADS_PER_WORKER * settings.concurrency
        self._drop_stale_uploads()
        self._sweep_orphans()

    # ------------------------------------------------------------------ api

    def submit_upload(self, data: bytes | bytearray, *, preset: str, shot_key: str | None) -> Job:
        """Queue an upload job; its photo waits on disk. Blocking file I/O – async callers
        run it in a worker thread. Raises QueueFullError and JobStorageError."""
        job = self._create("upload", vehicle_id="upload", photo_id="upload", preset=preset, shot_key=shot_key, user_id=None)
        upload = job.directory / UPLOAD_FILE_NAME
        try:
            upload.write_bytes(data)  # waits on disk, not in memory (deleted when the job starts)
        except OSError as error:
            self._discard(job)  # a partly written photo and the job folder go too
            log.error("Upload job %s not queued: its photo could not be stored: %s", job.id, error)
            raise JobStorageError(error) from error
        self._executor.submit(self._run, job, lambda: (upload.read_bytes(), shot_key))
        return job

    def submit_contract(
        self, *, vehicle_id: str, photo_id: str, preset: str, shot_key: str | None, user_id: str | None
    ) -> Job:
        store = self.store
        if store is None:
            raise StoreUnavailableError("Supabase storage is not configured on the processor")
        job = self._create(
            "contract", vehicle_id=vehicle_id, photo_id=photo_id, preset=preset, shot_key=shot_key, user_id=user_id
        )

        def load() -> tuple[bytes, str | None]:
            original = store.fetch_original(vehicle_id, photo_id)
            job.shot_key = original.shot_key
            return original.data, original.shot_key

        self._executor.submit(self._run, job, load)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def result_path(self, job_id: str) -> Path | None:
        job = self.get(job_id)
        if job is None or job.status != "complete":
            return None
        path = job.directory / "result.jpg"
        return path if path.is_file() else None

    def debug_files(self, job_id: str) -> list[str]:
        job = self.get(job_id)
        if job is None or not self.settings.debug:
            return []
        directory = job.directory / "debug"
        return [name for name in DEBUG_FILE_NAMES if (directory / name).is_file()]

    def debug_file(self, job_id: str, name: str) -> Path | None:
        if not self.settings.debug or name not in DEBUG_FILE_NAMES:
            return None
        job = self.get(job_id)
        if job is None:
            return None
        path = job.directory / "debug" / name
        return path if path.is_file() else None

    def cleanup(self) -> int:
        """Remove finished jobs older than the TTL (returns removed count)."""
        cutoff = _now() - timedelta(hours=self.settings.job_ttl_hours)
        removed = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status in ("complete", "failed") and job.updated_at < cutoff:
                    removed.append(self._jobs.pop(job_id))
        for job in removed:
            shutil.rmtree(job.directory, ignore_errors=True)
        return len(removed) + self._sweep_orphans()

    def _drop_stale_uploads(self) -> None:
        """At start-up: photos of upload jobs an earlier process never ran (jobs live in memory)."""
        for path in self.jobs_dir.glob(f"*/{UPLOAD_FILE_NAME}"):
            path.unlink(missing_ok=True)

    def _sweep_orphans(self) -> int:
        """Delete job folders of earlier processes (unknown ids) once they exceed the TTL."""
        cutoff = (_now() - timedelta(hours=self.settings.job_ttl_hours)).timestamp()
        with self._lock:
            known = set(self._jobs)
        try:
            directories = list(self.jobs_dir.iterdir())
        except OSError:  # e.g. the data folder was cleared: the next job creates it again
            return 0
        removed = 0
        for directory in directories:
            if not directory.is_dir() or directory.name in known:
                continue
            try:
                newest = max([directory.stat().st_mtime] + [p.stat().st_mtime for p in directory.rglob("*")])
            except OSError:
                continue
            if newest < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        return removed

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- internals

    def _create(self, kind: str, **kwargs) -> Job:
        self.cleanup()
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, kind=kind, directory=self.jobs_dir / job_id, **kwargs)
        with self._lock:
            pending = [j for j in self._jobs.values() if j.status in ("queued", "processing")]
            uploads = sum(1 for j in pending if j.kind == "upload")
            if len(pending) >= MAX_PENDING_JOBS or (kind == "upload" and uploads >= self.max_pending_uploads):
                raise QueueFullError(f"{len(pending)} jobs pending")
            self._jobs[job_id] = job
        try:
            job.directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._discard(job)  # never left behind as a queued job that cannot run
            log.error("%s job %s not queued: its folder could not be created: %s", kind, job_id, error)
            raise JobStorageError(error) from error
        return job

    def _discard(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
        shutil.rmtree(job.directory, ignore_errors=True)

    def _update(self, job: Job, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()

    def _run(self, job: Job, load: Callable[[], tuple[bytes, str | None]]) -> None:
        self._update(job, status="processing", progress=0.01)
        debug = DebugSink(job.directory / "debug") if self.settings.debug else NULL_DEBUG
        try:
            try:
                preset = load_preset(self.settings, job.preset)
                data, shot_key = load()
            finally:
                # a queued upload's photo is only kept until its job starts
                (job.directory / UPLOAD_FILE_NAME).unlink(missing_ok=True)
            outcome = process_photo(
                data,
                preset=preset,
                segmenter=self.segmenter,
                backgrounds=self.backgrounds,
                shot_key=shot_key,
                debug=debug,
                progress=lambda value, _step: self._update(job, progress=min(0.99, max(job.progress, value))),
            )
            (job.directory / "result.jpg").write_bytes(outcome.jpeg)
            if job.kind == "contract":
                assert self.store is not None
                path = self.store.store_processed(
                    vehicle_id=job.vehicle_id,
                    photo_id=job.photo_id,
                    shot_key=shot_key or "unknown",
                    preset=job.preset,
                    jpeg=outcome.jpeg,
                )
                result = {"kind": "stored", "processedStoragePath": path}
            else:
                result = {
                    "kind": "file",
                    "resultUrl": f"/jobs/{job.id}/result",
                    "width": outcome.width,
                    "height": outcome.height,
                    "bytes": len(outcome.jpeg),
                }
            metadata = {**outcome.metadata, "debugFiles": _served_debug_files(debug)}
            self._update(
                job,
                status="complete",
                progress=1.0,
                result=result,
                warnings=[w.__dict__ for w in outcome.warnings],
                metadata=metadata,
            )
        except QualityGateError as error:
            # the photo would give a bad listing image: nothing is stored, the app asks for a retake
            log.info("Job %s rejected by the quality gate: %s", job.id, error.code)
            self._update(
                job,
                status="failed",
                error=error.user_message,
                metadata={
                    "errorCode": error.code,
                    "qualityGate": error.details,
                    "debugFiles": _served_debug_files(debug),
                },
            )
        except Exception as error:  # noqa: BLE001 - mapped to user-facing messages
            code = _error_code(error)
            if code in ("unknown", "service", "configuration"):
                log.exception("Job %s failed (%s)", job.id, code)
            else:
                log.warning("Job %s failed: %s (%s)", job.id, code, error)
            # debug images written before the failure help to find the cause
            self._update(
                job,
                status="failed",
                error=ERROR_MESSAGES[code],
                metadata={"errorCode": code, "debugFiles": _served_debug_files(debug)},
            )


def _served_debug_files(debug: DebugSink) -> list[str]:
    """Debug files of a job as GET /jobs/{id}/debug/{name} serves them (allow-list), in writing order."""
    return [name for name in debug.files if name in DEBUG_FILE_NAMES] if debug.enabled else []


def _error_code(error: Exception) -> str:
    if isinstance(error, ModelUnavailableError):
        return "service"
    if isinstance(error, ShowroomUnavailableError):
        return "showroom"
    if isinstance(error, (PresetConfigError, PlacementError)):
        return "configuration"
    if isinstance(error, DecodeError):
        return error.code  # decode | image_too_large | source_resolution_too_low
    if isinstance(error, SegmentationError):
        return "segmentation"
    if isinstance(error, PresetError):
        return "preset"
    if isinstance(error, PhotoNotFoundError):
        return "not_found"
    if isinstance(error, StorageError):
        return "storage"
    return "unknown"
