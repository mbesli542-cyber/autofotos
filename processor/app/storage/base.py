"""Storage contract for jobs coming from the Next.js app (JSON contract).

The Next.js `RealImageProcessor` sends `{vehicleId, photoId, preset, …}`. The
processor then loads the ORIGINAL from the private `vehicle-originals` bucket
and writes the result as a SEPARATE file to `vehicle-processed`. Originals are
never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StorageError(Exception):
    """Storage backend failed (network, permissions, …)."""


class PhotoNotFoundError(StorageError):
    """The photo does not exist, is archived or does not belong to the vehicle."""


@dataclass(frozen=True)
class OriginalPhoto:
    data: bytes
    shot_key: str
    original_storage_path: str
    content_type: str | None = None


class PhotoStore(Protocol):
    def fetch_original(self, vehicle_id: str, photo_id: str) -> OriginalPhoto:
        """Download the active (non-archived) original of a photo."""
        ...

    def store_processed(
        self,
        *,
        vehicle_id: str,
        photo_id: str,
        shot_key: str,
        preset: str,
        jpeg: bytes,
    ) -> str:
        """Upload the processed JPEG and link it to the photo record.

        Returns the bucket-relative path, e.g.
        `{vehicleId}/{preset}/{shotKey}/{photoId}.jpg` (same scheme as
        `buildProcessedStoragePath` in the Next.js app).
        """
        ...


def processed_storage_path(vehicle_id: str, preset: str, shot_key: str, photo_id: str) -> str:
    return f"{vehicle_id}/{preset}/{shot_key}/{photo_id}.jpg"
