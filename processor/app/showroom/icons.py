"""Simple line icons for the branding texts (globe before the website, phone before the number).

The shapes are the path data of two Lucide icons, copied verbatim from
``lucide-react`` 1.52.0 (``dist/esm/icons/globe.mjs``, ``phone.mjs``): 24 × 24
view box, stroke width 2, round caps and joins. They are rasterised here as
vector strokes (no image assets, nothing generated).

Lucide is licensed under the ISC License:

    ISC License

    Copyright (c) 2026 Lucide Icons and Contributors

    Permission to use, copy, modify, and/or distribute this software for any
    purpose with or without fee is hereby granted, provided that the above
    copyright notice and this permission notice appear in all copies.

    THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
    WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
    MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
    ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
    WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
    ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
    OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

from __future__ import annotations

import math
import re

import cv2
import numpy as np

#: Lucide view box and stroke width.
VIEW_BOX = 24.0
STROKE_WIDTH = 2.0

#: name → list of shapes: ("circle", cx, cy, r) or ("path", d)
LUCIDE_ICONS: dict[str, tuple[tuple, ...]] = {
    "globe": (
        ("circle", 12.0, 12.0, 10.0),
        ("path", "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"),
        ("path", "M2 12h20"),
    ),
    "phone": (
        (
            "path",
            "M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2"
            "A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233"
            " 14 14 0 0 0 6.392 6.384",
        ),
    ),
}

ICON_NAMES = frozenset(LUCIDE_ICONS)

_TOKEN = re.compile(r"[MmLlHhVvAaCcZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "A": 7, "C": 6, "Z": 0}


def _arc_points(p0, rx, ry, phi_deg, large, sweep, p1, steps_per_rad=12.0):
    """SVG elliptical arc (endpoint parametrisation, SVG 1.1 F.6.5) → points after p0."""
    x1, y1 = p0
    x2, y2 = p1
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    xp, yp = cos_p * dx + sin_p * dy, -sin_p * dx + cos_p * dy
    lam = (xp * xp) / (rx * rx) + (yp * yp) / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
    den = rx * rx * yp * yp + ry * ry * xp * xp
    coef = math.sqrt(max(num / den, 0.0)) if den > 0 else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * yp / ry, -coef * ry * xp / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = angle(1, 0, (xp - cxp) / rx, (yp - cyp) / ry)
    dt = angle((xp - cxp) / rx, (yp - cyp) / ry, (-xp - cxp) / rx, (-yp - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(2, int(math.ceil(abs(dt) * steps_per_rad)))
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cos_p * ex - sin_p * ey + cx, sin_p * ex + cos_p * ey + cy))
    out[-1] = (x2, y2)
    return out


def path_polylines(d: str) -> list[list[tuple[float, float]]]:
    """Flatten an SVG path (M L H V A C Z, absolute and relative) into polylines."""
    tokens = _TOKEN.findall(d)
    lines: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    start = (0.0, 0.0)
    i = 0
    command = ""
    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command in "Zz":
                if current:
                    current.append(start)
                    lines.append(current)
                current = [start]
                x, y = start
                continue
        upper = command.upper()
        rel = command.islower()
        n = _ARGS[upper]
        args = [float(t) for t in tokens[i : i + n]]
        if len(args) < n:
            raise ValueError(f"truncated path data: {d!r}")
        i += n
        if upper == "M":
            if current and len(current) > 1:
                lines.append(current)
            x, y = (x + args[0], y + args[1]) if rel else (args[0], args[1])
            start = (x, y)
            current = [(x, y)]
            command = "l" if rel else "L"  # further pairs are line-tos
        elif upper == "L":
            x, y = (x + args[0], y + args[1]) if rel else (args[0], args[1])
            current.append((x, y))
        elif upper == "H":
            x = x + args[0] if rel else args[0]
            current.append((x, y))
        elif upper == "V":
            y = y + args[0] if rel else args[0]
            current.append((x, y))
        elif upper == "A":
            end = (x + args[5], y + args[6]) if rel else (args[5], args[6])
            current.extend(_arc_points((x, y), args[0], args[1], args[2], bool(args[3]), bool(args[4]), end))
            x, y = end
        elif upper == "C":
            pts = [(x + args[k], y + args[k + 1]) if rel else (args[k], args[k + 1]) for k in (0, 2, 4)]
            p0 = (x, y)
            for s in range(1, 17):
                t = s / 16.0
                a, b, c = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t
                e = t**3
                current.append(
                    (
                        a * p0[0] + b * pts[0][0] + c * pts[1][0] + e * pts[2][0],
                        a * p0[1] + b * pts[0][1] + c * pts[1][1] + e * pts[2][1],
                    )
                )
            x, y = pts[2]
    if current and len(current) > 1:
        lines.append(current)
    return lines


def icon_mask(name: str, size_px: int, supersample: int = 4) -> np.ndarray:
    """Anti-aliased coverage mask (size_px × size_px, 0..1) of a Lucide icon."""
    if name not in LUCIDE_ICONS:
        raise ValueError(f"unknown icon {name!r}")
    size_px = max(int(size_px), 4)
    big = size_px * supersample
    scale = big / VIEW_BOX
    canvas = np.zeros((big, big), np.uint8)
    thickness = max(1, int(round(STROKE_WIDTH * scale)))
    shift = 4  # sub-pixel precision of cv2 drawing
    factor = 1 << shift
    polylines = []
    for shape in LUCIDE_ICONS[name]:
        if shape[0] == "circle":
            _, cx, cy, r = shape
            n = 96
            angles = [2 * math.pi * k / n for k in range(n + 1)]
            polylines.append([(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles])
        else:
            polylines.extend(path_polylines(shape[1]))
    for line in polylines:
        pts = np.round(np.asarray(line, np.float64) * scale * factor).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [pts], False, 255, thickness=thickness, lineType=cv2.LINE_AA, shift=shift)
        # round caps and joins (Lucide: stroke-linecap/linejoin round)
        for px, py in pts.reshape(-1, 2):
            cv2.circle(canvas, (int(px), int(py)), int(thickness * factor / 2), 255, -1, cv2.LINE_AA, shift)
    mask = canvas.astype(np.float32) / 255.0
    return np.clip(cv2.resize(mask, (size_px, size_px), interpolation=cv2.INTER_AREA), 0.0, 1.0)
