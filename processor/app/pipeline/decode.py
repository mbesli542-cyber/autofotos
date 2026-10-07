"""STEP 1 – decode an uploaded photo.

- applies the EXIF orientation (phones store rotated pixels + a flag),
- converts embedded colour profiles (e.g. iPhone Display P3) to sRGB so the
  paint colour is interpreted correctly – no colour is "invented" here,
- supports JPEG, PNG, WebP and HEIC/HEIF (pillow-heif).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageCms, ImageOps

try:  # HEIC/HEIF from iPhones
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

#: Refuse absurdly large images (decompression bombs). 80 MP covers every phone.
MAX_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

_SRGB_PROFILE = ImageCms.createProfile("sRGB")


class DecodeError(Exception):
    """The upload is not a readable image."""


@dataclass(frozen=True)
class DecodedImage:
    #: sRGB pixels, uint8, shape (H, W, 3), already upright.
    rgb: np.ndarray
    format: str | None
    #: EXIF orientation value that was applied (1 = none).
    orientation: int
    #: True if an embedded ICC profile was converted to sRGB.
    converted_from_profile: str | None

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])


def _to_srgb(image: Image.Image) -> tuple[Image.Image, str | None]:
    icc = image.info.get("icc_profile")
    if not icc:
        return image, None
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        description = ImageCms.getProfileDescription(source).strip() or "embedded"
        if "srgb" in description.lower():
            return image, None
        converted = ImageCms.profileToProfile(
            image,
            source,
            _SRGB_PROFILE,
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            outputMode="RGB",
        )
        return converted, description
    except Exception:
        # Broken profile: fall back to interpreting the pixels as sRGB.
        return image, None


def decode_image(data: bytes) -> DecodedImage:
    if not data:
        raise DecodeError("empty upload")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Image.DecompressionBombError as error:
        raise DecodeError("image too large") from error
    except Exception as error:
        raise DecodeError("not a readable image") from error

    fmt = image.format
    orientation = 1
    try:
        orientation = int(image.getexif().get(0x0112, 1) or 1)
    except Exception:
        orientation = 1
    icc = image.info.get("icc_profile")

    image = ImageOps.exif_transpose(image)
    if icc and "icc_profile" not in image.info:
        image.info["icc_profile"] = icc

    if image.mode in ("RGBA", "LA", "P", "PA"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image)
        image = background.convert("RGB")
    elif image.mode in ("I;16", "I;16B", "I;16L", "I"):
        array = np.asarray(image, dtype=np.float32)
        array = np.clip(array / max(array.max(), 1.0) * 255.0, 0, 255).astype(np.uint8)
        image = Image.fromarray(array).convert("RGB")
    elif image.mode == "CMYK":
        image, _ = _to_srgb(image)
        image = image.convert("RGB")

    if image.mode != "RGB":
        image = image.convert("RGB")

    image, converted = _to_srgb(image)
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    if rgb.shape[0] < 64 or rgb.shape[1] < 64:
        raise DecodeError("image too small")
    return DecodedImage(rgb=rgb, format=fmt, orientation=orientation, converted_from_profile=converted)
