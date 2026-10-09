"""Service configuration, read from environment variables.

All variables are optional; defaults are tuned for local development.
Variables may also be put into ``processor/.env`` (or the file named by
PROCESSOR_ENV_FILE); real environment variables always win over the file.
Secrets (API key, Supabase service role key) are never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROCESSOR_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROCESSOR_ROOT.parent


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines into os.environ without overriding existing values."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


@dataclass(frozen=True)
class Settings:
    #: Shared secret expected as "Authorization: Bearer <key>". Empty = no auth (local dev only).
    api_key: str | None = None
    #: Directory with `presets/` and `brand/` (defaults to the Next.js `public/` folder).
    assets_dir: Path = REPO_ROOT / "public"
    #: Working directory for job inputs/results/debug files.
    data_dir: Path = PROCESSOR_ROOT / "data"
    #: Directory for segmentation model weights (*.onnx).
    models_dir: Path = PROCESSOR_ROOT / "models"
    #: Segmentation backend: "onnx" (default) – see app/pipeline/segmentation.py.
    segmenter: str = "onnx"
    #: Model name from the registry in app/pipeline/segmentation.py.
    segmentation_model: str = "birefnet-general-lite"
    #: Download missing model weights on first use (verified by SHA-256).
    auto_download_models: bool = True
    #: Write intermediate images (mask, cutout, …) per job. Never enable on public instances.
    debug: bool = False
    #: Number of jobs processed in parallel (CPU heavy – keep at 1 for the prototype).
    concurrency: int = 1
    #: ONNX Runtime intra-op threads (0 = library default).
    threads: int = 0
    #: Hours after which finished jobs and their files are removed.
    job_ttl_hours: int = 24
    #: Max upload size for /jobs/upload in bytes.
    max_upload_bytes: int = 40 * 1024 * 1024
    #: Supabase (optional) – needed for the JSON contract used by the Next.js app.
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    #: Origins allowed to call the API from a browser (comma separated). Empty = none.
    cors_origins: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def presets_dir(self) -> Path:
        return self.assets_dir / "presets"

    @property
    def brand_dir(self) -> Path:
        return self.assets_dir / "brand"

    @classmethod
    def from_env(cls) -> Settings:
        load_env_file(Path(os.environ.get("PROCESSOR_ENV_FILE") or PROCESSOR_ROOT / ".env"))
        cors = _env("PROCESSOR_CORS_ORIGINS", "") or ""
        return cls(
            api_key=_env("PROCESSOR_API_KEY") or _env("IMAGE_PROCESSING_API_KEY"),
            assets_dir=Path(_env("PROCESSOR_ASSETS_DIR", str(REPO_ROOT / "public")) or ""),
            data_dir=Path(_env("PROCESSOR_DATA_DIR", str(PROCESSOR_ROOT / "data")) or ""),
            models_dir=Path(_env("PROCESSOR_MODELS_DIR", str(PROCESSOR_ROOT / "models")) or ""),
            segmenter=_env("PROCESSOR_SEGMENTER", "onnx") or "onnx",
            segmentation_model=_env("PROCESSOR_SEGMENTATION_MODEL", "birefnet-general-lite")
            or "birefnet-general-lite",
            auto_download_models=_env_bool("PROCESSOR_AUTO_DOWNLOAD_MODELS", True),
            debug=_env_bool("PROCESSOR_DEBUG", False),
            concurrency=max(1, _env_int("PROCESSOR_CONCURRENCY", 1)),
            threads=max(0, _env_int("PROCESSOR_THREADS", 0)),
            job_ttl_hours=max(1, _env_int("PROCESSOR_JOB_TTL_HOURS", 24)),
            max_upload_bytes=_env_int("PROCESSOR_MAX_UPLOAD_MB", 40) * 1024 * 1024,
            supabase_url=_env("SUPABASE_URL"),
            supabase_service_role_key=_env("SUPABASE_SERVICE_ROLE_KEY"),
            cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
        )
