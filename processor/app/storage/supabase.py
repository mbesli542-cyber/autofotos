"""Supabase implementation of :class:`~app.storage.base.PhotoStore`.

Talks to the Supabase REST (PostgREST) and Storage APIs with the service role
key using a synchronous ``httpx`` client:

* ``fetch_original`` reads the active (non-archived) ``vehicle_photos`` row of
  the given vehicle and downloads its original from the private
  ``vehicle-originals`` bucket (read only).
* ``store_processed`` uploads the result to the private ``vehicle-processed``
  bucket (``{vehicleId}/{preset}/{shotKey}/{photoId}.jpg``, upsert because a
  processed file may be regenerated) and links it via
  ``processed_storage_path`` / ``processed_preset``.

Safety rules:

* Originals are never written: the only write to Storage targets
  ``vehicle-processed``.
* Ids are validated as UUIDs and every path segment is percent-encoded before
  any URL is built; ``..``/empty segments and foreign vehicle folders are
  rejected.
* The service role key is only sent as ``apikey``/``Authorization`` headers. It
  is never logged and never part of an exception message (messages contain
  only fixed text, HTTP status codes and sanitised Supabase error codes).
* Redirects are never followed (the key must not leave the configured host).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from .base import OriginalPhoto, PhotoNotFoundError, StorageError, processed_storage_path

log = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_PRESETS",
    "InvalidStorageRequestError",
    "ORIGINALS_BUCKET",
    "PROCESSED_BUCKET",
    "SupabasePhotoStore",
]

ORIGINALS_BUCKET = "vehicle-originals"
PROCESSED_BUCKET = "vehicle-processed"

#: Same values as the `vehicle_photos.processed_preset` check constraint.
ALLOWED_PRESETS = frozenset({"autoexperten_standard", "autoexperten_dark", "original_plus"})

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
#: Same rule as the `vehicle_photos.shot_key` check constraint.
_SHOT_KEY_RE = re.compile(r"[a-z0-9_]{1,40}")
#: Supabase error codes (e.g. "PGRST205", "not_found", "23514") we may echo in messages.
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.\- ]{1,48}")
_MAX_PATH_LENGTH = 1024
_JPEG_MAGIC = b"\xff\xd8\xff"


class InvalidStorageRequestError(StorageError, ValueError):
    """The caller passed an invalid preset, shot key or JPEG payload."""


class SupabasePhotoStore:
    """`PhotoStore` backed by Supabase (service role, server side only)."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = _normalize_base_url(url)
        if not _is_valid_key(service_role_key):
            raise ValueError("Supabase service role key is missing or malformed")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        self._secret = service_role_key
        self._auth_headers = {"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}"}
        self._timeout = float(timeout)
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=self._timeout, follow_redirects=False)
        self._closed = False

    def __repr__(self) -> str:  # never expose the key
        return f"SupabasePhotoStore(url={self._base!r})"

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Close the HTTP client if this store created it (injected clients stay open)."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SupabasePhotoStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ PhotoStore

    def fetch_original(self, vehicle_id: str, photo_id: str) -> OriginalPhoto:
        vid = _require_uuid(vehicle_id)
        pid = _require_uuid(photo_id)

        lookup_url = (
            f"{self._base}/rest/v1/vehicle_photos"
            "?select=id,vehicle_id,shot_key,original_storage_path"
            f"&id=eq.{pid}&vehicle_id=eq.{vid}&archived_at=is.null"
        )
        response = self._send("GET", lookup_url, operation="photo lookup", headers={"Accept": "application/json"})
        if not response.is_success:
            raise self._http_error(response, "photo lookup")
        rows = _parse_json(response, "photo lookup")
        if not isinstance(rows, list):
            raise StorageError("Supabase photo lookup returned an unexpected response")
        if not rows:
            raise PhotoNotFoundError("Photo not found, archived or not part of this vehicle")
        row = rows[0]
        if not isinstance(row, dict):
            raise StorageError("Supabase photo lookup returned an unexpected response")
        # Defence in depth: the filter must have matched exactly this photo of this vehicle.
        if str(row.get("id", "")).lower() != pid or str(row.get("vehicle_id", "")).lower() != vid:
            raise PhotoNotFoundError("Photo not found, archived or not part of this vehicle")

        shot_key = row.get("shot_key")
        if not isinstance(shot_key, str) or not _SHOT_KEY_RE.fullmatch(shot_key):
            raise StorageError("Photo record has an invalid shot key")
        original_path = row.get("original_storage_path")
        encoded_path = _encode_storage_path(original_path, vehicle_id=vid)

        download_url = f"{self._base}/storage/v1/object/{ORIGINALS_BUCKET}/{encoded_path}"
        response = self._send("GET", download_url, operation="original download")
        if not response.is_success:
            if _is_storage_not_found(response):
                raise PhotoNotFoundError("Original file not found in storage")
            raise self._http_error(response, "original download")
        data = response.content
        if not data:
            raise StorageError("Original file in storage is empty")
        return OriginalPhoto(
            data=data,
            shot_key=shot_key,
            original_storage_path=str(original_path),
            content_type=_media_type(response),
        )

    def store_processed(
        self,
        *,
        vehicle_id: str,
        photo_id: str,
        shot_key: str,
        preset: str,
        jpeg: bytes,
    ) -> str:
        vid = _require_uuid(vehicle_id)
        pid = _require_uuid(photo_id)
        if not isinstance(shot_key, str) or not _SHOT_KEY_RE.fullmatch(shot_key):
            raise InvalidStorageRequestError("Invalid shot key")
        if not isinstance(preset, str) or preset not in ALLOWED_PRESETS:
            raise InvalidStorageRequestError("Invalid processing preset")
        if not isinstance(jpeg, (bytes, bytearray, memoryview)):
            raise InvalidStorageRequestError("Processed image must be JPEG bytes")
        payload = bytes(jpeg)
        if not payload.startswith(_JPEG_MAGIC):
            raise InvalidStorageRequestError("Processed image must be a non-empty JPEG")

        path = processed_storage_path(vid, preset, shot_key, pid)
        encoded_path = _encode_storage_path(path, vehicle_id=vid)

        # The ONLY storage write of this class – always into the processed bucket.
        upload_url = f"{self._base}/storage/v1/object/{PROCESSED_BUCKET}/{encoded_path}"
        response = self._send(
            "POST",
            upload_url,
            operation="processed upload",
            headers={"Content-Type": "image/jpeg", "x-upsert": "true"},
            content=payload,
        )
        if not response.is_success:
            raise self._http_error(response, "processed upload")

        update_url = f"{self._base}/rest/v1/vehicle_photos?id=eq.{pid}&vehicle_id=eq.{vid}"
        body = json.dumps({"processed_storage_path": path, "processed_preset": preset}).encode("utf-8")
        response = self._send(
            "PATCH",
            update_url,
            operation="photo update",
            # count=exact lets PostgREST report the number of updated rows in
            # Content-Range ("*/0" = the record vanished in the meantime).
            headers={"Content-Type": "application/json", "Prefer": "return=minimal, count=exact"},
            content=body,
        )
        if not response.is_success:
            raise self._http_error(response, "photo update")
        if _content_range_total(response) == 0:
            raise PhotoNotFoundError("Photo record no longer exists")
        return path

    # ------------------------------------------------------------ http

    def _send(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        if self._closed:
            raise StorageError("Supabase photo store is closed")
        request_headers = {**(headers or {}), **self._auth_headers}
        try:
            response = self._client.request(
                method,
                url,
                headers=request_headers,
                content=content,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            log.warning("Supabase %s timed out (%s)", operation, type(exc).__name__)
            raise StorageError(f"Supabase {operation} timed out") from None
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("Supabase %s failed: network error (%s)", operation, type(exc).__name__)
            raise StorageError(f"Supabase {operation} failed (network error: {type(exc).__name__})") from None
        if response.is_redirect:
            log.warning("Supabase %s returned an unexpected redirect (HTTP %s)", operation, response.status_code)
            raise StorageError(f"Supabase {operation} failed (unexpected redirect, HTTP {response.status_code})")
        return response

    def _http_error(self, response: httpx.Response, operation: str) -> StorageError:
        status = response.status_code
        code = _error_code(response)
        if status in (401, 403):
            reason = "access denied – check the service role key"
        elif status == 404:
            reason = "resource not found – check URL, table and bucket"
        elif status == 413:
            reason = "file too large"
        elif status >= 500:
            reason = "Supabase unavailable"
        else:
            reason = "request rejected"
        detail = f"HTTP {status}" + (f", {code}" if code else "")
        message = self._scrub(f"Supabase {operation} failed: {reason} ({detail})")
        log.warning("%s", message)
        return StorageError(message)

    def _scrub(self, text: str) -> str:
        return text.replace(self._secret, "[redacted]") if self._secret else text


# ---------------------------------------------------------------- helpers


def _normalize_base_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Supabase URL is missing")
    try:
        parsed = httpx.URL(url.strip())
    except httpx.InvalidURL:
        raise ValueError("Supabase URL is invalid") from None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("Supabase URL must be an http(s) URL")
    if parsed.query or parsed.fragment or parsed.userinfo:
        raise ValueError("Supabase URL must not contain credentials, a query or a fragment")
    netloc = parsed.netloc.decode("ascii")
    path = parsed.raw_path.decode("ascii").split("?", 1)[0].rstrip("/")
    return f"{parsed.scheme}://{netloc}{path}"


def _is_valid_key(key: object) -> bool:
    return (
        isinstance(key, str)
        and bool(key)
        and key.isascii()
        and all(33 <= ord(char) <= 126 for char in key)
    )


def _require_uuid(value: object) -> str:
    """Canonical lower-case UUID or PhotoNotFoundError (nothing else may reach a URL)."""
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise PhotoNotFoundError("Invalid photo or vehicle id")
    return value.lower()


def _encode_storage_path(path: object, *, vehicle_id: str) -> str:
    """Percent-encode a bucket-relative object path segment by segment.

    Keeps the ``/`` separators, rejects absolute paths, empty/``.``/``..``
    segments, backslashes and control characters, and requires the first
    segment to be the vehicle id (the storage policies key access on it).
    """
    if not isinstance(path, str) or not path or len(path) > _MAX_PATH_LENGTH:
        raise StorageError("Photo record has an invalid storage path")
    if "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise StorageError("Photo record has an invalid storage path")
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise StorageError("Photo record has an invalid storage path")
    if len(segments) < 2 or segments[0].lower() != vehicle_id:
        raise StorageError("Storage path does not belong to this vehicle")
    return "/".join(quote(segment, safe="") for segment in segments)


def _parse_json(response: httpx.Response, operation: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise StorageError(f"Supabase {operation} returned invalid JSON") from None


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_code(response: httpx.Response) -> str | None:
    body = _json_body(response)
    if not body:
        return None
    for field in ("code", "error"):
        value = body.get(field)
        if isinstance(value, (str, int)) and _ERROR_CODE_RE.fullmatch(str(value)):
            return str(value)
    return None


def _is_storage_not_found(response: httpx.Response) -> bool:
    """True if Storage says the OBJECT is missing.

    Storage reports that as HTTP 404 or (older versions) HTTP 400 with
    ``statusCode: "404"``. A missing bucket is a misconfiguration, not a
    missing photo, and stays a plain StorageError.
    """
    body = _json_body(response) or {}
    details = " ".join(str(body.get(field, "")) for field in ("code", "error", "message")).lower()
    if "bucket" in details:
        return False
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    return str(body.get("statusCode", "")) == "404" or str(body.get("error", "")).lower() in {
        "not_found",
        "not found",
        "nosuchkey",
    }


def _media_type(response: httpx.Response) -> str | None:
    value = response.headers.get("content-type")
    if not value:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None


def _content_range_total(response: httpx.Response) -> int | None:
    """Total from a PostgREST Content-Range header ("0-0/1", "*/0"); None if unknown."""
    value = response.headers.get("content-range", "")
    _, _, total = value.rpartition("/")
    total = total.strip()
    return int(total) if total.isdigit() else None
