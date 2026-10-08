"""The AutoExperten processing pipeline V1 (deterministic, no generative AI).

    original → decode (orientation, sRGB) → segmentation → high-res alpha
    → cut-out of the ORIGINAL vehicle pixels → placement → showroom background
    → contact shadow → edge harmonisation → conservative light matching
    → JPEG export

Interior/detail shots are not composited (a showroom behind a cockpit makes
no sense); they are exported unchanged except for orientation/size.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..presets import EXTERIOR_SHOTS, SHOWROOM_FALLBACK, BackgroundProvider, Preset
from .color import linear_to_u8, srgb_to_linear
from .composite import extract_vehicle, feather_layer, over, place_layer, resample_layer
from .debug import NULL_DEBUG, DebugSink
from .decode import decode_image
from .export import encode_jpeg
from .harmonize import decontaminate_edges, light_wrap
from .light import effective_limits, estimate_correction, guarded_correction
from .mask import QualityWarning, assess_mask, clean_mask, mask_bbox, refine_alpha
from .placement import compute_placement, output_size
from .segmentation import VehicleSegmenter
from .shadow import apply_shadow, bottom_profile, build_shadow, contact_rise

ProgressFn = Callable[[float, str], None]

PIPELINE_VERSION = "1.0.0"


@dataclass
class ProcessResult:
    jpeg: bytes
    width: int
    height: int
    warnings: list[QualityWarning] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _noop(_progress: float, _step: str) -> None:
    return None


class _Timer:
    def __init__(self) -> None:
        self.times: dict[str, int] = {}
        self._start = time.perf_counter()

    def mark(self, step: str) -> None:
        now = time.perf_counter()
        self.times[step] = int(round((now - self._start) * 1000))
        self._start = now


class ShowroomNotReleasedError(Exception):
    """Only the emergency fallback showroom exists – results must not be stored."""


def process_photo(
    data: bytes,
    *,
    preset: Preset,
    segmenter: VehicleSegmenter,
    backgrounds: BackgroundProvider,
    shot_key: str | None = None,
    debug: DebugSink = NULL_DEBUG,
    progress: ProgressFn = _noop,
    require_master_showroom: bool = False,
) -> ProcessResult:
    """`require_master_showroom`: refuse exterior shots while only the emergency
    fallback showroom is available (used for results stored in the app)."""
    timer = _Timer()
    progress(0.02, "decode")
    decoded = decode_image(data)
    debug.rgb("original.jpg", decoded.rgb)
    timer.mark("decode")

    base_meta = {
        "pipelineVersion": PIPELINE_VERSION,
        "preset": preset.id,
        "shotKey": shot_key,
        "source": {
            "width": decoded.width,
            "height": decoded.height,
            "format": decoded.format,
            "exifOrientation": decoded.orientation,
            "convertedFromProfile": decoded.converted_from_profile,
        },
    }

    if shot_key is not None and shot_key not in EXTERIOR_SHOTS:
        return _passthrough(decoded.rgb, preset, debug, timer, base_meta, progress)
    if require_master_showroom and backgrounds.source(preset) == SHOWROOM_FALLBACK:
        raise ShowroomNotReleasedError("final showroom master photo missing")

    # STEP 2 – segmentation
    progress(0.08, "segment")
    coarse = segmenter.segment(decoded.rgb)
    if coarse.shape != decoded.rgb.shape[:2]:
        raise ValueError("segmenter returned a mask of the wrong size")
    timer.mark("segment")

    # STEP 3 – high-resolution alpha
    progress(0.45, "mask")
    alpha = refine_alpha(decoded.rgb, coarse)
    alpha, mask_info = clean_mask(alpha)
    bbox = mask_bbox(alpha)
    warnings = assess_mask(alpha, bbox, mask_info)
    debug.gray("mask.png", alpha)
    timer.mark("mask")

    # STEP 4 – cut out the ORIGINAL vehicle pixels (linear light)
    progress(0.55, "cutout")
    linear = srgb_to_linear(decoded.rgb)
    layer = extract_vehicle(linear, alpha, bbox)
    clean_rgb = decontaminate_edges(layer.rgb, layer.alpha, radius=max(2.0, 0.002 * bbox.width))
    if debug.enabled:
        debug.rgba("vehicle-transparent.png", linear_to_u8(clean_rgb), layer.alpha)
    timer.mark("cutout")

    # STEP 9 (measured on the original, applied to the vehicle layer only)
    correction_plan = estimate_correction(decoded.rgb, alpha, preset.adjustments)
    corrected_rgb, correction = guarded_correction(clean_rgb, layer.alpha, correction_plan)
    layer = type(layer)(rgb=corrected_rgb, alpha=layer.alpha, offset_x=layer.offset_x, offset_y=layer.offset_y)
    timer.mark("light")

    # STEP 6 – size and position
    progress(0.62, "placement")
    placement_cfg = preset.placement_for(shot_key)
    width, height = output_size(bbox, preset.output, placement_cfg)
    # the branded showroom decides how high the vehicle may reach (logo stays visible)
    showroom = backgrounds.get(preset, width, height)
    if require_master_showroom and showroom.is_fallback:  # master turned out unreadable
        raise ShowroomNotReleasedError("final showroom master photo unusable")
    min_top = None
    if showroom.branding.boxes:
        min_top = showroom.branding.bottom / height + preset.branding.clearance
    profile, has_profile = bottom_profile(alpha, bbox.x0, bbox.x1)
    rise = contact_rise(profile, has_profile, bbox.height)
    placement = compute_placement(
        bbox,
        width,
        height,
        placement_cfg,
        floor_horizon=showroom.floor_horizon,
        contact_rise=rise,
        min_top=min_top,
    )
    if placement.limited_by == "horizon" and placement.width < 0.85 * placement_cfg.width_ratio * width:
        warnings.append(
            QualityWarning(
                "steep_perspective",
                "Die Perspektive ist sehr steil – das Fahrzeug wird kleiner dargestellt. "
                "Bitte etwas weiter entfernt und auf Höhe der Fahrzeugmitte fotografieren.",
            )
        )
    if placement.scale > 1.25:
        warnings.append(
            QualityWarning(
                "low_resolution",
                "Das Fahrzeug musste stark vergrößert werden – für beste Qualität näher heran oder mit höherer Auflösung fotografieren.",
            )
        )
    scaled = feather_layer(resample_layer(layer, placement.scale), sigma=0.6)
    vehicle_rgb, vehicle_alpha, origin = place_layer(
        scaled,
        placement.left,
        placement.top,
        width,
        height,
    )
    timer.mark("placement")

    # STEP 5 – showroom background (branding already composited, vehicle not yet)
    progress(0.72, "background")
    debug.rgb("background.jpg", np.asarray(showroom.rgb))
    background = srgb_to_linear(np.asarray(showroom.rgb))
    if debug.enabled:
        debug.rgb("composite-before-shadow.jpg", linear_to_u8(over(background, vehicle_rgb, vehicle_alpha)))
    timer.mark("background")

    # STEP 7 – contact shadow (on the floor only, under the vehicle)
    progress(0.8, "shadow")
    shadow = build_shadow(vehicle_alpha, placement, preset.shadow, floor_y=showroom.floor_horizon * height)
    debug.gray("shadow.png", shadow)
    floor = apply_shadow(background, shadow, preset.shadow.color)
    timer.mark("shadow")

    # STEP 8 – edge harmonisation + composite
    progress(0.88, "composite")
    limits = effective_limits(preset.adjustments)
    wrapped = light_wrap(
        vehicle_rgb, vehicle_alpha, floor, strength=limits["light_wrap"], width_px=max(1.5, 0.0012 * width)
    )
    final = linear_to_u8(over(floor, wrapped, vehicle_alpha))
    timer.mark("composite")

    # STEP 10 – export
    progress(0.95, "export")
    jpeg = encode_jpeg(final, preset.output.jpeg_quality)
    debug.raw("final.jpg", jpeg)
    timer.mark("export")

    if showroom.is_fallback:
        warnings.append(
            QualityWarning(
                "showroom_fallback",
                "Fallback-Showroom aktiv – das finale AutoExperten-Showroom-Foto fehlt noch.",
            )
        )
    metadata = {
        **base_meta,
        "shotKind": "exterior_showroom",
        "segmenter": getattr(segmenter, "name", type(segmenter).__name__),
        "output": {"width": width, "height": height},
        "mask": {
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            "coverage": round(mask_info.coverage, 4),
            "componentsTotal": mask_info.components_total,
            "componentsKept": mask_info.components_kept,
            "holesFilled": mask_info.holes_filled,
            "splitParts": mask_info.split_parts,
            "uncertainFraction": round(mask_info.uncertain_fraction, 4),
        },
        "placement": {
            "scale": round(placement.scale, 4),
            "left": round(placement.left, 1),
            "top": round(placement.top, 1),
            "width": round(placement.width, 1),
            "height": round(placement.height, 1),
            "limitedBy": placement.limited_by,
            "contactRise": round(rise, 1),
            "layerOrigin": list(origin),
        },
        "adjustments": correction.as_dict(),
        "showroomSource": showroom.source,
        "showroomPlaceholder": showroom.is_fallback,
        "branding": {"boxes": [list(b) for b in showroom.branding.boxes], "bottom": showroom.branding.bottom},
        "timingsMs": timer.times,
    }
    debug.json("metadata.json", {**metadata, "warnings": [w.__dict__ for w in warnings]})
    progress(1.0, "done")
    return ProcessResult(jpeg=jpeg, width=width, height=height, warnings=warnings, metadata=metadata)


def _passthrough(
    rgb: np.ndarray, preset: Preset, debug: DebugSink, timer: _Timer, base_meta: dict, progress: ProgressFn
) -> ProcessResult:
    """Interior/detail shots: no background replacement, no colour changes."""
    progress(0.5, "passthrough")
    height, width = rgb.shape[:2]
    limit = preset.output.long_edge_max
    scale = min(1.0, limit / float(max(height, width)))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (int(round(width * scale)), int(round(height * scale))), interpolation=cv2.INTER_AREA)
    jpeg = encode_jpeg(rgb, preset.output.jpeg_quality)
    debug.raw("final.jpg", jpeg)
    timer.mark("export")
    metadata = {
        **base_meta,
        "shotKind": "interior_passthrough",
        "output": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "showroomPlaceholder": False,
        "timingsMs": timer.times,
    }
    debug.json("metadata.json", metadata)
    progress(1.0, "done")
    return ProcessResult(jpeg=jpeg, width=int(rgb.shape[1]), height=int(rgb.shape[0]), metadata=metadata)
