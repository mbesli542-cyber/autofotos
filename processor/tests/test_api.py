"""HTTP API: request validation, auth, job lifecycle, results, debug files."""

import io
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.storage.base import OriginalPhoto, PhotoNotFoundError
from tests.conftest import encode_jpeg

VEHICLE_ID = "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301"
PHOTO_ID = "bbbbbbbb-0000-0000-0000-000000000001"


class FakeStore:
    def __init__(self, data: bytes, shot_key: str = "front_left_45"):
        self.data = data
        self.shot_key = shot_key
        self.stored: list[dict] = []

    def fetch_original(self, vehicle_id, photo_id):
        if photo_id != PHOTO_ID:
            raise PhotoNotFoundError("missing")
        return OriginalPhoto(self.data, self.shot_key, f"{vehicle_id}/{self.shot_key}/{photo_id}.jpg")

    def store_processed(self, *, vehicle_id, photo_id, shot_key, preset, jpeg):
        self.stored.append(dict(vehicle_id=vehicle_id, photo_id=photo_id, shot_key=shot_key, preset=preset, size=len(jpeg)))
        return f"{vehicle_id}/{preset}/{shot_key}/{photo_id}.jpg"


def wait_for(client, job_id, statuses=("complete", "failed"), timeout=60, headers=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=headers or {}).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {statuses}")


@pytest.fixture
def photo(vehicle):
    return encode_jpeg(vehicle[0], 95)


@pytest.fixture
def make_client(settings, fake_segmenter, photo):
    def _make(store="fake", **overrides):
        s = replace(settings, **overrides)
        st = FakeStore(photo) if store == "fake" else store
        app = create_app(s, segmenter=fake_segmenter, store=st, warm_up=False)
        return TestClient(app), st

    return _make


def test_health_needs_no_auth_but_details_do(make_client):
    client, _ = make_client(api_key="secret")
    with client:
        public = client.get("/health")
        details = client.get("/health", headers={"Authorization": "Bearer secret"}).json()
    assert public.status_code == 200
    assert public.json() == {"status": "ok", "version": details["version"]}  # nothing else leaks
    assert details["auth"] is True
    assert details["showroomPlaceholder"] is True  # preset still flagged as placeholder


def test_unauthenticated_requests_are_rejected_before_the_body_is_parsed(make_client):
    client, _ = make_client(api_key="secret")
    with client:
        response = client.post("/jobs", content=b"{not json", headers={"Content-Type": "application/json"})
        assert response.status_code == 401  # not 400 – the body was never looked at
        assert client.post("/jobs/upload", files={"file": ("a.jpg", b"x" * 1000, "image/jpeg")}).status_code == 401
        assert client.get("/docs").status_code in (401, 404)
        assert client.get("/openapi.json", headers={"Authorization": "Bearer secret"}).status_code == 404


def test_oversized_bodies_are_rejected(make_client, photo):
    client, _ = make_client(max_upload_bytes=10_000)
    limit = 10_000 + 1024 * 1024
    with client:
        declared = client.post(
            "/jobs/upload", content=b"0" * 16, headers={"Content-Length": str(limit + 1), "Content-Type": "image/jpeg"}
        )
        assert declared.status_code == 413

        def chunks():  # no Content-Length – streamed (chunked) body
            for _ in range(limit // 65536 + 2):
                yield b"0" * 65536

        streamed = client.post("/jobs/upload", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=x"})
        assert streamed.status_code == 413
        assert streamed.json()["error"]["code"] == "too_large"
        # within the transport limit but above the photo limit -> handler answers 413 too
        too_big_photo = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")})
        assert too_big_photo.status_code == 413


def test_queue_is_bounded(make_client, fake_segmenter, photo):
    client, _ = make_client()
    gate = threading.Event()
    fake_segmenter.gate = gate
    with client:
        codes = [
            client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).status_code
            for _ in range(5)
        ]
        gate.set()
    assert codes[:4] == [202, 202, 202, 202]
    assert codes[4] == 503


def test_service_role_requires_an_api_key(settings, fake_segmenter):
    unsafe = replace(settings, api_key=None, supabase_url="https://x.supabase.co", supabase_service_role_key="k" * 40)
    with pytest.raises(RuntimeError):
        create_app(unsafe, segmenter=fake_segmenter, warm_up=False)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID},
        {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "ai_magic"},
        {"vehicleId": "../etc", "photoId": PHOTO_ID, "preset": "autoexperten_standard"},
        {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard", "shotKey": "Front Left"},
    ],
)
def test_contract_request_validation(make_client, body):
    client, _ = make_client()
    with client:
        response = client.post("/jobs", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_auth_is_enforced_when_configured(make_client):
    client, _ = make_client(api_key="secret")
    body = {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
    with client:
        assert client.post("/jobs", json=body).status_code == 401
        assert client.post("/jobs", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = client.post("/jobs", json=body, headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 202
        assert client.get(f"/jobs/{ok.json()['jobId']}").status_code == 401


def test_contract_job_lifecycle_stores_result_as_separate_file(make_client):
    client, store = make_client()
    with client:
        response = client.post(
            "/jobs",
            json={"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard", "userId": "u1"},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] in {"queued", "processing"}
        assert {"jobId", "vehicleId", "photoId", "preset", "progress", "createdAt", "updatedAt", "result", "error"} <= set(job)
        done = wait_for(client, job["jobId"])
    assert done["status"] == "complete", done
    assert done["progress"] == 1.0
    assert done["result"] == {
        "kind": "stored",
        "processedStoragePath": f"{VEHICLE_ID}/autoexperten_standard/front_left_45/{PHOTO_ID}.jpg",
    }
    assert store.stored and store.stored[0]["size"] > 10_000


def test_contract_job_without_storage_is_rejected(make_client):
    client, _ = make_client(store=None)
    with client:
        response = client.post(
            "/jobs", json={"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
        )
    assert response.status_code == 503


def test_unknown_photo_fails_with_german_message(make_client):
    client, _ = make_client()
    with client:
        job = client.post(
            "/jobs",
            json={"vehicleId": VEHICLE_ID, "photoId": "cccccccc-0000-0000-0000-000000000009", "preset": "autoexperten_standard"},
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert done["error"] == "Das Originalfoto wurde nicht gefunden."


def test_upload_job_returns_downloadable_result(make_client, photo):
    client, _ = make_client()
    with client:
        response = client.post(
            "/jobs/upload",
            files={"file": ("car.jpg", photo, "image/jpeg")},
            data={"preset": "autoexperten_standard", "shotKey": "front_left_45"},
        )
        assert response.status_code == 202
        done = wait_for(client, response.json()["jobId"])
        assert done["status"] == "complete", done
        assert done["result"]["kind"] == "file"
        result = client.get(done["result"]["resultUrl"])
    assert result.status_code == 200
    assert result.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(result.content))
    assert image.size == (done["result"]["width"], done["result"]["height"])
    assert any(w["code"] == "showroom_placeholder" for w in done["warnings"])


def test_job_reports_queued_processing_complete_in_order(make_client, fake_segmenter, photo):
    client, _ = make_client()
    gate = threading.Event()
    fake_segmenter.gate = gate
    with client:
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        processing = wait_for(client, job["jobId"], statuses=("processing",))
        assert 0 < processing["progress"] < 1
        assert client.get(f"/jobs/{job['jobId']}/result").status_code == 409  # not ready yet
        gate.set()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "complete"


def test_undecodable_upload_fails_cleanly(make_client):
    client, _ = make_client()
    with client:
        job = client.post("/jobs/upload", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert "Foto konnte nicht gelesen werden" in done["error"]


def test_upload_rejects_non_images_and_unknown_preset(make_client, photo):
    client, _ = make_client()
    with client:
        assert client.post("/jobs/upload", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
        bad = client.post(
            "/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, data={"preset": "magic"}
        )
        assert bad.status_code == 400


def test_unavailable_preset_fails_job_with_message(make_client, photo):
    client, _ = make_client()
    with client:
        job = client.post(
            "/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, data={"preset": "autoexperten_dark"}
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert done["error"] == "Dieser Bearbeitungsstil ist noch nicht verfügbar."


def test_debug_files_only_when_enabled(make_client, photo):
    client, _ = make_client(debug=False)
    with client:
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
        assert done["metadata"]["debugFiles"] == []
        assert client.get(f"/jobs/{job['jobId']}/debug").status_code == 404
        assert client.get(f"/jobs/{job['jobId']}/debug/mask.png").status_code == 404

    client, _ = make_client(debug=True)
    with client:
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
        assert "mask.png" in done["metadata"]["debugFiles"]
        listing = client.get(f"/jobs/{job['jobId']}/debug").json()["files"]
        assert "vehicle-transparent.png" in listing
        mask = client.get(f"/jobs/{job['jobId']}/debug/mask.png")
        assert mask.status_code == 200 and mask.headers["content-type"] == "image/png"
        assert client.get(f"/jobs/{job['jobId']}/debug/..%2Fresult.jpg").status_code == 404
        assert client.get(f"/jobs/{job['jobId']}/debug/secret.txt").status_code == 404


def test_unknown_and_malformed_job_ids(make_client):
    client, _ = make_client()
    with client:
        assert client.get("/jobs/" + "a" * 32).status_code == 404
        assert client.get("/jobs/not-a-job").status_code == 404


def test_health_reports_a_model_that_cannot_be_loaded(settings):
    class BrokenSegmenter:
        name = "broken"
        loaded = False

        def warm_up(self):
            raise RuntimeError("model file not readable")

        def segment(self, rgb):  # pragma: no cover - never reached
            raise RuntimeError

    app = create_app(settings, segmenter=BrokenSegmenter(), store=None, warm_up=True)
    with TestClient(app) as client:
        deadline = time.time() + 5
        response = client.get("/health")
        while response.status_code == 200 and time.time() < deadline:
            time.sleep(0.05)
            response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["modelError"] is True


def test_job_folders_of_earlier_processes_expire(settings, fake_segmenter, tmp_path):
    import os

    from app.jobs.manager import JobManager
    from app.presets import BackgroundProvider

    s = replace(settings, data_dir=tmp_path, job_ttl_hours=1)
    old = tmp_path / "jobs" / ("a" * 32)
    (old / "debug").mkdir(parents=True)
    (old / "result.jpg").write_bytes(b"x")
    (old / "debug" / "original.jpg").write_bytes(b"x")
    fresh = tmp_path / "jobs" / ("b" * 32)
    fresh.mkdir()
    long_ago = time.time() - 3 * 3600
    for path in [old, old / "debug", old / "result.jpg", old / "debug" / "original.jpg"]:
        os.utime(path, (long_ago, long_ago))
    manager = JobManager(s, fake_segmenter, BackgroundProvider(s))
    try:
        assert not old.exists()  # removed at start-up
        assert fresh.exists()  # younger than the TTL
    finally:
        manager.shutdown()


def test_missing_model_is_reported_as_a_service_problem_not_a_photo_problem(settings, photo):
    from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter

    segmenter = OnnxSegmenter(
        MODEL_REGISTRY["isnet-general-use"], settings.data_dir / "no-models", auto_download=False
    )
    app = create_app(settings, segmenter=segmenter, store=None, warm_up=False)
    with TestClient(app) as client:
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert done["error"] == "Die Bildbearbeitung ist derzeit nicht verfügbar. Bitte später erneut versuchen."


def test_contract_jobs_are_not_limited_by_the_upload_queue(make_client, fake_segmenter):
    client, _ = make_client()
    gate = threading.Event()
    fake_segmenter.gate = gate
    body = {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
    with client:
        codes = [client.post("/jobs", json=body).status_code for _ in range(8)]
        gate.set()
    assert codes == [202] * 8
