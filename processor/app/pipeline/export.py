"""STEP 10 – JPEG export (sRGB, embedded profile, no camera metadata)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageCms

_SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def encode_jpeg(rgb: np.ndarray, quality: int = 92) -> bytes:
    """High-quality JPEG for listing portals (4:4:4 chroma, progressive).

    EXIF/GPS data of the original is intentionally not copied.
    """
    quality = int(min(max(quality, 85), 95))
    buffer = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(
        buffer,
        "JPEG",
        quality=quality,
        subsampling=0,
        optimize=True,
        progressive=True,
        icc_profile=_SRGB_ICC,
    )
    return buffer.getvalue()


def encode_png(image: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    mode = "RGBA" if image.ndim == 3 and image.shape[2] == 4 else ("RGB" if image.ndim == 3 else "L")
    Image.fromarray(image, mode).save(buffer, "PNG", optimize=False, compress_level=3)
    return buffer.getvalue()
