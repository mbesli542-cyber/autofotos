"""Quality gate: exterior photos that would give a bad listing image are rejected.

A rejection fails the job with a German message that tells the photographer
what to do; nothing is stored. Thresholds come from the preset's ``quality``
section and are deliberately generous – normal phone photos (chest/eye height
0.8–1.8 m, any normal car incl. SUVs) must pass.

Codes (checked in this priority order, the first failing one is reported):

- ``source_resolution_too_low``  source long edge too small (checked right after decoding)
- ``vehicle_cropped``            solid vehicle touches the left/right/bottom border (or the top over a long run)
- ``vehicle_too_small`` / ``source_resolution_too_low``
                                 the vehicle would have to be enlarged more than ``maxUpscale``:
                                 "too small" if it covers less than ``minVehicleWidthRatio`` of the
                                 photo width, otherwise the photo itself has too few pixels
- ``mask_low_confidence``        the cut-out is unreliable (soft edges, split, coverage, fill, or a
                                 foreign object detected in the outline that could not be cut)
- ``perspective_mismatch``       bbox aspect / contact rise far from the plate's proxy car
                                 (camera much higher than the plate camera, e.g. a parking deck)
- ``ground_contact_uncertain``   fewer than ``minContacts`` plausible tyre contacts (3/4 + side shots)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..presets import QualityConfig
from ..showroom.plates import Plate
from .mask import BBox, MaskInfo
from .placement import PlacementResult
from .vehicle_geometry import VehicleGeometry

MESSAGES = {
    "source_resolution_too_low": (
        "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen."
    ),
    "vehicle_too_small": "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
    "vehicle_cropped": (
        "Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren."
    ),
    "mask_low_confidence": (
        "Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen."
    ),
    "ground_contact_uncertain": (
        "Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein."
    ),
    "perspective_mismatch": (
        "Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren."
    ),
}

#: Gate checks in reporting priority (first failing check wins).
CHECK_ORDER = ("resolution", "cropped", "upscale", "mask", "perspective", "contacts")

#: Shots whose tyres must be visible (3/4 and side views).
CONTACT_SHOTS = frozenset(
    {"front_left_45", "front_right_45", "rear_left_45", "rear_right_45", "left_side", "right_side"}
)
#: Two contacts closer than this (× vehicle width) are not two tyres.
MIN_CONTACT_SPAN = 0.25
#: Below this proxy rise (× proxy width) the view has no usable contact rise (side, front, rear).
MIN_PROXY_RISE = 0.04


class QualityGateError(Exception):
    """The photo fails the quality gate – `.code`, German `.user_message`, `.details`."""

    def __init__(self, code: str, details: dict | None = None):
        if code not in MESSAGES:
            raise ValueError(f"unknown quality gate code {code!r}")
        self.code = code
        self.user_message = MESSAGES[code]
        self.details = dict(details or {})
        super().__init__(f"{code}: {self.user_message}")


@dataclass
class GateReport:
    """Decisions of every check (metadata) and the error to raise, if any."""

    checks: dict[str, dict] = field(default_factory=dict)

    def add(self, name: str, code: str | None, **values) -> None:
        self.checks[name] = {"passed": code is None, **({"code": code} if code else {}), **_round(values)}

    @property
    def failed_code(self) -> str | None:
        for name in CHECK_ORDER:
            check = self.checks.get(name)
            if check is not None and not check["passed"]:
                return check["code"]
        return None

    def as_dict(self) -> dict:
        return {"passed": self.failed_code is None, "checks": self.checks}

    def raise_if_failed(self, **context) -> None:
        code = self.failed_code
        if code is not None:
            raise QualityGateError(code, {"code": code, **self.as_dict(), **context})


def _round(values: dict) -> dict:
    out = {}
    for key, value in values.items():
        if isinstance(value, float):
            out[key] = round(value, 4)
        elif isinstance(value, (np.floating, np.integer)):
            out[key] = round(float(value), 4)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- checks


def check_source_resolution(width: int, height: int, cfg: QualityConfig) -> None:
    """Raise before any expensive work when the photo has too few pixels."""
    long_edge = max(width, height)
    if long_edge < cfg.min_source_long_edge:
        report = GateReport()
        report.add(
            "resolution", "source_resolution_too_low", longEdge=long_edge, minLongEdge=cfg.min_source_long_edge
        )
        report.raise_if_failed()


def touching_borders(alpha: np.ndarray, bbox: BBox, cfg: QualityConfig) -> list[str]:
    """Image borders where the SOLID vehicle is cut ("left", "right", "bottom", "top").

    A side counts when solid pixels reach a thin band along it (≥ `borderPx`,
    0.4 % of the image size – segmenters fade out a few px before the border)
    over a substantial run: a cut cross-section, not a bumper tip, mirror or
    antenna that merely comes close.
    """
    height, width = alpha.shape
    solid = alpha >= 0.5
    bx = max(1, int(cfg.border_px), int(round(0.004 * width)))
    by = max(1, int(cfg.border_px), int(round(0.004 * height)))
    rows_left = int(solid[:, :bx].any(axis=1).sum())
    rows_right = int(solid[:, -bx:].any(axis=1).sum())
    cols_bottom = int(solid[-by:, :].any(axis=0).sum())
    cols_top = int(solid[:by, :].any(axis=0).sum())
    sides = []
    if rows_left >= max(4.0, 0.08 * bbox.height):
        sides.append("left")
    if rows_right >= max(4.0, 0.08 * bbox.height):
        sides.append("right")
    if cols_bottom >= max(4.0, 0.03 * bbox.width):
        sides.append("bottom")
    if cols_top >= max(4.0, cfg.top_touch_ratio * bbox.width):  # roof cut, not an antenna
        sides.append("top")
    return sides


def check_cropped(report: GateReport, alpha: np.ndarray, bbox: BBox, cfg: QualityConfig) -> None:
    sides = touching_borders(alpha, bbox, cfg)
    report.add("cropped", "vehicle_cropped" if sides else None, sides=sides)


def check_mask(report: GateReport, alpha: np.ndarray, bbox: BBox, info: MaskInfo, cfg: QualityConfig) -> None:
    solid = float((alpha[bbox.y0 : bbox.y1, bbox.x0 : bbox.x1] >= 0.5).sum())
    fill = solid / max(bbox.width * bbox.height, 1)
    reasons = []
    if info.uncertain_fraction > cfg.max_uncertain_fraction:
        reasons.append("uncertain_edges")
    if info.split_parts > cfg.max_split_parts:
        reasons.append("split")
    if not cfg.min_coverage <= info.coverage <= cfg.max_coverage:
        reasons.append("coverage")
    if fill < cfg.min_fill_ratio:
        reasons.append("fill")
    if info.foreign_suspected:
        # a background object (cone, post) sticks out of the outline and the cut line was not
        # certain – an accepted result must never contain it, and guessing could cut the car
        reasons.append("foreign_object")
    report.add(
        "mask",
        "mask_low_confidence" if reasons else None,
        reasons=reasons,
        uncertainFraction=float(info.uncertain_fraction),
        splitParts=info.split_parts,
        coverage=float(info.coverage),
        fillRatio=fill,
        foreignSuspected=len(info.foreign_suspected),
    )


def check_upscale(
    report: GateReport, placement: PlacementResult, bbox: BBox, source_width: int, cfg: QualityConfig
) -> None:
    width_ratio = bbox.width / max(source_width, 1)
    code = None
    if placement.scale > cfg.max_upscale + 1e-6:
        code = "vehicle_too_small" if width_ratio < cfg.min_vehicle_width_ratio else "source_resolution_too_low"
    report.add(
        "upscale",
        code,
        scale=placement.scale,
        maxUpscale=cfg.max_upscale,
        vehicleWidthRatio=width_ratio,
        minVehicleWidthRatio=cfg.min_vehicle_width_ratio,
    )


def check_perspective(report: GateReport, geometry: VehicleGeometry, plate: Plate, cfg: QualityConfig) -> None:
    reasons = []
    expected_aspect = plate.proxy_aspect()
    aspect_factor = geometry.aspect / max(expected_aspect, 1e-6)
    if aspect_factor < cfg.min_aspect_factor:
        reasons.append("aspect_low")
    elif aspect_factor > cfg.max_aspect_factor:
        reasons.append("aspect_high")
    expected_rise = plate.proxy_rise_ratio()
    values = {
        "aspect": geometry.aspect,
        "expectedAspect": expected_aspect,
        "aspectFactor": aspect_factor,
        "contactRiseRatio": geometry.contact_rise_ratio,
        "outerRiseRatio": geometry.outer_rise_ratio,
        "expectedRiseRatio": expected_rise,
    }
    if expected_rise >= MIN_PROXY_RISE:
        high = cfg.max_rise_factor * expected_rise + cfg.rise_tolerance
        measured_ok = len(geometry.contacts) >= 2 and geometry.contact_span >= MIN_CONTACT_SPAN
        if measured_ok and geometry.contact_rise_ratio > high:
            reasons.append("rise_high")
        # the outer-zone estimate also sees photos from above, where the far tyres hide under the body
        if geometry.outer_rise_ratio > (cfg.max_rise_factor + 0.6) * expected_rise + 2 * cfg.rise_tolerance:
            reasons.append("outer_rise_high")
        if measured_ok and cfg.min_rise_factor > 0 and geometry.contact_rise_ratio < cfg.min_rise_factor * expected_rise:
            reasons.append("rise_low")
        rise_factor = geometry.contact_rise_ratio / expected_rise if measured_ok else 0.0
        outer_factor = geometry.outer_rise_ratio / expected_rise
        # seen from above: steep floor AND more roof/bonnet than the plate camera would show
        if aspect_factor < cfg.high_view_aspect_factor and (
            rise_factor > cfg.high_view_rise_factor or outer_factor > cfg.high_view_rise_factor + 0.3
        ):
            reasons.append("high_view")
        values["riseFactor"] = geometry.contact_rise_ratio / expected_rise
        values["outerRiseFactor"] = outer_factor
    report.add("perspective", "perspective_mismatch" if reasons else None, reasons=sorted(set(reasons)), **values)


def check_contacts(report: GateReport, geometry: VehicleGeometry, shot_key: str, cfg: QualityConfig) -> None:
    required = shot_key in CONTACT_SHOTS
    enough = len(geometry.contacts) >= cfg.min_contacts and (
        cfg.min_contacts < 2 or geometry.contact_span >= MIN_CONTACT_SPAN
    )
    report.add(
        "contacts",
        "ground_contact_uncertain" if required and not enough else None,
        required=required,
        plausible=len(geometry.contacts),
        minContacts=cfg.min_contacts,
        span=geometry.contact_span,
        confidence=geometry.contact_confidence,
    )
