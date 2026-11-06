"""Interior shots: the AutoExperten showroom seen THROUGH the glass (opt-in).

Dealer decision: an interior photo stays EXACTLY as photographed – dashboard,
steering wheel, mirror, pillars, wipers, stickers on the glass, the glass's own
reflections. ONLY what is seen through the see-through glass (windscreen, side
windows: the car park, other cars, buildings) is replaced by a defocused view
of the shipped studio plate, as if the car stood inside the showroom. Nothing
is generated: the backdrop is a crop of the fixed, branded plate, blurred
(phone close-up depth of field), light-matched to the photo's own glass region,
with the photo's measured sensor noise. Where the glass region cannot be
determined safely the photo stays the original with a German hint – never a
bad result.

Off by default (preset ``glassBackdrop.enabled``). The glass alpha comes from a
``GlassMaskProvider`` the caller injects (``process_photo(..., glass_masks=)``):
a callable ``(rgb, shot_key) -> GlassMask | None`` – a mask PNG next to the photo
for the CLI and the tests (``file_glass_masks``), a prompted segmentation
model later. The provider decides WHAT is glass; this module decides whether
the region is safe to use (``assess_glass``, ``assess_crop``) and renders the
composite (``compose_glass_backdrop``). The mask source is pluggable, the
rules are not.

Panes and views: a mask is split into its panes (connected components, unless
the provider delivers panes itself). Each pane gets a plate view by shot and
position (``view_for_pane``): ``forward`` (windscreen of cockpit/front_interior
photos: the brand wall ahead, the official logo in view), ``side`` (side
windows: the slat panel and the olive tree, zoomed in below the lettering;
panes in the right half of the photo – the car's right side when photographed
from the left door – get the crop mirrored so the slats/LED read correctly),
``rear`` (a rear-window pane of rear_seats photos: the plain white panel
below the lettering, defocused more). The crop of a side/rear pane may never
contain any part of the branding box (``assess_crop``): letter fragments can
never appear by accident; the forward view is the only one that shows the
lettering.

Composite (linear light), per pane group::

    k   = 1 - glassKeep                     (glassKeep: share of the original glass pixels kept –
                                             tint, noise, strong reflections stay visible)
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
  a black window would be a bad result,
- (``assess_crop``) a side/rear crop would show part of the branding
  (``branding_in_view``).
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
from .segmentation import ModelUnavailableError

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
    "VIEW_REAR",
    "VIEW_SIDE",
    "assess_crop",
    "assess_glass",
    "branding_in_crop",
    "compose_glass_backdrop",
    "GlassMasksUnavailable",
    "file_glass_masks",
    "glass_backdrop_applies",
    "glass_mask_beside",
    "pane_centroid",
    "plan_glass_crop",
    "run_glass",
    "split_panes",
    "view_for_pane",
]

SKIP_CODE = "glass_backdrop_skipped"

#: Plate views: a cockpit photo looks forward (the brand wall ahead), side windows look sideways
#: (the plate's slat/plant side), a rear window at the plain wall below the lettering.
VIEW_FORWARD = "forward"
VIEW_SIDE = "side"
VIEW_REAR = "rear"
VIEWS = (VIEW_FORWARD, VIEW_SIDE, VIEW_REAR)
#: Default view per interior shot (``view_for_pane`` refines it by the pane's position).
SHOT_VIEWS = {
    "cockpit": VIEW_FORWARD,
    "front_interior": VIEW_FORWARD,
    "driver_seat": VIEW_SIDE,
    "rear_seats": VIEW_SIDE,
}
assert set(SHOT_VIEWS) == set(GLASS_BACKDROP_SHOTS)
#: A rear_seats pane whose centroid lies in the middle third, upper half of the photo is the rear window.
REAR_WINDOW_U = (1.0 / 3.0, 2.0 / 3.0)
REAR_WINDOW_V_MAX = 0.5

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
#: The branding box must stay this many blur sigmas away from a side/rear crop.
BRANDING_MARGIN_SIGMAS = 3.0
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
    "branding_in_view": "Innenraumfoto: Showroom-Schriftzug läge in der Scheibe – Außenansicht nicht ersetzt.",
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
    plate view behind it (None: decided by shot and position, ``view_for_pane``)."""

    alpha: np.ndarray
    view: str | None = None


@dataclass(frozen=True)
class GlassMask:
    """The glass alpha of a photo (H, W float32 in 0..1, 1 = see-through glass). ``panes``: the
    panes with their own views; empty = the connected components of ``alpha``, each in the view
    its shot and position give (``view_for_pane``)."""

    alpha: np.ndarray
    source: str = "unknown"
    panes: tuple[GlassPane, ...] = ()

    def panes_for(self, shot_key: str | None) -> tuple[GlassPane, ...]:
        panes = self.panes or tuple(GlassPane(a) for a in split_panes(self.alpha))
        resolved = []
        for pane in panes:
            view = pane.view
            if view not in VIEWS:
                u, v = pane_centroid(pane.alpha)
                view = view_for_pane(shot_key, u, v)
            resolved.append(GlassPane(pane.alpha, view))
        return tuple(resolved)


#: ``(rgb uint8 H×W×3, shot_key) -> GlassMask | None`` – None: no glass found / nothing to say. Raises
#: nothing for an unclear photo (that is the gate's job) – only ``GlassMasksUnavailable`` when it has
#: no model to look with, or ``segmentation.ModelUnavailableError`` when that model cannot be loaded
#: (both: the original with the ``no_mask_provider`` hint). The mask must have the photo's size.
GlassMaskProvider = Callable[[np.ndarray, "str | None"], "GlassMask | None"]


class GlassMasksUnavailable(Exception):
    """The provider cannot look at the photo right now (SAM 2.1 off, its graphs missing or refused at
    warm-up): the shot stays the original with the ``no_mask_provider`` hint – never a guess."""


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


def split_panes(alpha: np.ndarray) -> tuple[np.ndarray, ...]:
    """The panes of a glass alpha: the connected components (8-connected) of its solid region
    (alpha >= 0.5), each as a full-size alpha; soft pixels belong to the nearest solid component
    (the feather tails of two panes may meet in the pillar between them). Soft pixels far from
    any solid region form panes of their own. Panes separated by a pillar or frame get their own
    view and mirror decision."""
    solid = alpha >= REGION_ALPHA
    count, labels = cv2.connectedComponents(solid.astype(np.uint8), connectivity=8)
    labels = labels.astype(np.float32)  # a max filter grows the labels (cv2.dilate: float, not int32)
    soft = (alpha > 0.0) & ~solid
    kernel = np.ones((3, 3), np.uint8)
    for _ in range(64):  # grow the labels into the soft rim (a feather is a few px wide)
        open_soft = soft & (labels == 0)
        if not open_soft.any():
            break
        labels = np.where(open_soft, cv2.dilate(labels, kernel), labels)
    rest = soft & (labels == 0)
    if rest.any():
        extra, rest_labels = cv2.connectedComponents(rest.astype(np.uint8), connectivity=8)
        labels = np.where(rest, rest_labels.astype(np.float32) + (count - 1), labels)
        count += extra - 1
    labels = np.rint(labels).astype(np.int32)
    return tuple(np.where(labels == index, alpha, 0.0).astype(np.float32) for index in range(1, count))


def pane_centroid(alpha: np.ndarray) -> tuple[float, float]:
    """Alpha-weighted centroid of a pane as fractions (u, v) of the photo width/height."""
    height, width = alpha.shape
    total = float(alpha.sum())
    if total <= 0.0:
        return 0.5, 0.5
    xs = np.arange(width, dtype=np.float64) + 0.5
    ys = np.arange(height, dtype=np.float64) + 0.5
    u = float((alpha.sum(axis=0) * xs).sum() / total) / width
    v = float((alpha.sum(axis=1) * ys).sum() / total) / height
    return u, v


def view_for_pane(shot_key: str | None, u: float, v: float) -> str:
    """The plate view of a pane by shot and position: cockpit/front_interior panes look forward,
    driver_seat panes sideways; a rear_seats pane in the middle third of the upper half is the rear
    window (``rear``), the others are side windows."""
    default = SHOT_VIEWS.get(shot_key or "", VIEW_FORWARD)
    if shot_key == "rear_seats" and REAR_WINDOW_U[0] < u < REAR_WINDOW_U[1] and v < REAR_WINDOW_V_MAX:
        return VIEW_REAR
    return default


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


def branding_in_crop(crop: Crop, boxes, margin: float) -> bool:
    """Does the plate ``crop`` (plate px), grown by ``margin`` px, overlap any branding box
    ((x0, y0, x1, y1) in plate px)?"""
    x0, y0 = crop.x0 - margin, crop.y0 - margin
    x1, y1 = crop.x0 + crop.width + margin, crop.y0 + crop.height + margin
    return any(bx0 < x1 and bx1 > x0 and by0 < y1 and by1 > y0 for bx0, by0, bx1, by1 in boxes)


def assess_crop(crop: Crop, view: str, boxes, margin: float) -> GlassGate:
    """Second gate, on what the crop would show: a side/rear crop may never contain any part of
    the branding (plus ``margin`` plate px for the blur) – letter fragments never appear by
    accident. The forward view shows the brand wall on purpose and is exempt."""
    metrics = {"view": view, "crop": crop.as_dict(), "brandingMarginPx": round(margin, 1)}
    if view != VIEW_FORWARD and branding_in_crop(crop, boxes, margin):
        return GlassGate(False, "branding_in_view", metrics)
    return GlassGate(True, None, metrics)


# --------------------------------------------------------------------------- backdrop


@dataclass(frozen=True)
class PlateContext:
    """What the composite needs from the branded showroom plate (sRGB uint8 H×W×3) and its
    geometry: the horizon (eye level) as a fraction of the plate height, the branding boxes
    ((x0, y0, x1, y1) in plate px)."""

    rgb: np.ndarray
    horizon_v: float
    branding_boxes: tuple[tuple[int, int, int, int], ...] = ()

    @classmethod
    def from_showroom(cls, showroom: ShowroomPlate) -> PlateContext:
        boxes = tuple((int(b[1]), int(b[2]), int(b[3]), int(b[4])) for b in showroom.branding.boxes)
        return cls(rgb=np.asarray(showroom.rgb), horizon_v=float(showroom.plate.horizon_v), branding_boxes=boxes)

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


def plan_glass_crop(plate_w: int, plate_h: int, horizon_v: float, aspect: float, view: GlassViewConfig) -> Crop:
    """The plate window seen through the glass: ``cropHeight`` of the plate at the photo's aspect
    (never wider than the plate), centred on ``centerX``, its upper edge at ``top`` of the plate
    height – both clamped into the plate. ``Crop.horizon`` reports where the plate's horizon (eye
    level) lies in the crop (fraction of its height; outside 0..1 = not in view)."""
    height = view.crop_height * plate_h
    width = height * aspect
    if width > plate_w:
        width = float(plate_w)
        height = width / aspect
    x0 = float(np.clip(view.center_x * plate_w - 0.5 * width, 0.0, plate_w - width))
    y0 = float(np.clip(view.top * plate_h, 0.0, plate_h - height))
    return Crop(x0, y0, width, height, (horizon_v * plate_h - y0) / height)


def blur_sigma_px(cfg: GlassBackdropConfig, view: str, long_edge: float) -> float:
    return cfg.blur * cfg.view(view).blur_scale * long_edge


def compose_glass_backdrop(
    rgb: np.ndarray,
    glass_alpha: np.ndarray,
    plate_ctx: PlateContext,
    cfg: GlassBackdropConfig,
    *,
    view: str = VIEW_FORWARD,
    mirrored: bool = False,
    seed: int = 0,
) -> tuple[np.ndarray, dict]:
    """The original ``rgb`` (uint8) with the defocused plate crop of ``view`` (horizontally flipped
    when ``mirrored``) seen through ``glass_alpha`` (H×W float32 in 0..1). Returns (uint8
    composite, metadata incl. the uint8 ``backdrop``). Every pixel with alpha == 0 is returned
    bit-identical; the glass keeps ``glassKeep`` of its original pixels."""
    if view not in VIEWS:
        raise ValueError(f"unknown glass view {view!r}")
    height, width = glass_alpha.shape
    if rgb.shape[:2] != (height, width):
        raise ValueError("glass alpha and photo differ in size")
    long_edge = float(max(width, height))
    plate_w, plate_h = plate_ctx.size
    crop = plan_glass_crop(plate_w, plate_h, plate_ctx.horizon_v, width / height, cfg.view(view))
    plate = resample_crop(plate_ctx.rgb, crop, width, height)
    if mirrored:
        plate = np.ascontiguousarray(plate[:, ::-1])
    plate = srgb_to_linear(plate)
    # depth of field: everything behind the glass is far away – the wall's blur, the floor at
    # floorBlur × it (1 = the same)
    sigma = blur_sigma_px(cfg, view, long_edge)
    backdrop = defocus(plate, crop.horizon * height, sigma, cfg.floor_blur)
    photo = srgb_to_linear(rgb)
    region = cv2.erode((glass_alpha >= REGION_ALPHA).astype(np.uint8), _disk(REGION_ERODE_PX)) > 0
    backdrop, light = light_match(backdrop, photo, region, cfg.light_match)
    grain = noise_level(rgb, region) * cfg.grain
    if grain > 0:
        noise = np.random.default_rng(seed).normal(0.0, grain, backdrop.shape).astype(np.float32)
        backdrop = srgb_to_linear(np.clip(linear_to_srgb(backdrop) + noise, 0.0, 1.0))
    weight = (glass_alpha * (1.0 - cfg.glass_keep)).astype(np.float32)[..., None]
    out = linear_to_u8(photo * (1.0 - weight) + backdrop * weight)
    untouched = glass_alpha <= 0.0
    out[untouched] = rgb[untouched]
    info = {
        "view": view,
        "mirrored": mirrored,
        "crop": crop.as_dict(),
        "blurSigmaPx": round(sigma, 2),
        "lightMatch": light,
        "grainSigma": round(grain * 255.0, 2),
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


def _groups(panes: tuple[GlassPane, ...], cfg: GlassBackdropConfig) -> list[dict]:
    """Panes grouped by (view, mirrored): one backdrop per group, the group's alpha = the union.
    A pane in the right half of the photo gets the mirrored crop when its view says so."""
    groups: dict[tuple[str, bool], dict] = {}
    for pane in panes:
        u, v = pane_centroid(pane.alpha)
        view = pane.view if pane.view in VIEWS else VIEW_FORWARD
        mirrored = bool(cfg.view(view).mirror_right and u > 0.5)
        group = groups.setdefault((view, mirrored), {"view": view, "mirrored": mirrored, "alpha": None, "panes": []})
        group["alpha"] = pane.alpha if group["alpha"] is None else np.maximum(group["alpha"], pane.alpha)
        group["panes"].append(
            {"centroid": [round(u, 3), round(v, 3)], "fraction": round(float(np.mean(pane.alpha >= REGION_ALPHA)), 4)}
        )
    return list(groups.values())


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
    try:
        mask = glass_masks(rgb, shot_key)
    except GlassMasksUnavailable as error:
        log.info("Glass backdrop (%s) without glass masks: %s", shot_key, error)
        return skip("no_mask_provider", maskSource=None, detail=str(error))
    except ModelUnavailableError as error:
        # the provider has a model to look with but cannot load it (a graph missing or refused at its
        # first use, ONNX Runtime failing on it): the same outcome, loudly – never a failed interior job
        log.error("Glass backdrop (%s): the glass-mask model is unavailable: %s", shot_key, error)
        return skip("no_mask_provider", maskSource=None, detail=str(error))
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
    panes = mask.panes_for(shot_key)
    for pane in panes:
        if pane.alpha.shape != alpha.shape or not np.isfinite(pane.alpha).all():
            return skip("mask_invalid", gate=gate.as_dict(), **source)

    progress(0.3, "glass-plate")
    try:
        plate = backgrounds.plate_set(preset).plate(GLASS_PLATE)
        showroom = backgrounds.get(preset, GLASS_PLATE, plate.size[0], plate.size[1])
    except PresetConfigError as error:  # incl. ShowroomUnavailableError – the original stays
        log.warning("Glass backdrop without showroom plate: %s", error)
        return skip("showroom_unavailable", gate=gate.as_dict(), **source)
    context = PlateContext.from_showroom(showroom)
    mark("plate")

    # what each pane group would show: a side/rear crop may never contain the branding
    height, width = alpha.shape
    plate_w, plate_h = context.size
    groups = _groups(panes, cfg)
    crops = []
    for group in groups:
        crop = plan_glass_crop(plate_w, plate_h, context.horizon_v, width / height, cfg.view(group["view"]))
        margin = BRANDING_MARGIN_SIGMAS * blur_sigma_px(cfg, group["view"], float(max(width, height))) * crop.height / height
        check = assess_crop(crop, group["view"], context.branding_boxes, margin)
        crops.append(check.as_dict())
        if not check.passed:
            return skip(check.reason or "branding_in_view", gate=gate.as_dict(), crops=crops, **source)

    progress(0.5, "glass-composite")
    out = rgb
    groups_meta = []
    background = None
    for group in groups:
        group_alpha = np.clip(np.ascontiguousarray(group["alpha"], dtype=np.float32), 0.0, 1.0)
        out, info = compose_glass_backdrop(out, group_alpha, context, cfg, view=group["view"], mirrored=group["mirrored"])
        backdrop = info.pop("backdrop")
        a = group_alpha[..., None]
        background = backdrop if background is None else (background * (1.0 - a) + backdrop * a).astype(np.uint8)
        groups_meta.append({**info, "panes": group["panes"]})
    if background is not None:
        debug.rgb("background.jpg", background)
    mark("composite")
    meta = {
        "applied": True,
        "plate": GLASS_PLATE,
        **source,
        "gate": gate.as_dict(),
        "panes": groups_meta,
        "timingsMs": times,
    }
    return GlassOutcome(rgb=out, applied=True, warnings=[], meta=meta)
