"""STEP 3 (alpha) – the segmentation model's alpha, snapped onto real colour edges.

Input is the segmentation model's own alpha, up-sampled to the photo with a
mild choke (``model_alpha`` – the honest reference: nothing in it is guessed
from the photo; ``refine_edges`` always starts from it, so its guarantees do
not depend on the caller). ``refine_edges`` only changes the uncertain edge
band of it; the opaque body and the background stay as they are and no pixel
colour is changed (the alpha only decides which ORIGINAL pixels are shown –
nothing is generated). Afterwards ``mask.clean_mask`` keeps the vehicle.

Why: the model alpha is computed at 1024 px; up-sampled to a 12 MP photo its
transition is 3–4 px wide and not placed on the real edge. A colour (RGB)
guided filter with a small ``eps`` snaps it onto the colour edge where car and
background really differ (He et al., "Guided Image Filtering": the local linear
colour model of closed-form matting, solved in one pass) – also where they
differ in colour more than in brightness (red paint on grey tiles).

Where the cut line has no colour contrast – black tyre on dark ground shadow,
dark lip over dark asphalt – a colour model only fits sensor noise (grainy
edge) or follows edges INSIDE the car (orange bumper over black lip). There the
model alpha is kept. The trust weight is the product of two smoothsteps: the
colour contrast inside the filter window and the colour distance between the
vehicle side and the background side of the current edge. The same weight,
measured along the outline, is reported as edge confidence (metadata only,
never a gate: low = the cut line is the model's guess, not backed by an edge in
the photo).

Limits (the fringe trade-off): a colour model also "sees" the car in
similar-coloured background next to the edge – dark ground under a dark tyre,
dark twigs next to dark trim. Unclamped it pulled that background into the
cut-out (ragged dark fringe under the tyres, specks next to the edge). So
outside the model's own 0.5 contour the colour model may only REMOVE alpha,
never add any above the model (applied last, for every pixel); the colour edge
can tighten the cut-out, not grow it. Inside the contour it may still make edge
pixels partly transparent – on dark busy background (twigs behind a black fin
antenna) this shows the twig texture the model alpha had smeared into a soft
dark rim. New transparency only appears within the band radius of background
the model alpha already has (no see-through hairlines along chrome trim or
window frames).

Thin parts: a part narrower than the filter window (antenna mast, mirror arm)
is attenuated – the box-averaged coefficients come mostly from windows that
see only background; on a real photo an antenna mast was eroded to a stub.
Pixels of the model's 0.5 mask that do not survive an opening with a disk of
``THIN_OPEN_FACTOR`` × the filter radius (``thin_parts``) therefore keep at
least the model alpha. Their soft outer fringe (outside the 0.5 contour) may
still be tightened by the colour edge.

Measured on the 12 accepted regression photos (no ground truth there): vs. the
model alpha the 0.5 contour moves inwards where a colour edge backs it and the
soft transition (0.05 < alpha < 0.95, per outline pixel) grows from 3.0–4.1 to
4.0–5.2 px; alpha above the model outside its contour: none, by construction;
0.3–0.7 s per 12 MP photo. On all 22 cached photos the thin-part rule restores
the antenna mast to the model alpha and changes the 0.5 contour elsewhere by at
most 1 px (mirror tips, fin tips, mudflap corners). The visible gain
over the plain model alpha is small (crisper on clean colour edges, more
texture on dark busy edges); the semi-synthetic ground-truth set of the
research phase (real cut-outs on real backgrounds, cleaned like the pipeline)
measures alpha SAD 1.32 (model alpha) → 1.16 px per outline px, unchanged by
the thin-part rule. The former first stage (a grey-guided filter with a large
``eps``, ``refine_alpha``) was dropped: it doubled the transition width (8–11 px
instead of 3–4 px), carried a halo of old background (twigs, snow, gravel;
about 2 px of alpha per column under the tyres instead of 0.6–0.9) around the
whole car, produced the speckle the ``busy_background`` warning used to count
and cost 0.6–2.7 s; feeding ``refine_edges`` with it instead of the model alpha
changed the result by < 3 %.

Deliberately NOT done (evaluated on the regression photos):
- closed-form / KNN matting (pymatting): not better on these photos (blocky at
  the band ends, pulls dark ground shadow into the car, noisy against trees)
  and it needs numba + llvmlite + scipy (~350 MB);
- opening "see-through" gaps (roof rails, spoilers) by colour similarity to
  the exterior background: paint and glass reflect the surroundings, so it
  marked real body parts (bumper lip, window frame, roof edge) – removing parts
  of the car is worse than a thin strip of old background under a roof rail.

Cost: the filter runs only on 64-px tiles that contain band pixels (a mosaic of
the tiles with an apron of >= 2r+1 px gives the full-frame result), in chunks
of 48 tiles so the memory does not grow with the outline length.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

#: Colour guided filter radius in model pixels (1 model px = long edge / model input).
RADIUS_MODEL_PX = 1.5
#: Edge band radius in model pixels around the uncertain model output.
BAND_MODEL_PX = 2.0
#: Regulariser of the colour guided filter (sRGB 0..1, per channel variance).
EPS = 1e-4
#: Colour std inside the filter window (sRGB 0..1, sqrt of the covariance trace):
#: below LOW the colour model would fit noise, above HIGH it is trusted.
CONTRAST_LOW = 0.035
CONTRAST_HIGH = 0.09
#: Colour distance (sRGB 0..1, Euclidean) between the mean vehicle-side and the
#: mean background-side colour next to the edge: below LOW they cannot be told
#: apart (black tyre on dark shadow), above HIGH the colour model is trusted.
SEPARATION_LOW = 0.06
SEPARATION_HIGH = 0.15
#: Mild choke of the model alpha (removes the faint old-background halo).
CHOKE = 0.04
#: Coarse alpha strictly between these values is "uncertain".
UNCERTAIN_LOW = 0.02
UNCERTAIN_HIGH = 0.98
#: Tile size of the band-only evaluation and tiles per mosaic (bounds the memory).
TILE = 64
CHUNK_TILES = 48
#: Thin parts of the model's 0.5 mask – everything that does not survive an
#: opening with a disk of this × the filter radius (dilated by 1 px): antenna
#: masts, mirror arms, wiper tips, sharp corners. They keep at least the model
#: alpha (see ``thin_parts``).
THIN_OPEN_FACTOR = 2.0
#: Lowest part of the vehicle (× outline height): tyres, sills, ground-shadow zone.
BOTTOM_ZONE = 0.25
#: An outline pixel counts as "backed by an edge in the photo" at this weight.
CONFIDENT_WEIGHT = 0.5


def _box(image: np.ndarray, radius: int) -> np.ndarray:
    size = 2 * radius + 1
    return cv2.boxFilter(image, -1, (size, size), normalize=True, borderType=cv2.BORDER_REFLECT)


def _smoothstep(x: np.ndarray, low: float, high: float) -> np.ndarray:
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _choke(alpha: np.ndarray) -> np.ndarray:
    return np.clip((alpha - CHOKE) / (1.0 - 2.0 * CHOKE), 0.0, 1.0)


def thin_parts(solid: np.ndarray, radius: int, region: np.ndarray | None = None) -> np.ndarray:
    """Pixels of the binary mask ``solid`` that do not survive a morphological
    opening with a disk of ``radius`` (the opened mask dilated by 1 px): parts
    narrower than about 2·radius + 1 px and sharp corners.

    Only evaluated inside the bounding box of ``region`` (with a margin of
    2·radius + 2 px, so the result there equals the full-frame opening); False
    elsewhere. The image border does not erode the mask.
    """
    solid = np.asarray(solid, dtype=bool)
    out = np.zeros(solid.shape, bool)
    if region is None:
        region = solid
    ys, xs = np.nonzero(region)
    if len(xs) == 0 or radius < 1:
        return out
    pad = 2 * radius + 2
    h, w = solid.shape
    y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad + 1, h)
    x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad + 1, w)
    crop = solid[y0:y1, x0:x1].astype(np.uint8)
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    thick = cv2.dilate(cv2.morphologyEx(crop, cv2.MORPH_OPEN, disk), np.ones((3, 3), np.uint8))
    out[y0:y1, x0:x1] = (crop > 0) & (thick == 0)
    return out


def model_alpha(coarse: np.ndarray) -> np.ndarray:
    """The segmentation model's own alpha (up-sampled to the photo) with the mild
    choke – the reference ``refine_edges`` starts from (float32 in [0, 1])."""
    return _choke(np.asarray(coarse, dtype=np.float32)).astype(np.float32)


def colour_guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> tuple[np.ndarray, np.ndarray]:
    """Colour guided filter (He et al.) – returns (filtered src, local colour std).

    ``guide`` float32 (h, w, 3) in [0, 1], ``src`` float32 (h, w). The local colour
    std is sqrt(trace(cov(guide))) over the (2r+1)² window.
    """
    guide = np.ascontiguousarray(guide, dtype=np.float32)
    src = np.ascontiguousarray(src, dtype=np.float32)
    g_r, g_g, g_b = guide[..., 0], guide[..., 1], guide[..., 2]
    mean_i = _box(guide, radius)
    m_r, m_g, m_b = mean_i[..., 0], mean_i[..., 1], mean_i[..., 2]
    mean_p = _box(src, radius)
    mean_ip = _box(guide * src[..., None], radius)
    c_r = mean_ip[..., 0] - m_r * mean_p
    c_g = mean_ip[..., 1] - m_g * mean_p
    c_b = mean_ip[..., 2] - m_b * mean_p
    diag = _box(guide * guide, radius)
    off = _box(np.stack([g_r * g_g, g_r * g_b, g_g * g_b], axis=-1), radius)
    v_rr = diag[..., 0] - m_r * m_r
    v_gg = diag[..., 1] - m_g * m_g
    v_bb = diag[..., 2] - m_b * m_b
    v_rg = off[..., 0] - m_r * m_g
    v_rb = off[..., 1] - m_r * m_b
    v_gb = off[..., 2] - m_g * m_b
    contrast = np.sqrt(np.maximum(v_rr + v_gg + v_bb, 0.0))
    v_rr = v_rr + eps
    v_gg = v_gg + eps
    v_bb = v_bb + eps
    # inverse of the symmetric, positive definite 3x3 matrix (adjugate / determinant)
    i_rr = v_gg * v_bb - v_gb * v_gb
    i_rg = v_rb * v_gb - v_rg * v_bb
    i_rb = v_rg * v_gb - v_rb * v_gg
    i_gg = v_rr * v_bb - v_rb * v_rb
    i_gb = v_rg * v_rb - v_rr * v_gb
    i_bb = v_rr * v_gg - v_rg * v_rg
    det = np.maximum(v_rr * i_rr + v_rg * i_rg + v_rb * i_rb, 1e-12)
    a_r = (i_rr * c_r + i_rg * c_g + i_rb * c_b) / det
    a_g = (i_rg * c_r + i_gg * c_g + i_gb * c_b) / det
    a_b = (i_rb * c_r + i_gb * c_g + i_bb * c_b) / det
    offset = mean_p - a_r * m_r - a_g * m_g - a_b * m_b
    coeff = _box(np.stack([a_r, a_g, a_b], axis=-1), radius)
    q = (coeff * guide).sum(axis=-1) + _box(offset, radius)
    return q.astype(np.float32), contrast.astype(np.float32)


def side_separation(guide: np.ndarray, alpha: np.ndarray, radius: int) -> np.ndarray:
    """|mean colour where alpha >= 0.9 − mean colour where alpha <= 0.1| inside the
    (2·radius+1)² window; 0 where one of the two sides is missing.

    Unlike the window's colour variance this ignores edges INSIDE the vehicle
    (orange bumper over a black lip): the colour model is only trusted where the
    vehicle and the background really differ next to the cut line.
    """
    fg = (alpha >= 0.9).astype(np.float32)
    bg = (alpha <= 0.1).astype(np.float32)
    w_fg = _box(fg, radius)
    w_bg = _box(bg, radius)
    mean_fg = _box(guide * fg[..., None], radius) / np.maximum(w_fg, 1e-6)[..., None]
    mean_bg = _box(guide * bg[..., None], radius) / np.maximum(w_bg, 1e-6)[..., None]
    distance = np.sqrt(((mean_fg - mean_bg) ** 2).sum(axis=-1))
    return np.where((w_fg > 0.02) & (w_bg > 0.02), distance, 0.0).astype(np.float32)


def trust_weight(contrast: np.ndarray, separation: np.ndarray) -> np.ndarray:
    """0 = no colour evidence for the cut line (use the model alpha), 1 = colour edge."""
    return _smoothstep(contrast, CONTRAST_LOW, CONTRAST_HIGH) * _smoothstep(separation, SEPARATION_LOW, SEPARATION_HIGH)


def _active_tiles(band: np.ndarray) -> list[tuple[int, int]]:
    h, w = band.shape
    th, tw = -(-h // TILE), -(-w // TILE)
    padded = np.zeros((th * TILE, tw * TILE), bool)
    padded[:h, :w] = band
    hits = padded.reshape(th, TILE, tw, TILE).any(axis=(1, 3))
    return [(int(ty) * TILE, int(tx) * TILE) for ty, tx in zip(*np.nonzero(hits))]


def _tile_window(image: np.ndarray, ty: int, tx: int, apron: int) -> np.ndarray:
    """``image[ty-apron : ty+TILE+apron, tx-apron : tx+TILE+apron]`` with reflected
    borders – the values the full-frame box filter (BORDER_REFLECT) would see."""
    h, w = image.shape[:2]
    y0, y1, x0, x1 = ty - apron, ty + TILE + apron, tx - apron, tx + TILE + apron
    window = image[max(y0, 0) : min(y1, h), max(x0, 0) : min(x1, w)]
    top, bottom, left, right = max(0, -y0), max(0, y1 - h), max(0, -x0), max(0, x1 - w)
    if top or bottom or left or right:
        window = cv2.copyMakeBorder(window, top, bottom, left, right, cv2.BORDER_REFLECT)
    return window


def _filter_tiles(
    rgb: np.ndarray,
    coarse: np.ndarray,
    alpha: np.ndarray,
    tiles: list[tuple[int, int]],
    radius: int,
    band_radius: int,
    apron: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Colour guided filter, trust weight and change permissions on a mosaic of
    the active tiles.

    Each tile carries an apron of >= 2r+1 px; every core pixel depends on pixels
    within max(2r, band radius) only, so the cores equal the full-frame result.
    Returns mosaics (filtered alpha, trust weight, may-open, may-close) and the
    mosaic column count. "may-open": within the band radius of background that
    ``alpha`` already has – new transparency never appears inside the solid
    body (no hairlines along chrome trim or window frames); "may-close": the
    same for opacity next to the existing vehicle.
    """
    size = TILE + 2 * apron
    cols = max(1, int(np.ceil(np.sqrt(len(tiles)))))
    rows = -(-len(tiles) // cols)
    mosaic_i = np.zeros((rows * size, cols * size, 3), np.float32)
    mosaic_p = np.zeros((rows * size, cols * size), np.float32)
    mosaic_a = np.zeros((rows * size, cols * size), np.float32)
    for n, (ty, tx) in enumerate(tiles):
        my, mx = (n // cols) * size, (n % cols) * size
        mosaic_i[my : my + size, mx : mx + size] = _tile_window(rgb, ty, tx, apron) * np.float32(1.0 / 255.0)
        mosaic_p[my : my + size, mx : mx + size] = _tile_window(coarse, ty, tx, apron)
        mosaic_a[my : my + size, mx : mx + size] = _tile_window(alpha, ty, tx, apron)
    sharp, contrast = colour_guided_filter(mosaic_i, mosaic_p, radius, EPS)
    weight = trust_weight(contrast, side_separation(mosaic_i, mosaic_a, 2 * radius))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band_radius + 1, 2 * band_radius + 1))
    solid = (mosaic_a >= 0.5).astype(np.uint8)
    may_open = cv2.dilate(1 - solid, kernel).astype(bool)
    may_close = cv2.dilate(solid, kernel).astype(bool)
    return _choke(sharp), weight, may_open, may_close, cols


def refine_edges(
    rgb: np.ndarray,
    coarse: np.ndarray,
    *,
    model_input_size: int = 1024,
) -> tuple[np.ndarray, dict]:
    """Snap the model alpha's transition onto real colour edges where the photo backs it.

    ``rgb`` uint8 (h, w, 3) original photo; ``coarse`` the segmenter output
    up-sampled to the photo (float in [0, 1]). Starts from ``model_alpha(coarse)``
    and returns the refined alpha (float32, same size) and JSON-serialisable
    diagnostics, among them ``edgeConfidence``, ``lowContrastFraction`` and
    ``bottomLowContrastFraction`` (job metadata only – never a quality gate).

    Guarantees (for every pixel, by construction): only the edge band differs
    from the model alpha; outside the model's 0.5 contour the result never
    exceeds the model alpha; thin parts of the model's 0.5 mask (``thin_parts``:
    antenna masts, mirror arms) never fall below it.
    """
    started = time.perf_counter()
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("expected an RGB image")
    if rgb.shape[:2] != coarse.shape:
        raise ValueError("rgb and coarse must have the same size")
    height, width = coarse.shape
    coarse = np.asarray(coarse, dtype=np.float32)
    alpha = model_alpha(coarse)
    upscale = max(height, width) / float(model_input_size)
    radius = int(np.clip(round(RADIUS_MODEL_PX * upscale), 1, 16))
    band_radius = int(np.clip(round(BAND_MODEL_PX * upscale), 2, 24))
    uncertain = ((coarse > UNCERTAIN_LOW) & (coarse < UNCERTAIN_HIGH)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band_radius + 1, 2 * band_radius + 1))
    band = cv2.dilate(uncertain, kernel).astype(bool)
    outline = _outline(alpha)
    refined = alpha.copy()
    band_px = int(band.sum())
    # The colour model attenuates parts narrower than its window (the box-averaged
    # coefficients come mostly from background-only windows): it eroded an antenna
    # mast to a stub. Thin parts of the model's own mask keep at least its alpha.
    thin = thin_parts(alpha >= 0.5, int(round(THIN_OPEN_FACTOR * radius)), band) & band if band_px else band
    weights: list[np.ndarray] = []
    rows: list[np.ndarray] = []
    changed, change_sum, protected = 0, 0.0, 0

    tiles = _active_tiles(band) if band_px else []
    apron = max(2 * radius + 1, band_radius + 1)
    size = TILE + 2 * apron
    for first in range(0, len(tiles), CHUNK_TILES):  # bounded memory: one mosaic per chunk
        chunk = tiles[first : first + CHUNK_TILES]
        sharp_m, weight_m, open_m, close_m, cols = _filter_tiles(rgb, coarse, alpha, chunk, radius, band_radius, apron)
        for n, (ty, tx) in enumerate(chunk):
            hh, ww = min(TILE, height - ty), min(TILE, width - tx)
            my, mx = (n // cols) * size + apron, (n % cols) * size + apron
            tile_band = band[ty : ty + hh, tx : tx + ww]
            weight = weight_m[my : my + hh, mx : mx + ww] * tile_band
            model = alpha[ty : ty + hh, tx : tx + ww]
            after = model + weight * (sharp_m[my : my + hh, mx : mx + ww] - model)
            # new transparency only next to existing background, new opacity only next to the vehicle
            lower = np.where(open_m[my : my + hh, mx : mx + ww], 0.0, model)
            upper = np.where(close_m[my : my + hh, mx : mx + ww], 1.0, model)
            after = np.where(tile_band, np.clip(after, lower, upper), model)
            # thin vehicle parts (antenna mast, mirror arm) keep at least the model alpha
            tile_thin = thin[ty : ty + hh, tx : tx + ww]
            raised = tile_thin & (after < model)
            protected += int(raised.sum())
            after = np.where(raised, model, after)
            # fringe clamp (last): outside the model's 0.5 contour the colour model may only
            # REMOVE alpha – dark busy background next to a dark edge (twigs, ground under a
            # tyre) otherwise gains a loose fringe / ragged rim of old background
            after = np.where(model < 0.5, np.minimum(after, model), after)
            refined[ty : ty + hh, tx : tx + ww] = after
            delta = np.abs(after - model)[tile_band]
            changed += int((delta > 0.1).sum())
            change_sum += float(delta.sum())
            tile_outline = outline[ty : ty + hh, tx : tx + ww]
            if tile_outline.any():
                weights.append(weight[tile_outline])
                rows.append(np.nonzero(tile_outline)[0] + ty)

    diagnostics: dict = {
        "radius": radius,
        "bandRadius": band_radius,
        "eps": EPS,
        "bandPx": band_px,
        "tiles": len(tiles),
        "changedPx": changed,
        "meanAbsChange": round(change_sum / max(band_px, 1), 4),
        "thinPx": int(thin.sum()),
        "thinProtectedPx": protected,
    }
    diagnostics.update(_edge_stats(outline, weights, rows))
    diagnostics["ms"] = round((time.perf_counter() - started) * 1000)
    return refined, diagnostics


def _outline(alpha: np.ndarray) -> np.ndarray:
    """Inner outline (1 px) of the solid vehicle (alpha >= 0.5)."""
    solid = (alpha >= 0.5).astype(np.uint8)
    return cv2.erode(solid, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_REPLICATE) < solid


def _edge_stats(outline: np.ndarray, weights: list[np.ndarray], rows: list[np.ndarray]) -> dict:
    """Share of the cut line backed by a colour edge in the photo (overall and in
    the tyre/sill zone). Outline pixels outside the band count as weight 0."""
    ys = np.nonzero(outline)[0]
    total = len(ys)
    if total == 0:
        return {"outlinePx": 0, "edgeConfidence": 0.0, "lowContrastFraction": 1.0, "bottomLowContrastFraction": 1.0}
    top, bottom = int(ys.min()), int(ys.max())
    limit = bottom - BOTTOM_ZONE * max(bottom - top, 1)
    w = np.concatenate(weights) if weights else np.zeros(0, np.float32)
    y = np.concatenate(rows) if rows else np.zeros(0, np.int64)
    low = int((w < CONFIDENT_WEIGHT).sum()) + (total - len(w))
    zone_total = int((ys >= limit).sum())
    in_zone = y >= limit
    zone_low = int((w[in_zone] < CONFIDENT_WEIGHT).sum()) + (zone_total - int(in_zone.sum()))
    return {
        "outlinePx": total,
        "edgeConfidence": round(float(w.sum()) / total, 4),
        "lowContrastFraction": round(low / total, 4),
        "bottomLowContrastFraction": round(zone_low / max(zone_total, 1), 4),
    }
