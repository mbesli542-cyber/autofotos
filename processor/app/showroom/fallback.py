"""EMERGENCY DEVELOPER FALLBACK – procedural showroom plate.

This is NOT the AutoExperten Standard design. The final background is a real,
photorealistic master photo at ``public/presets/autoexperten-standard-showroom.jpg``;
this renderer only keeps the pipeline usable while that file is missing.
Every job that uses it carries the ``showroom_fallback`` warning. Do not
invest further work in it.

The rendered fallback is stored at
``public/presets/fallback/autoexperten-standard-fallback.jpg``
(``processor/scripts/render_fallback_showroom.py``). Like the real master it
contains NO branding – the official logo and texts are added by
``app/showroom/branding.py`` for every background.

The plate is rendered procedurally with numpy / OpenCV / Pillow only – no
downloads and no generative AI. It shows an empty, bright dealership:

* white plaster wall lit by warm ceiling downlights (light scallops), EMPTY –
  the branding is added by app/showroom/branding.py (the old built-in sign
  rendering below is only used when logo_path and brand are passed),
* vertical wooden slat panels and blue LED light strips at the sides,
* a warm oak plank floor in one-point perspective with a satin sheen that
  reflects the wall,
* tall potted plants at the outer edges, soft depth of field and vignette.

Composition contract (relied on by the processing pipeline):

* wall/floor junction (baseboard) at ``y = floor_horizon * height``,
* the area ``x in [8 %, 92 %]``, ``y in [28 %, 97 %]`` contains only wall and
  floor (no objects) – the vehicle is placed there,
* identical inputs always give identical pixels (seeded RNG, no clock).

Everything is computed in display-linear light (sRGB primaries) and encoded
to sRGB at the end. Sizes are expressed as fractions of the frame or in
metres on the wall plane, so the plate renders consistently at any size.

The logo is never redrawn or recoloured: its official pixels are only trimmed
to the alpha bounding box and scaled; the tone curve is the identity below
``_SHOULDER`` and the vignette is applied before the signs are composited, so
the logo colours come out as in the official file (plus sensor grain). A
missing logo file only drops the logo (warning logged); missing system fonts
fall back to Pillow's default font.
"""

from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

__all__ = ["ShowroomBrandText", "render_fallback_showroom"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShowroomBrandText:
    city: str = "SCHWETZINGEN"
    website: str = "www.autoexperten-rn.de"
    phone: str = "+49 6202 9262357"

    @property
    def contact_line(self) -> str:
        parts = [p for p in (self.website.strip(), self.phone.strip()) if p]
        return "  \u00b7  ".join(parts)


# ---------------------------------------------------------------------------
# Colours (display-linear unless noted)
# ---------------------------------------------------------------------------

#: Official logo colours (sRGB 8 bit) measured from the official PNG.
BRAND_BLUE_SRGB = (7, 136, 234)
BRAND_GRAY_SRGB = (64, 63, 63)

_WARM_LIGHT = np.array([1.0, 0.88, 0.71], np.float32)  # ~3000 K downlights
_AMBIENT = np.array([0.985, 0.99, 1.0], np.float32)  # neutral showroom fill
_FLOOR_BOUNCE = np.array([1.0, 0.80, 0.60], np.float32)  # warm bounce from the oak floor

#: Tone-mapping shoulder start. Everything below is mapped 1:1, so the logo
#: colours come out exactly as in the official file.
_SHOULDER = 0.86


def _srgb_to_linear(c: np.ndarray | float) -> np.ndarray:
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _srgb8_to_linear(rgb: tuple[int, int, int]) -> np.ndarray:
    return _srgb_to_linear(np.array(rgb, np.float32) / 255.0)


_LED_BLUE = _srgb8_to_linear(BRAND_BLUE_SRGB)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _rng(seed: int, stream: int) -> np.random.Generator:
    """Independent, reproducible random stream per scene component."""
    return np.random.default_rng(np.random.SeedSequence([int(seed) & 0xFFFFFFFF, stream]))


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _noise(
    rng: np.random.Generator, h: int, w: int, cell_y: float, cell_x: float | None = None
) -> np.ndarray:
    """Smooth value noise (zero mean, ~unit std) with the given feature size in px."""
    cell_x = cell_y if cell_x is None else cell_x
    cy = max(1, int(round(cell_y)))
    cx = max(1, int(round(cell_x)))
    gh = h // cy + 4
    gw = w // cx + 4
    grid = rng.standard_normal((gh, gw), dtype=np.float32)
    big = cv2.resize(grid, (gw * cx, gh * cy), interpolation=cv2.INTER_CUBIC)
    out = big[cy : cy + h, cx : cx + w]
    std = float(out.std()) or 1.0
    return (out - float(out.mean())) / std


def _periodic_noise(rng: np.random.Generator, th: int, tw: int, cell_y: int, cell_x: int) -> np.ndarray:
    """Tileable smooth noise (th x tw), th % cell_y == tw % cell_x == 0."""
    gh, gw = th // cell_y, tw // cell_x
    grid = rng.standard_normal((gh, gw), dtype=np.float32)
    pad = 3
    grid = np.pad(grid, pad, mode="wrap")
    big = cv2.resize(grid, (grid.shape[1] * cell_x, grid.shape[0] * cell_y), interpolation=cv2.INTER_CUBIC)
    out = big[pad * cell_y : pad * cell_y + th, pad * cell_x : pad * cell_x + tw]
    return (out - out.mean()) / (out.std() or 1.0)


def _blur(img: np.ndarray, sx: float, sy: float | None = None) -> np.ndarray:
    sy = sx if sy is None else sy
    if sx <= 0.05 and sy <= 0.05:
        return img
    return cv2.GaussianBlur(
        img, (0, 0), sigmaX=max(sx, 0.05), sigmaY=max(sy, 0.05), borderType=cv2.BORDER_REFLECT
    )


def _big_blur(img: np.ndarray, sx: float, sy: float | None = None) -> np.ndarray:
    """Large Gaussian blur computed on a downscaled copy (fast, smooth)."""
    sy = sx if sy is None else sy
    f = int(max(1, min(8, math.floor(min(sx, sy) / 3.0))))
    if f <= 1:
        return _blur(img, sx, sy)
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = _blur(small, sx / f, sy / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _lowres_field(h: int, w: int, step: int, fn) -> np.ndarray:
    """Evaluate a smooth field fn(ys, xs) on a coarse grid and upsample it."""
    hl = -(-h // step)
    wl = -(-w // step)
    ys = (np.arange(hl, dtype=np.float32) * step + step * 0.5)[:, None]
    xs = (np.arange(wl, dtype=np.float32) * step + step * 0.5)[None, :]
    field = np.ascontiguousarray(fn(ys, xs), dtype=np.float32)
    big = cv2.resize(field, (wl * step, hl * step), interpolation=cv2.INTER_LINEAR)
    return big[:h, :w]


def _over(dst: np.ndarray, premul: np.ndarray, alpha: np.ndarray, x0: int, y0: int) -> None:
    """Premultiplied 'over' composite of a patch into dst (in place, clipped)."""
    ph, pw = alpha.shape
    H, W = dst.shape[:2]
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + pw), min(H, y0 + ph)
    if xa >= xb or ya >= yb:
        return
    a = alpha[ya - y0 : yb - y0, xa - x0 : xb - x0, None]
    p = premul[ya - y0 : yb - y0, xa - x0 : xb - x0]
    region = dst[ya:yb, xa:xb]
    region *= 1.0 - a
    region += p


def _multiply_patch(dst: np.ndarray, factor: np.ndarray, x0: int, y0: int) -> None:
    ph, pw = factor.shape[:2]
    H, W = dst.shape[:2]
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + pw), min(H, y0 + ph)
    if xa >= xb or ya >= yb:
        return
    f = factor[ya - y0 : yb - y0, xa - x0 : xb - x0]
    if f.ndim == 2 and dst.ndim == 3:
        f = f[..., None]
    dst[ya:yb, xa:xb] *= f


# ---------------------------------------------------------------------------
# Scene geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scene:
    w: int
    h: int
    #: First floor row = wall/floor junction (floor_horizon * height).
    yb: int
    #: Eye level / vanishing point row (above the junction).
    y_eye: float
    cx: float
    #: Focal length in px.
    focal: float
    #: Camera height above the floor in metres.
    cam_h: float
    #: Pixels per metre on the back-wall plane.
    ppm: float
    #: Distance camera -> back wall in metres.
    z_wall: float
    #: Wall/ceiling junction row (0 = no visible ceiling).
    y_ceil: float
    #: Pixel scale relative to the 3200x2400 reference.
    unit: float

    def floor_y(self, z: float) -> float:
        """Screen row of a floor point at depth z (metres)."""
        return self.y_eye + self.focal * self.cam_h / z

    def ppm_at(self, z: float) -> float:
        return self.focal / z


def _make_scene(width: int, height: int, floor_horizon: float) -> _Scene:
    fh = float(min(max(floor_horizon, 0.30), 0.90))
    yb = int(round(fh * height))
    yb = min(max(yb, 8), height - 8)
    y_eye = max(yb - 0.165 * height, 0.06 * height)
    if yb - y_eye < 4:
        y_eye = yb - 4.0
    cam_h = 1.5
    focal = 1.2 * height
    ppm = (yb - y_eye) / cam_h
    z_wall = focal / ppm
    # Ceiling at ~5.4 m (about 2.6 % of the frame for the default layout).
    y_ceil = y_eye - (5.4 - cam_h) * ppm
    if y_ceil < 0.006 * height:
        y_ceil = 0.0
    return _Scene(
        w=width,
        h=height,
        yb=yb,
        y_eye=float(y_eye),
        cx=width / 2.0,
        focal=float(focal),
        cam_h=cam_h,
        ppm=float(ppm),
        z_wall=float(z_wall),
        y_ceil=float(y_ceil),
        unit=min(width / 3200.0, height / 2400.0),
    )


# ---------------------------------------------------------------------------
# Fonts and brand layout
# ---------------------------------------------------------------------------

_FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "~/.local/share/fonts",
    "~/.fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "C:/Windows/Fonts",
)

_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "city": (
        "Inter-SemiBold.otf",
        "Inter-Medium.otf",
        "InterDisplay-SemiBold.otf",
        "LiberationSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "FreeSansBold.ttf",
        "Arial Bold.ttf",
        "arialbd.ttf",
        "Helvetica.ttc",
    ),
    "info": (
        "Inter-Medium.otf",
        "Inter-Regular.otf",
        "LiberationSans-Regular.ttf",
        "DejaVuSans.ttf",
        "FreeSans.ttf",
        "Arial.ttf",
        "arial.ttf",
        "Helvetica.ttc",
    ),
}


@lru_cache(maxsize=1)
def _font_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    for directory in _FONT_DIRS:
        try:
            root = Path(directory).expanduser()
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    index.setdefault(path.name.lower(), path)
        except OSError:
            continue
    return index


def _load_font(role: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(4, int(size))
    try:
        index = _font_index()
    except Exception:  # pragma: no cover - defensive, font lookup must never crash
        index = {}
    for name in _FONT_CANDIDATES.get(role, ()):
        path = index.get(name.lower())
        if path is None:
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # pragma: no cover - very old Pillow / no FreeType
        return ImageFont.load_default()


def _font_for_cap_height(role: str, cap_px: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    probe = 200
    font = _load_font(role, probe)
    try:
        box = font.getbbox("H")
        cap = float(box[3] - box[1])
    except Exception:
        cap = 0.0
    if cap <= 1:
        return font
    return _load_font(role, int(round(probe * cap_px / cap)))


def _text_mask(text: str, font, tracking: float) -> np.ndarray:
    """Anti-aliased coverage mask (float32 0..1) of a tracked text line, tightly cropped."""
    advances = []
    for ch in text:
        try:
            advances.append(float(font.getlength(ch)))
        except Exception:
            box = font.getbbox(ch)
            advances.append(float(box[2] - box[0]))
    total = sum(advances) + tracking * max(0, len(text) - 1)
    try:
        ascent, descent = font.getmetrics()
    except Exception:
        ascent, descent = font.getbbox("Hg")[3], 0
    pad = 8
    img = Image.new("L", (int(math.ceil(total)) + 2 * pad, int(ascent + descent) + 2 * pad), 0)
    draw = ImageDraw.Draw(img)
    x = float(pad)
    for ch, adv in zip(text, advances, strict=True):
        draw.text((round(x), pad), ch, fill=255, font=font)
        x += adv + tracking
    mask = np.asarray(img, dtype=np.float32) / 255.0
    rows = np.where(mask.max(axis=1) > 0.02)[0]
    cols = np.where(mask.max(axis=0) > 0.02)[0]
    if rows.size == 0 or cols.size == 0:
        return np.zeros((1, 1), np.float32)
    return np.ascontiguousarray(mask[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1])


def _tracked_text(text: str, role: str, cap_px: float, tracking_em: float, ss: int = 2) -> np.ndarray:
    font = _font_for_cap_height(role, cap_px * ss)
    size = getattr(font, "size", cap_px * ss / 0.72)
    mask = _text_mask(text, font, tracking_em * size)
    if ss > 1 and mask.size > 1:
        h, w = mask.shape
        mask = cv2.resize(mask, (max(1, round(w / ss)), max(1, round(h / ss))), interpolation=cv2.INTER_AREA)
    return mask


@dataclass
class _Sign:
    """A brand element placed on the wall (display-linear, premultiplied)."""

    premul: np.ndarray
    alpha: np.ndarray
    x: int
    y: int
    #: Stand-off depth in px (visible underside + cast shadow), 0 = flat print.
    depth: float


def _load_logo(logo_path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Official logo, trimmed to its alpha bbox: (linear premultiplied RGB, alpha)."""
    try:
        with Image.open(logo_path) as im:
            rgba = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
    except (OSError, ValueError) as exc:
        logger.warning("Showroom fallback: logo not available (%s): %s", logo_path, exc)
        return None
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > 8.0 / 255.0)
    if ys.size == 0:
        return None
    rgba = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    alpha = rgba[..., 3]
    premul = _srgb_to_linear(rgba[..., :3]) * alpha[..., None]
    return premul.astype(np.float32), alpha.astype(np.float32)


def _brand_layout(sc: _Scene, logo_path: Path, brand: ShowroomBrandText) -> list[_Sign]:
    signs: list[_Sign] = []
    h, w = sc.h, sc.w
    # Logo: centred, top at 7.5 % of the height.
    logo_w = min(0.30 * w, 0.40 * h)
    logo_top = 0.075 * h
    logo_bottom = logo_top + 0.065 * h
    logo = _load_logo(logo_path)
    if logo is not None:
        premul, alpha = logo
        lh0, lw0 = alpha.shape
        lw = max(8, int(round(logo_w)))
        lh = max(2, int(round(lw * lh0 / lw0)))
        premul = cv2.resize(premul, (lw, lh), interpolation=cv2.INTER_AREA)
        alpha = np.clip(cv2.resize(alpha, (lw, lh), interpolation=cv2.INTER_AREA), 0.0, 1.0)
        premul = np.minimum(premul, alpha[..., None])
        x = int(round(sc.cx - lw / 2.0))
        y = int(round(logo_top))
        signs.append(_Sign(premul, alpha, x, y, depth=0.0013 * h))
        logo_bottom = y + lh
    # City name: spaced capitals, dark gray stand-off letters.
    gray = _srgb8_to_linear(BRAND_GRAY_SRGB)
    city = brand.city.strip().upper()
    y_next = logo_bottom + 0.021 * h
    if city:
        mask = _tracked_text(city, "city", cap_px=0.0165 * h, tracking_em=0.42)
        target = 0.62 * logo_w
        if mask.shape[1] > target * 1.15:
            scale = target / mask.shape[1]
            mask = cv2.resize(
                mask,
                (max(1, int(mask.shape[1] * scale)), max(1, int(mask.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        mask = np.clip(mask, 0.0, 1.0)
        x = int(round(sc.cx - mask.shape[1] / 2.0))
        y = int(round(y_next))
        signs.append(_Sign(mask[..., None] * gray, mask, x, y, depth=0.0008 * h))
        y_next = y + mask.shape[0] + 0.019 * h
    # Contact line: smaller, flat print.
    line = brand.contact_line
    if line:
        mask = _tracked_text(line, "info", cap_px=0.0098 * h, tracking_em=0.06)
        max_w = 0.80 * logo_w
        if mask.shape[1] > max_w:
            scale = max_w / mask.shape[1]
            mask = cv2.resize(
                mask,
                (max(1, int(mask.shape[1] * scale)), max(1, int(mask.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        mask = np.clip(mask, 0.0, 1.0)
        color = _srgb8_to_linear((74, 73, 73))
        x = int(round(sc.cx - mask.shape[1] / 2.0))
        y = int(round(y_next))
        signs.append(_Sign(mask[..., None] * color, mask, x, y, depth=0.0))
    return signs


# ---------------------------------------------------------------------------
# Wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WallLayout:
    panel_l: float  # inner edge of the left slat panel (px)
    panel_r: float  # inner edge of the right slat panel (px)
    leds: tuple[float, ...]  # LED strip centres (px)
    lights: tuple[float, ...]  # downlight x positions (px)
    light_dist: float  # downlight distance from the wall (px on wall plane)


def _wall_layout(sc: _Scene) -> _WallLayout:
    w = sc.w
    panel = 0.088 * w
    led_off = 0.040 * sc.ppm
    spacing = 1.8 * sc.ppm
    n = int(math.ceil((w / 2 + spacing) / spacing))
    lights = tuple(
        sc.cx + k * spacing for k in range(-n, n + 1) if -spacing < sc.cx + k * spacing < w + spacing
    )
    return _WallLayout(
        panel_l=panel,
        panel_r=w - panel,
        leds=(panel + led_off, w - panel - led_off),
        lights=lights,
        light_dist=0.30 * sc.ppm,
    )


def _wall_irradiance(sc: _Scene, lay: _WallLayout) -> np.ndarray:
    """Smooth light on the wall plane (rows 0..yb) from fill + downlights + floor bounce.

    Each downlight sits ``light_dist`` in front of the wall just below the
    ceiling and points straight down; its cone cuts the wall in the familiar
    "scallop" (bright cusp, soft hyperbolic upper edge, long fading tail).
    """
    h, w = sc.yb, sc.w
    d = lay.light_dist
    y_light = sc.y_ceil if sc.y_ceil > 0 else -0.35 * sc.ppm
    theta_in, theta_out = math.radians(21.0), math.radians(31.0)
    cos_in, cos_out = math.cos(theta_in), math.cos(theta_out)
    r_cusp = d / math.sin(0.5 * (theta_in + theta_out))

    def scallops(ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        total = np.zeros(np.broadcast_shapes(ys.shape, xs.shape), np.float32)
        dy = ys - y_light
        for lx in lay.lights:
            dx = xs - lx
            r = np.sqrt(dx * dx + dy * dy + d * d)
            c = np.maximum(dy, 0.0) / r
            beam = _smoothstep(cos_out, cos_in, c)
            q = r_cusp / r
            # d/r^3 law near the cusp + broad tail (wide beam, inter-reflections)
            total += beam * (0.70 * np.minimum(q, 1.4) ** 3 + 0.30 * np.minimum(q, 1.4) ** 1.1)
        return total

    def fill(ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        nx = (xs - sc.cx) / (0.5 * sc.w)
        yv = ys / max(1.0, h)
        # slightly brighter towards the left (daylight from the showroom front)
        horiz = (1.0 - 0.17 * nx * nx) * (1.0 - 0.035 * nx)
        # brightest at mid height, a touch darker behind the vehicle so that
        # white cars keep their contour against the wall
        vert = 0.58 + 0.34 * _smoothstep(0.02, 0.55, yv) - 0.09 * _smoothstep(0.50, 1.0, yv)
        return horiz * vert

    def field(ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        f = fill(ys, xs)
        bounce = np.broadcast_to(0.07 * np.exp(-(h - ys) / (0.6 * sc.ppm)), f.shape)
        out = f[..., None] * (1.06 * _AMBIENT)
        out += scallops(ys, xs)[..., None] * (0.54 * _WARM_LIGHT)
        out += bounce[..., None] * _FLOOR_BOUNCE
        return out

    return _lowres_field(h, w, max(2, int(round(4 * sc.unit))), field)


def _render_wall(
    sc: _Scene, lay: _WallLayout, signs: list[_Sign], seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Wall + ceiling radiance and emissive layer, rows 0..yb (exclusive)."""
    h, w = sc.yb, sc.w
    u = sc.unit
    rng = _rng(seed, 11)
    E = _wall_irradiance(sc, lay)
    xs = np.arange(w, dtype=np.float32) + 0.5
    ys = (np.arange(h, dtype=np.float32) + 0.5)[:, None]

    # --- plaster albedo (very subtle mottling) ---------------------------------
    lum = _noise(rng, h, w, 260 * u)
    lum *= 0.875 * 0.010
    lum += 0.875
    albedo = np.empty((h, w, 3), np.float32)
    np.multiply(lum[..., None], np.array([1.0, 0.995, 0.985], np.float32), out=albedo)
    del lum

    # --- LED strips: recessed channel, emissive diffuser, blue glow on the wall ----
    emit = np.zeros((h, w, 3), np.float32)
    led_core = 0.0105 * sc.ppm  # half width of the diffuser
    chan = 0.022 * sc.ppm  # half width of the recessed channel
    y_top = int(max(0.0, sc.y_ceil))
    vert = _smoothstep(sc.y_ceil - 2, sc.y_ceil + 0.10 * sc.ppm, ys)[y_top:, 0]
    for lx in lay.leds:
        win = int(1.6 * sc.ppm)
        x0, x1 = max(0, int(lx - win)), min(w, int(lx + win) + 1)
        dx = np.abs(xs[x0:x1] - lx)
        in_chan = np.clip(chan - dx + 0.5, 0.0, 1.0)
        glow = 0.80 * np.exp(-dx / (0.05 * sc.ppm)) + 0.45 * np.exp(-dx / (0.30 * sc.ppm))
        glow = glow * (1.0 - in_chan) + 2.6 * in_chan
        E[y_top:, x0:x1] += (glow[None, :, None] * 0.95 * _LED_BLUE) * vert[:, None, None]
        # channel interior: dark anodised profile
        albedo[y_top:, x0:x1] *= (1.0 - 0.82 * in_chan)[None, :, None]
        core = np.clip(led_core - dx + 0.5, 0.0, 1.0)
        centre = np.exp(-((dx / (0.5 * led_core + 0.5)) ** 2))
        col = core[:, None] * (1.55 * _LED_BLUE) + (core * centre)[:, None] * np.array(
            [0.10, 0.22, 0.25], np.float32
        )
        emit[y_top:, x0:x1] += col[None, :, :] * vert[:, None, None]

    # --- vertical wooden slat panels -------------------------------------------
    period = 0.062 * sc.ppm
    slat_frac = 0.70
    for side in (-1, 1):
        if side < 0:
            x0, x1 = 0, int(math.ceil(lay.panel_l)) + 1
            local = (lay.panel_l - xs[x0:x1]) / period  # 0 at the inner edge
        else:
            x0, x1 = int(lay.panel_r), w
            local = (xs[x0:x1] - lay.panel_r) / period
        inside = np.clip(local * period + 0.5, 0.0, 1.0)  # AA at the inner panel edge
        idx = np.floor(local).astype(np.int64)
        q = local - idx
        d_edge = np.minimum(q, slat_frac - q) * period  # px to nearest slat edge (neg in gap)
        face = np.clip(d_edge + 0.5, 0.0, 1.0)
        bevel = 1.0 - 0.22 * (1.0 - np.clip(d_edge / (0.22 * slat_frac * period), 0.0, 1.0)) ** 2
        side_light = 1.0 + 0.05 * np.sign(q - slat_frac / 2) * side
        n_slats = int(idx.max()) + 2
        tone = np.exp(rng.normal(0.0, 0.065, n_slats)).astype(np.float32)
        warm = rng.normal(0.0, 0.025, n_slats).astype(np.float32)
        ii = np.clip(idx, 0, n_slats - 1)
        width = x1 - x0
        grain = 0.07 * _noise(rng, h, width, 90 * u, 2.2 * u) + 0.04 * _noise(rng, h, width, 380 * u, 6 * u)
        oak = _srgb_to_linear(np.array([186, 140, 100], np.float32) / 255.0)
        slat = np.empty((h, width, 3), np.float32)
        base = (tone[ii] * bevel * side_light)[None, :]
        slat[...] = oak
        slat *= (base * (1.0 + grain))[..., None]
        slat[..., 0] *= 1.0 + warm[ii][None, :]
        slat[..., 2] *= 1.0 - warm[ii][None, :]
        gap = np.array([0.018, 0.016, 0.015], np.float32)
        slat = slat * face[None, :, None] + gap * (1.0 - face[None, :, None])
        m = inside[None, :, None]
        albedo[:, x0:x1] = albedo[:, x0:x1] * (1 - m) + slat * m
        # the panel protrudes a little: slightly less fill light in the gaps
        E[:, x0:x1] *= 1.0 - 0.06 * m

    # --- skirting board ----------------------------------------------------------
    bb_h = max(2, int(round(0.075 * sc.ppm)))
    y0 = h - bb_h
    xa, xb = int(lay.leds[0] + chan) + 1, int(lay.leds[1] - chan)
    albedo[y0:h, xa:xb] = np.array([0.80, 0.80, 0.79], np.float32)
    albedo[y0 : y0 + max(1, int(0.004 * sc.ppm)), xa:xb] *= 1.12  # lit top edge
    E[y0 - max(1, int(0.006 * sc.ppm)) : y0, xa:xb] *= 0.82  # shadow line above the board
    # contact occlusion at the floor
    occl = 1.0 - 0.35 * np.exp(-(h - ys) / (0.03 * sc.ppm))
    E *= occl[..., None]

    # --- ceiling -------------------------------------------------------------------
    yc = int(round(sc.y_ceil))
    if yc > 0:
        t = ys[:yc] / max(1, yc)  # 0 top .. 1 junction
        ceil_light = 0.26 + 0.34 * t**2.2
        albedo[:yc] = 0.80
        E[:yc] = (ceil_light * np.ones((1, w), np.float32))[..., None] * _AMBIENT
        gap_h = max(1, int(round(0.012 * sc.ppm)))
        E[yc : yc + gap_h] *= 0.55  # shadow gap at the wall/ceiling junction
        # recessed downlights seen from below
        z_l = sc.z_wall - lay.light_dist / sc.ppm
        for lx in lay.lights:
            y_dl = sc.y_eye - (sc.y_eye - sc.y_ceil) * sc.z_wall / z_l
            rx = 0.075 * sc.focal / z_l
            ry = rx * math.sin(math.atan2(sc.y_eye - y_dl, sc.focal))
            bx0, bx1 = max(0, int(lx - 2 * rx)), min(w, int(lx + 2 * rx) + 2)
            by0, by1 = max(0, int(y_dl - 3 * ry - 2)), min(yc, int(y_dl + 3 * ry) + 3)
            if bx0 >= bx1 or by0 >= by1:
                continue
            gx = (xs[bx0:bx1] - lx)[None, :] / rx
            gy = (ys[by0:by1] - y_dl) / max(ry, 0.5)
            rr = np.sqrt(gx * gx + gy * gy)
            aa = 1.5 / max(rx, 1.0)
            disc = np.clip((0.78 - rr) / aa + 0.5, 0.0, 1.0)
            trim = np.clip((1.0 - rr) / aa + 0.5, 0.0, 1.0) - disc
            albedo[by0:by1, bx0:bx1] = (
                albedo[by0:by1, bx0:bx1] * (1 - trim[..., None]) + trim[..., None] * 0.95
            )
            emit[by0:by1, bx0:bx1] += disc[..., None] * (9.0 * _WARM_LIGHT)

    # --- shadows of the stand-off letters -----------------------------------------
    for sign in signs:
        if sign.depth <= 0:
            continue
        ah, aw = sign.alpha.shape
        pad = int(6 * sign.depth + 24 * u) + 4
        canvas = np.zeros((ah + 2 * pad, aw + 2 * pad), np.float32)
        canvas[pad : pad + ah, pad : pad + aw] = sign.alpha
        contact = _shift_down(canvas, 0.9 * sign.depth)
        contact = _blur(contact, 0.7 * sign.depth + 0.6 * u)
        cast = _shift_down(canvas, 5.0 * sign.depth)
        cast = _blur(cast, 3.2 * sign.depth + 1.0 * u)
        shade = (1.0 - 0.38 * contact) * (1.0 - 0.20 * cast)
        _multiply_patch(E, shade, sign.x - pad, sign.y - pad)

    albedo *= E
    albedo += emit
    return albedo, emit


def _shift_down(a: np.ndarray, dy: float) -> np.ndarray:
    m = np.float32([[1, 0, 0], [0, 1, dy]])
    return cv2.warpAffine(a, m, (a.shape[1], a.shape[0]), flags=cv2.INTER_LINEAR, borderValue=0)


# ---------------------------------------------------------------------------
# Floor
# ---------------------------------------------------------------------------


def _grain_textures(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Tileable oak grain (rows = along the plank, cols = across the plank).

    Long fine fibre lines + growth rings whose contours form the typical
    flat-sawn "cathedral" arcs + broad tonal streaks. Returns a sharp and an
    across-blurred (mip) version.
    """
    th, tw = 2048, 512
    ys = np.arange(th, dtype=np.float32)[:, None]
    xs = np.arange(tw, dtype=np.float32)[None, :]
    # slow lateral wander of the fibres along the plank
    warp = _periodic_noise(rng, th, tw, 256, 512)[:, :1] * 7.0
    mx = np.ascontiguousarray(np.broadcast_to((xs + warp) % tw, (th, tw)), np.float32)
    my = np.ascontiguousarray(np.broadcast_to(ys, (th, tw)), np.float32)
    fibres = _periodic_noise(rng, th, tw, 256, 2)
    fibres = cv2.remap(fibres, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    streaks = _periodic_noise(rng, th, tw, 512, 16)
    streaks = cv2.remap(streaks, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    broad = _periodic_noise(rng, th, tw, 1024, 64)
    # growth rings: contours of (smooth field across u) + (slope along v)
    field = _periodic_noise(rng, th, tw, 512, 128) * 1.2 + _periodic_noise(rng, th, tw, 256, 32) * 0.05
    phase = field * 2.4 + ys / th * 6.0  # integer slope keeps it tileable along v
    rings = 0.5 + 0.5 * np.cos(2.0 * np.pi * phase * 3.0)
    rings = rings**6  # thin dark latewood lines
    g0 = 0.35 * fibres + 0.45 * streaks + 0.55 * broad - 1.1 * (rings - rings.mean())
    g0 = (g0 - g0.mean()) / g0.std()
    padded = np.pad(g0, ((0, 0), (8, 8)), mode="wrap")
    g1 = cv2.GaussianBlur(padded, (0, 0), sigmaX=2.2, sigmaY=0.3)[:, 8:-8]
    return np.ascontiguousarray(g0, np.float32), np.ascontiguousarray(g1, np.float32)


def _render_floor_albedo(sc: _Scene, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Oak plank floor albedo (rows yb..h) and per-pixel gloss factor, 2x supersampled."""
    rng = _rng(seed, 21)
    w, h, yb = sc.w, sc.h, sc.yb
    ss = 2
    plank_w = 0.21  # m
    plank_len = 2.30  # m
    gppm_u = 1500.0  # grain texels per metre across
    gppm_v = 320.0  # along
    g0, g1 = _grain_textures(rng)
    th, tw = g0.shape

    x_extent = (w / 2 + 4) * sc.cam_h / max(1e-3, yb - sc.y_eye)
    n_lanes = int(math.ceil(x_extent / plank_w)) + 3
    lane_off = n_lanes + 1
    n_l = 2 * n_lanes + 3
    z_max = sc.z_wall + plank_len * 2
    n_seg = int(math.ceil(z_max / plank_len)) + 3
    lane_shift = rng.uniform(0.0, plank_len, n_l).astype(np.float32)
    nb = n_l * n_seg
    tone = np.exp(rng.normal(0.0, 0.045, nb)).astype(np.float32)
    warm = rng.normal(0.0, 0.022, nb).astype(np.float32)
    gstr = rng.uniform(0.04, 0.072, nb).astype(np.float32)
    off_u = rng.uniform(0, tw, nb).astype(np.float32)
    off_v = rng.uniform(0, th, nb).astype(np.float32)
    gloss = rng.uniform(0.80, 1.15, nb).astype(np.float32)
    lane_phase = 0.37

    oak = _srgb_to_linear(np.array([188, 140, 90], np.float32) / 255.0)
    rows_ss = (h - yb) * ss
    albedo = np.empty((h - yb, w, 3), np.float32)
    gloss_map = np.empty((h - yb, w), np.float32)
    xs = (np.arange(w * ss, dtype=np.float32) + 0.5) / ss
    block = 128
    far_px = sc.ppm  # px per metre at the wall (across)
    for r0 in range(0, rows_ss, block):
        r1 = min(rows_ss, r0 + block)
        ys = yb + (np.arange(r0, r1, dtype=np.float32) + 0.5) / ss
        dy = (ys - sc.y_eye)[:, None]
        z = sc.focal * sc.cam_h / dy  # (b,1) metres
        X = (xs[None, :] - sc.cx) * (sc.cam_h / dy)
        lane_f = X / plank_w + lane_phase
        lane = np.floor(lane_f)
        uu = lane_f - lane
        li = np.clip(lane.astype(np.int64) + lane_off, 0, n_l - 1)
        zs = z + lane_shift[li]
        seg_f = zs / plank_len
        seg = np.floor(seg_f)
        vv = seg_f - seg
        si = np.clip(seg.astype(np.int64), 0, n_seg - 1)
        b = li * n_seg + si
        mx = np.mod(uu * (plank_w * gppm_u) + off_u[b], tw).astype(np.float32)
        my = np.mod(z * gppm_v + off_v[b], th).astype(np.float32)
        s0 = cv2.remap(g0, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        s1 = cv2.remap(g1, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        px_per_m = dy / sc.cam_h * ss  # across, supersampled px
        mip = _smoothstep(1.6 * far_px * ss, 0.9 * far_px * ss, px_per_m)
        grain = s0 * (1 - mip) + s1 * mip
        # seams between lanes (micro bevel) and staggered end joints
        bevel_px = 0.0028 * px_per_m
        du = np.minimum(uu, 1.0 - uu) * plank_w * px_per_m
        sw = np.maximum(bevel_px, 0.6)
        seam_u = 0.42 * np.clip(bevel_px / 0.6, 0.12, 1.0) * np.exp(-((du / sw) ** 2))
        dz_px_per_m = dy * dy / (sc.focal * sc.cam_h) * ss
        dv = np.minimum(vv, 1.0 - vv) * plank_len * dz_px_per_m
        bev_v = 0.0028 * dz_px_per_m
        swv = np.maximum(bev_v, 0.6)
        seam_v = 0.40 * np.clip(bev_v / 0.6, 0.1, 1.0) * np.exp(-((dv / swv) ** 2))
        seam = (1.0 - seam_u) * (1.0 - seam_v)
        lum = tone[b] * (1.0 + gstr[b] * 1.6 * grain) * seam
        blk = np.empty(lum.shape + (3,), np.float32)
        wv = warm[b]
        blk[..., 0] = lum * oak[0] * (1.0 + wv)
        blk[..., 1] = lum * oak[1]
        blk[..., 2] = lum * oak[2] * (1.0 - 1.4 * wv)
        gl = gloss[b] * (0.35 + 0.65 * seam)
        o0, o1 = r0 // ss, r1 // ss
        albedo[o0:o1] = cv2.resize(blk, (w, o1 - o0), interpolation=cv2.INTER_AREA)
        gloss_map[o0:o1] = cv2.resize(gl.astype(np.float32), (w, o1 - o0), interpolation=cv2.INTER_AREA)
    return albedo, gloss_map


def _floor_reflectance(sc: _Scene) -> np.ndarray:
    """Effective specular reflectance per floor row (Schlick Fresnel x satin gloss)."""
    ys = np.arange(sc.yb, sc.h, dtype=np.float32) + 0.5
    sin_a = np.sin(np.arctan2(ys - sc.y_eye, sc.focal))
    return (0.60 * (0.04 + 0.96 * (1.0 - sin_a) ** 5)).astype(np.float32)


def _render_floor(sc: _Scene, lay: _WallLayout, wall: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Floor radiance (rows yb..h) and the specular reflection term it contains."""
    h, w, yb = sc.h, sc.w, sc.yb
    fh = h - yb
    u = sc.unit
    rad, gloss = _render_floor_albedo(sc, seed)  # albedo, turned into radiance in place

    # --- diffuse light on the floor -----------------------------------------------
    z_car = sc.focal * sc.cam_h / max(1.0, 0.84 * sc.h - sc.y_eye)

    def light(ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        dy = np.maximum(ys + yb - sc.y_eye, 1e-3)
        z = sc.focal * sc.cam_h / dy
        X = (xs - sc.cx) * sc.cam_h / dy
        lateral = 0.80 + 0.20 * np.exp(-((X / 5.0) ** 2))
        front = 0.86 + 0.14 * _smoothstep(sc.z_wall * 0.25, sc.z_wall * 0.6, z)
        near_wall = 1.0 + 0.20 * np.exp(-(sc.z_wall - z) / 1.4)
        stage = 1.0 + 0.10 * np.exp(-((X / 3.2) ** 2 + ((z - z_car) / 2.6) ** 2))
        e = lateral * near_wall * stage * front
        return e[..., None] * (0.92 * np.array([1.0, 0.97, 0.93], np.float32))

    E = _lowres_field(fh, w, max(2, int(round(4 * u))), light)
    ys = (np.arange(fh, dtype=np.float32) + 0.5)[:, None]
    junction = 1.0 - 0.30 * np.exp(-ys / (0.018 * sc.ppm)) - 0.10 * np.exp(-ys / (0.12 * sc.ppm))
    E *= junction[..., None]

    # LED spill: small blue pools on the floor at the foot of each strip
    reach = 0.9  # metres
    r_end = min(fh, int(math.ceil(sc.floor_y(max(0.5, sc.z_wall - reach)) - yb)) + 1)
    if r_end > 0:
        dy = ys[:r_end] + yb - sc.y_eye
        z = sc.focal * sc.cam_h / dy
        for lx in lay.leds:
            half = int(reach * float(dy[-1, 0]) / sc.cam_h) + 2  # reach in px at the nearest row
            x0, x1 = max(0, int(lx - half)), min(w, int(lx + half) + 1)
            if x0 >= x1:
                continue
            xs = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5
            X = (xs - sc.cx) * sc.cam_h / dy
            X_led = (lx - sc.cx) / sc.ppm
            dist = np.sqrt((X - X_led) ** 2 + (sc.z_wall - z) ** 2)
            spill = 0.16 * np.exp(-dist / 0.16) + 0.05 * np.exp(-dist / 0.6)
            E[:r_end, x0:x1] += spill[..., None] * _LED_BLUE

    rad *= E
    del E

    # --- glossy reflection of the wall (computed at half resolution) ------------------
    n = min(fh, yb)
    src = np.empty((fh, w, 3), np.float32)
    src[:n] = wall[yb - n : yb][::-1]
    if n < fh:
        src[n:] = wall[0:1]
    hw2, hh2 = max(1, w // 2), max(1, fh // 2)
    half = cv2.resize(src, (hw2, hh2), interpolation=cv2.INTER_AREA)
    del src
    l0 = _blur(half, 1.25 * u, 4.5 * u)
    l1 = _big_blur(half, 4.5 * u, 17.0 * u)
    l2 = _big_blur(half, 12.0 * u, 45.0 * u)
    yh = (np.arange(hh2, dtype=np.float32) + 0.5)[:, None] * (fh / hh2)
    d = yh / max(1.0, 0.30 * h)
    w0 = 1.0 - _smoothstep(0.0, 0.10, d)
    w2 = _smoothstep(0.08, 0.75, d)
    w1 = np.clip(1.0 - w0 - w2, 0.0, 1.0)
    l0 *= w0[..., None]
    l0 += l1 * w1[..., None]
    l0 += l2 * w2[..., None]
    refl = cv2.resize(l0, (w, fh), interpolation=cv2.INTER_LINEAR)
    del l0, l1, l2, half
    R = _floor_reflectance(sc)[:, None]
    refl *= (R * gloss)[..., None]
    rad *= 1.0 - 0.5 * R[..., None]
    rad += refl
    return rad, refl


# ---------------------------------------------------------------------------
# Plants
# ---------------------------------------------------------------------------


@dataclass
class _Plant:
    premul: np.ndarray
    alpha: np.ndarray
    x0: int
    y0: int
    contact_y: float  # floor contact row (image coords)
    cx: float
    pot_r: float
    ell_bot: float
    #: Pixels per metre used for the plant (may be reduced to fit the corridor).
    ppm: float


_PLANT_DEPTH = 0.60  # metres in front of the back wall


def _render_plant(sc: _Scene, seed: int, side: int) -> _Plant:
    """Small-leaved indoor tree (ficus/olive style) in a tall anthracite planter."""
    rng = _rng(seed, 40 + (0 if side < 0 else 1))
    u = sc.unit
    S = 3  # supersampling
    z = sc.z_wall - _PLANT_DEPTH
    contact = sc.floor_y(z)
    # keep every plant pixel in the outer 8 % of the frame (narrow/portrait
    # frames get a proportionally smaller plant)
    limit = 0.077 * sc.w
    ppm = sc.ppm_at(z)
    ppm *= min(1.0, limit / (2.45 * 0.25 * ppm))
    pot_r = 0.25 * ppm
    canopy_rx = 0.50 * ppm
    leaf_len = 0.08 * ppm
    # pot fully visible, canopy may be cropped by the frame edge on the outer side
    cxp = max(1.2 * pot_r, min(0.034 * sc.w, limit - 1.4 * pot_r))
    if side > 0:
        cxp = sc.w - cxp
    pot_h = 0.66 * ppm
    trunk_h = 1.20 * ppm
    canopy_ry = 0.90 * ppm
    pw = int(2 * (canopy_rx + leaf_len) + 24 * u + 8)
    ph = int(pot_h + trunk_h + canopy_ry + leaf_len + 0.15 * ppm + 24 * u + 8)
    x0 = int(round(cxp - pw / 2))
    y0 = int(round(contact + 0.06 * ppm + 10 * u - ph))
    W, H = pw * S, ph * S
    prem = np.zeros((H, W, 3), np.float32)
    alpha = np.zeros((H, W), np.float32)

    pcx = (cxp - x0) * S
    p_contact = (contact - y0) * S
    r = pot_r * S
    top_y = p_contact - pot_h * S

    def ell(y_row: float, rr: float) -> float:
        return rr * (y_row / S + y0 - sc.y_eye) / sc.focal

    e_top = ell(top_y, r)
    r_bot = r * 0.86
    e_bot = ell(p_contact, r_bot)
    ys = (np.arange(H, dtype=np.float32) + 0.5)[:, None]
    xs = (np.arange(W, dtype=np.float32) + 0.5)[None, :]

    def put(col: np.ndarray, cov: np.ndarray, ya: int, yb_: int, xa: int, xb: int) -> None:
        a = cov[..., None]
        prem[ya:yb_, xa:xb] = prem[ya:yb_, xa:xb] * (1 - a) + col * a
        alpha[ya:yb_, xa:xb] = alpha[ya:yb_, xa:xb] * (1 - cov) + cov

    # --- pot opening: rim top surface and soil -------------------------------------
    ya, yb_ = max(0, int(top_y - e_top - 4)), min(H, int(top_y + e_top + 4))
    xa, xb = max(0, int(pcx - r - 4)), min(W, int(pcx + r + 4))
    gx = xs[:, xa:xb] - pcx
    gy = ys[ya:yb_] - top_y
    rr_out = np.sqrt((gx / r) ** 2 + (gy / max(e_top, 0.5)) ** 2)
    r_in = r - 0.02 * ppm * S
    rr_in = np.sqrt((gx / r_in) ** 2 + (gy / max(e_top * r_in / r, 0.5)) ** 2)
    aa = 1.2 / max(e_top, 1.0)
    cov_out = np.clip((1.0 - rr_out) / aa + 0.5, 0.0, 1.0)
    cov_in = np.clip((1.0 - rr_in) / aa + 0.5, 0.0, 1.0)
    put(np.full(cov_out.shape + (3,), 0.10, np.float32), cov_out, ya, yb_, xa, xb)
    soil = np.array([0.016, 0.012, 0.009], np.float32) * np.ones(cov_in.shape + (3,), np.float32)
    put(soil, cov_in, ya, yb_, xa, xb)

    # --- branches and leaves ----------------------------------------------------------
    # A short trunk forks into a few curved branches; leaves cluster along the
    # outer part of every branch. Everything is drawn back to front.
    trunk_top = (pcx + rng.normal(0.0, 0.03) * canopy_rx * S, top_y - trunk_h * S)
    branches: list[np.ndarray] = []
    n_br = 5
    for i in range(n_br):
        frac = (i + 0.5) / n_br
        base_t = rng.uniform(0.70, 1.0)
        bx0 = pcx + (trunk_top[0] - pcx) * base_t
        by0 = top_y + (trunk_top[1] - top_y) * base_t
        a = math.radians(-90.0 + (frac - 0.5) * 120.0 + rng.normal(0.0, 10.0))
        length = rng.uniform(0.55, 0.95) * 0.78 * ppm * S
        curl = rng.normal(0.0, 0.25)
        pts = []
        for t in np.linspace(0.0, 1.0, 10):
            ang_t = a + curl * t
            pts.append((bx0 + math.cos(ang_t) * length * t, by0 + math.sin(ang_t) * length * t * 1.05))
        branches.append(np.array(pts, np.float32))
    trunk = np.array(
        [
            (
                pcx + (trunk_top[0] - pcx) * t + math.sin(t * 3.1) * 0.015 * ppm * S,
                top_y + 0.02 * ppm * S + (trunk_top[1] - top_y) * t,
            )
            for t in np.linspace(0.0, 1.0, 12)
        ],
        np.float32,
    )
    leaves_x, leaves_y = [], []
    for br in branches:
        for t, n_l in ((0.35, 50), (0.55, 80), (0.75, 110), (0.95, 150)):
            k = int(round(t * (len(br) - 1)))
            px_, py_ = br[k]
            spread = (0.12 + 0.19 * t) * ppm * S
            leaves_x.append(rng.normal(px_, spread, n_l))
            leaves_y.append(rng.normal(py_, spread * 0.85, n_l))
    tx, ty = trunk_top
    leaves_x.append(rng.normal(tx, 0.20 * ppm * S, 160))
    leaves_y.append(rng.normal(ty - 0.06 * ppm * S, 0.17 * ppm * S, 160))
    lx = np.concatenate(leaves_x)
    ly = np.concatenate(leaves_y)
    # keep the canopy inside its corridor: squeeze the inner side (the outer
    # side may be cropped by the frame edge)
    inner = (
        (limit - x0) * S - leaf_len * S * 0.6 if side < 0 else (sc.w - limit - x0) * S + leaf_len * S * 0.6
    )
    reach = np.concatenate([lx] + [br[:, 0] for br in branches]) - pcx
    reach = reach.max() if side < 0 else -reach.min()
    allowed = abs(inner - pcx)
    squeeze = min(1.0, allowed / max(1.0, float(reach)))

    def squeeze_x(x: np.ndarray) -> np.ndarray:
        inward = (x - pcx) * (-side) > 0
        return np.where(inward, pcx + (x - pcx) * squeeze, x)

    lx = squeeze_x(lx)
    branches = [np.stack([squeeze_x(br[:, 0]), br[:, 1]], axis=1).astype(np.float32) for br in branches]
    keep = (lx > leaf_len * S) & (lx < W - leaf_len * S) & (ly > leaf_len * S) & (ly < top_y - 0.15 * ppm * S)
    lx, ly = lx[keep], ly[keep]
    n = lx.size
    cy_mid = float(np.median(ly))
    cy_span = max(1.0, float(np.percentile(ly, 95) - np.percentile(ly, 5)))
    rel_y = np.clip((ly - cy_mid) / cy_span * 2.0, -1.0, 1.0)  # -1 top .. 1 bottom
    rel_x = np.clip((lx - pcx) / (canopy_rx * S), -1.0, 1.0)
    depth = rng.uniform(-1.0, 1.0, n)
    ang = rng.uniform(0.0, 180.0, n)
    size = rng.uniform(0.75, 1.2, n) * leaf_len * S
    foreshort = rng.uniform(0.30, 1.0, n)
    light = 0.45 * (1.0 - (rel_y + 1.0) / 2.0) + 0.40 * (depth + 1.0) / 2.0 + 0.15 * (0.5 - 0.5 * rel_x)
    light = np.clip(light + rng.normal(0.0, 0.12, n), 0.0, 1.0)
    hue = rng.normal(0.0, 1.0, n)
    sheen = rng.uniform(0.0, 1.0, n) < 0.14
    order = np.argsort(depth)
    leaf_dark = np.array([0.008, 0.020, 0.008])
    leaf_lit = np.array([0.060, 0.120, 0.034])
    leaf_yellow = np.array([0.085, 0.125, 0.028])
    bark = (0.050, 0.040, 0.031)
    trunk_w = max(2, int(0.035 * ppm * S))

    def draw_poly(pts: np.ndarray, width: int) -> None:
        for (xa_, ya_), (xb2, yb2) in itertools.pairwise(pts):
            p0, p1 = (int(xa_), int(ya_)), (int(xb2), int(yb2))
            cv2.line(prem, p0, p1, bark, width, cv2.LINE_8)
            cv2.line(alpha, p0, p1, 1.0, width, cv2.LINE_8)

    drawn_wood = False
    for k in order:
        if not drawn_wood and depth[k] > -0.1:
            draw_poly(trunk, trunk_w)
            for br in branches:
                draw_poly(br, max(1, int(trunk_w * 0.45)))
            drawn_wood = True
        lt = float(light[k])
        col = leaf_dark + (leaf_lit - leaf_dark) * lt
        col = col + (leaf_yellow - leaf_lit) * max(0.0, float(hue[k])) * 0.25 * lt
        if sheen[k]:  # glossy leaf mirroring the bright showroom
            col = col * 0.7 + np.array([0.05, 0.065, 0.06]) * (0.5 + lt)
        c = (float(col[0]), float(col[1]), float(col[2]))
        axes = (max(1, int(size[k] * 0.5)), max(1, int(size[k] * 0.21 * foreshort[k])))
        center = (int(lx[k]), int(ly[k]))
        cv2.ellipse(prem, center, axes, float(ang[k]), 0, 360, c, -1, cv2.LINE_8)
        cv2.ellipse(alpha, center, axes, float(ang[k]), 0, 360, 1.0, -1, cv2.LINE_8)
    if not drawn_wood:
        draw_poly(trunk, trunk_w)

    # --- pot body (tall, slightly tapered, matte anthracite) --------------------------
    ya, yb_ = max(0, int(top_y - 2)), min(H, int(p_contact + 3))
    xa, xb = max(0, int(pcx - r - 4)), min(W, int(pcx + r + 4))
    yy = ys[ya:yb_]
    tt = np.clip((yy - top_y) / max(1.0, p_contact - top_y), 0.0, 1.0)
    rad_y = r - (r - r_bot) * tt
    gx = xs[:, xa:xb] - pcx
    nxp = np.clip(gx / rad_y, -1.0, 1.0)
    root = np.sqrt(np.clip(1.0 - nxp * nxp, 0.0, 1.0))
    top_edge = top_y + e_top * root
    bot_edge = p_contact - e_bot + e_bot * root
    cov = np.clip(rad_y - np.abs(gx) + 0.5, 0.0, 1.0)
    cov = cov * np.clip(yy - top_edge + 0.5, 0.0, 1.0) * np.clip(bot_edge - yy + 0.5, 0.0, 1.0)
    diffuse = np.clip(-0.35 * nxp + 0.82 * root, 0.0, 1.0)
    rim_light = 0.8 * np.abs(nxp) ** 10
    lip = np.exp(-(((yy - top_edge) / (0.010 * ppm * S)) ** 2)) * 0.9
    vert = 1.0 - 0.35 * tt**1.5
    shade = (0.28 + 0.80 * diffuse) * vert + rim_light + lip
    pot_col = np.array([0.036, 0.036, 0.038], np.float32)
    put((pot_col * shade[..., None]).astype(np.float32), cov.astype(np.float32), ya, yb_, xa, xb)

    prem = cv2.resize(prem, (pw, ph), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, (pw, ph), interpolation=cv2.INTER_AREA)
    sig = 1.4 * u
    prem = _blur(prem, sig)
    alpha = np.clip(_blur(alpha, sig), 0.0, 1.0)
    prem = np.minimum(prem, alpha[..., None])
    return _Plant(prem, alpha, x0, y0, contact, cxp, pot_r, e_bot / S, ppm)


def _composite_plant(img: np.ndarray, refl_term: np.ndarray, sc: _Scene, plant: _Plant) -> None:
    u = sc.unit
    ph, pw = plant.alpha.shape
    ppm = plant.ppm
    # soft shadow on the wall/panel behind (light from the front ceiling)
    sh = _big_blur(plant.alpha, 0.07 * ppm, 0.07 * ppm)
    sh = _shift_down(sh, 0.10 * ppm)
    wall_shade = 1.0 - 0.18 * sh
    _multiply_patch(img, wall_shade, plant.x0, plant.y0)
    # contact / ambient occlusion shadow on the floor
    pad = int(plant.pot_r * 1.6)
    sw, sh_ = int(2 * pad), int(pad)
    gy, gx = np.mgrid[0:sh_, 0:sw].astype(np.float32)
    ex = (gx - sw / 2) / (plant.pot_r * 1.25)
    ey = (gy - sh_ * 0.35) / max(plant.ell_bot * 1.9 + 2.0, 2.0)
    occ = np.exp(-(ex * ex + ey * ey) * 1.6)
    occ = _blur(occ, 2.0 * u)
    _multiply_patch(img, 1.0 - 0.55 * occ, int(plant.cx - sw / 2), int(plant.contact_y - sh_ * 0.35))
    # glossy floor reflection of the plant (replaces the wall reflection behind it)
    c = int(round(plant.contact_y - plant.y0))
    c = min(max(c, 1), ph)
    src_p = plant.premul[:c][::-1]
    src_a = plant.alpha[:c][::-1]
    n = src_a.shape[0]
    fade = np.exp(-np.arange(n, dtype=np.float32) / (0.30 * ppm))[:, None]
    src_p = _blur(src_p * fade[..., None], 1.5 * u, 7.0 * u)
    src_a = _blur(src_a * fade, 1.5 * u, 7.0 * u)
    ry0 = plant.y0 + c  # first reflected row in image coords
    R = _floor_reflectance(sc)
    H, W = img.shape[:2]
    for k in range(n):
        y = ry0 + k
        if y < sc.yb or y >= H:
            continue
        xa, xb = max(0, plant.x0), min(W, plant.x0 + pw)
        if xa >= xb:
            continue
        a = src_a[k, xa - plant.x0 : xb - plant.x0, None]
        p = src_p[k, xa - plant.x0 : xb - plant.x0]
        rt = refl_term[y - sc.yb, xa:xb]
        img[y, xa:xb] += -rt * a + R[y - sc.yb] * p
    _over(img, plant.premul, plant.alpha, plant.x0, plant.y0)


# ---------------------------------------------------------------------------
# Brand composite, optics, encoding
# ---------------------------------------------------------------------------


def _composite_signs(img: np.ndarray, signs: list[_Sign]) -> None:
    for sign in signs:
        steps = int(round(sign.depth))
        for k in range(steps, 0, -1):
            # visible underside of stand-off letters (camera is below the sign)
            sp = _shift_down(sign.premul, float(k))
            sa = _shift_down(sign.alpha, float(k))
            _over(img, sp * 0.45, sa, sign.x, sign.y)
        _over(img, sign.premul, sign.alpha, sign.x, sign.y)


def _depth_of_field(img: np.ndarray, sc: _Scene) -> np.ndarray:
    """Thin-lens style blur: focus on the vehicle, wall and foreground slightly soft."""
    u = sc.unit
    y_focus = 0.80 * sc.h
    wall_sigma = 0.75 * u
    k = wall_sigma / max(1e-3, abs(1.0 - (sc.yb - sc.y_eye) / (y_focus - sc.y_eye)))
    ys = np.arange(sc.h, dtype=np.float32) + 0.5
    sig = np.empty(sc.h, np.float32)
    sig[: sc.yb] = wall_sigma
    sig[sc.yb :] = k * np.abs(1.0 - (ys[sc.yb :] - sc.y_eye) / (y_focus - sc.y_eye))
    top = 1.3 * u
    sig = np.clip(sig, 0.0, top)
    # blend between the sharp image and two blur levels, row by row
    mid = 0.5 * top
    w_mid = np.where(sig <= mid, sig / mid, (top - sig) / (top - mid))
    w_top = np.where(sig <= mid, 0.0, (sig - mid) / (top - mid))
    w_0 = 1.0 - w_mid - w_top
    out = img * w_0[:, None, None]
    out += _blur(img, mid) * w_mid[:, None, None]
    out += _blur(img, top) * w_top[:, None, None]
    return out


def _bloom(emit: np.ndarray, sc: _Scene) -> np.ndarray:
    """Lens glare around the light sources (computed at half resolution)."""
    u = sc.unit
    h, w = emit.shape[:2]
    half = cv2.resize(emit, (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA)
    b = 0.30 * _blur(half, 2.5 * u) + 0.22 * _big_blur(half, 14.0 * u) + 0.10 * _big_blur(half, 45.0 * u)
    return cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR)


def _vignette(sc: _Scene) -> np.ndarray:
    def fn(ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        nx = (xs - sc.cx) / (0.5 * sc.w)
        ny = (ys - 0.52 * sc.h) / (0.5 * sc.h)
        rr = np.sqrt(0.75 * nx * nx + ny * ny)
        return 1.0 - 0.30 * _smoothstep(0.50, 1.50, rr)

    return _lowres_field(sc.h, sc.w, max(2, int(round(8 * sc.unit))), fn)


_LUT_MAX = 4.0  # display-linear range covered by the encoding LUT


@lru_cache(maxsize=1)
def _encode_lut() -> np.ndarray:
    """Display-linear [0, _LUT_MAX] (16 bit index) -> sRGB in 8-bit units (float).

    Values up to the shoulder map 1:1 (official logo colours stay exact),
    brighter values are compressed softly instead of clipping.
    """
    x = np.linspace(0.0, _LUT_MAX, 65536, dtype=np.float64)
    sh = _SHOULDER
    y = np.where(x > sh, sh + (1.0 - sh) * (1.0 - np.exp(-(x - sh) / (1.0 - sh))), x)
    srgb = np.where(y <= 0.0031308, y * 12.92, 1.055 * np.power(y, 1 / 2.4) - 0.055)
    return (srgb * 255.0).astype(np.float32)


def _encode(img: np.ndarray, seed: int) -> np.ndarray:
    """Highlight shoulder + sRGB encoding (LUT), fine sensor grain, 8-bit quantisation."""
    np.clip(img, 0.0, _LUT_MAX, out=img)
    img *= 65535.0 / _LUT_MAX
    img += 0.5
    out = _encode_lut()[img.astype(np.uint16)]
    h, w = img.shape[:2]
    grain = _rng(seed, 99).standard_normal((h, w), dtype=np.float32)
    grain *= 0.85
    out += grain[..., None]
    out += 0.5
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_fallback_showroom(
    width: int,
    height: int,
    *,
    floor_horizon: float = 0.62,
    seed: int = 7,
    logo_path: Path | None = None,
    brand: ShowroomBrandText | None = None,
) -> Image.Image:
    """Return the fallback plate as RGB image (width x height), deterministic.

    Without ``logo_path``/``brand`` (the default used by the pipeline) the wall
    stays empty; branding is applied separately by ``app/showroom/branding.py``.
    """
    width, height = int(width), int(height)
    if width < 64 or height < 64:
        raise ValueError("showroom fallback needs at least 64x64 px")
    sc = _make_scene(width, height, floor_horizon)
    lay = _wall_layout(sc)
    signs = _brand_layout(sc, Path(logo_path), brand) if logo_path is not None and brand is not None else []

    wall, wall_emit = _render_wall(sc, lay, signs, seed)
    floor, refl_term = _render_floor(sc, lay, wall, seed)
    img = np.concatenate([wall, floor], axis=0)
    del floor

    for side in (-1, 1):
        _composite_plant(img, refl_term, sc, _render_plant(sc, seed, side))

    # Lens vignette on the scene only: the brand elements are composited
    # afterwards so the official logo colours are reproduced exactly.
    vignette = _vignette(sc)[..., None]
    img *= vignette
    _composite_signs(img, signs)
    img = _depth_of_field(img, sc)

    emit = np.zeros_like(img)
    emit[: sc.yb] = wall_emit
    del wall_emit
    bloom = _bloom(emit, sc)
    del emit
    bloom *= vignette
    img += bloom
    del bloom
    return Image.fromarray(_encode(img, seed), "RGB")
