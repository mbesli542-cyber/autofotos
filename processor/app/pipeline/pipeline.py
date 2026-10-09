"""The AutoExperten processing pipeline (deterministic, no generative AI).

Exterior shots:

    decode (orientation, sRGB) → resolution gate → segmentation → model alpha
    snapped onto colour edges → clean-up (foreign spikes cut) → bbox
    → vehicle geometry (tyre contacts, near end, perspective)
    → plate selection (the shot's 3D-showroom plate; mirrored 3/4 plate if the
      photo shows the other side) → placement v2 on the plate → quality gate
    → cut-out of the ORIGINAL vehicle pixels → conservative light matching
    → scale/place → branded plate → grounding (tyre contact + underbody + calibrated
      ambient shadow) → floor reflection
    → light wrap → composite → JPEG export

Interior/detail shots are not composited (a showroom behind a cockpit makes
no sense); they are exported unchanged except for orientation/size.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..presets import EXTERIOR_SHOTS, SHOWROOM_PLATES, BackgroundProvider, Preset
from ..showroom.plates import MIRROR_PLATES, PlateSet
from .color import linear_to_u8, srgb_to_linear
from .composite import extract_vehicle, feather_layer, over, place_layer, resample_layer
from .debug import NULL_DEBUG, DebugSink
from .decode import decode_image
from .export import encode_jpeg
from .grounding import (
    apply_grounding,
    build_ground_model,
    clean_ground_fringe,
    draw_ground_model,
    ground_contacts,
    ground_line_without_contacts,
)
from .harmonize import decontaminate_edges, light_wrap
from .light import effective_limits, estimate_correction, guarded_correction
from .mask import QualityWarning, assess_mask, clean_mask, mask_bbox
from .matting import model_alpha, refine_edges
from .placement import compute_placement, output_size
from .reflection import apply_reflection
from .quality import (
    GateReport,
    check_contacts,
    check_cropped,
    check_mask,
    check_perspective,
    check_source_resolution,
    check_upscale,
)
from .segmentation import VehicleSegmenter
from .vehicle_geometry import VehicleGeometry, analyse_vehicle

ProgressFn = Callable[[float, str], None]

PIPELINE_VERSION = "2.0.0"

SHOT_TITLES = {
    "front_left_45": "Vorne links (45°)",
    "front_right_45": "Vorne rechts (45°)",
    "rear_left_45": "Hinten links (45°)",
    "rear_right_45": "Hinten rechts (45°)",
}

#: The photo's near end must be at least this certain before another plate is used.
PLATE_SWITCH_CONFIDENCE = 0.3


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


def select_plate(shot_key: str, geometry: VehicleGeometry, plates: PlateSet) -> tuple[str, dict]:
    """The shot's own plate – or, for a 3/4 shot whose photo shows the car from the
    other side (near end on the opposite image side), the mirrored plate of the
    same front/rear group. Decided against each plate's proxy geometry."""
    own = plates.plate(shot_key)
    expected = own.expected_near_end()
    decision = {
        "shotPlate": shot_key,
        "expectedNearEnd": expected,
        "photoNearEnd": geometry.near_end,
        "photoNearEndConfidence": round(geometry.near_end_confidence, 3),
        "switched": False,
    }
    partner_key = MIRROR_PLATES.get(shot_key)
    if (
        partner_key is None
        or expected is None
        or geometry.near_end is None
        or geometry.near_end == expected
        or geometry.near_end_confidence < PLATE_SWITCH_CONFIDENCE
    ):
        return shot_key, decision
    partner = plates.plate(partner_key)
    if partner.expected_near_end() != geometry.near_end:
        return shot_key, decision
    decision["switched"] = True
    return partner_key, decision


def process_photo(
    data: bytes,
    *,
    preset: Preset,
    segmenter: VehicleSegmenter,
    backgrounds: BackgroundProvider,
    shot_key: str | None = None,
    debug: DebugSink = NULL_DEBUG,
    progress: ProgressFn = _noop,
) -> ProcessResult:
    """Raises QualityGateError (app/pipeline/quality.py) for photos that would give a
    bad listing image and ShowroomUnavailableError while the plate set is unusable."""
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
    shot = shot_key or "front_left_45"
    # fail fast (before the expensive segmentation) without plates or with a tiny photo
    plates = backgrounds.plate_set(preset)
    check_source_resolution(decoded.width, decoded.height, preset.quality)

    # segmentation
    progress(0.08, "segment")
    coarse = segmenter.segment(decoded.rgb)
    if coarse.shape != decoded.rgb.shape[:2]:
        raise ValueError("segmenter returned a mask of the wrong size")
    timer.mark("segment")

    # high-resolution alpha: the model alpha, snapped onto colour edges (never grown
    # beyond the model's own contour, thin parts never thinned), then the vehicle is kept
    # (foreign spikes cut; an uncut one fails the mask check of the quality gate)
    progress(0.45, "mask")
    alpha, edges = refine_edges(decoded.rgb, coarse)
    model = model_alpha(coarse)
    alpha, mask_info = clean_mask(alpha, rgb=decoded.rgb, model=model)
    del model
    bbox = mask_bbox(alpha)
    warnings = [w for w in assess_mask(alpha, bbox, mask_info) if w.code != "vehicle_cropped"]
    debug.gray("mask.png", alpha)
    timer.mark("mask")

    # geometry → plate → placement
    progress(0.52, "geometry")
    geometry = analyse_vehicle(alpha, bbox, rgb=decoded.rgb)
    plate_key, selection = select_plate(shot, geometry, plates)
    plate = plates.plate(plate_key)
    if selection["switched"]:
        warnings.append(
            QualityWarning(
                "plate_mirrored",
                f"Das Foto zeigt das Fahrzeug von der anderen Seite – es wurde der Showroom-Hintergrund "
                f"„{SHOT_TITLES.get(plate_key, plate_key)}“ verwendet. Bitte die Aufnahmeposition prüfen.",
            )
        )
    width, height = output_size(bbox, preset.output, plate.target_width_ratio)
    showroom = backgrounds.get(preset, plate_key, width, height)
    placement_cfg = preset.placement_for(shot)
    # the roof stays below every branding element on the wall (minus the clearance)
    boxes = showroom.branding.boxes
    min_top = max(b[4] for b in boxes) + preset.branding.clearance * height if boxes else None
    x0 = int(np.clip(round(0.5 * width - 0.5 * plate.target_width_ratio * width), 0, width - 1))
    x1 = int(np.clip(round(0.5 * width + 0.5 * plate.target_width_ratio * width), x0 + 1, width))
    # lowest tyre contact onto the proxy's lowest tyre contact; without visible tyre
    # contacts (front/rear: the bumper hides them) lowest point onto the proxy's lowest point
    ground_v = plate.ground_v if geometry.contacts else ground_line_without_contacts(plate)
    placement = compute_placement(
        bbox,
        width,
        height,
        placement_cfg,
        target_width_ratio=plate.target_width_ratio,
        ground_v=min(ground_v, 0.97),
        ground_y_src=geometry.ground_y,
        roof_y_src=geometry.solid_top,
        contact_rise=geometry.contact_rise,
        floor_y=float(showroom.floor_top[x0:x1].max()),
        min_top=min_top,
    )
    timer.mark("geometry")

    # quality gate (cheap checks, all before the pixel work)
    gate = GateReport()
    check_cropped(gate, alpha, bbox, preset.quality)
    check_upscale(gate, placement, bbox, decoded.width, preset.quality)
    check_mask(gate, alpha, bbox, mask_info, preset.quality)
    check_perspective(gate, geometry, plate, preset.quality)
    check_contacts(gate, geometry, shot, preset.quality)
    if debug.enabled:
        debug.rgb("geometry.jpg", _geometry_overlay(decoded.rgb, alpha, geometry))
    gate.raise_if_failed(
        plateUsed=plate_key, plateSelection=selection, geometry=geometry.as_dict(), placement=placement.as_dict()
    )
    if placement.target_fraction < placement_cfg.min_target_fraction:
        warnings.append(
            QualityWarning(
                "vehicle_reduced",
                "Das Fahrzeug wird kleiner als üblich dargestellt (hohes Fahrzeug oder steile Perspektive), "
                "damit Logo und Boden frei bleiben.",
            )
        )
    if placement.scale > 1.25:
        warnings.append(
            QualityWarning(
                "low_resolution",
                "Das Fahrzeug musste vergrößert werden – für beste Qualität näher heran oder mit höherer Auflösung fotografieren.",
            )
        )

    # cut out the ORIGINAL vehicle pixels (linear light)
    progress(0.6, "cutout")
    linear = srgb_to_linear(decoded.rgb)
    layer = extract_vehicle(linear, alpha, bbox)
    clean_rgb = decontaminate_edges(layer.rgb, layer.alpha, radius=max(2.0, 0.002 * bbox.width))
    if debug.enabled:
        debug.rgba("vehicle-transparent.png", linear_to_u8(clean_rgb), layer.alpha)
    timer.mark("cutout")

    # conservative light matching (measured on the original, applied to the vehicle layer only)
    correction_plan = estimate_correction(decoded.rgb, alpha, preset.adjustments)
    corrected_rgb, correction = guarded_correction(clean_rgb, layer.alpha, correction_plan)
    layer = type(layer)(rgb=corrected_rgb, alpha=layer.alpha, offset_x=layer.offset_x, offset_y=layer.offset_y)
    timer.mark("light")

    # scale and place
    progress(0.66, "placement")
    scaled = feather_layer(resample_layer(layer, placement.scale), sigma=0.6)
    vehicle_rgb, vehicle_alpha, origin = place_layer(scaled, placement.left, placement.top, width, height)
    contacts_out = ground_contacts(geometry.contacts, placement, bbox)
    timer.mark("placement")

    # branded plate (vehicle not yet)
    progress(0.72, "background")
    debug.rgb("background.jpg", np.asarray(showroom.rgb))
    background = srgb_to_linear(np.asarray(showroom.rgb))
    if debug.enabled:
        debug.rgb("composite-before-shadow.jpg", linear_to_u8(over(background, vehicle_rgb, vehicle_alpha)))
    timer.mark("background")

    # grounding v3: ground model (car pose, footprint, floor line, tyres) → light ground/snow
    # remnants removed from the cut-out's bottom → tyre contact + underbody + calibrated ambient
    # shadow → floor reflection (replaces the plate's own where the car blocks it), floor only
    progress(0.8, "shadow")
    ground = build_ground_model(vehicle_alpha, placement, contacts_out, showroom)
    fringe: dict = {}
    vehicle_alpha = clean_ground_fringe(vehicle_rgb, vehicle_alpha, ground, info=fringe)
    grounding: dict = {"fringe": fringe}
    floor = apply_grounding(
        background, vehicle_alpha, placement, contacts_out, showroom, preset, info=grounding, model=ground
    )
    if ground.pose.clamped:
        grounding["warnings"] = ["poseClamped"]
    reflection: dict = {}
    floor = apply_reflection(
        floor, vehicle_rgb, vehicle_alpha, contacts_out, showroom, placement, preset.reflection,
        info=reflection, model=ground, background_linear=background,
    )  # fmt: skip
    if debug.enabled:
        small = min(1.0, 1600.0 / max(decoded.rgb.shape[:2]))

        def to_source(x, y):
            return (
                (bbox.x0 + (x - placement.left) / placement.scale) * small,
                (bbox.y0 + (y - placement.top) / placement.scale) * small,
            )

        overlay = _geometry_overlay(decoded.rgb, alpha, geometry)
        debug.rgb("geometry.jpg", draw_ground_model(overlay, ground, transform=to_source))
        debug.rgb("grounding.jpg", draw_ground_model(linear_to_u8(over(floor, vehicle_rgb, vehicle_alpha)), ground))
        light = background.mean(axis=-1)
        # black plate pixels carry no measurable darkening (avoid 0/0 speckles above the floor)
        darkening = np.where(light > 1e-4, 1.0 - floor.mean(axis=-1) / np.maximum(light, 1e-4), 0.0)
        debug.gray("shadow.png", np.clip(darkening, 0.0, 1.0))
    timer.mark("shadow")

    # edge harmonisation + composite
    progress(0.88, "composite")
    limits = effective_limits(preset.adjustments)
    wrapped = light_wrap(
        vehicle_rgb, vehicle_alpha, floor, strength=limits["light_wrap"], width_px=max(1.5, 0.0012 * width)
    )
    final = linear_to_u8(over(floor, wrapped, vehicle_alpha))
    timer.mark("composite")

    # export
    progress(0.95, "export")
    jpeg = encode_jpeg(final, preset.output.jpeg_quality)
    debug.raw("final.jpg", jpeg)
    timer.mark("export")

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
            "modelFragments": mask_info.model_fragments,
            "detachedDropped": mask_info.detached_dropped,
            "foreignRemoved": mask_info.foreign_removed,
            "foreignSuspected": mask_info.foreign_suspected,
            # colour-edge diagnostics (edgeConfidence, lowContrastFraction, …): metadata only, never a gate
            "edges": edges,
        },
        "plateUsed": plate_key,
        "plate": plate.summary(),
        "plateSelection": selection,
        "geometry": geometry.as_dict(),
        "contacts": {
            "source": [c.as_list() for c in geometry.contacts],
            "output": [[round(c.x, 1), round(c.y, 1)] for c in contacts_out],
        },
        "qualityGate": gate.as_dict(),
        "placement": {**placement.as_dict(), "layerOrigin": list(origin)},
        "grounding": grounding,
        "reflection": reflection,
        "adjustments": correction.as_dict(),
        "showroomSource": SHOWROOM_PLATES,
        "showroomPlaceholder": False,
        "branding": {"boxes": [list(b) for b in showroom.branding.boxes], "bottom": showroom.branding.bottom},
        "timingsMs": timer.times,
    }
    debug.json("metadata.json", {**metadata, "warnings": [w.__dict__ for w in warnings]})
    progress(1.0, "done")
    return ProcessResult(jpeg=jpeg, width=width, height=height, warnings=warnings, metadata=metadata)


def _geometry_overlay(rgb: np.ndarray, alpha: np.ndarray, geometry: VehicleGeometry) -> np.ndarray:
    """Debug view: dimmed photo, vehicle mask outline, bbox and tyre contacts."""
    scale = min(1.0, 1600.0 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb.copy()
    mask = cv2.resize(alpha, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 0.5
    out = (small.astype(np.float32) * 0.55).astype(np.uint8)
    out[mask] = (0.6 * small[mask] + 0.4 * np.array([40, 120, 255])).astype(np.uint8)
    b = geometry.bbox
    cv2.rectangle(out, (int(b.x0 * scale), int(b.y0 * scale)), (int(b.x1 * scale), int(b.y1 * scale)), (255, 255, 0), 2)
    for c in geometry.candidates:
        colour = (0, 255, 0) if c.confidence >= 0.5 else (255, 60, 60)
        cv2.circle(out, (int(c.x * scale), int(c.y * scale)), 7, colour, 2)
    return out


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
