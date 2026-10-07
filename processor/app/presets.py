"""Processing presets ("Bearbeitungsstile") and their showroom background.

A preset is a JSON file in `<assets>/presets/` (default: the Next.js
`public/presets/` folder), e.g. `autoexperten-standard.json`. It references a
fixed, reusable master showroom image. Replacing that image (same file name)
changes the look of every future result without touching code.

If the master image does not exist yet, a deterministic placeholder showroom is
rendered (app/showroom/placeholder.py) so the pipeline still works.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .config import Settings


class PresetError(Exception):
    """Unknown or unavailable preset."""


@dataclass(frozen=True)
class Placement:
    """Where the vehicle goes in the output frame (fractions of width/height)."""

    center_x: float = 0.5
    #: Lowest vehicle pixel (tyre contact) as a fraction of the output height.
    ground_line: float = 0.84
    #: Target vehicle width as a fraction of the output width.
    width_ratio: float = 0.78
    #: The vehicle may never be taller than this fraction of the output height.
    max_height_ratio: float = 0.66
    #: Minimum free margin on every side (fraction of the respective dimension).
    min_margin: float = 0.04
    #: Never enlarge the source vehicle more than this factor (avoids blur).
    max_upscale: float = 1.6


@dataclass(frozen=True)
class ShadowConfig:
    #: Darkness of the contact shadow right under the tyres (0..1).
    contact_opacity: float = 0.82
    #: Vertical reach of the contact shadow, fraction of vehicle width.
    contact_height: float = 0.012
    #: Darkness of the broad soft shadow under the car body (0..1).
    ambient_opacity: float = 0.42
    #: Height of the ambient ellipse, fraction of vehicle width.
    ambient_height: float = 0.075
    #: Width of the ambient ellipse relative to the vehicle width.
    ambient_spread: float = 1.04
    #: Blur of the ambient shadow, fraction of vehicle width.
    ambient_blur: float = 0.03
    #: Shadow tint (sRGB) – warm dark brown suits a wooden floor.
    color: tuple[int, int, int] = (34, 25, 18)


@dataclass(frozen=True)
class VehicleAdjustments:
    """Configured maxima for corrections on the VEHICLE layer.

    These are additionally clamped by hard limits in app/pipeline/light.py –
    configuration can never make vehicle corrections aggressive.
    """

    exposure_ev_limit: float = 0.25
    contrast_limit: float = 0.0
    white_balance_limit: float = 0.02
    light_wrap: float = 0.10


@dataclass(frozen=True)
class OutputConfig:
    aspect: tuple[int, int] = (4, 3)
    long_edge_min: int = 2400
    long_edge_max: int = 3200
    jpeg_quality: int = 92


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    #: Master showroom image, relative to the presets directory.
    background_image: str
    #: True while the background is the procedural placeholder.
    background_is_placeholder: bool
    #: Official logo for the brand wall, relative to the brand directory.
    logo_image: str
    #: Fraction of the frame height where wall meets floor (used by the placeholder).
    floor_horizon: float
    output: OutputConfig = field(default_factory=OutputConfig)
    placement: Placement = field(default_factory=Placement)
    shot_placement: dict[str, Placement] = field(default_factory=dict)
    shadow: ShadowConfig = field(default_factory=ShadowConfig)
    adjustments: VehicleAdjustments = field(default_factory=VehicleAdjustments)

    def placement_for(self, shot_key: str | None) -> Placement:
        if shot_key and shot_key in self.shot_placement:
            return self.shot_placement[shot_key]
        return self.placement


PRESET_FILES = {
    "autoexperten_standard": "autoexperten-standard.json",
}

#: Exterior shots get the showroom treatment; everything else is kept as is.
EXTERIOR_SHOTS = frozenset(
    {
        "front_left_45",
        "front",
        "front_right_45",
        "left_side",
        "right_side",
        "rear_left_45",
        "rear",
        "rear_right_45",
    }
)


def _camel_to_snake(name: str) -> str:
    out = []
    for char in name:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def _build(cls, data: dict | None):
    if not data:
        return cls()
    kwargs = {}
    for key, value in data.items():
        snake = _camel_to_snake(key)
        if snake not in cls.__dataclass_fields__:
            raise PresetError(f"Unknown preset field '{key}' for {cls.__name__}")
        kwargs[snake] = tuple(value) if isinstance(value, list) else value
    return cls(**kwargs)


def parse_preset(data: dict) -> Preset:
    background = data.get("background", {})
    base_placement = _build(Placement, data.get("placement"))
    shots = {
        key: Placement(**{**base_placement.__dict__, **_build_dict(Placement, value)})
        for key, value in (data.get("shotPlacement") or {}).items()
    }
    return Preset(
        id=data["id"],
        name=data["name"],
        background_image=background["image"],
        background_is_placeholder=bool(background.get("placeholder", False)),
        logo_image=data.get("logo", "official/AutoExperten_Logo.png"),
        floor_horizon=float(data.get("floorHorizon", 0.62)),
        output=_build(OutputConfig, data.get("output")),
        placement=base_placement,
        shot_placement=shots,
        shadow=_build(ShadowConfig, data.get("shadow")),
        adjustments=_build(VehicleAdjustments, data.get("vehicleAdjustments")),
    )


def _build_dict(cls, data: dict) -> dict:
    return {
        _camel_to_snake(k): v
        for k, v in data.items()
        if _camel_to_snake(k) in cls.__dataclass_fields__
    }


def load_preset(settings: Settings, preset_id: str) -> Preset:
    filename = PRESET_FILES.get(preset_id)
    if not filename:
        raise PresetError(f"Preset '{preset_id}' is not available yet")
    path = settings.presets_dir / filename
    if not path.is_file():
        raise PresetError(f"Preset file missing: {path}")
    return parse_preset(json.loads(path.read_text(encoding="utf-8")))


class BackgroundProvider:
    """Loads (or renders) the showroom background and caches it per output size."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache: dict[tuple[str, int, int], np.ndarray] = {}
        self._lock = threading.Lock()

    def master_path(self, preset: Preset) -> Path:
        return self._settings.presets_dir / preset.background_image

    def is_placeholder(self, preset: Preset) -> bool:
        return preset.background_is_placeholder or not self.master_path(preset).is_file()

    def get(self, preset: Preset, width: int, height: int) -> np.ndarray:
        """Background as sRGB uint8 array (H, W, 3), cover-fitted to the size."""
        key = (preset.id, width, height)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            image = self._load_master(preset, width, height)
            array = np.asarray(image.convert("RGB"), dtype=np.uint8)
            array.setflags(write=False)
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = array
            return array

    def _load_master(self, preset: Preset, width: int, height: int) -> Image.Image:
        path = self.master_path(preset)
        if path.is_file():
            master = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            return ImageOps.fit(master, (width, height), Image.LANCZOS, centering=(0.5, 0.5))
        from .showroom.placeholder import ShowroomBrandText, render_placeholder_showroom

        return render_placeholder_showroom(
            width,
            height,
            logo_path=self._settings.brand_dir / preset.logo_image,
            brand=ShowroomBrandText(),
            floor_horizon=preset.floor_horizon,
        )
