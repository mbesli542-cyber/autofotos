"""SupabasePhotoStore against a fake Supabase (httpx.MockTransport, no network)."""

from __future__ import annotations

import json
import logging
import traceback
from urllib.parse import unquote

import httpx
import pytest

from app.storage.base import OriginalPhoto, PhotoNotFoundError, StorageError
from app.storage.supabase import (
    ORIGINALS_BUCKET,
    PROCESSED_BUCKET,
    InvalidStorageRequestError,
    SupabasePhotoStore,
)

BASE = "https://abcdefgh.supabase.co"
KEY = "sb_secret_TESTSERVICEROLEKEY_0123456789abcdef"
VEHICLE = "11111111-2222-4333-8444-555555555555"
OTHER_VEHICLE = "99999999-8888-4777-8666-555555555555"
PHOTO = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
ARCHIVED_PHOTO = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
FOREIGN_PHOTO = "cccccccc-dddd-4eee-8fff-000000000000"
ORIGINAL_PATH = f"{VEHICLE}/front_left_45/{PHOTO}.jpg"
ORIGINAL_BYTES = b"\xff\xd8\xff\xe0original-jpeg-bytes"
PROCESSED_JPEG = b"\xff\xd8\xff\xdbprocessed-jpeg-bytes"


class FakeSupabase:
    """Minimal PostgREST + Storage simulation that records every request."""

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix  # path prefix of the Supabase base URL (reverse proxy)
        self.requests: list[httpx.Request] = []
        self.rows: list[dict] = [
            self._row(PHOTO, VEHICLE, "front_left_45", ORIGINAL_PATH),
            self._row(ARCHIVED_PHOTO, VEHICLE, "front", f"{VEHICLE}/front/{ARCHIVED_PHOTO}.jpg", archived=True),
            self._row(FOREIGN_PHOTO, OTHER_VEHICLE, "front", f"{OTHER_VEHICLE}/front/{FOREIGN_PHOTO}.jpg"),
        ]
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {
            (ORIGINALS_BUCKET, row["original_storage_path"]): (ORIGINAL_BYTES, "image/jpeg")
            for row in self.rows
        }
        #: (method, url-prefix) -> response factory overriding the simulation
        self.overrides: dict[tuple[str, str], object] = {}

    @staticmethod
    def _row(photo_id, vehicle_id, shot_key, path, *, archived=False):
        return {
            "id": photo_id,
            "vehicle_id": vehicle_id,
            "shot_key": shot_key,
            "original_storage_path": path,
            "processed_storage_path": None,
            "processed_preset": None,
            "archived_at": "2026-10-01T10:00:00+00:00" if archived else None,
        }

    # -- transport -----------------------------------------------------

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for (method, prefix), factory in self.overrides.items():
            if request.method == method and url.startswith(prefix):
                return factory(request) if callable(factory) else factory
        path = request.url.path.removeprefix(self.prefix)
        if path == "/rest/v1/vehicle_photos":
            return self._rest(request)
        if path.startswith("/storage/v1/object/"):
            return self._storage(request)
        return httpx.Response(404, json={"message": "no route"})

    def _matching_rows(self, request: httpx.Request) -> list[dict]:
        result = []
        for row in self.rows:
            ok = True
            for column, condition in request.url.params.multi_items():
                if column == "select":
                    continue
                op, _, value = condition.partition(".")
                if op == "eq":
                    ok &= str(row.get(column)) == value
                elif op == "is" and value == "null":
                    ok &= row.get(column) is None
                else:
                    return []
            if ok:
                result.append(row)
        return result

    def _rest(self, request: httpx.Request) -> httpx.Response:
        rows = self._matching_rows(request)
        if request.method == "GET":
            columns = request.url.params["select"].split(",")
            return httpx.Response(200, json=[{c: r[c] for c in columns} for r in rows])
        if request.method == "PATCH":
            changes = json.loads(request.content)
            for row in rows:
                row.update(changes)
            return httpx.Response(204, headers={"Content-Range": f"*/{len(rows)}"})
        return httpx.Response(405)

    def _storage(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.raw_path.decode("ascii").removeprefix(self.prefix).removeprefix("/storage/v1/object/")
        raw = raw.split("?", 1)[0]
        bucket, _, encoded = raw.partition("/")
        key = (bucket, unquote(encoded))
        if request.method == "GET":
            if key not in self.objects:
                # Older Storage API versions: HTTP 400 + statusCode "404".
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"})
            data, content_type = self.objects[key]
            return httpx.Response(200, content=data, headers={"Content-Type": content_type})
        if request.method == "POST":
            if key in self.objects and request.headers.get("x-upsert") != "true":
                return httpx.Response(400, json={"statusCode": "409", "error": "Duplicate", "message": "exists"})
            self.objects[key] = (request.content, request.headers["content-type"])
            return httpx.Response(200, json={"Key": f"{bucket}/{key[1]}"})
        return httpx.Response(405)


@pytest.fixture
def fake() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def store(fake: FakeSupabase):
    client = fake.client()
    s = SupabasePhotoStore(BASE, KEY, client=client)
    yield s
    s.close()
    client.close()


def assert_auth(request: httpx.Request) -> None:
    assert request.headers["apikey"] == KEY
    assert request.headers["authorization"] == f"Bearer {KEY}"


def assert_secret_free(exc: BaseException) -> None:
    rendered = "".join(traceback.format_exception(exc))
    assert KEY not in str(exc)
    assert KEY not in repr(exc)
    assert KEY not in rendered


# ------------------------------------------------------------- fetch_original


def test_fetch_original_happy_path(store: SupabasePhotoStore, fake: FakeSupabase):
    original = store.fetch_original(VEHICLE, PHOTO)

    assert original == OriginalPhoto(
        data=ORIGINAL_BYTES,
        shot_key="front_left_45",
        original_storage_path=ORIGINAL_PATH,
        content_type="image/jpeg",
    )
    assert len(fake.requests) == 2
    lookup, download = fake.requests
    assert lookup.method == "GET"
    assert str(lookup.url) == (
        f"{BASE}/rest/v1/vehicle_photos?select=id,vehicle_id,shot_key,original_storage_path"
        f"&id=eq.{PHOTO}&vehicle_id=eq.{VEHICLE}&archived_at=is.null"
    )
    assert lookup.headers["accept"] == "application/json"
    assert_auth(lookup)
    assert download.method == "GET"
    assert str(download.url) == f"{BASE}/storage/v1/object/vehicle-originals/{ORIGINAL_PATH}"
    assert_auth(download)


def test_fetch_original_normalises_uppercase_ids(store: SupabasePhotoStore, fake: FakeSupabase):
    original = store.fetch_original(VEHICLE.upper(), PHOTO.upper())
    assert original.data == ORIGINAL_BYTES
    assert f"id=eq.{PHOTO}&vehicle_id=eq.{VEHICLE}" in str(fake.requests[0].url)


def test_fetch_original_keeps_base_path_prefix_and_strips_trailing_slash():
    fake = FakeSupabase(prefix="/supa")
    with fake.client() as client, SupabasePhotoStore("https://db.example.com/supa/", KEY, client=client) as store:
        store.fetch_original(VEHICLE, PHOTO)
    assert str(fake.requests[0].url).startswith("https://db.example.com/supa/rest/v1/vehicle_photos?select=")
    assert str(fake.requests[1].url).startswith("https://db.example.com/supa/storage/v1/object/vehicle-originals/")


def test_fetch_original_not_found(store: SupabasePhotoStore, fake: FakeSupabase):
    unknown = "dddddddd-eeee-4fff-8000-111111111111"
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, unknown)
    assert len(fake.requests) == 1  # no storage download attempted


def test_archived_photo_is_filtered_by_query(store: SupabasePhotoStore, fake: FakeSupabase):
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, ARCHIVED_PHOTO)
    params = fake.requests[0].url.params
    assert params["archived_at"] == "is.null"
    assert params["id"] == f"eq.{ARCHIVED_PHOTO}"
    assert len(fake.requests) == 1


def test_foreign_photo_is_filtered_by_vehicle(store: SupabasePhotoStore, fake: FakeSupabase):
    # FOREIGN_PHOTO exists, but belongs to OTHER_VEHICLE.
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, FOREIGN_PHOTO)
    params = fake.requests[0].url.params
    assert params["vehicle_id"] == f"eq.{VEHICLE}"
    assert len(fake.requests) == 1


def test_row_of_other_vehicle_returned_anyway_is_rejected(store: SupabasePhotoStore, fake: FakeSupabase):
    # Defence in depth if the filter were ever ignored by the server.
    fake.overrides[("GET", f"{BASE}/rest/v1/")] = httpx.Response(
        200,
        json=[{"id": FOREIGN_PHOTO, "vehicle_id": OTHER_VEHICLE, "shot_key": "front",
               "original_storage_path": f"{OTHER_VEHICLE}/front/{FOREIGN_PHOTO}.jpg"}],
    )
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, FOREIGN_PHOTO)
    assert len(fake.requests) == 1


INVALID_IDS = [
    "",
    "not-a-uuid",
    "../../etc/passwd",
    f"{PHOTO}\n",
    f" {PHOTO}",
    f"{PHOTO}&id=neq.x",
    f"{PHOTO}/..",
    PHOTO.replace("-", ""),
    "gggggggg-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    None,
    123,
]


@pytest.mark.parametrize("bad_id", INVALID_IDS)
def test_invalid_ids_are_rejected_without_http(store: SupabasePhotoStore, fake: FakeSupabase, bad_id):
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(bad_id, PHOTO)
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, bad_id)
    with pytest.raises(PhotoNotFoundError):
        store.store_processed(
            vehicle_id=bad_id, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG
        )
    with pytest.raises(PhotoNotFoundError):
        store.store_processed(
            vehicle_id=VEHICLE, photo_id=bad_id, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    "bad_path",
    [
        f"{VEHICLE}/../{OTHER_VEHICLE}/front/x.jpg",
        f"{VEHICLE}/front/../../x.jpg",
        f"{VEHICLE}/./front/x.jpg",
        f"/{VEHICLE}/front/x.jpg",
        f"{VEHICLE}//x.jpg",
        f"{VEHICLE}/front/",
        f"{VEHICLE}\\..\\x.jpg",
        f"{VEHICLE}/front/x\n.jpg",
        f"{OTHER_VEHICLE}/front/{PHOTO}.jpg",
        VEHICLE,
        "",
        None,
        "x" * 2000,
    ],
)
def test_path_traversal_and_foreign_paths_are_rejected(store: SupabasePhotoStore, fake: FakeSupabase, bad_path):
    fake.rows[0]["original_storage_path"] = bad_path
    with pytest.raises(StorageError) as info:
        store.fetch_original(VEHICLE, PHOTO)
    assert not isinstance(info.value, PhotoNotFoundError)
    assert len(fake.requests) == 1  # only the lookup – no storage request was built
    assert all("/storage/" not in str(r.url) for r in fake.requests)


def test_storage_path_segments_are_percent_encoded(store: SupabasePhotoStore, fake: FakeSupabase):
    odd = f"{VEHICLE}/front left/ä?#%.jpg"
    fake.rows[0]["original_storage_path"] = odd
    fake.objects[(ORIGINALS_BUCKET, odd)] = (ORIGINAL_BYTES, "image/jpeg; charset=binary")
    original = store.fetch_original(VEHICLE, PHOTO)
    download = fake.requests[1]
    assert download.url.raw_path.decode() == (
        f"/storage/v1/object/vehicle-originals/{VEHICLE}/front%20left/%C3%A4%3F%23%25.jpg"
    )
    assert download.url.query == b""
    assert original.original_storage_path == odd
    assert original.content_type == "image/jpeg"


def test_invalid_shot_key_in_record_is_rejected(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.rows[0]["shot_key"] = "../front"
    with pytest.raises(StorageError):
        store.fetch_original(VEHICLE, PHOTO)
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"statusCode": "404", "code": "NoSuchKey", "error": "not_found", "message": "Object not found"}),
        httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"}),
    ],
)
def test_missing_original_object_is_not_found(store: SupabasePhotoStore, fake: FakeSupabase, response):
    fake.overrides[("GET", f"{BASE}/storage/v1/object/")] = response
    with pytest.raises(PhotoNotFoundError):
        store.fetch_original(VEHICLE, PHOTO)


def test_missing_bucket_is_storage_error_not_not_found(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.overrides[("GET", f"{BASE}/storage/v1/object/")] = httpx.Response(
        400, json={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}
    )
    with pytest.raises(StorageError) as info:
        store.fetch_original(VEHICLE, PHOTO)
    assert not isinstance(info.value, PhotoNotFoundError)


def test_empty_original_is_storage_error(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.objects[(ORIGINALS_BUCKET, ORIGINAL_PATH)] = (b"", "image/jpeg")
    with pytest.raises(StorageError):
        store.fetch_original(VEHICLE, PHOTO)


# ------------------------------------------------------------ store_processed


def test_store_processed_uploads_to_processed_bucket_and_patches_row(store: SupabasePhotoStore, fake: FakeSupabase):
    path = store.store_processed(
        vehicle_id=VEHICLE,
        photo_id=PHOTO,
        shot_key="front_left_45",
        preset="autoexperten_standard",
        jpeg=PROCESSED_JPEG,
    )

    expected_path = f"{VEHICLE}/autoexperten_standard/front_left_45/{PHOTO}.jpg"
    assert path == expected_path
    assert len(fake.requests) == 2
    upload, patch = fake.requests

    assert upload.method == "POST"
    assert str(upload.url) == f"{BASE}/storage/v1/object/vehicle-processed/{expected_path}"
    assert upload.headers["content-type"] == "image/jpeg"
    assert upload.headers["x-upsert"] == "true"
    assert upload.content == PROCESSED_JPEG
    assert_auth(upload)

    assert patch.method == "PATCH"
    assert str(patch.url) == f"{BASE}/rest/v1/vehicle_photos?id=eq.{PHOTO}&vehicle_id=eq.{VEHICLE}"
    assert patch.headers["content-type"] == "application/json"
    assert "return=minimal" in patch.headers["prefer"]
    assert json.loads(patch.content) == {
        "processed_storage_path": expected_path,
        "processed_preset": "autoexperten_standard",
    }
    assert_auth(patch)

    # Result stored separately; the original object and record path are untouched.
    assert fake.objects[(PROCESSED_BUCKET, expected_path)] == (PROCESSED_JPEG, "image/jpeg")
    assert fake.objects[(ORIGINALS_BUCKET, ORIGINAL_PATH)] == (ORIGINAL_BYTES, "image/jpeg")
    assert fake.rows[0]["processed_storage_path"] == expected_path
    assert fake.rows[0]["processed_preset"] == "autoexperten_standard"
    assert fake.rows[0]["original_storage_path"] == ORIGINAL_PATH


def test_store_processed_can_regenerate_and_never_writes_originals(store: SupabasePhotoStore, fake: FakeSupabase):
    for preset in ("autoexperten_standard", "autoexperten_dark", "original_plus", "autoexperten_standard"):
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front_left_45", preset=preset, jpeg=PROCESSED_JPEG)
    writes = [r for r in fake.requests if r.method != "GET" and "/storage/" in r.url.path]
    assert len(writes) == 4
    assert all(r.url.path.startswith("/storage/v1/object/vehicle-processed/") for r in writes)
    assert not any(ORIGINALS_BUCKET in str(r.url) for r in fake.requests)


@pytest.mark.parametrize("shot_key", ["Front", "front-left", "../front", "a" * 41, "", "front\n", None])
def test_store_processed_rejects_invalid_shot_key(store: SupabasePhotoStore, fake: FakeSupabase, shot_key):
    with pytest.raises(InvalidStorageRequestError):
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key=shot_key, preset="autoexperten_standard", jpeg=PROCESSED_JPEG)
    assert fake.requests == []


@pytest.mark.parametrize("preset", ["original", "AUTOEXPERTEN_STANDARD", "../autoexperten_standard", "", None])
def test_store_processed_rejects_invalid_preset(store: SupabasePhotoStore, fake: FakeSupabase, preset):
    with pytest.raises(InvalidStorageRequestError):
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset=preset, jpeg=PROCESSED_JPEG)
    assert fake.requests == []


@pytest.mark.parametrize("jpeg", [b"", b"\x89PNG\r\n\x1a\n", "not bytes", None])
def test_store_processed_rejects_non_jpeg_payload(store: SupabasePhotoStore, fake: FakeSupabase, jpeg):
    with pytest.raises(InvalidStorageRequestError):
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=jpeg)
    assert fake.requests == []


def test_invalid_request_error_is_storage_and_value_error():
    assert issubclass(InvalidStorageRequestError, StorageError)
    assert issubclass(InvalidStorageRequestError, ValueError)


def test_store_processed_reports_vanished_record(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.rows.clear()  # e.g. vehicle deleted while processing → PATCH matches 0 rows
    with pytest.raises(PhotoNotFoundError):
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG)


def test_store_processed_accepts_patch_without_content_range(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.overrides[("PATCH", f"{BASE}/rest/v1/")] = httpx.Response(204)
    path = store.store_processed(
        vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="original_plus", jpeg=PROCESSED_JPEG
    )
    assert path == f"{VEHICLE}/original_plus/front/{PHOTO}.jpg"


# --------------------------------------------------------------- error mapping


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 500, 503])
def test_lookup_http_errors_map_to_storage_error(store: SupabasePhotoStore, fake: FakeSupabase, status):
    fake.overrides[("GET", f"{BASE}/rest/v1/")] = httpx.Response(status, json={"code": "PGRST205", "message": "x"})
    with pytest.raises(StorageError) as info:
        store.fetch_original(VEHICLE, PHOTO)
    # A failing lookup (also 404 = table missing) is a backend problem, not a missing photo.
    assert not isinstance(info.value, PhotoNotFoundError)
    assert f"HTTP {status}" in str(info.value)
    assert len(fake.requests) == 1


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_download_http_errors_map_to_storage_error(store: SupabasePhotoStore, fake: FakeSupabase, status):
    fake.overrides[("GET", f"{BASE}/storage/v1/object/")] = httpx.Response(status, text="boom")
    with pytest.raises(StorageError) as info:
        store.fetch_original(VEHICLE, PHOTO)
    assert not isinstance(info.value, PhotoNotFoundError)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(413, json={"statusCode": "413", "error": "Payload too large"}),
        httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found"}),
        httpx.Response(404, json={"error": "Bucket not found"}),
        httpx.Response(500, text="internal"),
    ],
)
def test_upload_errors_map_to_storage_error_and_skip_patch(store: SupabasePhotoStore, fake: FakeSupabase, response):
    fake.overrides[("POST", f"{BASE}/storage/v1/object/")] = response
    with pytest.raises(StorageError) as info:
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG)
    assert not isinstance(info.value, PhotoNotFoundError)
    assert [r.method for r in fake.requests] == ["POST"]
    assert fake.rows[0]["processed_storage_path"] is None


@pytest.mark.parametrize("status", [400, 401, 404, 500])
def test_patch_errors_map_to_storage_error(store: SupabasePhotoStore, fake: FakeSupabase, status):
    fake.overrides[("PATCH", f"{BASE}/rest/v1/")] = httpx.Response(status, json={"code": "P0001", "message": "nope"})
    with pytest.raises(StorageError) as info:
        store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG)
    assert not isinstance(info.value, PhotoNotFoundError)
    assert "P0001" in str(info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"rows": []}),
        httpx.Response(200, json=["not-a-row"]),
    ],
)
def test_unexpected_lookup_payload_is_storage_error(store: SupabasePhotoStore, fake: FakeSupabase, response):
    fake.overrides[("GET", f"{BASE}/rest/v1/")] = response
    with pytest.raises(StorageError) as info:
        store.fetch_original(VEHICLE, PHOTO)
    assert not isinstance(info.value, PhotoNotFoundError)


def test_redirects_are_not_followed(store: SupabasePhotoStore, fake: FakeSupabase):
    fake.overrides[("GET", f"{BASE}/rest/v1/")] = httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    with pytest.raises(StorageError):
        store.fetch_original(VEHICLE, PHOTO)
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(f"connection refused while sending apikey={KEY}"),
        httpx.ReadTimeout(f"timed out {KEY}"),
        httpx.RemoteProtocolError(f"protocol {KEY}"),
    ],
)
def test_network_errors_map_to_storage_error_without_secret(fake: FakeSupabase, error, caplog):
    def explode(request: httpx.Request) -> httpx.Response:
        raise error

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(explode)) as client:
        store = SupabasePhotoStore(BASE, KEY, client=client)
        with pytest.raises(StorageError) as info:
            store.fetch_original(VEHICLE, PHOTO)
        assert_secret_free(info.value)
        with pytest.raises(StorageError) as info:
            store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG)
        assert_secret_free(info.value)
    assert KEY not in caplog.text


# --------------------------------------------------------------------- secrets


def test_secret_echoed_by_server_never_reaches_exceptions_or_logs(store: SupabasePhotoStore, fake: FakeSupabase, caplog):
    caplog.set_level(logging.DEBUG)
    echo = httpx.Response(401, json={"code": KEY, "error": KEY, "message": f"Invalid key {KEY}", "hint": KEY})
    fake.overrides[("GET", f"{BASE}/rest/v1/")] = echo
    fake.overrides[("POST", f"{BASE}/storage/v1/object/")] = echo
    for call in (
        lambda: store.fetch_original(VEHICLE, PHOTO),
        lambda: store.store_processed(vehicle_id=VEHICLE, photo_id=PHOTO, shot_key="front", preset="autoexperten_standard", jpeg=PROCESSED_JPEG),
    ):
        with pytest.raises(StorageError) as info:
            call()
        assert_secret_free(info.value)
    assert KEY not in caplog.text
    assert KEY not in repr(store)


def test_invalid_configuration_never_echoes_the_key():
    with pytest.raises(ValueError) as info:
        SupabasePhotoStore(BASE, f"{KEY} trailing")
    assert KEY not in str(info.value)
    for bad_key in ("", "with\nnewline", "ümlaut-key", None):
        with pytest.raises(ValueError):
            SupabasePhotoStore(BASE, bad_key)  # type: ignore[arg-type]
    for bad_url in ("", "ftp://example.com", "https://", "https://x.supabase.co?apikey=1", f"https://user:{KEY}@x.supabase.co"):
        with pytest.raises(ValueError) as info:
            SupabasePhotoStore(bad_url, KEY)
        assert KEY not in str(info.value)
    with pytest.raises(ValueError):
        SupabasePhotoStore(BASE, KEY, timeout=0)


# ------------------------------------------------------------------- lifecycle


def test_close_closes_owned_client_only(fake: FakeSupabase):
    owned = SupabasePhotoStore(BASE, KEY)
    owned_client = owned._client
    owned.close()
    owned.close()  # idempotent
    assert owned_client.is_closed

    with fake.client() as injected:
        store = SupabasePhotoStore(BASE, KEY, client=injected)
        store.close()
        assert not injected.is_closed
        with pytest.raises(StorageError):
            store.fetch_original(VEHICLE, PHOTO)
    assert fake.requests == []


def test_context_manager_closes_owned_client():
    with SupabasePhotoStore(BASE, KEY) as store:
        client = store._client
        assert not client.is_closed
    assert client.is_closed
