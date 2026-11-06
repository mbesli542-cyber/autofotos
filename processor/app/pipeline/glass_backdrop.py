"""Interior shots: the AutoExperten showroom seen THROUGH the glass (opt-in).

Dealer decision: an interior photo stays EXACTLY as photographed – dashboard,
steering wheel, mirror, pillars, wipers, stickers on the glass, the glass's own
reflections. ONLY what is seen through the see-through glass (windscreen, side
windows: the car park, other cars, buildings) is replaced by a defocused view
of the shipped studio plate, as if the car stood inside the showroom. Nothing
is generated: the backdrop is a crop of the fixed, branded plate (the official
logo appears when it is in view), blurred (phone close-up depth of field),
light-matched to the photo's own glass region, with the photo's measured
sensor noise. Where the glass region cannot be determined safely the photo
stays the original with a German hint – never a bad result.

Off by default (preset ``glassBackdrop.enabled``). The glass alpha comes from a
``GlassMaskProvider`` the caller injects (``process_photo(..., glass_masks=)``):
a callable ``(rgb, shot_key) -> GlassMask | None`` – a mask PNG next to the photo
for the CLI and the tests (``file_glass_masks``), a prompted segmentation
model later. The provider decides WHAT is glass; this module decides whether
the region is safe to use (``assess_glass``) and renders the composite
(``compose_glass_backdrop``). The mask source is pluggable, the rules are not.

Composite (linear light), per pane with its view (forward / side)::

    k   = 1 - glassKeep                     (glassKeep: share of the original glass pixels kept –
                                             tint, dirt, reflections and stickers stay visible)
    out = original * (1 - alpha * k) + backdrop * alpha * k

Only pixels with alpha > 0 change; every pixel with alpha == 0 is bit-identical
to the original (tested).

Gate (``assess_glass``) – the original with the hint ``glass_backdrop_skipped``
(``reason``) when:
- there is no glass (``no_glass``) or the region is tiny (``minGlassFraction``),
- the region's boundary is undecided: the soft alpha band (0.05 < alpha < 0.95)
  is wider than ``maxUndecided`` × the long edge per outline pixel,
- the mask leaked into the interior: the region touches more than
  ``maxBorderSides`` photo borders, or the BOTTOM border over more than
  ``maxBottomTouch`` of the width (glass is never at the bottom of an interior
  photo – the dashboard, seats or floor are),
- the inside of the region reads as interior (``maxInteriorStructure``): the
  share of the region's core that is dark structure (sRGB luminance below
  ``INTERIOR_DARK`` at the working scale). Through the glass the outside is
  bright in daylight; black steering wheels, pillars, the mirror and trim are
  not – a mask that includes them exceeds the limit (measured: correct masks
  ≤ 0.035, a mask with the steering wheel 0.1, with the mirror 0.05). A night
  photo (dark glass) is rejected too, which is right: a bright showroom through
  a black window would be a bad result.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..presets import (
    GLASS_BACKDROP_SHOTS,
    BackgroundProvider,
    GlassBackdropConfig,
    GlassViewConfig,
    Preset,
    PresetConfigError,
    ShowroomPlate,
    glass_backdrop_applies,
)
from .color import linear_to_srgb, linear_to_u8, srgb_to_linear
from .debug import NULL_DEBUG, DebugSink
from .detail_backdrop import Crop, defocus, light_match, noise_level, resample_crop
from .mask import QualityWarning

log = logging.getLogger(__name__)

__all__ = [
    "GlassGate",
    "GlassMask",
    "GlassMaskProvider",
    "GlassOutcome",
    "GlassPane",
    "PlateContext",
    "SHOT_VIEWS",
    "VIEW_FORWARD",
    "VIEW_SIDE",
    "assess_glass",
    "compose_glass_backdrop",
    "file_glass_masks",
    "glass_backdrop_applies",
    "glass_mask_beside",
    "plan_glass_crop",
    "run_glass",
]

SKIP_CODE = "glass_backdrop_skipped"

#: Plate views: a cockpit photo looks forward (the brand wall ahead), side windows look sideways
#: (the plate's slat/plant side).
VIEW_FORWARD = "forward"
VIEW_SIDE = "side"
VIEWS = (VIEW_FORWARD, VIEW_SIDE)
#: Default view per interior shot (a mask provider may override it per pane).
SHOT_VIEWS = {
    "cockpit": VIEW_FORWARD,
    "front_interior": VIEW_FORWARD,
    "driver_seat": VIEW_SIDE,
    "rear_seats": VIEW_SIDE,
}
assert set(SHOT_VIEWS) == set(GLASS_BACKDROP_SHOTS)

#: Working long edge (px) of the gate's measurements.
WORK_PX = 1024
#: Soft alpha band of the gate: alpha strictly between these is "undecided".
SOFT_LOW, SOFT_HIGH = 0.05, 0.95
#: The region's core (gate): eroded by this share of the working long edge (boundary inaccuracies
#: of a few px never count as interior structure).
CORE_ERODE = 0.006
#: Interior structure: sRGB luminance (0..1) below this at the working scale is "dark".
INTERIOR_DARK = 0.15
#: A border counts as touched when the region covers more than this share of its length.
BORDER_TOUCH = 0.01
#: The light match / grain measure the photo's glass region eroded by this many px.
REGION_ERODE_PX = 3
#: The photo's glass region is the composite's region at or above this alpha.
REGION_ALPHA = 0.5
#: Alpha may overshoot 0..1 by this much (float blur of a provider) and is clipped; more is invalid.
ALPHA_TOLERANCE = 1e-3
#: Mask PNG next to a photo: <stem><suffix>.
MASK_SUFFIX = "-glass.png"
#: The plate entry whose branded image is seen through the glass (the shipped set has ONE plate
#: image and one fixed studio camera for all eight entries, so any entry shows the same room).
GLASS_PLATE = "front"

MESSAGES = {
    "no_glass": "Innenraumfoto: keine Scheibe erkannt – Außenansicht nicht ersetzt.",
    "glass_too_small": "Innenraumfoto: Scheibenbereich zu klein – Außenansicht nicht ersetzt.",
    "edge_undecided": "Innenraumfoto: Scheibenrand nicht eindeutig – Außenansicht nicht ersetzt.",
    "mask_leaked": "Innenraumfoto: Scheibenbereich nicht sicher erkannt – Außenansicht nicht ersetzt.",
    "interior_structure": "Innenraumfoto: Innenraum im Scheibenbereich erkannt – Außenansicht nicht ersetzt.",
    "no_mask_provider": "Innenraumfoto: Scheibenerkennung nicht verfügbar – Außenansicht nicht ersetzt.",
    "mask_invalid": "Innenraumfoto: Scheibenbereich nicht sicher erkannt – Außenansicht nicht ersetzt.",
    "showroom_unavailable": "Innenraumfoto: Showroom-Hintergrund nicht verfügbar – das Original bleibt unverändert.",
}


def skip_warning(reason: str) -> QualityWarning:
    return QualityWarning(SKIP_CODE, MESSAGES.get(reason, MESSAGES["mask_leaked"]))


# --------------------------------------------------------------------------- mask source


@dataclass(frozen=True)
class GlassPane:
    """One pane of see-through glass: its alpha (H, W float32, 1 = the outside is visible) and the
    plate view behind it."""

    alpha: np.ndarray
    view: str = VIEW_FORWARD


@dataclass(frozen=True)
class GlassMask:
    """The glass alpha of a photo (H, W float32 in 0..1, 1 = see-through glass). ``panes``: the
    panes with their own views; empty = ONE pane in the shot's default view (``SHOT_VIEWS``)."""

    alpha: np.ndarray
    source: str = "unknown"
    panes: tuple[GlassPane, ...] = ()

    def panes_for(self, view: str) -> tuple[GlassPane, ...]:
        if self.panes:
            return self.panes
        return (GlassPane(self.alpha, view),)


#: ``(rgb uint8 H×W×3, shot_key) -> GlassMask | None`` – None: no glass found / nothing to say. Raises
#: nothing for an unclear photo (that is the gate's job). The mask must have the photo's size.
GlassMaskProvider = Callable[[np.ndarray, "str | None"], "GlassMask | None"]


def glass_mask_beside(photo: Path) -> Path:
    """The mask PNG the CLI/tests expect next to a photo: ``<stem>-glass.png``."""
    return photo.with_name(photo.stem + MASK_SUFFIX)


def file_glass_masks(path: Path) -> GlassMaskProvider:
    """A provider that loads ONE grey mask PNG (255 = see-through glass; resized to the photo if
    needed) – the hand-made masks of the CLI and the tests. A missing file = no glass."""

    def provide(rgb: np.ndarray, _shot_key: str | None) -> GlassMask | None:
        if not path.is_file():
            return None
        with Image.open(path) as image:
            grey = np.asarray(image.convert("L"))
        height, width = rgb.shape[:2]
        if grey.shape != (height, width):
            grey = cv2.resize(grey, (width, height), interpolation=cv2.INTER_AREA)
        return GlassMask(alpha=grey.astype(np.float32) / 255.0, source=f"file:{path.name}")

    return provide


# --------------------------------------------------------------------------- gate


@dataclass
class GlassGate:
    passed: bool
    reason: str | None = None
    metrics: dict = field(default_factory=dict)

    def warning(self) -> QualityWarning | None:
        return None if self.passed else skip_warning(self.reason or "mask_leaked")

    def as_dict(self) -> dict:
        return {"passed": self.passed, "reason": self.reason, **self.metrics}


def _disk(radius: int) -> np.ndarray:
    radius = max(int(radius), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def _touch(edge: np.ndarray) -> float:
    return float(np.mean(edge)) if edge.size else 0.0


def assess_glass(rgb: np.ndarray, glass_alpha: np.ndarray, cfg: GlassBackdropConfig) -> GlassGate:
    """Is the glass region safe to replace? See the module docstring for the rules; every rule's
    measurement is in ``metrics`` (also for a passed photo)."""
    if glass_alpha.shape != rgb.shape[:2]:
        return GlassGate(False, "mask_invalid", {"maskShape": list(glass_alpha.shape)})
    if (
        not np.isfinite(glass_alpha).all()
        or float(glass_alpha.min()) < -ALPHA_TOLERANCE
        or float(glass_alpha.max()) > 1.0 + ALPHA_TOLERANCE
    ):
        return GlassGate(False, "mask_invalid", {})
    glass_alpha = np.clip(glass_alpha, 0.0, 1.0)
    height, width = glass_alpha.shape
    long_edge = float(max(height, width))
    region = glass_alpha >= REGION_ALPHA
    fraction = float(np.mean(region))
    metrics: dict = {"glassFraction": round(fraction, 4)}
    if fraction <= 0.0:
        return GlassGate(False, "no_glass", metrics)
    if fraction < cfg.min_glass_fraction:
        return GlassGate(False, "glass_too_small", metrics)

    # undecided boundary: soft band width (soft px per outline px) relative to the long edge
    soft = (glass_alpha > SOFT_LOW) & (glass_alpha < SOFT_HIGH)
    outline = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    outline_px = max(float(np.count_nonzero(outline)) / 2.0, 1.0)
    undecided = float(np.count_nonzero(soft)) / outline_px / long_edge
    metrics["undecided"] = round(undecided, 5)
    if undecided > cfg.max_undecided:
        return GlassGate(False, "edge_undecided", metrics)

    # border touches: how much of each photo border the region covers
    touches = {
        "top": _touch(region[0]),
        "bottom": _touch(region[-1]),
        "left": _touch(region[:, 0]),
        "right": _touch(region[:, -1]),
    }
    metrics["borderTouch"] = {k: round(v, 4) for k, v in touches.items()}
    sides = sum(1 for v in touches.values() if v > BORDER_TOUCH)
    metrics["borderSides"] = sides
    if sides > cfg.max_border_sides or touches["bottom"] > cfg.max_bottom_touch:
        return GlassGate(False, "mask_leaked", metrics)

    # interior structure inside the region's core (working scale)
    scale = min(1.0, WORK_PX / long_edge)
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    grey = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), size, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    small = cv2.resize(glass_alpha, size, interpolation=cv2.INTER_AREA)
    core = cv2.erode((small >= REGION_ALPHA).astype(np.uint8), _disk(int(round(CORE_ERODE * max(size))))) > 0
    interior = cv2.erode((small < SOFT_LOW).astype(np.uint8), _disk(int(round(CORE_ERODE * max(size))))) > 0
    dark = grey < INTERIOR_DARK
    structure = float(np.mean(dark[core])) if core.any() else 1.0
    metrics["interiorStructure"] = round(structure, 4)
    metrics["interiorDarkOutside"] = round(float(np.mean(dark[interior])), 4) if interior.any() else None
    # edge density inside vs outside (diagnostics only: the outside through glass has as many
    # edges as the interior – other cars, buildings – so it never decides)
    gx = cv2.Sobel(grey, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(grey, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    edges = np.hypot(gx, gy) > 0.06
    metrics["edgeDensity"] = {
        "glass": round(float(np.mean(edges[core])), 4) if core.any() else None,
        "interior": round(float(np.mean(edges[interior])), 4) if interior.any() else None,
    }
    if structure > cfg.max_interior_structure:
        return GlassGate(False, "interior_structure", metrics)
    return GlassGate(True, None, metrics)


# --------------------------------------------------------------------------- backdrop


@dataclass(frozen=True)
class PlateContext:
    """What the composite needs from the branded showroom plate (sRGB uint8 H×W×3) and its
    geometry: the horizon (eye level) as a fraction of the plate height."""

    rgb: np.ndarray
    horizon_v: float

    @classmethod
    def from_showroom(cls, showroom: ShowroomPlate) -> PlateContext:
        return cls(rgb=np.asarray(showroom.rgb), horizon_v=float(showroom.plate.horizon_v))

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


def plan_glass_crop(plate_w: int, plate_h: int, horizon_v: float, aspect: float, view: GlassViewConfig) -> Crop:
    """The plate window seen through the glass: ``cropHeight`` of the plate at the photo's aspect
    (never wider than the plate), centred on ``centerX``, the plate's horizon (eye level) at
    ``horizon`` of the photo height – clamped into the plate. ``Crop.horizon`` reports where the
    horizon ended up (fraction of the crop height)."""
    height = view.crop_height * plate_h
    width = height * aspect
    if width > plate_w:
        width = float(plate_w)
        height = width / aspect
    x0 = float(np.clip(view.center_x * plate_w - 0.5 * width, 0.0, plate_w - width))
    horizon_row = horizon_v * plate_h
    y0 = float(np.clip(horizon_row - view.horizon * height, 0.0, plate_h - height))
    return Crop(x0, y0, width, height, (horizon_row - y0) / height)


def compose_glass_backdrop(
    rgb: np.ndarray,
    glass_alpha: np.ndarray,
    plate_ctx: PlateContext,
    cfg: GlassBackdropConfig,
    *,
    view: str = VIEW_FORWARD,
    seed: int = 0,
) -> tuple[np.ndarray, dict]:
    """The original ``rgb`` (uint8) with the defocused plate crop of ``view`` seen through
    ``glass_alpha`` (H×W float32 in 0..1). Returns (uint8 composite, metadata). Every pixel with
    alpha == 0 is returned bit-identical; the glass keeps ``glassKeep`` of its original pixels."""
    if view not in VIEWS:
        raise ValueError(f"unknown glass view {view!r}")
    height, width = glass_alpha.shape
    if rgb.shape[:2] != (height, width):
        raise ValueError("glass alpha and photo differ in size")
    long_edge = float(max(width, height))
    plate_w, plate_h = plate_ctx.size
    view_cfg = cfg.view(view)
    crop = plan_glass_crop(plate_w, plate_h, plate_ctx.horizon_v, width / height, view_cfg)
    plate = srgb_to_linear(resample_crop(plate_ctx.rgb, crop, width, height))
    # depth of field: everything behind the glass is far away – the wall's blur, the floor at
    # floorBlur × it (1 = the same)
    horizon_y = crop.horizon * height
    backdrop = defocus(plate, horizon_y, cfg.blur * long_edge, cfg.floor_blur)
    photo = srgb_to_linear(rgb)
    region = cv2.erode((glass_alpha >= REGION_ALPHA).astype(np.uint8), _disk(REGION_ERODE_PX)) > 0
    backdrop, light = light_match(backdrop, photo, region, cfg.light_match)
    sigma = noise_level(rgb, region) * cfg.grain
    if sigma > 0:
        noise = np.random.default_rng(seed).normal(0.0, sigma, backdrop.shape).astype(np.float32)
        backdrop = srgb_to_linear(np.clip(linear_to_srgb(backdrop) + noise, 0.0, 1.0))
    weight = (glass_alpha * (1.0 - cfg.glass_keep)).astype(np.float32)[..., None]
    out = linear_to_u8(photo * (1.0 - weight) + backdrop * weight)
    untouched = glass_alpha <= 0.0
    out[untouched] = rgb[untouched]
    info = {
        "view": view,
        "crop": crop.as_dict(),
        "blurSigmaPx": round(cfg.blur * long_edge, 2),
        "lightMatch": light,
        "grainSigma": round(sigma * 255.0, 2),
        "glassKeep": cfg.glass_keep,
        "backdrop": linear_to_u8(backdrop),
    }
    return out, info


# --------------------------------------------------------------------------- pipeline step


@dataclass
class GlassOutcome:
    """The interior photo to export (the composite, or the original when skipped) and why."""

    rgb: np.ndarray
    applied: bool
    warnings: list[QualityWarning]
    meta: dict

    @property
    def shot_kind(self) -> str:
        return "interior_glass_backdrop" if self.applied else "interior_passthrough"


def _fit(rgb: np.ndarray, long_edge_max: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    scale = min(1.0, long_edge_max / float(max(height, width)))
    if scale >= 1.0:
        return rgb
    size = (int(round(width * scale)), int(round(height * scale)))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)


def _overlay(rgb: np.ndarray, alpha: np.ndarray, gate: GlassGate) -> np.ndarray:
    """Debug view: dimmed photo, the glass region tinted (green: passed, red: rejected), its outline."""
    scale = min(1.0, 1600.0 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb.copy()
    a = cv2.resize(alpha, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA)
    tint = np.array([60, 220, 60] if gate.passed else [255, 60, 60], np.float32)
    photo = small.astype(np.float32)
    out = photo * (1.0 - 0.4 * a[..., None]) + 0.4 * tint[None, None, :] * a[..., None]
    outline = cv2.morphologyEx((a >= REGION_ALPHA).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    out[outline] = (255, 255, 0)
    out = np.clip(out, 0, 255).astype(np.uint8)
    text = f"{'ok' if gate.passed else gate.reason} glass={gate.metrics.get('glassFraction')} structure={gate.metrics.get('interiorStructure')}"
    cv2.putText(out, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def run_glass(
    rgb: np.ndarray,
    *,
    preset: Preset,
    backgrounds: BackgroundProvider,
    glass_masks: GlassMaskProvider | None,
    shot_key: str | None,
    debug: DebugSink = NULL_DEBUG,
    progress: Callable[[float, str], None] = lambda _p, _s: None,
) -> GlassOutcome:
    """The glass-backdrop step for one decoded interior photo (only for shots
    ``glass_backdrop_applies`` accepts). Never raises for an unclear photo: it is returned as the
    original (at most ``output.longEdgeMax``) with a ``glass_backdrop_skipped`` hint."""
    cfg = preset.glass_backdrop
    rgb = _fit(rgb, preset.output.long_edge_max)
    times: dict[str, int] = {}
    started = time.perf_counter()

    def mark(step: str) -> None:
        nonlocal started
        now = time.perf_counter()
        times[step] = int(round((now - started) * 1000))
        started = now

    def skip(reason: str, **extra) -> GlassOutcome:
        meta = {"applied": False, "reason": reason, **extra, "timingsMs": times}
        return GlassOutcome(rgb=rgb, applied=False, warnings=[skip_warning(reason)], meta=meta)

    if glass_masks is None:
        return skip("no_mask_provider")
    progress(0.1, "glass-mask")
    mask = glass_masks(rgb, shot_key)
    mark("mask")
    if mask is None:
        return skip("no_glass", maskSource=None)
    source = {"maskSource": mask.source}
    alpha = np.ascontiguousarray(mask.alpha, dtype=np.float32)
    debug.gray("mask.png", alpha)
    gate = assess_glass(rgb, alpha, cfg)
    mark("gate")
    if debug.enabled:
        debug.rgb("geometry.jpg", _overlay(rgb, alpha, gate))
    if not gate.passed:
        return skip(gate.reason or "mask_leaked", gate=gate.as_dict(), **source)

    progress(0.3, "glass-plate")
    try:
        plate = backgrounds.plate_set(preset).plate(GLASS_PLATE)
        showroom = backgrounds.get(preset, GLASS_PLATE, plate.size[0], plate.size[1])
    except PresetConfigError as error:  # incl. ShowroomUnavailableError – the original stays
        log.warning("Glass backdrop without showroom plate: %s", error)
        return skip("showroom_unavailable", gate=gate.as_dict(), **source)
    context = PlateContext.from_showroom(showroom)
    mark("plate")

    progress(0.5, "glass-composite")
    default_view = SHOT_VIEWS.get(shot_key or "", VIEW_FORWARD)
    out = rgb
    panes_meta = []
    background = None
    for pane in mask.panes_for(default_view):
        view = pane.view if pane.view in VIEWS else default_view
        pane_alpha = np.ascontiguousarray(pane.alpha, dtype=np.float32)
        if pane_alpha.shape != alpha.shape or not np.isfinite(pane_alpha).all():
            return skip("mask_invalid", gate=gate.as_dict(), **source)
        pane_alpha = np.clip(pane_alpha, 0.0, 1.0)
        out, info = compose_glass_backdrop(out, pane_alpha, context, cfg, view=view)
        backdrop = info.pop("backdrop")
        a = pane_alpha[..., None]
        background = backdrop if background is None else (background * (1.0 - a) + backdrop * a).astype(np.uint8)
        panes_meta.append(info)
    if background is not None:
        debug.rgb("background.jpg", background)
    mark("composite")
    meta = {
        "applied": True,
        "plate": GLASS_PLATE,
        **source,
        "gate": gate.as_dict(),
        "panes": panes_meta,
        "timingsMs": times,
    }
    return GlassOutcome(rgb=out, applied=True, warnings=[], meta=meta)
