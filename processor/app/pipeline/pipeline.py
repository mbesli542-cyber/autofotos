"""The AutoExperten processing pipeline (deterministic, no generative AI).

Exterior shots:

    decode (orientation, sRGB) → resolution gate → vehicle detector (full frame)
    → segmentation (portrait/square frames: on a window around the vehicle)
    → model alpha snapped onto colour edges → clean-up (foreign spikes cut;
    semantic check: objects the detector does not see as part of THE vehicle
    dropped, cut in their contested zone by SAM 2.1 or a zoom pass, or the
    photo rejected; vehicle parts the mask lost – taken from the
    whole-frame pass after a window pass, otherwise the photo is rejected, a
    partly lost piece gives a hint) → bbox
    → vehicle geometry (tyre contacts, near end, perspective)
    → plate selection (the shot's 3D-showroom plate; mirrored 3/4 plate if the
      photo shows the other side) → placement v2 on the plate → quality gate
    → cut-out of the ORIGINAL vehicle pixels → light matching (conservative; or, with the
      preset's vehicleAdjustments.studioLight, the guarded global studio light match)
    → scale/place → branded plate → grounding (tyre contact + underbody + calibrated
      ambient shadow) → floor reflection
    → light wrap → composite → JPEG export

Interior/detail shots are not composited (a showroom behind a cockpit makes
no sense); they are exported unchanged except for orientation/size.
EXPERIMENTAL, off by default: detail shots listed in the preset's
``detailBackdrop`` get a defocused showroom crop behind the original part
(detail_backdrop.py) – or stay the original with a hint when the part mask is
unclear.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..presets import EXTERIOR_SHOTS, SHOWROOM_PLATES, BackgroundProvider, Preset, PresetConfigError
from ..showroom.plates import MIRROR_PLATES, PlateSet
from .color import linear_to_u8, srgb_to_linear
from .composite import extract_vehicle, feather_layer, over, place_layer, resample_layer
from .debug import NULL_DEBUG, DebugSink
from .decode import decode_image
from .detail_backdrop import detail_backdrop_applies, run_detail
from .detection import vehicle_box
from .export import encode_jpeg
from .grounding import (
    apply_grounding,
    build_ground_model,
    clean_ground_fringe,
    draw_ground_model,
    ground_contacts,
)
from .harmonize import decontaminate_edges, light_wrap
from .light import (
    effective_limits,
    estimate_correction,
    guarded_correction,
    measure_studio,
    studio_light_correction,
)
from .mask import QualityWarning, assess_mask, clean_mask, mask_bbox
from .matting import model_alpha, refine_edges
from .quality import (
    GateReport,
    check_contacts,
    check_cropped,
    check_foreign,
    check_mask,
    check_parts,
    check_perspective,
    check_source_resolution,
    check_upscale,
)
from .reflection import apply_reflection
from .room_depth import place_in_room
from .segmentation import VehicleSegmenter, merge_parts, segment_vehicle
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
        """Time since the previous mark, added to ``step`` (a step may be marked in parts)."""
        now = time.perf_counter()
        self.times[step] = self.times.get(step, 0) + int(round((now - self._start) * 1000))
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
        if detail_backdrop_applies(preset.detail_backdrop, shot_key):
            # EXPERIMENTAL, opt-in (preset detailBackdrop): the original part in front of a defocused
            # plate crop – or the original with a hint when the part mask is unclear
            detail = run_detail(
                decoded.rgb, preset=preset, segmenter=segmenter, backgrounds=backgrounds, debug=debug, progress=progress
            )
            timer.mark("detail")
            meta = {**base_meta, "detailBackdrop": detail.meta}
            return _passthrough(
                detail.rgb,
                preset,
                debug,
                timer,
                meta,
                progress,
                warnings=detail.warnings,
                shot_kind=detail.shot_kind,
                progress_at=0.9,  # run_detail reported up to 0.8 – never step back
            )
        return _passthrough(decoded.rgb, preset, debug, timer, base_meta, progress)
    shot = shot_key or "front_left_45"
    # fail fast (before the expensive segmentation) without plates, without a usable
    # official logo or with a tiny photo
    plates = backgrounds.plate_set(preset)
    if config_error := backgrounds.config_error(preset):
        raise PresetConfigError(config_error)
    check_source_resolution(decoded.width, decoded.height, preset.quality)

    # the vehicle detector (instance segmentation, detection.py): its full-frame view
    # places the window of portrait/square frames and starts the semantic check
    detector = getattr(segmenter, "detector", None)
    detections = None
    if detector is not None:
        progress(0.05, "detect")
        detections = detector.detect(decoded.rgb)
        timer.mark("detect")

    # segmentation (BiRefNet; portrait/square frames on the ORIGINAL pixels of a window
    # around the vehicle – as one thin band of a tall frame the car merged with its surroundings)
    progress(0.08, "segment")
    box = vehicle_box(detections, decoded.width, decoded.height) if detections else None
    coarse, segmentation = segment_vehicle(decoded.rgb, segmenter, box)
    timer.mark("segment")

    # high-resolution alpha: the model alpha, snapped onto colour edges (never grown
    # beyond the model's own contour, thin parts never thinned), then the vehicle is kept
    # (foreign spikes cut; objects the detector does not see as part of the vehicle
    # dropped when separate; an uncut one fails the quality gate, as does a vehicle part
    # the mask lost)
    progress(0.45, "mask")
    zoom = _zoom_segmenter(decoded.rgb, segmenter, segmentation)
    sam = _sam_prompter(decoded.rgb, getattr(segmenter, "sam2", None), segmentation)
    alpha, edges, mask_info = _vehicle_alpha(decoded.rgb, coarse, detector, detections, zoom, sam)
    if (mask_info.parts_missing or mask_info.parts_partial) and segmentation.get("windowUsed"):
        # the window pass lost (a piece of) a part the detector sees (a dark mirror in front of a
        # dark car, the base of a mirror); the whole-frame pass may have it – taken only inside
        # that part's claim, as far as it belongs to the part between colour edges
        timer.mark("mask")
        progress(0.5, "segment-parts")
        started = time.perf_counter()
        whole = segmenter.segment(decoded.rgb)
        if whole.shape != coarse.shape:
            raise ValueError("segmenter returned a mask of the wrong size")
        merged, taken = merge_parts(
            coarse,
            whole,
            mask_info.part_regions,
            mask_info.semantic_scale,
            rgb=decoded.rgb,
            cell=mask_info.semantic_cell,
        )
        del whole
        segmentation["partsFromWholeFrame"] = {
            "missing": mask_info.parts_missing,
            "partial": mask_info.parts_partial,
            "taken": taken,
            "ms": int(round((time.perf_counter() - started) * 1000)),
        }
        if taken:
            coarse = merged
            alpha, edges, mask_info = _vehicle_alpha(decoded.rgb, coarse, detector, detections, zoom, sam)
        del merged
        timer.mark("partsFromWholeFrame")
    del coarse
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
    placement_cfg = preset.placement_for(shot)
    # the plate's spot – or (preview variants: placement.depthOffsetM / widthScale) further back
    # in the room / larger, fitted per photo so the car stays in front of the wall (room_depth.py)
    room = place_in_room(
        plate,
        plate_key,
        preset=preset,
        placement_cfg=placement_cfg,
        backgrounds=backgrounds,
        bbox=bbox,
        geometry=geometry,
        alpha=alpha,
    )
    plate, showroom, placement = room.plate, room.showroom, room.placement
    width, height = room.width, room.height
    timer.mark("geometry")

    # quality gate (cheap checks, all before the pixel work)
    gate = GateReport()
    check_cropped(gate, alpha, bbox, preset.quality)
    check_upscale(gate, placement, bbox, decoded.width, preset.quality, decoded.height)
    check_mask(gate, alpha, bbox, mask_info, preset.quality)
    check_perspective(gate, geometry, plate, preset.quality)
    check_contacts(gate, geometry, shot, preset.quality)
    check_foreign(gate, mask_info)
    check_parts(gate, mask_info)
    if debug.enabled:
        debug.rgb("geometry.jpg", _geometry_overlay(decoded.rgb, alpha, geometry, mask_info, segmentation))
    gate.raise_if_failed(
        plateUsed=plate_key,
        plateSelection=selection,
        geometry=geometry.as_dict(),
        placement=placement.as_dict(),
        # why a foreign object was reported (boxes in photo px, the detector's view)
        mask={
            "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
            "foreignRemoved": mask_info.foreign_removed,
            "foreignSuspected": mask_info.foreign_suspected,
            "partsMissing": mask_info.parts_missing,
            "partsPartial": mask_info.parts_partial,
            "vehicleDetected": mask_info.vehicle_detected,
            "semantic": mask_info.semantic,
        },
        segmentation=segmentation,
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

    # light matching of the vehicle layer: the conservative correction (measured on the original),
    # or – vehicleAdjustments.studioLight (the dealer's decision) – the guarded global light match
    # towards the studio's light measured on the plate around the car's spot
    if preset.adjustments.studio_light.enabled:
        studio = measure_studio(
            np.asarray(showroom.rgb), (placement.left, placement.top, placement.width, placement.height)
        )
        corrected_rgb, adjustments_meta = studio_light_correction(
            clean_rgb, layer.alpha, studio, preset.adjustments.studio_light
        )
    else:
        correction_plan = estimate_correction(decoded.rgb, alpha, preset.adjustments)
        corrected_rgb, correction = guarded_correction(clean_rgb, layer.alpha, correction_plan)
        adjustments_meta = correction.as_dict()
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
    if not ground.contacts_plausible:
        # two "tyres" of one side far closer than a wheelbase (an exhaust tip or a diffuser taken
        # as a tyre): the floor line ignores the side line through them, the result is shown
        # for a check
        grounding["warnings"] = [*grounding.get("warnings", []), "contactsImplausible"]
        warnings.append(
            QualityWarning(
                "ground_contact_uncertain",
                "Die Reifen wurden nicht sicher erkannt (z. B. Auspuff oder Diffusor statt Reifen) – "
                "bitte den Schatten unter dem Fahrzeug prüfen.",
            )
        )
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

        overlay = _geometry_overlay(decoded.rgb, alpha, geometry, mask_info, segmentation)
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
        "detector": getattr(detector, "name", None) if detector is not None else None,
        "sam2": getattr(getattr(segmenter, "sam2", None), "name", None),
        "segmentation": segmentation,
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
            "partsMissing": mask_info.parts_missing,
            "partsPartial": mask_info.parts_partial,
            "vehicleDetected": mask_info.vehicle_detected,
            # semantic check: the detector's vehicle, tiles, verified regions (metadata)
            "semantic": mask_info.semantic,
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
        "placement": {
            **placement.as_dict(),
            "layerOrigin": list(origin),
            **({"depthOffsetM": round(room.depth_offset_m, 3), "roomFit": room.info} if room.info else {}),
        },
        "grounding": grounding,
        "reflection": reflection,
        "adjustments": adjustments_meta,
        "showroomSource": SHOWROOM_PLATES,
        "showroomPlaceholder": False,
        "branding": {"boxes": [list(b) for b in showroom.branding.boxes], "bottom": showroom.branding.bottom},
        "timingsMs": timer.times,
    }
    debug.json("metadata.json", {**metadata, "warnings": [w.__dict__ for w in warnings]})
    progress(1.0, "done")
    return ProcessResult(jpeg=jpeg, width=width, height=height, warnings=warnings, metadata=metadata)


def _vehicle_alpha(rgb: np.ndarray, coarse: np.ndarray, detector, detections, zoom=None, sam=None):
    """(alpha, edge diagnostics, MaskInfo) of the coarse segmentation: snapped onto colour
    edges, then cleaned up (and semantically checked with the detector; ``sam``: SAM 2.1 on a
    window, for the SAM cut of a glued foreign region in its contested zone; ``zoom``: BiRefNet on
    a window, for the zoom cut)."""
    alpha, edges = refine_edges(rgb, coarse)
    model = model_alpha(coarse)
    alpha, mask_info = clean_mask(
        alpha, rgb=rgb, model=model, detector=detector, detections=detections, zoom_segment=zoom, sam=sam
    )
    return alpha, edges, mask_info


class _SamPrompter:
    """SAM 2.1 on a window of the ORIGINAL pixels (``mask.PromptSegmenter``): the window's
    embedding is computed once per job (the mask step may run twice); the encodes are listed in
    ``segmentation["samPasses"]``. The sessions load on the first encode – a job without a
    contested region never loads them."""

    def __init__(self, rgb: np.ndarray, sam2, segmentation: dict):
        self._rgb = rgb
        self._sam2 = sam2
        self._segmentation = segmentation
        self._done: dict[tuple[int, int, int, int], object] = {}

    def encode(self, window: tuple[int, int, int, int]):
        if window not in self._done:
            x0, y0, x1, y1 = window
            started = time.perf_counter()
            self._done[window] = self._sam2.encode(np.ascontiguousarray(self._rgb[y0:y1, x0:x1]))
            self._segmentation.setdefault("samPasses", []).append(
                {"window": list(window), "ms": int(round((time.perf_counter() - started) * 1000))}
            )
        return self._done[window]

    def predict(self, embedding, points, box=None, multimask=True):
        return self._sam2.predict(embedding, points, box, multimask=multimask)


def _sam_prompter(rgb: np.ndarray, sam2, segmentation: dict) -> _SamPrompter | None:
    """The SAM 2.1 prompter of ``mask.sam_cut`` for this photo, or None without a segmenter."""
    return None if sam2 is None else _SamPrompter(rgb, sam2, segmentation)


def _zoom_segmenter(rgb: np.ndarray, segmenter: VehicleSegmenter, segmentation: dict):
    """BiRefNet on a window of the ORIGINAL pixels (the zoom pass of ``mask.zoom_cut``), each
    window run once per job (the mask step may run twice); passes are listed in ``segmentation``."""
    done: dict[tuple[int, int, int, int], np.ndarray] = {}

    def segment(window: tuple[int, int, int, int]) -> np.ndarray:
        if window not in done:
            x0, y0, x1, y1 = window
            started = time.perf_counter()
            done[window] = segmenter.segment(np.ascontiguousarray(rgb[y0:y1, x0:x1]))
            segmentation.setdefault("zoomPasses", []).append(
                {"window": list(window), "ms": int(round((time.perf_counter() - started) * 1000))}
            )
        return done[window]

    return segment


def _geometry_overlay(
    rgb: np.ndarray,
    alpha: np.ndarray,
    geometry: VehicleGeometry,
    mask_info=None,
    segmentation: dict | None = None,
) -> np.ndarray:
    """Debug view: dimmed photo, vehicle mask outline, bbox and tyre contacts; the
    segmentation window (white), semantic regions (red: foreign, green: foreign and cut, cyan: a vehicle part
    the mask lacks, orange: checked and kept as it is) and the detector's vehicle box
    (magenta)."""
    scale = min(1.0, 1600.0 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb.copy()
    mask = cv2.resize(alpha, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 0.5
    out = (small.astype(np.float32) * 0.55).astype(np.uint8)
    out[mask] = (0.6 * small[mask] + 0.4 * np.array([40, 120, 255])).astype(np.uint8)

    def box(values, colour, thickness=2):
        x0, y0, x1, y1 = (int(round(v * scale)) for v in values)
        cv2.rectangle(out, (x0, y0), (x1, y1), colour, thickness)

    b = geometry.bbox
    box((b.x0, b.y0, b.x1, b.y1), (255, 255, 0))
    if segmentation and segmentation.get("windowUsed"):
        box(segmentation["window"], (255, 255, 255), 1)
    semantic = getattr(mask_info, "semantic", None) or {}
    vehicle = semantic.get("vehicle")
    if vehicle:
        box(vehicle["box"], (255, 0, 255), 1)
    for finding in semantic.get("findings", []):
        colour = (
            (60, 220, 60)  # foreign, cut along the line of the zoom pass
            if finding.get("action") == "cut"
            else (255, 40, 40) if finding.get("foreign") else (0, 200, 255) if finding.get("missing") else (255, 160, 0)
        )
        box(finding["box"], colour, 3)
    for c in geometry.candidates:
        colour = (0, 255, 0) if c.confidence >= 0.5 else (255, 60, 60)
        cv2.circle(out, (int(c.x * scale), int(c.y * scale)), 7, colour, 2)
    return out


def _passthrough(
    rgb: np.ndarray,
    preset: Preset,
    debug: DebugSink,
    timer: _Timer,
    base_meta: dict,
    progress: ProgressFn,
    *,
    warnings: list[QualityWarning] | None = None,
    shot_kind: str = "interior_passthrough",
    progress_at: float = 0.5,
) -> ProcessResult:
    """Interior/detail shots: no background replacement, no colour changes (and the export
    of the experimental detail backdrop, ``shot_kind`` "detail_backdrop")."""
    progress(progress_at, "passthrough")
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
        "shotKind": shot_kind,
        "output": {"width": int(rgb.shape[1]), "height": int(rgb.shape[0])},
        "showroomPlaceholder": False,
        "timingsMs": timer.times,
    }
    debug.json("metadata.json", metadata)
    progress(1.0, "done")
    return ProcessResult(
        jpeg=jpeg, width=int(rgb.shape[1]), height=int(rgb.shape[0]), warnings=list(warnings or []), metadata=metadata
    )
