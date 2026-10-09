"""Processing presets ("Bearbeitungsstile") and their showroom plates.

A preset is a JSON file in `<assets>/presets/` (default: the Next.js
`public/presets/` folder), e.g. `autoexperten-standard.json`. Its background is
ONE physical 3D showroom rendered from eight shot-specific cameras
(`background.plates` → `plates.json`, rendered by processor/showroom3d/): every
exterior shot is placed onto the plate of its own camera, so perspective,
floor and light fit the photographed angle. The plates are EMPTY (no branding);
the official logo and the texts are composited per plate in wall space
(app/showroom/wall_branding.py, `branding` section of the JSON).

There is no fallback: while the plate set is missing or invalid every exterior
job fails with "AutoExperten Showroom-Master fehlt.".
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field, fields, replace
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from PIL import Image, ImageOps

from .config import Settings
from .showroom.branding import (
    BrandingAssetError,
    BrandingConfig,
    BrandingLayout,
    logo_stamp,
)
from .showroom.plates import Plate, PlateSet, PlateSetError, load_plate_set
from .showroom.wall_branding import apply_wall_branding

log = logging.getLogger(__name__)

#: /health and job metadata: the complete plate set is usable / it is not.
SHOWROOM_PLATES = "plates"
SHOWROOM_MISSING = "missing"


class PresetError(Exception):
    """Unknown or unavailable preset."""


@dataclass(frozen=True)
class Placement:
    """Size limits for the vehicle (fractions of the output frame).

    Target width, horizontal centre and ground line come from the plate
    (`vehicle.targetWidthRatio`, centred, lowest proxy tyre contact).
    """

    #: The vehicle may never be taller than this fraction of the output height.
    max_height_ratio: float = 0.66
    #: Minimum free margin on every side (fraction of the respective dimension).
    min_margin: float = 0.03
    #: Warn when the limits shrink the vehicle below this fraction of the target width.
    min_target_fraction: float = 0.9


@dataclass(frozen=True)
class ShadowConfig:
    """Grounding v3 (app/pipeline/grounding.py) – opacities 0..1, distances in floor metres."""

    #: Darkness of the compact contact shadow right under each tyre's contact patch
    #: (size from physical tyre dimensions and the fitted car pose).
    contact_opacity: float = 0.92
    #: Darkness of the thin occlusion crease where a tyre meets the floor.
    crease_opacity: float = 0.85
    #: Darkness of the floor seen under the body, right below the lower outline (deep under the car).
    underbody_opacity: float = 0.85
    #: Darkness at the floor line (the car footprint's near edge, below bumpers and sills).
    edge_opacity: float = 0.4
    #: Beyond the floor line the underbody shadow fades out over this many metres.
    underbody_falloff: float = 0.12
    #: Scale of the plate's rendered proxy-car shadow (soft ambient occlusion).
    ambient_opacity: float = 0.35
    #: The ambient occlusion is kept only within this many metres of the footprint.
    ambient_reach: float = 0.6
    #: The floor keeps at least this fraction of its light (no black holes).
    min_floor_light: float = 0.04


@dataclass(frozen=True)
class ReflectionConfig:
    """The vehicle's reflection on the lacquered floor (app/pipeline/reflection.py).

    Inside the car's mirror image the plate's own floor reflection (LED streaks, wall
    glow – the plate's reflection pass) is removed and the mirrored ORIGINAL vehicle
    pixels are added as a colour-neutral specular term with the plate's measured
    reflectance.
    """

    enabled: bool = True
    #: × the plate's measured floor reflectance (plates.json "reflectance"); 1 = as rendered.
    strength: float = 1.0
    #: Upper limit of the reflectance applied to the car's mirror image.
    max_reflectance: float = 0.2
    #: Reflectance for plates without a reflection pass.
    default_reflectance: float = 0.1
    #: The reflection (and the occlusion of the plate's own reflection) fades out between
    #: half of and this many × the vehicle height below the floor line.
    fade: float = 1.0
    #: Gloss blur: Gaussian sigma (px) per px of distance from the floor line (horizontal;
    #: vertically 4× as much – glossy floors stretch reflections towards the camera).
    blur_rate: float = 0.04


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
class QualityConfig:
    """Quality gate (app/pipeline/quality.py): photos that would give a bad listing image are rejected."""

    #: Source photos with a shorter long edge are rejected (source_resolution_too_low).
    min_source_long_edge: int = 1600
    #: The vehicle may be enlarged at most this much (else vehicle_too_small / source_resolution_too_low).
    max_upscale: float = 1.5
    #: Below this vehicle width (fraction of the source width) an upscale failure means "too small".
    min_vehicle_width_ratio: float = 0.45
    #: Mask confidence: soft edge pixels per solid vehicle pixel, separated large parts, coverage.
    max_uncertain_fraction: float = 0.3
    max_split_parts: int = 3
    min_coverage: float = 0.004
    max_coverage: float = 0.85
    #: Mask area / bbox area outside this range is not a car silhouette.
    min_fill_ratio: float = 0.3
    #: Solid vehicle pixels within this many px of the left/right/bottom border = cropped.
    border_px: int = 2
    #: ... and the top border, when touched over this fraction of the vehicle width.
    top_touch_ratio: float = 0.15
    #: Plausible tyre contacts needed for 3/4 and side shots.
    min_contacts: int = 2
    #: Contact rise / vehicle width relative to the plate's proxy (3/4 shots). A much
    #: larger rise = camera far above the plate camera. minRiseFactor 0 = off: a small
    #: rise also comes from 3/4 photos taken closer to the side, which are fine.
    max_rise_factor: float = 2.6
    min_rise_factor: float = 0.0
    #: Extra absolute tolerance for the rise (fraction of the vehicle width).
    rise_tolerance: float = 0.03
    #: Vehicle bbox aspect relative to the plate's proxy.
    min_aspect_factor: float = 0.5
    max_aspect_factor: float = 1.9
    #: Photo from above (3/4 shots): a steep floor (contact rise > highViewRiseFactor × the
    #: plate's, outer-zone rise > highViewRiseFactor + 0.3) together with a tall bbox
    #: (aspect < highViewAspectFactor × the proxy's – roof and bonnet seen from above).
    high_view_rise_factor: float = 1.7
    high_view_aspect_factor: float = 0.8


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    #: plates.json of the 3D showroom plate set, relative to the presets directory.
    plates: str
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    placement: Placement = field(default_factory=Placement)
    shot_placement: dict[str, Placement] = field(default_factory=dict)
    shadow: ShadowConfig = field(default_factory=ShadowConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    adjustments: VehicleAdjustments = field(default_factory=VehicleAdjustments)
    quality: QualityConfig = field(default_factory=QualityConfig)

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
    "max_height_ratio", "min_margin", "min_target_fraction",
    "top", "max_width", "max_height", "cap_height", "opacity", "clearance", "light_match",
    "shadow_opacity", "shadow_offset", "shadow_blur",
    "contact_opacity", "crease_opacity", "underbody_opacity", "edge_opacity", "ambient_opacity",
    "min_floor_light",
    "min_vehicle_width_ratio", "max_uncertain_fraction", "min_coverage", "max_coverage",
    "min_fill_ratio", "top_touch_ratio", "rise_tolerance",
}  # fmt: skip
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TOP_LEVEL_KEYS = {
    "id", "name", "description", "background", "branding", "output",
    "placement", "shotPlacement", "shadow", "reflection", "vehicleAdjustments", "quality",
}  # fmt: skip
_BACKGROUND_KEYS = {"plates", "notes"}


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


def _parse_quality(data) -> QualityConfig:
    quality = _build(QualityConfig, data, "quality")
    if quality.max_upscale < 1.0:
        raise PresetConfigError("quality.maxUpscale must be at least 1 (1 = never enlarge)")
    if not 320 <= quality.min_source_long_edge <= 12000:
        raise PresetConfigError("quality.minSourceLongEdge must be a pixel size between 320 and 12000")
    if quality.min_coverage >= quality.max_coverage:
        raise PresetConfigError("quality.minCoverage must be smaller than quality.maxCoverage")
    if quality.min_rise_factor >= quality.max_rise_factor or quality.max_rise_factor < 1.0:
        raise PresetConfigError("quality.minRiseFactor must be below quality.maxRiseFactor, and maxRiseFactor >= 1")
    if quality.min_aspect_factor >= 1.0 or quality.max_aspect_factor <= 1.0:
        raise PresetConfigError("quality.minAspectFactor < 1 < quality.maxAspectFactor required")
    if quality.high_view_rise_factor < 1.0 or not 0.0 < quality.high_view_aspect_factor <= 1.0:
        raise PresetConfigError("quality.highViewRiseFactor >= 1 and 0 < quality.highViewAspectFactor <= 1 required")
    if quality.min_contacts > 4:
        raise PresetConfigError("quality.minContacts must be 0..4")
    return quality


def _parse_shadow(data) -> ShadowConfig:
    shadow = _build(ShadowConfig, data, "shadow")
    for name, value in (("underbodyFalloff", shadow.underbody_falloff), ("ambientReach", shadow.ambient_reach)):
        if not 0.05 <= value <= 2.0:
            raise PresetConfigError(f"shadow.{name} must be 0.05..2 (metres on the floor), got {value}")
    if shadow.edge_opacity > shadow.underbody_opacity:
        raise PresetConfigError("shadow.edgeOpacity must not exceed shadow.underbodyOpacity (darkest under the car)")
    if shadow.min_floor_light < 0.02:
        raise PresetConfigError("shadow.minFloorLight must be at least 0.02 (never pure black)")
    return shadow


def _parse_reflection(data) -> ReflectionConfig:
    reflection = _build(ReflectionConfig, data, "reflection")
    if reflection.strength > 1.5:
        raise PresetConfigError("reflection.strength must be 0..1.5 (× the plate's measured floor reflectance)")
    for name, value in (("maxReflectance", reflection.max_reflectance), ("defaultReflectance", reflection.default_reflectance)):
        if value > 0.3:
            raise PresetConfigError(f"reflection.{name} must be 0..0.3 (a lacquered floor, not a mirror), got {value}")
    if not 0.2 <= reflection.fade <= 2.0:
        raise PresetConfigError("reflection.fade must be 0.2..2 (× the vehicle height)")
    if reflection.blur_rate > 0.2:
        raise PresetConfigError("reflection.blurRate must be 0..0.2 (px blur per px of distance)")
    return reflection


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
    if not isinstance(background, dict) or not isinstance(background.get("plates"), str):
        raise PresetConfigError("preset needs background.plates (the plates.json of the 3D showroom)")
    unknown = set(background) - _BACKGROUND_KEYS
    if unknown:
        raise PresetConfigError(f"Unknown preset field(s) in background: {', '.join(sorted(unknown))}")
    plates = PurePosixPath(background["plates"])
    if plates.is_absolute() or ".." in plates.parts or plates.suffix != ".json":
        raise PresetConfigError("background.plates must be a relative path to a .json file inside presets/")

    for section in (
        "branding", "output", "placement", "shotPlacement", "shadow", "reflection", "vehicleAdjustments", "quality",
    ):  # fmt: skip
        if section in data and not isinstance(data[section], dict):
            raise PresetConfigError(f"{section} must be an object")
    base_placement = _build(Placement, data.get("placement"), "placement")
    shot_data = data.get("shotPlacement") or {}
    shots = {}
    for key, value in shot_data.items():
        if key not in EXTERIOR_SHOTS:
            raise PresetConfigError(
                f"Unknown shot '{key}' in shotPlacement (allowed: {', '.join(sorted(EXTERIOR_SHOTS))})"
            )
        if not isinstance(value, dict):
            raise PresetConfigError(f"shotPlacement.{key} must be an object")
        shots[key] = _merge(base_placement, value, f"shotPlacement.{key}")
    for name, placement in [("placement", base_placement), *((f"shotPlacement.{k}", v) for k, v in shots.items())]:
        if placement.max_height_ratio < 0.2:
            raise PresetConfigError(f"{name}.maxHeightRatio ({placement.max_height_ratio}) leaves no room for the vehicle")
    branding = parse_branding(data.get("branding"))
    if branding.enabled:
        # fractions of the brand wall (top 0 = ceiling side, 1 = floor): keep the
        # signage on the upper wall so that the vehicle roof stays below it
        lowest = max(
            branding.logo.top + branding.logo.max_height,
            *(t.top + 2 * t.cap_height for t in (branding.city, branding.website, branding.phone) if t.text.strip()),
        )
        if lowest > 0.7:
            raise PresetConfigError("branding must stay on the upper part of the brand wall (bottom <= 0.7)")
    return Preset(
        id=data["id"],
        name=data["name"],
        plates=str(plates),
        branding=branding,
        output=_build(OutputConfig, data.get("output"), "output"),
        placement=base_placement,
        shot_placement=shots,
        shadow=_parse_shadow(data.get("shadow")),
        reflection=_parse_reflection(data.get("reflection")),
        adjustments=_build(VehicleAdjustments, data.get("vehicleAdjustments"), "vehicleAdjustments"),
        quality=_parse_quality(data.get("quality")),
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


class ShowroomUnavailableError(PresetConfigError):
    """The showroom plate set is missing or unusable – exterior jobs must fail."""


@dataclass(frozen=True)
class ShowroomPlate:
    """One branded showroom plate at one output size (everything read-only)."""

    #: Plate (shot) key, e.g. "front_left_45".
    key: str
    plate: Plate
    #: Branded plate, sRGB uint8 (H, W, 3).
    rgb: np.ndarray
    #: Where the branding elements landed (output pixels).
    branding: BrandingLayout
    #: Proxy-car floor shadow as a LINEAR multiplier (H, W), 1 = no shadow.
    shadow: np.ndarray
    #: Per column: first row (float px) that shows the floor in front of the wall.
    floor_top: np.ndarray
    source: str = SHOWROOM_PLATES
    #: The plate floor's own glossy reflection, LINEAR RGB (H, W, 3), signed (the plate minus its
    #: gloss-free render); None without a reflection pass.
    reflection: np.ndarray | None = None

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def is_fallback(self) -> bool:  # kept for callers of the old API – plates are never a fallback
        return False

    def floor_mask(self) -> np.ndarray:
        rows = np.arange(self.height, dtype=np.float32)[:, None] + 0.5
        return rows >= self.floor_top[None, :]


class BackgroundProvider:
    """Plate provider: loads the plate set, brands one plate per output size, caches."""

    #: Branded plates kept in memory (each ≈ 60 MB at 3200 × 2400).
    CACHE_SIZE = 3

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache: dict[tuple, ShowroomPlate] = {}
        self._sets: dict[str, tuple[tuple, PlateSet | None, str | None]] = {}
        self._warned: set[tuple] = set()
        self._lock = threading.Lock()

    def plates_path(self, preset: Preset) -> Path:
        return self._settings.presets_dir / preset.plates

    @staticmethod
    def _stamp(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def _set_stamp(self, path: Path, known: PlateSet | None) -> tuple:
        files = known.files() if known is not None else [path]
        return tuple(self._stamp(f) for f in files)

    def _load(self, preset: Preset) -> tuple[PlateSet | None, str | None]:
        path = self.plates_path(preset)
        key = str(path)
        cached = self._sets.get(key)
        # a usable set is reused while none of its files changed; a broken one is re-checked every time
        if cached is not None and cached[1] is not None and cached[0] == self._set_stamp(path, cached[1]):
            return cached[1], None
        try:
            plate_set, error = load_plate_set(path), None
        except PlateSetError as exc:
            plate_set, error = None, str(exc)
            if cached is None or cached[2] != error:  # log each new problem once
                log.error("Showroom plate set unusable: %s", error)
        self._sets[key] = (self._set_stamp(path, plate_set), plate_set, error)
        return plate_set, error

    def plate_set(self, preset: Preset) -> PlateSet:
        """The validated plate set (raises ShowroomUnavailableError)."""
        with self._lock:
            plate_set, error = self._load(preset)
        if plate_set is None:
            raise ShowroomUnavailableError(error or "showroom plate set missing")
        return plate_set

    def master_error(self, preset: Preset) -> str | None:
        """Why the plate set cannot be used (None when it is usable)."""
        with self._lock:
            return self._load(preset)[1]

    def source(self, preset: Preset) -> str:
        return SHOWROOM_PLATES if self.master_error(preset) is None else SHOWROOM_MISSING

    def is_placeholder(self, preset: Preset) -> bool:
        """Kept for API compatibility: plates are never a placeholder."""
        return False

    def get(self, preset: Preset, shot_key: str, width: int, height: int) -> ShowroomPlate:
        """Branded plate of `shot_key` at (width, height) – same aspect ratio as the plates."""
        plate_set = self.plate_set(preset)
        plate = plate_set.plate(shot_key)
        logo = self._settings.brand_dir / preset.branding.logo.file
        stamps = (
            self._stamp(plate.image),
            self._stamp(plate.shadow) if plate.shadow else 0,
            self._stamp(plate.reflection) if plate.reflection else 0,
        )
        key = (preset.id, preset.plates, shot_key, width, height, stamps, logo_stamp(logo), preset.branding)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        if abs(plate.aspect / (width / height) - 1.0) > 0.01:
            raise ShowroomUnavailableError(
                f"plate {shot_key} is {plate.size[0]}x{plate.size[1]}, the output {width}x{height} has another aspect ratio"
            )
        try:
            base = self._load_plate_image(plate, width, height)
            shadow = self._load_shadow(plate, width, height)
            reflection = self._load_reflection(plate, width, height)
        except ShowroomUnavailableError:
            raise
        except Exception as error:  # unreadable/half-copied plate file
            raise ShowroomUnavailableError(f"plate {shot_key} unreadable: {error}") from None
        try:
            rgb, layout = apply_wall_branding(base, plate, preset.branding, self._settings.brand_dir)
        except BrandingAssetError as error:
            raise PresetConfigError(str(error)) from None
        rgb = np.ascontiguousarray(rgb)
        rgb.setflags(write=False)
        shadow.setflags(write=False)
        floor_top = plate.floor_top(width, height)
        floor_top.setflags(write=False)
        if reflection is not None:
            reflection.setflags(write=False)
        showroom = ShowroomPlate(
            key=shot_key, plate=plate, rgb=rgb, branding=layout, shadow=shadow, floor_top=floor_top, reflection=reflection
        )
        with self._lock:
            while len(self._cache) >= self.CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = showroom
        return showroom

    def _load_plate_image(self, plate: Plate, width: int, height: int) -> np.ndarray:
        with Image.open(plate.image) as image:
            photo = _to_srgb(ImageOps.exif_transpose(image))
            photo.load()
        photo = photo.convert("RGB")
        if photo.size != plate.size:
            raise ShowroomUnavailableError(
                f"plate {plate.key}: image is {photo.width}x{photo.height}, plates.json says {plate.size[0]}x{plate.size[1]}"
            )
        stamp = (str(plate.image), width)
        if photo.width < width and stamp not in self._warned:
            self._warned.add(stamp)
            log.warning("Showroom plate %s (%d px wide) is upscaled to %d px", plate.image.name, photo.width, width)
        if photo.size != (width, height):
            photo = photo.resize((width, height), Image.LANCZOS)
        return np.ascontiguousarray(np.asarray(photo, dtype=np.uint8))

    @staticmethod
    def _load_reflection(plate: Plate, width: int, height: int) -> np.ndarray | None:
        return _load_reflection_map(plate, width, height)

    @staticmethod
    def _load_shadow(plate: Plate, width: int, height: int) -> np.ndarray:
        if plate.shadow is None:
            return np.ones((height, width), np.float32)
        raw = cv2.imread(str(plate.shadow), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ShowroomUnavailableError(f"plate {plate.key}: shadow map unreadable")
        if raw.ndim == 3:
            raw = raw[..., 0]
        scale = 65535.0 if raw.dtype == np.uint16 else 255.0
        mult = raw.astype(np.float32) / scale
        if abs(mult.shape[1] / mult.shape[0] - plate.aspect) > 0.02:
            raise ShowroomUnavailableError(f"plate {plate.key}: shadow map has another aspect ratio than the plate")
        if mult.shape != (height, width):
            mult = cv2.resize(mult, (width, height), interpolation=cv2.INTER_LINEAR)
        return np.clip(mult, 0.0, 1.0).astype(np.float32)


def _load_reflection_map(plate: Plate, width: int, height: int) -> np.ndarray | None:
    """The plate floor's reflection pass as LINEAR RGB at the output size (None without one)."""
    if plate.reflection is None:
        return None
    raw = cv2.imread(str(plate.reflection), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.ndim != 3 or raw.shape[2] != 3:
        raise ShowroomUnavailableError(f"plate {plate.key}: reflection pass unreadable (16-bit RGB PNG expected)")
    scale = 65535.0 if raw.dtype == np.uint16 else 255.0
    rgb = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB).astype(np.float32) / scale - plate.reflection_offset
    if rgb.shape[:2] != (height, width):
        rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_CUBIC)
    return np.ascontiguousarray(np.clip(rgb, -1.0, 1.0), dtype=np.float32)


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
