"""HTTP API: request validation, auth, job lifecycle, results, debug files."""

import errno
import io
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
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
def photo(three_quarter):
    """Synthetic front-left 3/4 photo – matches the default shot key front_left_45."""
    return encode_jpeg(three_quarter[0], 95)


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
    assert details["showroomSource"] == "plates"  # test assets contain a (synthetic) plate set
    assert details["showroomMasterError"] is None
    assert details["showroomPlaceholder"] is False
    assert "fallbackShowroomAllowed" not in details


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


def test_queue_is_bounded(make_client, fake_segmenter, photo, settings):
    from app.jobs.manager import MAX_PENDING_UPLOADS_PER_WORKER, UPLOAD_FILE_NAME

    assert MAX_PENDING_UPLOADS_PER_WORKER == 12  # two employees × 3 parallel uploads fit with room to spare
    client, _ = make_client()
    gate = threading.Event()
    fake_segmenter.gate = gate
    upload = {"files": {"file": ("car.jpg", photo, "image/jpeg")}}
    with client:
        running = client.post("/jobs/upload", **upload)  # exterior: held at the segmentation gate
        wait_for(client, running.json()["jobId"], statuses=("processing",))
        # interior shots (no segmentation) keep the test fast once the gate opens
        responses = [client.post("/jobs/upload", **upload, data={"shotKey": "cockpit"}) for _ in range(12)]
        codes = [running.status_code] + [r.status_code for r in responses]
        # queued photos wait on disk (not in memory); the running job has read and deleted its own
        waiting = [path.read_bytes() for path in settings.data_dir.glob(f"jobs/*/{UPLOAD_FILE_NAME}")]
        gate.set()
        done = [wait_for(client, r.json()["jobId"]) for r in [running, *responses[:11]]]
    assert codes == [202] * 12 + [503]
    assert responses[11].json()["error"]["code"] == "busy"
    assert waiting == [photo] * 11
    assert {job["status"] for job in done} == {"complete"}
    assert not list(settings.data_dir.glob(f"jobs/*/{UPLOAD_FILE_NAME}"))  # deleted when the jobs ran


def test_photos_of_never_run_upload_jobs_are_deleted_at_start_up(settings, fake_segmenter):
    from app.jobs.manager import UPLOAD_FILE_NAME, JobManager
    from app.presets import BackgroundProvider

    stale = settings.data_dir / "jobs" / ("c" * 32)
    stale.mkdir(parents=True)
    (stale / UPLOAD_FILE_NAME).write_bytes(b"photo of an earlier process")
    (stale / "result.jpg").write_bytes(b"x")
    manager = JobManager(settings, fake_segmenter, BackgroundProvider(settings))
    try:
        assert not (stale / UPLOAD_FILE_NAME).exists()  # that job can never run (jobs live in memory)
        assert (stale / "result.jpg").exists()  # results follow the normal TTL
    finally:
        manager.shutdown()


# --------------------------------------------------------------------------- storing a queued photo (disk full, event loop)

NO_SPACE_MESSAGE = "Der Bildverarbeitungs-Service hat gerade keinen Speicherplatz. Bitte später erneut versuchen."


@pytest.mark.parametrize(
    "errno_code, code, message",
    [
        (errno.ENOSPC, "storage_full", NO_SPACE_MESSAGE),
        (errno.EDQUOT, "storage_full", NO_SPACE_MESSAGE),
        (
            errno.EIO,
            "storage_error",
            "Das Foto konnte gerade nicht zur Bearbeitung angenommen werden. Bitte später erneut versuchen.",
        ),
    ],
)
def test_a_photo_that_cannot_be_stored_is_refused_with_a_german_json_error(
    make_client, settings, photo, monkeypatch, errno_code, code, message
):
    from app.jobs.manager import UPLOAD_FILE_NAME

    write_bytes, disk = Path.write_bytes, {"full": True}

    def failing_disk(self, data):
        if self.name == UPLOAD_FILE_NAME and disk["full"]:
            write_bytes(self, data[: len(data) // 2])  # half written, then the write fails
            raise OSError(errno_code, os.strerror(errno_code), str(self))
        return write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_disk)
    client, _ = make_client()
    jobs = settings.data_dir / "jobs"
    with client:
        refused = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")})
        left_behind = list(jobs.iterdir())
        queued = [job.to_response() for job in client.app.state.manager._jobs.values()]
        disk["full"] = False
        accepted = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, data={"shotKey": "cockpit"})
        done = wait_for(client, accepted.json()["jobId"])
    assert refused.status_code == 503
    assert refused.json() == {"error": {"code": code, "message": message}}  # JSON, not a plain-text 500
    assert left_behind == [] and queued == []  # nothing queued, no folder or partial photo left
    assert accepted.status_code == 202 and done["status"] == "complete"


@pytest.mark.parametrize("endpoint", ["upload", "contract"])
def test_a_job_folder_that_cannot_be_created_is_refused_with_a_german_json_error(
    make_client, settings, photo, monkeypatch, endpoint
):
    jobs = settings.data_dir / "jobs"
    mkdir = Path.mkdir

    def full_disk(self, *args, **kwargs):
        if self.parent == jobs:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(self))
        return mkdir(self, *args, **kwargs)

    client, _ = make_client()
    with client:
        monkeypatch.setattr(Path, "mkdir", full_disk)
        if endpoint == "upload":
            response = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")})
        else:
            response = client.post(
                "/jobs", json={"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
            )
        queued = list(client.app.state.manager._jobs)
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "storage_full", "message": NO_SPACE_MESSAGE}}
    assert queued == [] and list(jobs.iterdir()) == []  # never a "queued" job that cannot run


def test_uploads_still_work_after_the_job_folder_was_removed(make_client, settings, photo):
    import shutil

    client, _ = make_client()
    with client:
        shutil.rmtree(settings.data_dir / "jobs")  # e.g. an operator cleared the data folder
        response = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, data={"shotKey": "cockpit"})
        done = wait_for(client, response.json()["jobId"])
    assert response.status_code == 202 and done["status"] == "complete"


@pytest.mark.parametrize(
    "errno_code, code, message",
    [
        (errno.ENOSPC, "storage_full", NO_SPACE_MESSAGE),
        (errno.EDQUOT, "storage_full", NO_SPACE_MESSAGE),
        (
            errno.EIO,
            "storage_error",
            "Das Foto konnte gerade nicht zur Bearbeitung angenommen werden. Bitte später erneut versuchen.",
        ),
    ],
)
def test_an_upload_whose_body_cannot_be_spooled_to_disk_is_a_german_json_error(
    make_client, settings, monkeypatch, errno_code, code, message
):
    """A disk that also holds the multipart spool directory fails inside Starlette's body parser,
    before the upload handler runs. FastAPI wraps that OSError as HTTP 400; the processor must
    still answer the storage JSON error (so the app retries), not a plain-text / format error."""
    import tempfile

    from starlette import formparsers

    real_spool = tempfile.SpooledTemporaryFile

    class FullSpool(real_spool):
        def rollover(self):  # Starlette rolls the >1 MB body over to disk here
            raise OSError(errno_code, os.strerror(errno_code))

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", FullSpool, raising=False)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", FullSpool)
    big = b"\xff" * (3 * 1024 * 1024)  # > 1 MB: Starlette spools it to a temp file and rolls over
    client, _ = make_client()
    jobs = settings.data_dir / "jobs"
    with client:
        response = client.post("/jobs/upload", files={"file": ("car.jpg", big, "image/jpeg")}, data={"shotKey": "cockpit"})
        queued = list(client.app.state.manager._jobs)
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": {"code": code, "message": message}}
    assert queued == [] and list(jobs.iterdir()) == []  # nothing queued, no folder left behind


def test_a_malformed_body_is_still_a_400_not_a_storage_error(make_client):
    # the storage handler only maps body-parse OSErrors (a full disk) to 503 – a genuinely broken
    # multipart body keeps its normal 400, not the retryable storage message
    client, _ = make_client()
    with client:
        response = client.post(
            "/jobs/upload", content=b"not a real multipart body",
            headers={"Content-Type": "multipart/form-data; boundary=zzz"},
        )
    assert response.status_code == 400
    assert response.json().get("error", {}).get("code") != "storage_full"


def test_status_requests_are_answered_while_an_upload_is_written_to_disk(make_client, photo, monkeypatch):
    from app.jobs.manager import UPLOAD_FILE_NAME

    write_bytes = Path.write_bytes
    writing, release = threading.Event(), threading.Event()

    def slow_disk(self, data):
        if self.name == UPLOAD_FILE_NAME:
            writing.set()
            release.wait(timeout=30)
        return write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", slow_disk)
    client, _ = make_client()
    upload = {}

    def send_upload():
        files = {"file": ("car.jpg", photo, "image/jpeg")}
        upload["response"] = client.post("/jobs/upload", files=files, data={"shotKey": "cockpit"})

    with client:
        assert client.get("/health").status_code == 200  # preset and plates loaded
        sender = threading.Thread(target=send_upload)
        sender.start()
        # if the write ran on the event loop, /health would only be answered once this releases it
        timer = threading.Timer(10, release.set)
        try:
            assert writing.wait(timeout=20)
            timer.start()
            health = client.get("/health")
            answered_while_writing = not release.is_set()
        finally:
            release.set()
            timer.cancel()
            sender.join(timeout=30)
        done = wait_for(client, upload["response"].json()["jobId"])
    assert health.status_code == 200 and answered_while_writing
    assert upload["response"].status_code == 202 and done["status"] == "complete"


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
    assert done["metadata"]["showroomSource"] == "plates"
    assert done["metadata"]["plateUsed"] == "front_left_45"


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


@pytest.mark.parametrize("preset", ["autoexperten_dark", "original_plus"])
def test_unavailable_presets_are_refused_before_a_job_is_queued(make_client, settings, photo, preset):
    client, store = make_client()
    expected = {"error": {"code": "preset_unavailable", "message": "Dieser Bearbeitungsstil ist noch nicht verfügbar."}}
    with client:
        upload = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, data={"preset": preset})
        contract = client.post("/jobs", json={"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": preset})
        no_store, _ = make_client(store=None)
        with no_store:  # also without Supabase: the style is the reason, not the storage
            contract_without_store = no_store.post(
                "/jobs", json={"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": preset}
            )
    for response in (upload, contract, contract_without_store):
        assert response.status_code == 400
        assert response.json() == expected
    assert not list((settings.data_dir / "jobs").iterdir())  # nothing queued, no photo stored
    assert store.stored == []


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


def test_health_reports_a_missing_vehicle_detector_like_a_missing_model(settings, monkeypatch):
    """The detector is required next to the segmentation model: without its file the
    warm-up fails and /health answers 503, with the detector named in the details."""
    from app.pipeline.detection import create_detector
    from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter

    segmenter = OnnxSegmenter(
        MODEL_REGISTRY["isnet-general-use"],
        settings.models_dir,
        auto_download=False,
        detector=create_detector(settings),  # no file in the test models dir
    )
    monkeypatch.setattr(segmenter, "_session", object())  # the segmentation model itself is fine
    app = create_app(settings, segmenter=segmenter, store=None, warm_up=True)
    with TestClient(app) as client:
        deadline = time.time() + 5
        response = client.get("/health")
        while response.status_code == 200 and time.time() < deadline:
            time.sleep(0.05)
            response = client.get("/health")
    body = response.json()
    assert response.status_code == 503 and body["modelError"] is True and body["modelLoaded"] is False
    assert body["detector"] == "rf-detr-seg-medium" and body["detectorLoaded"] is False


def test_health_reports_a_detector_export_with_another_output_layout(settings, monkeypatch):
    """A loadable detector file with other outputs fails its blank test inference at warm-up:
    /health answers 503 – not 200 while every exterior job would fail."""
    import onnxruntime

    from app.pipeline import detection
    from app.pipeline.segmentation import MODEL_REGISTRY, OnnxSegmenter

    class _OtherExport:
        def get_inputs(self):
            return [type("Input", (), {"name": "input"})()]

        def run(self, _names, _feeds):
            return [np.zeros((1, 300, 4), np.float32), np.zeros((1, 300, 91), np.float32)]  # no masks

    monkeypatch.setattr(detection, "ensure_model", lambda *args, **kwargs: settings.models_dir / "other.onnx")
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *args, **kwargs: _OtherExport())
    segmenter = OnnxSegmenter(
        MODEL_REGISTRY["isnet-general-use"],
        settings.models_dir,
        auto_download=False,
        detector=detection.create_detector(settings),
    )
    monkeypatch.setattr(segmenter, "_session", object())  # the segmentation model itself is fine
    app = create_app(settings, segmenter=segmenter, store=None, warm_up=True)
    with TestClient(app) as client:
        deadline = time.time() + 5
        response = client.get("/health")
        while response.status_code == 200 and time.time() < deadline:
            time.sleep(0.05)
            response = client.get("/health")
    body = response.json()
    assert response.status_code == 503 and body["modelError"] is True and body["detectorLoaded"] is False


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


def test_showroom_preview_is_the_branded_plate_of_a_shot(make_client):
    client, _ = make_client(api_key="secret")
    auth = {"Authorization": "Bearer secret"}
    with client:
        assert client.get("/showroom/autoexperten_standard/front_left_45.jpg").status_code == 401
        response = client.get("/showroom/autoexperten_standard/rear_right_45.jpg?width=1200", headers=auth)
        assert client.get("/showroom/magic/front_left_45.jpg", headers=auth).status_code == 404
        assert client.get("/showroom/autoexperten_standard/cockpit.jpg", headers=auth).status_code == 404
        assert client.get("/showroom/autoexperten_standard.jpg", headers=auth).status_code == 404  # old single master
        assert client.get("/showroom/autoexperten_standard/front.jpg?width=99999", headers=auth).status_code == 400
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-showroom-source"] == "plates"
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (1200, 900)


def test_missing_plates_fail_every_exterior_job_with_a_clear_message(make_client, settings, photo):
    (settings.presets_dir / "autoexperten-standard" / "front_left_45.jpg").unlink()
    body = {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
    client, store = make_client()
    with client:
        health = client.get("/health").json()
        upload = wait_for(client, client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()["jobId"])
        contract = wait_for(client, client.post("/jobs", json=body).json()["jobId"])
        preview = client.get("/showroom/autoexperten_standard/front.jpg")
    assert health["showroomSource"] == "missing" and health["showroomPlaceholder"] is False
    assert "front_left_45.jpg" in health["showroomMasterError"]
    assert preview.status_code == 503 and preview.json()["error"]["message"] == "AutoExperten Showroom-Master fehlt."
    for done in (upload, contract):
        assert done["status"] == "failed"
        assert done["error"] == "AutoExperten Showroom-Master fehlt."
        assert done["metadata"]["errorCode"] == "showroom"
    assert store.stored == []  # nothing was written to vehicle-processed


def test_a_broken_plates_json_is_reported_as_missing_showroom(make_client, settings, photo):
    (settings.presets_dir / "autoexperten-standard" / "plates.json").write_text('{"version": 1, "plates": {}}')
    client, _ = make_client()
    with client:
        health = client.get("/health").json()
        done = wait_for(client, client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()["jobId"])
    assert health["showroomSource"] == "missing" and "plates missing" in health["showroomMasterError"]
    assert done["error"] == "AutoExperten Showroom-Master fehlt."


def test_interior_shots_do_not_need_the_plates(make_client, settings, photo):
    (settings.presets_dir / "autoexperten-standard" / "plates.json").unlink()
    client, _ = make_client()
    with client:
        job = client.post(
            "/jobs/upload", files={"file": ("cockpit.jpg", photo, "image/jpeg")}, data={"shotKey": "cockpit"}
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "complete"
    assert done["metadata"]["shotKind"] == "interior_passthrough"


def test_quality_gate_rejection_fails_the_job_with_code_and_german_message(make_client, fake_segmenter):
    from tests.conftest import make_vehicle_photo

    rgb, mask = make_vehicle_photo(2400, 1800, body_ratio=0.3)  # far away: 30 % of the photo width
    fake_segmenter.register(mask)
    small = encode_jpeg(rgb, 95)
    client, store = make_client(store=FakeStore(small, shot_key="left_side"))
    body = {"vehicleId": VEHICLE_ID, "photoId": PHOTO_ID, "preset": "autoexperten_standard"}
    with client:
        upload = wait_for(
            client,
            client.post("/jobs/upload", files={"file": ("car.jpg", small, "image/jpeg")}, data={"shotKey": "left_side"}).json()["jobId"],
        )
        contract = wait_for(client, client.post("/jobs", json=body).json()["jobId"])
        result = client.get(f"/jobs/{upload['jobId']}/result")
    for done in (upload, contract):
        assert done["status"] == "failed"
        assert done["error"] == "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren."
        assert done["metadata"]["errorCode"] == "vehicle_too_small"
        assert done["metadata"]["qualityGate"]["checks"]["upscale"]["passed"] is False
        assert done["metadata"]["qualityGate"]["plateUsed"] == "left_side"
        assert done["result"] is None
    assert result.status_code == 409  # no result file
    assert store.stored == []  # nothing stored


def test_small_photos_are_rejected_before_segmentation(make_client, fake_segmenter):
    from tests.conftest import make_vehicle_photo

    rgb, mask = make_vehicle_photo(1200, 900)
    fake_segmenter.register(mask)
    client, _ = make_client()
    with client:
        job = client.post(
            "/jobs/upload", files={"file": ("car.jpg", encode_jpeg(rgb), "image/jpeg")}, data={"shotKey": "left_side"}
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["metadata"]["errorCode"] == "source_resolution_too_low"
    assert done["error"] == "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen."
    assert fake_segmenter.calls == 0


def test_misconfigured_preset_is_reported_not_crashing(make_client, settings, photo):
    (settings.presets_dir / "autoexperten-standard.json").write_text("{broken", encoding="utf-8")
    client, _ = make_client()
    with client:
        health = client.get("/health").json()
        showroom = client.get("/showroom/autoexperten_standard/front.jpg")
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
    assert health["presetError"] and health["showroomSource"] is None
    assert showroom.status_code == 503
    assert done["status"] == "failed" and "nicht richtig eingerichtet" in done["error"]


def test_failed_jobs_still_list_their_debug_files(make_client):
    client, _ = make_client(debug=True)
    with client:
        # a photo without a car: decoding works, segmentation finds nothing
        job = client.post(
            "/jobs/upload", files={"file": ("wall.jpg", encode_jpeg(np.full((600, 800, 3), 200, np.uint8)), "image/jpeg")}
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert "original.jpg" in done["metadata"]["debugFiles"]


# --------------------------------------------------------------------------- PROC-1: debug files


def _assert_every_listed_debug_file_is_served(client, job: dict, settings) -> list[str]:
    listed = job["metadata"]["debugFiles"]
    served = client.get(f"/jobs/{job['jobId']}/debug").json()["files"]
    assert sorted(served) == sorted(listed)
    media = {".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json"}
    for name in listed:
        response = client.get(f"/jobs/{job['jobId']}/debug/{name}")
        assert response.status_code == 200, name
        assert response.headers["content-type"].startswith(media[name[name.rindex(".") :]]), name
    # every file the pipeline wrote is listed: a new debug image must be added to DEBUG_FILE_NAMES
    written = sorted(p.name for p in (settings.data_dir / "jobs" / job["jobId"] / "debug").iterdir())
    assert written == sorted(listed)
    return listed


def test_every_debug_file_listed_for_a_job_can_be_downloaded(make_client, settings, fake_segmenter, photo):
    from tests.conftest import make_vehicle_photo

    client, _ = make_client(debug=True)
    rgb, mask = make_vehicle_photo(2400, 1800, body_ratio=0.3)  # rejected by the quality gate
    fake_segmenter.register(mask)
    with client:
        submitted = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()
        complete = wait_for(client, submitted["jobId"])
        rejected = wait_for(
            client,
            client.post(
                "/jobs/upload", files={"file": ("car.jpg", encode_jpeg(rgb), "image/jpeg")}, data={"shotKey": "left_side"}
            ).json()["jobId"],
        )
        assert complete["status"] == "complete" and rejected["status"] == "failed"
        listed = _assert_every_listed_debug_file_is_served(client, complete, settings)
        _assert_every_listed_debug_file_is_served(client, rejected, settings)
    assert "grounding.jpg" in listed and "shadow.png" in listed


# --------------------------------------------------------------------------- PROC-5: photo size limits


def test_a_valid_photo_above_80_megapixels_is_processed_reduced(make_client):
    # 10400 × 7800 JPEG (81.1 MP, e.g. a 108 MP phone mode) – decoded at 1/2 instead of "unreadable"
    image = Image.new("L", (10_400, 7_800), 120)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    del image
    client, _ = make_client()
    with client:
        job = client.post(
            "/jobs/upload", files={"file": ("cockpit.jpg", buffer.getvalue(), "image/jpeg")}, data={"shotKey": "cockpit"}
        ).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "complete", done
    assert (done["metadata"]["source"]["width"], done["metadata"]["source"]["height"]) == (5_200, 3_900)
    assert max(done["result"]["width"], done["result"]["height"]) <= 3_200


@pytest.mark.parametrize(
    "size, code, message",
    [
        (
            (10_000, 9_000),  # 90 MP PNG: no reduced decode possible
            "image_too_large",
            "Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler Kamera-Auflösung neu aufnehmen.",
        ),
        (
            (50, 50),
            "source_resolution_too_low",
            "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen.",
        ),
    ],
)
def test_photo_size_limits_fail_with_their_own_code_and_message(make_client, fake_segmenter, size, code, message):
    buffer = io.BytesIO()
    Image.new("L", size, 120).save(buffer, "PNG")
    client, _ = make_client()
    with client:
        job = client.post("/jobs/upload", files={"file": ("car.png", buffer.getvalue(), "image/png")}).json()
        done = wait_for(client, job["jobId"])
    assert done["status"] == "failed"
    assert done["metadata"]["errorCode"] == code
    assert done["error"] == message
    assert fake_segmenter.calls == 0


def test_unreadable_files_keep_the_decode_code(make_client):
    client, _ = make_client()
    with client:
        job = client.post("/jobs/upload", files={"file": ("x.jpg", b"\xff\xd8\xff\xe0broken", "image/jpeg")}).json()
        done = wait_for(client, job["jobId"])
    assert done["metadata"]["errorCode"] == "decode"
    assert done["error"] == "Das Foto konnte nicht gelesen werden. Bitte ein JPEG-, PNG- oder HEIC-Foto verwenden."


# --------------------------------------------------------------------------- PROC-MISS-1 / PROC-3: configuration in /health


@pytest.mark.parametrize("logo", ["missing", "corrupt", "empty"])
def test_health_reports_an_unusable_official_logo_as_preset_error(make_client, settings, photo, logo):
    path = settings.brand_dir / "official" / "AutoExperten_Logo.png"
    if logo == "missing":
        path.unlink()
    elif logo == "corrupt":
        path.write_bytes(b"\x89PNG\r\n not really")
    else:
        Image.new("RGBA", (40, 20), (0, 0, 0, 0)).save(path)  # fully transparent
    client, _ = make_client(api_key="secret")
    auth = {"Authorization": "Bearer secret"}
    with client:
        health = client.get("/health", headers=auth).json()
        job = client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}, headers=auth).json()
        done = wait_for(client, job["jobId"], headers=auth)
    # the app blocks processing on presetError ("Showroom/Logo" configuration message)
    assert health["presetError"] and "official logo" in health["presetError"]
    assert health["showroomSource"] == "plates" and health["showroomMasterError"] is None
    assert done["status"] == "failed" and done["metadata"]["errorCode"] == "configuration"


def test_health_has_no_preset_error_for_the_shipped_configuration(make_client):
    client, _ = make_client()
    with client:
        assert client.get("/health").json()["presetError"] is None


def _edit_preset(settings, change) -> None:
    import json

    path = settings.presets_dir / "autoexperten-standard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("aspect", [[16, 9], [3, 2]])
def test_health_reports_an_output_aspect_that_does_not_fit_the_plates(make_client, settings, fake_segmenter, photo, aspect):
    from app.jobs.manager import ERROR_MESSAGES

    _edit_preset(settings, lambda d: d["output"].update(aspect=aspect))
    client, _ = make_client()
    with client:
        health = client.get("/health").json()
        showroom = client.get("/showroom/autoexperten_standard/front.jpg")
        done = wait_for(client, client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()["jobId"])
    assert "does not match the showroom plates" in health["presetError"]
    # the plates themselves are usable – no "Showroom-Master fehlt" anywhere
    assert health["showroomSource"] == "plates" and health["showroomMasterError"] is None
    assert showroom.status_code == 503 and showroom.json()["error"]["code"] == "configuration"
    assert done["status"] == "failed" and done["metadata"]["errorCode"] == "configuration"
    assert done["error"] == ERROR_MESSAGES["configuration"]
    assert fake_segmenter.calls == 0  # refused before the expensive segmentation


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["output"].update(aspect=[4, 0]),
        lambda d: d["output"].update(longEdgeMax=0),
        lambda d: d["placement"].update(minMargin=0.6),
    ],
)
def test_impossible_output_or_placement_is_a_configuration_error_not_a_crash(make_client, settings, photo, change):
    _edit_preset(settings, change)
    client, _ = make_client()
    with client:
        health = client.get("/health").json()
        showroom = client.get("/showroom/autoexperten_standard/front.jpg")
        done = wait_for(client, client.post("/jobs/upload", files={"file": ("car.jpg", photo, "image/jpeg")}).json()["jobId"])
    assert health["presetError"]
    assert showroom.status_code == 503 and showroom.json()["error"]["code"] == "configuration"
    assert done["status"] == "failed" and done["metadata"]["errorCode"] == "configuration"
    assert "nicht richtig eingerichtet" in done["error"]
