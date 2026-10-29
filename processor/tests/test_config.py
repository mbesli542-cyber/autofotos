"""PROCESSOR_REQUIRE_API_KEY: the Docker image never runs an unauthenticated processor by accident,
local `uvicorn` development without Docker keeps working without a key."""

import logging
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import PROCESSOR_ROOT, Settings
from app.main import create_app

KEY_VARIABLES = ("PROCESSOR_API_KEY", "IMAGE_PROCESSING_API_KEY", "PROCESSOR_REQUIRE_API_KEY")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Environment without any key settings and without a processor/.env file."""
    monkeypatch.setenv("PROCESSOR_ENV_FILE", str(tmp_path / "no-such.env"))
    for name in KEY_VARIABLES + ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("true", True), ("1", True), ("false", False), ("0", False)],
)
def test_require_api_key_is_read_from_the_environment(clean_env, value, expected):
    if value is not None:
        clean_env.setenv("PROCESSOR_REQUIRE_API_KEY", value)
    assert Settings.from_env().require_api_key is expected


def test_a_required_key_refuses_to_start_without_one(settings, fake_segmenter, caplog):
    unsafe = replace(settings, require_api_key=True, api_key=None)
    with caplog.at_level(logging.ERROR, logger="autoexperten.processor"):
        with pytest.raises(RuntimeError, match="PROCESSOR_API_KEY is not set") as raised:
            create_app(unsafe, segmenter=fake_segmenter, warm_up=False)
    message = str(raised.value)
    assert "PROCESSOR_REQUIRE_API_KEY=false" in message  # names the explicit local opt-out
    assert any(record.getMessage() == message for record in caplog.records)  # also in the log


def test_the_docker_environment_without_a_key_refuses_to_start(clean_env, settings, fake_segmenter):
    # what `uvicorn app.main:app` does in the image: Settings.from_env() with ENV PROCESSOR_REQUIRE_API_KEY=true
    clean_env.setenv("PROCESSOR_REQUIRE_API_KEY", "true")
    clean_env.setenv("PROCESSOR_ASSETS_DIR", str(settings.assets_dir))
    clean_env.setenv("PROCESSOR_DATA_DIR", str(settings.data_dir))
    with pytest.raises(RuntimeError, match="refusing to start"):
        create_app(segmenter=fake_segmenter, warm_up=False)


def test_a_required_key_with_a_key_starts_and_reports_auth(settings, fake_segmenter):
    app = create_app(replace(settings, require_api_key=True, api_key="secret"), segmenter=fake_segmenter, warm_up=False)
    with TestClient(app) as client:
        details = client.get("/health", headers={"Authorization": "Bearer secret"}).json()
        anonymous = client.post("/jobs/upload", files={"file": ("car.jpg", b"x", "image/jpeg")})
    assert details["auth"] is True  # what the deploy steps check
    assert anonymous.status_code == 401


def test_local_development_without_docker_needs_no_key(settings, fake_segmenter):
    assert Settings().require_api_key is False
    app = create_app(replace(settings, api_key=None), segmenter=fake_segmenter, warm_up=False)
    with TestClient(app) as client:
        details = client.get("/health").json()  # open: everybody gets the details
    assert details["auth"] is False


def test_the_docker_image_and_the_deploy_template_require_a_key():
    dockerfile = (PROCESSOR_ROOT / "Dockerfile").read_text(encoding="utf-8")
    env_instructions = " ".join(re.findall(r"^ENV\s+((?:.*\\\n)*.*)$", dockerfile, flags=re.MULTILINE))
    assert re.search(r"\bPROCESSOR_REQUIRE_API_KEY=true\b", env_instructions)
    compose = (PROCESSOR_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    processor_service = compose.split("\n  caddy:", 1)[0]
    assert re.search(r'^\s+PROCESSOR_REQUIRE_API_KEY:\s*"true"\s*$', processor_service, flags=re.MULTILINE)
