"""Optional debug artifacts (PROCESSOR_DEBUG=true).

Files per job: original.jpg, mask.png, geometry.jpg (mask outline, bbox,
tyre contacts), vehicle-transparent.png, background.jpg (branded plate),
composite-before-shadow.jpg, shadow.png (grounding darkening), final.jpg,
metadata.json. Never enable debug output on a publicly reachable instance.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .export import encode_jpeg, encode_png

DEBUG_FILE_NAMES = (
    "original.jpg",
    "mask.png",
    "geometry.jpg",
    "vehicle-transparent.png",
    "background.jpg",
    "composite-before-shadow.jpg",
    "shadow.png",
    "final.jpg",
    "metadata.json",
)


class DebugSink:
    def __init__(self, directory: Path | None):
        self.directory = directory
        self.files: list[str] = []
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def _write(self, name: str, data: bytes) -> None:
        if self.directory is None:
            return
        (self.directory / name).write_bytes(data)
        if name not in self.files:
            self.files.append(name)

    def rgb(self, name: str, rgb: np.ndarray) -> None:
        if self.enabled:
            self._write(name, encode_jpeg(rgb, 90))

    def gray(self, name: str, values: np.ndarray) -> None:
        if self.enabled:
            data = values if values.dtype == np.uint8 else np.clip(values * 255.0 + 0.5, 0, 255).astype(np.uint8)
            self._write(name, encode_png(data))

    def rgba(self, name: str, rgb: np.ndarray, alpha: np.ndarray) -> None:
        if self.enabled:
            a = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
            self._write(name, encode_png(np.dstack([rgb, a])))

    def raw(self, name: str, data: bytes) -> None:
        if self.enabled:
            self._write(name, data)

    def json(self, name: str, payload: dict) -> None:
        if self.enabled:
            self._write(name, json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8"))


NULL_DEBUG = DebugSink(None)
