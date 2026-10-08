"""Processing jobs: queued → processing → complete | failed.

Kept in memory (prototype – one process, not optimised for scale). Results and
optional debug files are written below `<data_dir>/jobs/<jobId>/` and removed
after PROCESSOR_JOB_TTL_HOURS.
"""

from __future__ import annotations

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
from ..pipeline.pipeline import ShowroomNotReleasedError, process_photo
from ..pipeline.placement import PlacementError
from ..pipeline.segmentation import ModelUnavailableError, SegmentationError, VehicleSegmenter
from ..presets import BackgroundProvider, PresetConfigError, PresetError, load_preset
from ..storage.base import PhotoNotFoundError, PhotoStore, StorageError

log = logging.getLogger(__name__)

STATUSES = ("queued", "processing", "complete", "failed")

ERROR_MESSAGES = {
    "decode": "Das Foto konnte nicht gelesen werden. Bitte ein JPEG-, PNG- oder HEIC-Foto verwenden.",
    "segmentation": "Das Fahrzeug konnte im Foto nicht erkannt werden.",
    "preset": "Dieser Bearbeitungsstil ist noch nicht verfügbar.",
    "not_found": "Das Originalfoto wurde nicht gefunden.",
    "storage": "Das Foto konnte nicht geladen oder gespeichert werden. Bitte später erneut versuchen.",
    "service": "Die Bildbearbeitung ist derzeit nicht verfügbar. Bitte später erneut versuchen.",
    "configuration": "Die Bildbearbeitung ist auf dem Server nicht richtig eingerichtet (Showroom/Logo). Bitte den Administrator informieren.",
    "showroom": "Das finale AutoExperten-Showroom-Foto fehlt noch – die Bildbearbeitung ist noch nicht freigegeben.",
    "unknown": "Die Bearbeitung ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
}


class StoreUnavailableError(Exception):
    """JSON contract jobs need a configured photo store (Supabase)."""


class QueueFullError(Exception):
    """Too many jobs are waiting."""


#: Upload jobs hold their photo in memory while they wait – keep that queue short.
MAX_PENDING_UPLOADS_PER_WORKER = 4
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
        self._sweep_orphans()

    # ------------------------------------------------------------------ api

    def submit_upload(self, data: bytes, *, preset: str, shot_key: str | None) -> Job:
        job = self._create("upload", vehicle_id="upload", photo_id="upload", preset=preset, shot_key=shot_key, user_id=None)
        self._executor.submit(self._run, job, lambda: (data, shot_key))
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

    def _sweep_orphans(self) -> int:
        """Delete job folders of earlier processes (unknown ids) once they exceed the TTL."""
        cutoff = (_now() - timedelta(hours=self.settings.job_ttl_hours)).timestamp()
        with self._lock:
            known = set(self._jobs)
        removed = 0
        for directory in self.jobs_dir.iterdir():
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
        job.directory.mkdir(parents=True, exist_ok=True)
        return job

    def _update(self, job: Job, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()

    def _run(self, job: Job, load: Callable[[], tuple[bytes, str | None]]) -> None:
        self._update(job, status="processing", progress=0.01)
        try:
            preset = load_preset(self.settings, job.preset)
            data, shot_key = load()
            debug = DebugSink(job.directory / "debug") if self.settings.debug else NULL_DEBUG
            outcome = process_photo(
                data,
                preset=preset,
                segmenter=self.segmenter,
                backgrounds=self.backgrounds,
                shot_key=shot_key,
                debug=debug,
                progress=lambda value, _step: self._update(job, progress=min(0.99, max(job.progress, value))),
                # results stored in the app never use the emergency fallback showroom
                require_master_showroom=job.kind == "contract" and not self.settings.allow_fallback_showroom,
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
            metadata = {
                **outcome.metadata,
                "debugFiles": list(debug.files) if debug.enabled else [],
            }
            self._update(
                job,
                status="complete",
                progress=1.0,
                result=result,
                warnings=[w.__dict__ for w in outcome.warnings],
                metadata=metadata,
            )
        except Exception as error:  # noqa: BLE001 - mapped to user-facing messages
            code = _error_code(error)
            if code in ("unknown", "service", "configuration"):
                log.exception("Job %s failed (%s)", job.id, code)
            else:
                log.warning("Job %s failed: %s (%s)", job.id, code, error)
            self._update(job, status="failed", error=ERROR_MESSAGES[code], metadata={"errorCode": code})


def _error_code(error: Exception) -> str:
    if isinstance(error, ModelUnavailableError):
        return "service"
    if isinstance(error, ShowroomNotReleasedError):
        return "showroom"
    if isinstance(error, (PresetConfigError, PlacementError)):
        return "configuration"
    if isinstance(error, DecodeError):
        return "decode"
    if isinstance(error, SegmentationError):
        return "segmentation"
    if isinstance(error, PresetError):
        return "preset"
    if isinstance(error, PhotoNotFoundError):
        return "not_found"
    if isinstance(error, StorageError):
        return "storage"
    return "unknown"
