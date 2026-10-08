"""Processing presets ("Bearbeitungsstile") and their showroom background.

A preset is a JSON file in `<assets>/presets/` (default: the Next.js
`public/presets/` folder), e.g. `autoexperten-standard.json`. It references ONE
fixed master showroom photo (`background.image`, e.g.
`autoexperten-standard-showroom.jpg`) that every exterior vehicle of the preset
is placed on. The master is the EMPTY showroom without branding; the official
logo and the texts are composited deterministically on top
(`app/showroom/branding.py`, `branding` section of the JSON).

If the master photo is missing, the procedural emergency fallback
(`background.fallbackImage`, rendered by app/showroom/fallback.py) is used and
every job carries the `showroom_fallback` warning. As soon as the master file
exists it is always used.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .config import Settings
from .showroom.branding import BrandingConfig, BrandingLayout, apply_branding

SHOWROOM_MASTER = "master"
SHOWROOM_FALLBACK = "fallback"


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
    #: FINAL master showroom photo (empty, no branding), relative to the presets directory.
    background_image: str
    #: Procedural emergency fallback, used only while the master is missing.
    fallback_image: str | None
    #: Fraction of the frame height where wall meets floor in the background.
    floor_horizon: float
    branding: BrandingConfig = field(default_factory=BrandingConfig)
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


def _merge(default, data: dict | None):
    """Dataclass `default` with the camelCase fields of `data` applied."""
    if not data:
        return default
    names = {f.name for f in fields(default)}
    changes = {}
    for key, value in data.items():
        snake = _camel_to_snake(key)
        if snake not in names:
            raise PresetError(f"Unknown preset field '{key}' for {type(default).__name__}")
        changes[snake] = value
    return replace(default, **changes)


def parse_branding(data: dict | None) -> BrandingConfig:
    default = BrandingConfig()
    if not data:
        return default
    nested = {"logo", "city", "website", "phone", "mount"}
    flat = {k: v for k, v in data.items() if k not in nested}
    config = _merge(default, flat)
    return replace(
        config,
        **{name: _merge(getattr(default, name), data.get(name)) for name in nested if name in data},
    )


def parse_preset(data: dict) -> Preset:
    background = data.get("background", {})
    if "image" not in background:
        raise PresetError("Preset without background.image")
    base_placement = _build(Placement, data.get("placement"))
    shots = {
        key: Placement(**{**base_placement.__dict__, **_build_dict(Placement, value)})
        for key, value in (data.get("shotPlacement") or {}).items()
    }
    return Preset(
        id=data["id"],
        name=data["name"],
        background_image=background["image"],
        fallback_image=background.get("fallbackImage"),
        floor_horizon=float(background.get("floorHorizon", 0.62)),
        branding=parse_branding(data.get("branding")),
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


@dataclass(frozen=True)
class Showroom:
    """The branded background for one output size."""

    #: sRGB uint8 (H, W, 3), read-only.
    rgb: np.ndarray
    #: "master" (final showroom photo) or "fallback" (procedural emergency plate).
    source: str
    #: Where the branding elements were drawn.
    branding: BrandingLayout
    floor_horizon: float

    @property
    def is_fallback(self) -> bool:
        return self.source == SHOWROOM_FALLBACK


class BackgroundProvider:
    """Loads the showroom (master, else fallback), brands it, caches per size."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache: dict[tuple, Showroom] = {}
        self._lock = threading.Lock()

    def master_path(self, preset: Preset) -> Path:
        return self._settings.presets_dir / preset.background_image

    def fallback_path(self, preset: Preset) -> Path | None:
        if not preset.fallback_image:
            return None
        return self._settings.presets_dir / preset.fallback_image

    def source(self, preset: Preset) -> str:
        return SHOWROOM_MASTER if self.master_path(preset).is_file() else SHOWROOM_FALLBACK

    def is_placeholder(self, preset: Preset) -> bool:
        """True while the procedural fallback is used (kept for API compatibility)."""
        return self.source(preset) == SHOWROOM_FALLBACK

    def get(self, preset: Preset, width: int, height: int) -> Showroom:
        """Branded showroom background, cover-fitted to (width, height)."""
        master = self.master_path(preset)
        source = self.source(preset)
        path = master if source == SHOWROOM_MASTER else self.fallback_path(preset)
        stamp = path.stat().st_mtime_ns if path is not None and path.is_file() else 0
        key = (preset.id, width, height, source, stamp, repr(preset.branding))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            base = self._load_base(preset, path, width, height)
            rgb = np.ascontiguousarray(np.asarray(base.convert("RGB"), dtype=np.uint8))
            rgb, layout = apply_branding(rgb, preset.branding, self._settings.brand_dir)
            rgb.setflags(write=False)
            showroom = Showroom(rgb=rgb, source=source, branding=layout, floor_horizon=preset.floor_horizon)
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = showroom
            return showroom

    def _load_base(self, preset: Preset, path: Path | None, width: int, height: int) -> Image.Image:
        if path is not None and path.is_file():
            with Image.open(path) as image:
                photo = ImageOps.exif_transpose(image)
                photo = _to_srgb(photo).convert("RGB")
            return ImageOps.fit(photo, (width, height), Image.LANCZOS, centering=(0.5, 0.5))
        # neither master nor stored fallback: render the emergency plate
        from .showroom.fallback import render_fallback_showroom

        return render_fallback_showroom(width, height, floor_horizon=preset.floor_horizon)


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert an embedded colour profile (e.g. Display P3, Adobe RGB) to sRGB."""
    icc = image.info.get("icc_profile")
    if not icc:
        return image
    try:
        import io

        from PIL import ImageCms

        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(source) or "").lower():
            return image
        return ImageCms.profileToProfile(
            image.convert("RGB"),
            source,
            ImageCms.createProfile("sRGB"),
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            outputMode="RGB",
        )
    except Exception:
        return image
