"""STEP 1 – decode an uploaded photo.

- checks the declared size BEFORE any pixel is decoded (decompression bombs):
  PNG, WebP and JPEG headers are read from the raw bytes before Pillow parses
  the file, HEIF through pillow-heif's lazy open (container only),
- refuses animated PNG/WebP before Pillow parses them; camera formats with
  several images use their primary image (MPO from phones: the first image,
  HEIF: the primary image),
- refuses floods of PNG/WebP chunks and of JPEG segments, padding or junk before
  Pillow parses them (it does so in Python and keeps such chunks in memory),
- decodes JPEGs above ``MAX_PIXELS`` (108/200 MP phone modes) at 1/2 (1/4, 1/8)
  by libjpeg's DCT scaling instead of refusing them; every other photo above
  the limit fails with ``ImageTooLargeError``, tiny ones with
  ``ImageTooSmallError``,
- applies the EXIF orientation (phones store rotated pixels + a flag),
- converts embedded colour profiles (e.g. iPhone Display P3) to sRGB so the
  paint colour is interpreted correctly – also for PNG/WebP with alpha or a
  palette – no colour is "invented" here,
- supports JPEG, PNG, WebP and HEIC/HEIF (pillow-heif).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageCms, ImageOps, JpegImagePlugin

try:  # HEIC/HEIF from iPhones
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

#: Refuse absurdly large images (decompression bombs). 80 MP covers every phone's
#: normal mode. Checked explicitly before decoding – Pillow itself only warns up to
#: twice its limit, and older Pillow versions allocated some canvases before it.
MAX_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
#: JPEGs above MAX_PIXELS up to this size (200 MP phone modes) are decoded reduced –
#: but only "camera" JPEGs (one sequential scan with every component): libjpeg then
#: needs no full-size coefficient buffer, so memory stays bounded by the reduced output.
MAX_REDUCED_JPEG_PIXELS = 4 * MAX_PIXELS
#: Photos with a shorter side are not photos (reported like the quality gate's resolution check).
MIN_SIDE = 64

#: Only photo formats are decoded (no TIFF/EPS/… parsers on untrusted uploads).
#: Multi-picture JPEGs (MPO, written by many phones) open through "JPEG".
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP") + (("HEIF",) if HEIF_SUPPORTED else ())
#: Formats whose extra images are part of a normal camera photo (stereo/depth/gain
#: images, bursts): only the primary image is decoded, the others are never read.
MULTI_IMAGE_FORMATS = frozenset({"MPO", "HEIF"})

_SRGB_PROFILE = ImageCms.createProfile("sRGB")


class DecodeError(Exception):
    """The upload is not a readable image."""

    #: errorCode of the failed job (app/jobs/manager.py).
    code = "decode"


class ImageTooLargeError(DecodeError):
    """More pixels than MAX_PIXELS that cannot be decoded reduced."""

    code = "image_too_large"


class ImageTooSmallError(DecodeError):
    """A side is shorter than MIN_SIDE px."""

    code = "source_resolution_too_low"


@dataclass(frozen=True)
class DecodedImage:
    #: sRGB pixels, uint8, shape (H, W, 3), already upright.
    rgb: np.ndarray
    format: str | None
    #: EXIF orientation value that was applied (1 = none).
    orientation: int
    #: True if an embedded ICC profile was converted to sRGB.
    converted_from_profile: str | None
    #: The JPEG was decoded at 1/reduction of its size (DCT scaling, above MAX_PIXELS); 1 = full size.
    reduction: int = 1

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])


# --------------------------------------------------------------------------- headers


@dataclass(frozen=True)
class _Header:
    """What the raw bytes declare – read without Pillow, before anything is allocated."""

    format: str
    width: int
    height: int
    animated: bool = False
    #: JPEG only: one SOF (baseline/extended sequential) and a first scan with every component.
    single_scan: bool = False

    @property
    def pixels(self) -> int:
        return self.width * self.height


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Chunk budgets of a PNG (up to IEND). Pillow handles every chunk in Python and keeps
#: unknown ones in memory (40 MB of empty chunks: 7 s and +400 MB); photos have a handful
#: of other chunks and at most a few thousand IDAT chunks (libpng writes 8 KB each).
_MAX_PNG_CHUNKS = 2048
_MAX_PNG_IDAT_CHUNKS = 65536
#: JPEG start-of-frame markers (C4 DHT, C8 JPG and CC DAC are not frames).
_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
#: Baseline and extended sequential Huffman JPEG – what cameras write.
_JPEG_SEQUENTIAL = frozenset({0xC0, 0xC1})
#: Marker codes with a length field in Pillow 13's JpegImagePlugin.MARKER table (handler not
#: None): SOF/DHT/DAC, SOS/DQT/DNL/DRI/DHP/EXP, APP0–15 and COM. Walked as length-prefixed
#: segments, exactly as Pillow reads them. (A test pins both sets against Pillow's table.)
_JPEG_SEGMENT = frozenset(range(0xC0, 0xC8)) | frozenset(range(0xC9, 0xD0)) | frozenset(range(0xDA, 0xF0)) | {0xFE}
#: Marker codes WITHOUT a length field (handler None): JPG (C8), RSTn/SOI/EOI (D0–D9) and the
#: JPGn extension markers (F0–FD). Pillow walks past each with no payload, one byte at a time,
#: so our walk must do the same – treating them as length-prefixed would let an upload hide a
#: flood inside a fake "segment" that Pillow then parses in full (crafted 40 MB files: 10–20 s,
#: up to +1.2 GB). The escaped pair 0xFF00 is handled with them (no length either).
_JPEG_STANDALONE = frozenset({0xC8}) | frozenset(range(0xD0, 0xDA)) | frozenset(range(0xF0, 0xFE))
#: Budgets of a JPEG before its first scan. Pillow parses these markers in Python – padding
#: and junk byte by byte – and keeps every APPn/COM segment in a list (40 MB of empty
#: segments: 11–18 s and +0.7–1.2 GB, 40 MB of fill bytes: 26 s). Camera photos have about
#: ten segments there and neither padding nor junk.
_MAX_JPEG_SEGMENTS = 4096
#: Fill bytes (0xFF), escaped 0xFF00 pairs and standalone markers (RSTn, SOI, EOI, JPG/JPGn).
_MAX_JPEG_PADDING = 4096
#: Bytes between segments (Pillow and libjpeg skip them up to the next 0xFF).
_MAX_JPEG_JUNK = 64 * 1024
#: Total header bytes Pillow parses before the first scan. Real phone photos keep it under
#: 10 KiB (an embedded ICC profile or thumbnail stays well under 1 MiB); far above that the
#: header is only a flood of valid-looking segments whose payloads (Exif concatenation, long
#: SOF component lists) Pillow walks byte by byte (40 MB: up to 28 s and +1 GB). Refused here
#: before Pillow parses anything. The entropy-coded scan is never walked (we stop at the SOS).
_MAX_JPEG_HEADER_BYTES = 8 * 1024 * 1024
#: RIFF chunks of a WebP (libwebp keeps a record of each one) – photos have at most six.
_MAX_WEBP_CHUNKS = 256


def _png_header(data: bytes) -> _Header:
    """IHDR size and APNG chunks (acTL/fcTL/fdAT) before the image data; every chunk
    up to IEND counts against the chunk budgets."""
    width = height = 0
    animated = image_data = False
    chunks = idat = 0
    pos, end = len(_PNG_SIGNATURE), len(data)
    while pos + 8 <= end:
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        if kind == b"IDAT":
            image_data = True
            idat += 1
        else:
            chunks += 1
        if chunks > _MAX_PNG_CHUNKS or idat > _MAX_PNG_IDAT_CHUNKS:
            raise DecodeError("not a readable image (too many PNG chunks)")
        if kind == b"IEND":
            break
        if not image_data:  # what Pillow reads before decoding
            if kind == b"IHDR" and length >= 8 and pos + 16 <= end:
                # several IHDR chunks: keep the largest claim (Pillow uses the last one)
                width = max(width, int.from_bytes(data[pos + 8 : pos + 12], "big"))
                height = max(height, int.from_bytes(data[pos + 12 : pos + 16], "big"))
            elif kind in (b"acTL", b"fcTL", b"fdAT"):
                animated = True
        pos += 12 + length
    return _Header("PNG", width, height, animated=animated)


def _check_webp_chunks(data: bytes) -> None:
    """Raise for more RIFF chunks than a photo has (within the RIFF size, as libwebp reads it)."""
    end = min(len(data), 8 + int.from_bytes(data[4:8], "little"))
    pos, count = 12, 0
    while pos + 8 <= end:
        count += 1
        if count > _MAX_WEBP_CHUNKS:
            raise DecodeError("not a readable image (too many WebP chunks)")
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        pos += 8 + size + (size & 1)


def _webp_header(data: bytes) -> _Header | None:
    """Canvas size and animation flag of a WebP (simple VP8/VP8L or extended VP8X)."""
    if len(data) < 30:
        return None
    fourcc = data[12:16]
    if fourcc == b"VP8X":
        animated = bool(data[20] & 0x02)
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return _Header("WEBP", width, height, animated=animated)
    if fourcc == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return _Header("WEBP", width, height)
    if fourcc == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return _Header("WEBP", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


def _jpeg_header(data: bytes) -> _Header | None:
    """Frame size of a JPEG (MPO: of its first image) from the markers before the first scan.

    The markers are classified exactly as Pillow 13's JpegImagePlugin.MARKER table
    classifies them (length-prefixed segment vs standalone marker), and padding and
    junk are skipped the way its parser skips them, within the segment/padding/junk
    and total-header budgets – a flood is refused here, before Pillow parses it.
    `single_scan` only for exactly one sequential frame whose first scan holds every
    component and no junk between the segments – progressive or multi-scan files make
    libjpeg buffer the coefficients of the FULL image even when it decodes reduced.
    """
    end = len(data)
    pos = 2
    frame: tuple[int, int, int, int] | None = None  # marker, width, height, components
    frames = segments = padding = junk = 0
    while pos + 4 <= end:
        if pos - 2 > _MAX_JPEG_HEADER_BYTES:
            raise DecodeError("not a readable image (JPEG header too large)")
        if data[pos] != 0xFF:  # bytes between segments: skipped up to the next 0xFF
            following = data.find(b"\xff", pos)
            junk += (end if following < 0 else following) - pos
            if junk > _MAX_JPEG_JUNK:
                raise DecodeError("not a readable image (junk between the JPEG segments)")
            if following < 0:
                break
            pos = following
            continue
        marker = data[pos + 1]
        if marker == 0xFF:
            # fill byte / 0xFFFF padded marker: Pillow re-reads from the second 0xFF (1 byte)
            padding += 1
            if padding > _MAX_JPEG_PADDING:
                raise DecodeError("not a readable image (padding between the JPEG segments)")
            pos += 1
            continue
        if marker == 0x00 or marker in _JPEG_STANDALONE:
            # escaped 0xFF00, RSTn, SOI, EOI and the JPG/JPGn extension markers: no length field
            padding += 1
            if padding > _MAX_JPEG_PADDING:
                raise DecodeError("not a readable image (padding between the JPEG segments)")
            pos += 2
            continue
        if marker not in _JPEG_SEGMENT:
            # a reserved marker code (0x01..0xBF): Pillow raises "no marker found" – unreadable
            raise DecodeError("not a readable image (reserved JPEG marker)")
        segments += 1
        if segments > _MAX_JPEG_SEGMENTS:
            raise DecodeError("not a readable image (too many JPEG segments)")
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if pos + 2 + length > end:  # Pillow and libjpeg fail on it as well
            raise DecodeError("not a readable image (truncated JPEG segment)")
        if marker in _JPEG_SOF and length >= 8:
            frames += 1
            height = int.from_bytes(data[pos + 5 : pos + 7], "big")
            width = int.from_bytes(data[pos + 7 : pos + 9], "big")
            if frame is None or width * height > frame[1] * frame[2]:
                frame = (marker, width, height, data[pos + 9])
        elif marker == 0xDA:  # start of scan
            if frame is None:
                return None
            single = (
                frames == 1 and frame[0] in _JPEG_SEQUENTIAL and length >= 3 and data[pos + 4] == frame[3] and not junk
            )
            return _Header("JPEG", frame[1], frame[2], single_scan=single)
        pos += 2 + max(length, 2)  # a length below 2: Pillow and libjpeg go on after the length field
    return None if frame is None else _Header("JPEG", frame[1], frame[2])


def _header(data: bytes) -> _Header | None:
    """Declared size of PNG/WebP/JPEG uploads; None for HEIF and anything unrecognised.

    HEIF is opened by pillow-heif, which only parses the container (the size check
    follows before anything is decoded); a JPEG without a readable frame header is
    left to Pillow, whose JPEG open reads headers only.
    """
    if data.startswith(_PNG_SIGNATURE):
        return _png_header(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        header = _webp_header(data)
        if header is None:  # libwebp would allocate its canvas before decoding: unknown layout = unreadable
            raise DecodeError("not a readable image (unknown WebP layout)")
        _check_webp_chunks(data)
        return header
    if data[:3] == b"\xff\xd8\xff":
        return _jpeg_header(data)
    return None


# --------------------------------------------------------------------------- size limits


def _reduction(width: int, height: int) -> int:
    """Smallest DCT scaling (2, 4 or 8) that brings a JPEG within MAX_PIXELS (0: none does)."""
    for scale in (2, 4, 8):
        if -(-width // scale) * -(-height // scale) <= MAX_PIXELS:
            return scale
    return 0


def _check_size(width: int, height: int, what: str) -> None:
    """Raise for a size that is never decoded (not counting a JPEG's reduced decode)."""
    if width <= 0 or height <= 0:
        raise DecodeError(f"not a readable image ({what} declares {width}x{height})")
    if width * height > MAX_PIXELS:
        raise ImageTooLargeError(f"image too large ({what} {width}x{height})")
    if min(width, height) < MIN_SIDE:
        raise ImageTooSmallError(f"image too small ({what} {width}x{height})")


def _open(data: bytes, header: _Header | None) -> tuple[Image.Image, int]:
    """Open the photo (no pixels decoded yet) and set up a reduced JPEG decode if needed.

    Returns the image and its reduction (1 = full size).
    """
    reduction = 1
    if header is not None:
        if header.animated:
            raise DecodeError(f"animated {header.format} is not a photo")
        if header.format == "JPEG" and header.pixels > MAX_PIXELS:
            reduction = _reduction(header.width, header.height)
            if not (header.single_scan and header.pixels <= MAX_REDUCED_JPEG_PIXELS and reduction):
                kind = "single-scan" if header.single_scan else "progressive/multi-scan"
                raise ImageTooLargeError(f"image too large ({kind} JPEG {header.width}x{header.height})")
        else:
            _check_size(header.width, header.height, header.format)
    try:
        if reduction > 1:
            # Pillow's open() refuses more than twice its pixel limit before a reduced decode
            # could be set up; this file was checked above and is decoded reduced (draft below)
            image = JpegImagePlugin.jpeg_factory(io.BytesIO(data))
        else:
            image = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
    except Image.DecompressionBombError as error:
        raise ImageTooLargeError("image too large") from error
    except Exception as error:
        raise DecodeError("not a readable image") from error

    fmt = image.format or "unknown"
    if getattr(image, "n_frames", 1) > 1 and fmt not in MULTI_IMAGE_FORMATS:
        raise DecodeError(f"animated {fmt} is not a photo")
    if reduction > 1:
        assert header is not None
        wanted = (max(1, header.width // reduction), max(1, header.height // reduction))
        # libjpeg decodes at 1/reduction (orientation and colour profile are unaffected)
        if image.size != (header.width, header.height) or image.draft(image.mode, wanted) is None:
            raise ImageTooLargeError(f"image too large (JPEG {image.width}x{image.height})")
    _check_size(image.width, image.height, fmt)
    return image, reduction


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


_ORIENTATION_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


def _transpose(image: Image.Image, orientation: int) -> Image.Image:
    """Upright pixels for an EXIF orientation (what exif_transpose does, without the Exif)."""
    method = _ORIENTATION_TRANSPOSE.get(orientation)
    return image.transpose(method) if method is not None else image


def decode_image(data: bytes) -> DecodedImage:
    """Raises DecodeError (subclasses ImageTooLargeError / ImageTooSmallError for the size limits)."""
    if not data:
        raise DecodeError("empty upload")
    image, reduction = _open(data, _header(data))
    try:
        image.load()
    except Image.DecompressionBombError as error:
        raise ImageTooLargeError("image too large") from error
    except Exception as error:
        raise DecodeError("not a readable image") from error

    fmt = image.format
    orientation = 1
    try:
        orientation = int(image.getexif().get(0x0112, 1) or 1)
    except Exception:
        orientation = 1
    icc = image.info.get("icc_profile")

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        # exif_transpose re-serialises the whole Exif block and fails on tags
        # stored with an unexpected type – rotate by the orientation alone
        image = _transpose(image, orientation)
    if icc and "icc_profile" not in image.info:
        image.info["icc_profile"] = icc

    if image.mode in ("RGBA", "LA", "P", "PA"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        background.alpha_composite(image)
        image = background.convert("RGB")
        if icc:
            # the flattened copy is a new image: keep the colour space of its pixels (e.g. Display P3)
            image.info["icc_profile"] = icc
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
    rgb = np.array(image, dtype=np.uint8)
    if min(rgb.shape[:2]) < MIN_SIDE:  # HEIF may report another size than it decodes
        raise ImageTooSmallError(f"image too small ({rgb.shape[1]}x{rgb.shape[0]})")
    return DecodedImage(
        rgb=rgb, format=fmt, orientation=orientation, converted_from_profile=converted, reduction=reduction
    )
