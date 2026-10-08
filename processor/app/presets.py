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
import logging
import re
import threading
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .config import Settings
from .showroom.branding import BrandingAssetError, BrandingConfig, BrandingLayout, apply_branding, logo_stamp

log = logging.getLogger(__name__)

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


#: Fields that are fractions of the frame (or opacities) and must lie in 0..1.
_FRACTIONS = {
    "center_x", "ground_line", "width_ratio", "max_height_ratio", "min_margin",
    "top", "max_width", "max_height", "cap_height", "opacity", "clearance", "light_match",
    "shadow_opacity", "shadow_offset", "shadow_blur",
    "contact_opacity", "contact_height", "ambient_opacity", "ambient_height", "ambient_blur",
}  # fmt: skip
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TOP_LEVEL_KEYS = {
    "id", "name", "description", "background", "branding", "output",
    "placement", "shotPlacement", "shadow", "vehicleAdjustments",
}  # fmt: skip
_BACKGROUND_KEYS = {"image", "fallbackImage", "floorHorizon", "notes"}


class PresetConfigError(PresetError):
    """The preset JSON is invalid (typo, wrong type or value out of range)."""


def _check_value(owner: str, key: str, name: str, value, default):
    where = f"{owner}.{key}"
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise PresetConfigError(f"{where} must be true or false")
    elif isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PresetConfigError(f"{where} must be a number")
        if isinstance(default, int) and not isinstance(value, int):
            raise PresetConfigError(f"{where} must be a whole number")
        value = type(default)(value)
        if name in _FRACTIONS and not 0.0 <= value <= 1.0:
            raise PresetConfigError(f"{where} must be between 0 and 1 (fraction of the frame), got {value}")
        if value < 0:
            raise PresetConfigError(f"{where} must not be negative")
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise PresetConfigError(f"{where} must be a text")
        if name == "color" and not _HEX_COLOUR.match(value):
            raise PresetConfigError(f"{where} must be a colour like #403F3F")
        if name == "weight" and value not in ("semibold", "medium"):
            raise PresetConfigError(f"{where} must be 'semibold' or 'medium'")
    elif isinstance(default, tuple):
        if not isinstance(value, (list, tuple)) or len(value) != len(default):
            raise PresetConfigError(f"{where} must be a list of {len(default)} numbers")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in value):
            raise PresetConfigError(f"{where} must contain non-negative numbers")
        value = tuple(type(d)(v) for d, v in zip(default, value))
    return value


def _merge(default, data, owner: str | None = None):
    """Dataclass `default` with the (strictly validated) camelCase fields of `data`."""
    owner = owner or type(default).__name__
    if data is None:
        return default
    if not isinstance(data, dict):
        raise PresetConfigError(f"{owner} must be an object")
    names = {f.name for f in fields(default)}
    changes = {}
    for key, value in data.items():
        snake = _camel_to_snake(key)
        if snake not in names:
            raise PresetConfigError(f"Unknown preset field '{key}' in {owner}")
        changes[snake] = _check_value(owner, key, snake, value, getattr(default, snake))
    return replace(default, **changes)


def _build(cls, data, owner: str | None = None):
    return _merge(cls(), data, owner)


def parse_branding(data: dict | None) -> BrandingConfig:
    default = BrandingConfig()
    if data is None:
        return default
    if not isinstance(data, dict):
        raise PresetConfigError("branding must be an object")
    nested = {"logo", "city", "website", "phone", "mount"}
    flat = {k: v for k, v in data.items() if k not in nested}
    config = _merge(default, flat, "branding")
    return replace(
        config,
        **{name: _merge(getattr(default, name), data[name], f"branding.{name}") for name in nested if name in data},
    )


def parse_preset(data: dict) -> Preset:
    if not isinstance(data, dict):
        raise PresetConfigError("preset must be a JSON object")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise PresetConfigError(f"Unknown preset section(s): {', '.join(sorted(unknown))}")
    for key in ("id", "name"):
        if not isinstance(data.get(key), str):
            raise PresetConfigError(f"preset needs a text '{key}'")
    background = data.get("background")
    if not isinstance(background, dict) or not isinstance(background.get("image"), str):
        raise PresetConfigError("preset needs background.image")
    unknown = set(background) - _BACKGROUND_KEYS
    if unknown:
        raise PresetConfigError(f"Unknown preset field(s) in background: {', '.join(sorted(unknown))}")
    fallback = background.get("fallbackImage")
    if fallback is not None and not isinstance(fallback, str):
        raise PresetConfigError("background.fallbackImage must be a text")
    horizon = _check_value("background", "floorHorizon", "floor_horizon", background.get("floorHorizon", 0.62), 0.62)
    if not 0.05 <= horizon <= 0.95:
        raise PresetConfigError("background.floorHorizon must be a fraction of the height (e.g. 0.62)")

    for section in ("branding", "output", "placement", "shotPlacement", "shadow", "vehicleAdjustments"):
        if section in data and not isinstance(data[section], dict):
            raise PresetConfigError(f"{section} must be an object")
    base_placement = _build(Placement, data.get("placement"), "placement")
    shot_data = data.get("shotPlacement") or {}
    if not isinstance(shot_data, dict):
        raise PresetConfigError("shotPlacement must be an object")
    shots = {}
    for key, value in shot_data.items():
        if key not in EXTERIOR_SHOTS:
            raise PresetConfigError(
                f"Unknown shot '{key}' in shotPlacement (allowed: {', '.join(sorted(EXTERIOR_SHOTS))})"
            )
        if not isinstance(value, dict):
            raise PresetConfigError(f"shotPlacement.{key} must be an object")
        shots[key] = _merge(base_placement, value, f"shotPlacement.{key}")
    branding = parse_branding(data.get("branding"))
    for name, placement in [("placement", base_placement), *((f"shotPlacement.{k}", v) for k, v in shots.items())]:
        if placement.ground_line < horizon + 0.1:
            raise PresetConfigError(
                f"{name}.groundLine ({placement.ground_line}) must be well below background.floorHorizon ({horizon})"
            )
    if branding.enabled:
        lowest = max(
            branding.logo.top + branding.logo.max_height,
            *(t.top + 2 * t.cap_height for t in (branding.city, branding.website, branding.phone) if t.text.strip()),
        )
        if lowest + branding.clearance > horizon:
            raise PresetConfigError("branding must stay on the wall above background.floorHorizon")
    return Preset(
        id=data["id"],
        name=data["name"],
        background_image=background["image"],
        fallback_image=fallback,
        floor_horizon=horizon,
        branding=branding,
        output=_build(OutputConfig, data.get("output"), "output"),
        placement=base_placement,
        shot_placement=shots,
        shadow=_build(ShadowConfig, data.get("shadow"), "shadow"),
        adjustments=_build(VehicleAdjustments, data.get("vehicleAdjustments"), "vehicleAdjustments"),
    )


def load_preset(settings: Settings, preset_id: str) -> Preset:
    filename = PRESET_FILES.get(preset_id)
    if not filename:
        raise PresetError(f"Preset '{preset_id}' is not available yet")
    path = settings.presets_dir / filename
    if not path.is_file():
        raise PresetConfigError(f"Preset file missing: {path}")
    try:
        return parse_preset(json.loads(path.read_text(encoding="utf-8")))
    except PresetConfigError as error:
        raise PresetConfigError(f"{path.name}: {error}") from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise PresetConfigError(f"{path.name}: {error}") from None


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
        #: master path -> (mtime_ns, error) for a master that could not be read
        self._master_errors: dict[str, tuple[int, str]] = {}
        self._warned: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    def master_path(self, preset: Preset) -> Path:
        return self._settings.presets_dir / preset.background_image

    def fallback_path(self, preset: Preset) -> Path | None:
        if not preset.fallback_image:
            return None
        return self._settings.presets_dir / preset.fallback_image

    @staticmethod
    def _stamp(path: Path | None) -> int:
        try:
            return path.stat().st_mtime_ns if path is not None else 0
        except OSError:
            return 0

    def master_error(self, preset: Preset) -> str | None:
        """Why the master photo cannot be used although it exists (else None)."""
        path = self.master_path(preset)
        known = self._master_errors.get(str(path))
        if known and known[0] == self._stamp(path):
            return known[1]
        return None

    def source(self, preset: Preset) -> str:
        if self.master_path(preset).is_file() and self.master_error(preset) is None:
            return SHOWROOM_MASTER
        return SHOWROOM_FALLBACK

    def is_placeholder(self, preset: Preset) -> bool:
        """True while the procedural fallback is used (kept for API compatibility)."""
        return self.source(preset) == SHOWROOM_FALLBACK

    def get(self, preset: Preset, width: int, height: int) -> Showroom:
        """Branded showroom background, cover-fitted to (width, height)."""
        master = self.master_path(preset)
        logo = self._settings.brand_dir / preset.branding.logo.file
        with self._lock:
            source = self.source(preset)
            path = master if source == SHOWROOM_MASTER else self.fallback_path(preset)
            key = (
                preset.id, width, height, source, self._stamp(path), logo_stamp(logo),
                preset.floor_horizon, repr(preset.branding),
            )  # fmt: skip
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            base = None
            if source == SHOWROOM_MASTER:
                try:
                    base = self._load_photo(master, width, height)
                except Exception as error:  # unreadable/half-copied/unsupported master
                    log.error("Showroom master %s cannot be read (%s) – using the fallback", master, error)
                    self._master_errors[str(master)] = (self._stamp(master), str(error))
                    source, path = SHOWROOM_FALLBACK, self.fallback_path(preset)
            if base is None:
                base = self._load_fallback(preset, path, width, height)
            rgb = np.ascontiguousarray(np.asarray(base.convert("RGB"), dtype=np.uint8))
            try:
                rgb, layout = apply_branding(rgb, preset.branding, self._settings.brand_dir)
            except BrandingAssetError as error:
                raise PresetConfigError(str(error)) from None
            rgb.setflags(write=False)
            showroom = Showroom(rgb=rgb, source=source, branding=layout, floor_horizon=preset.floor_horizon)
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = showroom
            return showroom

    def _load_photo(self, path: Path, width: int, height: int) -> Image.Image:
        with Image.open(path) as image:
            photo = _to_srgb(ImageOps.exif_transpose(image))
            photo.load()
        photo = photo.convert("RGB")
        stamp = (str(path), self._stamp(path))
        if stamp not in self._warned:
            self._warned.add(stamp)
            aspect, wanted = photo.width / photo.height, width / height
            if abs(aspect / wanted - 1.0) > 0.01:
                log.warning(
                    "Showroom photo %s is %dx%d, not %d:%d – it is centre-cropped (floorHorizon refers to the crop)",
                    path.name, photo.width, photo.height, width, height,
                )  # fmt: skip
            if photo.width < width:
                log.warning("Showroom photo %s (%d px wide) is upscaled to %d px", path.name, photo.width, width)
        return ImageOps.fit(photo, (width, height), Image.LANCZOS, centering=(0.5, 0.5))

    def _load_fallback(self, preset: Preset, path: Path | None, width: int, height: int) -> Image.Image:
        if path is not None and path.is_file():
            try:
                return self._load_photo(path, width, height)
            except Exception as error:
                log.error("Showroom fallback %s cannot be read (%s) – rendering it", path, error)
        # neither master nor stored fallback: render the emergency plate
        from .showroom.fallback import render_fallback_showroom

        return render_fallback_showroom(width, height, floor_horizon=preset.floor_horizon)


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert an embedded colour profile (Display P3, Adobe RGB, CMYK …) to sRGB."""
    icc = image.info.get("icc_profile")
    if not icc:
        return image
    try:
        import io

        from PIL import ImageCms

        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(source) or "").lower():
            return image
        src = image if image.mode in ("RGB", "CMYK") else image.convert("RGB")
        return ImageCms.profileToProfile(
            src,
            source,
            ImageCms.createProfile("sRGB"),
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            outputMode="RGB",
        )
    except Exception:
        return image
