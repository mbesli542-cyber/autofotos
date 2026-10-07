"""FastAPI application – AutoExperten image processor.

Endpoints (contract used by the Next.js app's RealImageProcessor):
    POST /jobs                    JSON {vehicleId, photoId, preset, userId?, shotKey?} → 202 job
    GET  /jobs/{jobId}            job status/result
Single-photo testing:
    POST /jobs/upload             multipart (file, preset, shotKey) → 202 job
    GET  /jobs/{jobId}/result     processed JPEG
    GET  /jobs/{jobId}/debug      list of debug files (PROCESSOR_DEBUG=true only)
    GET  /jobs/{jobId}/debug/{n}  one debug file    (PROCESSOR_DEBUG=true only)
    GET  /health                  service status (no auth; details only with auth)

Every other endpoint requires "Authorization: Bearer <PROCESSOR_API_KEY>" when
the key is set (checked by app.guard.RequestGuard before any body is read).

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .guard import RequestGuard, is_authorized
from .jobs.manager import JobManager, QueueFullError, StoreUnavailableError
from .pipeline.pipeline import PIPELINE_VERSION
from .pipeline.segmentation import VehicleSegmenter, create_segmenter
from .presets import BackgroundProvider, PresetError, load_preset
from .storage.base import PhotoStore

log = logging.getLogger("autoexperten.processor")

PresetId = Literal["autoexperten_standard", "autoexperten_dark", "original_plus"]
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
SHOT_PATTERN = r"^[a-z0-9_]{1,40}$"
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class ContractJobRequest(BaseModel):
    vehicleId: str = Field(pattern=ID_PATTERN)
    photoId: str = Field(pattern=ID_PATTERN)
    preset: PresetId
    userId: str | None = Field(default=None, max_length=64)
    shotKey: str | None = Field(default=None, pattern=SHOT_PATTERN)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    settings: Settings | None = None,
    *,
    segmenter: VehicleSegmenter | None = None,
    store: PhotoStore | None = None,
    warm_up: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.supabase_configured and not settings.api_key:
        # The service role bypasses RLS – never expose it through an open API.
        raise RuntimeError("PROCESSOR_API_KEY is required when SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY are set")
    segmenter = segmenter or create_segmenter(settings)
    if store is None and settings.supabase_configured:
        from .storage.supabase import SupabasePhotoStore

        store = SupabasePhotoStore(settings.supabase_url or "", settings.supabase_service_role_key or "")
    backgrounds = BackgroundProvider(settings)
    manager = JobManager(settings, segmenter, backgrounds, store)
    model_state = {"error": False}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not settings.api_key:
            log.warning("PROCESSOR_API_KEY is not set – API is unauthenticated (local development only).")
        if settings.debug:
            log.warning("PROCESSOR_DEBUG=true – debug images are stored. Never enable on public instances.")
        if warm_up and hasattr(segmenter, "warm_up"):
            threading.Thread(target=_safe_warm_up, args=(segmenter, model_state), daemon=True).start()
        yield
        manager.shutdown()
        close = getattr(store, "close", None)
        if callable(close):
            close()  # releases the Supabase HTTP connection pool

    app = FastAPI(
        title="AutoExperten Image Processor",
        version=PIPELINE_VERSION,
        lifespan=lifespan,
        # interactive API docs only for local debugging
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    app.state.manager = manager
    app.state.settings = settings

    # Added before CORS so that CORS stays the outer layer (preflight requests
    # carry no Authorization header). Multipart overhead: 1 MiB on top.
    app.add_middleware(
        RequestGuard,
        api_key=settings.api_key,
        max_body_bytes=settings.max_upload_bytes + 1024 * 1024,
        public_paths=("/health", "/docs", "/openapi.json") if settings.debug else ("/health",),
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in err.get("loc", [])[1:]) for err in exc.errors()})
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Ungültige Anfrage.",
                    "fields": fields,
                }
            },
        )

    def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
        # second line of defence – RequestGuard already rejected unauthenticated calls
        if not is_authorized(authorization, settings.api_key):
            raise _AuthError()

    Auth = Depends(require_auth)

    @app.exception_handler(_AuthError)
    async def _auth_error(_request: Request, _exc: _AuthError):
        return error_response(401, "unauthorized", "Nicht autorisiert.")

    @app.get("/health")
    def health(authorization: Annotated[str | None, Header()] = None):
        loaded = bool(getattr(segmenter, "loaded", True))
        failed = model_state["error"] and not loaded
        status = 503 if failed else 200  # lets container health checks notice a broken model
        if not is_authorized(authorization, settings.api_key):
            # unauthenticated callers (health checks) only learn whether the service is up
            return JSONResponse(
                status_code=status, content={"status": "error" if failed else "ok", "version": PIPELINE_VERSION}
            )
        model = getattr(segmenter, "spec", None)
        try:
            preset = load_preset(settings, "autoexperten_standard")
            placeholder = backgrounds.is_placeholder(preset)
        except PresetError:
            placeholder = None
        body = {
            "status": "error" if failed else "ok",
            "version": PIPELINE_VERSION,
            "segmenter": getattr(segmenter, "name", type(segmenter).__name__),
            "model": getattr(model, "name", None),
            "modelLoaded": loaded,
            "modelError": failed,
            "debug": settings.debug,
            "auth": bool(settings.api_key),
            "supabase": store is not None,
            "showroomPlaceholder": placeholder,
        }
        return JSONResponse(status_code=status, content=body)

    @app.post("/jobs", status_code=202, dependencies=[Auth])
    def create_contract_job(body: ContractJobRequest):
        try:
            job = manager.submit_contract(
                vehicle_id=body.vehicleId,
                photo_id=body.photoId,
                preset=body.preset,
                shot_key=body.shotKey,
                user_id=body.userId,
            )
        except StoreUnavailableError:
            return error_response(
                503,
                "storage_not_configured",
                "Der Bildverarbeitungs-Service ist nicht mit Supabase verbunden.",
            )
        except QueueFullError:
            return _busy()
        return job.to_response()

    @app.post("/jobs/upload", status_code=202, dependencies=[Auth])
    async def create_upload_job(
        file: Annotated[UploadFile, File()],
        preset: Annotated[PresetId, Form()] = "autoexperten_standard",
        shotKey: Annotated[str | None, Form(pattern=SHOT_PATTERN)] = "front_left_45",
    ):
        if file.content_type and not (
            file.content_type.startswith("image/") or file.content_type == "application/octet-stream"
        ):
            return error_response(415, "unsupported_type", "Bitte eine Bilddatei hochladen.")
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > settings.max_upload_bytes:
                return error_response(413, "too_large", "Die Datei ist zu groß.")
        if not data:
            return error_response(400, "empty_file", "Die Datei ist leer.")
        try:
            job = manager.submit_upload(bytes(data), preset=preset, shot_key=shotKey)
        except QueueFullError:
            return _busy()
        return job.to_response()

    def _get_job(job_id: str):
        if not JOB_ID_RE.match(job_id):
            return None
        return manager.get(job_id)

    @app.get("/jobs/{job_id}", dependencies=[Auth])
    def get_job(job_id: str):
        job = _get_job(job_id)
        if job is None:
            return error_response(404, "not_found", "Auftrag wurde nicht gefunden.")
        return job.to_response()

    @app.get("/jobs/{job_id}/result", dependencies=[Auth])
    def get_result(job_id: str):
        job = _get_job(job_id)
        if job is None:
            return error_response(404, "not_found", "Auftrag wurde nicht gefunden.")
        path = manager.result_path(job_id)
        if path is None:
            return error_response(409, "not_ready", "Das Ergebnis ist noch nicht fertig.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/jobs/{job_id}/debug", dependencies=[Auth])
    def list_debug(job_id: str):
        if not settings.debug or _get_job(job_id) is None:
            return error_response(404, "not_found", "Nicht gefunden.")
        return {"files": manager.debug_files(job_id)}

    @app.get("/jobs/{job_id}/debug/{name}", dependencies=[Auth])
    def get_debug_file(job_id: str, name: str):
        if not settings.debug or _get_job(job_id) is None:
            return error_response(404, "not_found", "Nicht gefunden.")
        path = manager.debug_file(job_id, name)
        if path is None:
            return error_response(404, "not_found", "Nicht gefunden.")
        media = {".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json"}[path.suffix]
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    return app


class _AuthError(Exception):
    pass


def _busy() -> JSONResponse:
    return error_response(
        503, "busy", "Der Bildverarbeitungs-Service ist ausgelastet. Bitte gleich erneut versuchen."
    )


def _safe_warm_up(segmenter, state: dict) -> None:
    try:
        segmenter.warm_up()
    except Exception:  # pragma: no cover - logged, first job will retry
        state["error"] = True
        log.exception("Model warm-up failed")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def __getattr__(name: str):
    # `uvicorn app.main:app` – the app is created lazily so importing this
    # module in tests does not load models or read the environment.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)
