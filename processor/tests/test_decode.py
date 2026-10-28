import io
import struct
import time
import zlib

import numpy as np
import PIL
import pytest
from PIL import Image

from app.pipeline import decode as decode_module
from app.pipeline.decode import (
    DecodeError,
    ImageTooLargeError,
    ImageTooSmallError,
    decode_image,
)


def _jpeg_with_orientation(rgb: np.ndarray, orientation: int) -> bytes:
    image = Image.fromarray(rgb)
    exif = image.getexif()
    exif[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95, exif=exif.tobytes())
    return buffer.getvalue()


def test_exif_orientation_is_applied():
    rgb = np.zeros((100, 200, 3), np.uint8)
    rgb[:, :100] = (255, 0, 0)  # left half red
    decoded = decode_image(_jpeg_with_orientation(rgb, 6))  # 90° clockwise
    assert decoded.orientation == 6
    assert (decoded.height, decoded.width) == (200, 100)  # portrait now
    assert decoded.rgb[10, 50, 0] > 200  # red half is on top after rotation


def test_orientation_is_applied_when_other_exif_tags_have_unexpected_types():
    from PIL import TiffImagePlugin, TiffTags

    rgb = np.zeros((100, 200, 3), np.uint8)
    rgb[:, :100] = (255, 0, 0)  # left half red
    ifd = TiffImagePlugin.ImageFileDirectory_v2(prefix=b"II")
    ifd[0x0112] = 6
    ifd[0x0100] = TiffImagePlugin.IFDRational(5, 2)  # ImageWidth stored as RATIONAL
    ifd.tagtype[0x0100] = TiffTags.RATIONAL
    exif = b"Exif\x00\x00II\x2a\x00\x08\x00\x00\x00" + ifd.tobytes(8)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, "JPEG", quality=95, exif=exif)
    decoded = decode_image(buffer.getvalue())  # exif_transpose itself raises struct.error here
    assert decoded.orientation == 6
    assert (decoded.height, decoded.width) == (200, 100)
    assert decoded.rgb[10, 50, 0] > 200  # same pixels as with a well-formed Exif block


def test_png_with_alpha_becomes_rgb():
    rgba = np.zeros((80, 80, 4), np.uint8)
    rgba[..., 2] = 255
    rgba[..., 3] = 128
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
    decoded = decode_image(buffer.getvalue())
    assert decoded.rgb.shape == (80, 80, 3)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\xff\xd8\xff\xe0broken"])
def test_garbage_is_rejected(data):
    with pytest.raises(DecodeError):
        decode_image(data)


def _encoded(fmt: str, size=(96, 72), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, 0).save(buffer, fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "GIF"])
def test_only_photo_formats_are_decoded(fmt):
    with pytest.raises(DecodeError):
        decode_image(_encoded(fmt))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_photo_formats_are_decoded(fmt):
    assert decode_image(_encoded(fmt)).rgb.shape == (72, 96, 3)


def test_images_above_the_pixel_limit_are_rejected():
    # 100 MP – Pillow alone would only warn (it raises above twice its limit)
    with pytest.raises(DecodeError, match="too large"):
        decode_image(_encoded("PNG", (10_000, 10_000), "L"))


# --------------------------------------------------------------------------- hardening (SEC-1, PROC-2, PROC-5)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _crafted_apng(width: int, height: int, dispose: int = 1) -> bytes:
    """~130 bytes claiming a huge canvas: Pillow 12.3 allocated it (dispose 1) before its bomb check."""
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    actl = _chunk(b"acTL", struct.pack(">II", 1, 0))
    fctl = _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 100, dispose, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00" * 5))
    return b"\x89PNG\r\n\x1a\n" + ihdr + actl + fctl + idat + _chunk(b"IEND", b"")


def _crafted_png(width: int, height: int) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr + _chunk(b"IDAT", zlib.compress(b"\x00" * 7)) + _chunk(b"IEND", b"")


def _crafted_webp(width: int, height: int, *, animated: bool = False) -> bytes:
    vp8x = bytes([0x02 if animated else 0]) + bytes(3) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(vp8x)) + vp8x
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.fixture
def pillow_must_not_parse(monkeypatch):
    """Fails the test if Pillow is asked to open the file at all."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("Pillow was asked to parse a file that must be refused before")

    monkeypatch.setattr(decode_module.Image, "open", refuse)
    monkeypatch.setattr(decode_module.JpegImagePlugin, "jpeg_factory", refuse)


def test_the_security_fixes_of_pillow_13_are_installed():
    # Pillow 12.3: APNG canvas allocated before the bomb check, quadratic eXIf parsing (+38 more)
    assert int(PIL.__version__.split(".")[0]) >= 13, f"Pillow {PIL.__version__} – run pip install -r requirements.txt"


@pytest.mark.parametrize("dispose", [0, 1])
def test_crafted_apng_bombs_are_refused_before_pillow_parses_them(pillow_must_not_parse, dispose):
    data = _crafted_apng(20_000, 20_000, dispose)
    assert len(data) < 200
    with pytest.raises(DecodeError, match="animated"):
        decode_image(data)


def test_animated_png_and_webp_are_refused():
    frames = [Image.new("RGB", (96, 96), c) for c in ((255, 0, 0), (0, 0, 255))]
    for fmt in ("PNG", "WEBP"):
        buffer = io.BytesIO()
        frames[0].save(buffer, fmt, save_all=True, append_images=frames[1:], duration=100)
        with pytest.raises(DecodeError, match="animated") as info:
            decode_image(buffer.getvalue())
        assert info.value.code == "decode"


@pytest.mark.parametrize(
    "data",
    [
        _crafted_png(100_000, 100_000),  # 10 GP
        _crafted_png(10_000, 9_000),  # 90 MP, just above the limit
        _crafted_webp(16_384, 16_384),
        _crafted_webp(20_000, 20_000, animated=False),
    ],
)
def test_declared_sizes_above_the_limit_are_refused_before_decoding(pillow_must_not_parse, data):
    with pytest.raises(ImageTooLargeError) as info:
        decode_image(data)
    assert info.value.code == "image_too_large"


def test_an_animated_webp_header_is_refused_before_decoding(pillow_must_not_parse):
    with pytest.raises(DecodeError, match="animated"):
        decode_image(_crafted_webp(2000, 1500, animated=True))


def test_png_exif_is_parsed_in_linear_time():
    # 2 MB of repeated "Exif\0\0" headers: 22 s with Pillow 12.3 (quadratic), ~0.02 s with 13
    raw = b"".join(b"\x00" + b"\x80" * (64 * 3) for _ in range(64))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + _chunk(b"eXIf", b"Exif\x00\x00" * (2 * 1024 * 1024 // 6))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    start = time.perf_counter()
    assert decode_image(data).rgb.shape == (64, 64, 3)
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize("size", [(63, 400), (400, 40), (8, 8)])
def test_tiny_images_are_reported_as_too_low_resolution(size):
    with pytest.raises(ImageTooSmallError) as info:
        decode_image(_encoded("PNG", size))
    assert info.value.code == "source_resolution_too_low"


def test_the_message_limit_matches_the_code():
    # ERROR_MESSAGES["image_too_large"] (manager.py) and the app say "über 80 Megapixel"
    assert decode_module.MAX_PIXELS == 80_000_000


def _marked_jpeg(size, *, mode="L", orientation=1, progressive=False, icc=None) -> bytes:
    """Flat grey JPEG with a white block in the top-left corner (cheap even at 81 MP)."""
    image = Image.new(mode, size, 90 if mode == "L" else (200, 30, 30))
    block = Image.new(mode, (size[0] // 4, size[1] // 4), 255 if mode == "L" else (255, 255, 255))
    image.paste(block, (0, 0))
    exif = image.getexif()
    if orientation != 1:
        exif[0x0112] = orientation
    buffer = io.BytesIO()
    options = {"quality": 90, "progressive": progressive, "exif": exif.tobytes()}
    if icc:
        options["icc_profile"] = icc
    image.save(buffer, "JPEG", **options)
    return buffer.getvalue()


def test_a_108_megapixel_style_jpeg_is_decoded_reduced_not_refused():
    # 10400 × 7800 = 81.1 MP (above the limit) – decoded at 1/2 by libjpeg's DCT scaling
    decoded = decode_image(_marked_jpeg((10_400, 7_800), orientation=6))
    assert decoded.reduction == 2
    assert (decoded.width, decoded.height) == (3_900, 5_200)  # portrait after the EXIF rotation
    assert decoded.orientation == 6
    # the white corner block (top-left in the file) is top-RIGHT after a 90° clockwise rotation
    assert decoded.rgb[100, -100].min() > 240 and decoded.rgb[100, 100].max() < 120
    assert decoded.width * decoded.height <= decode_module.MAX_PIXELS


@pytest.fixture
def small_limits(monkeypatch):
    """Scale the pixel limits down so that the size rules can be tested with small files."""
    monkeypatch.setattr(decode_module, "MAX_PIXELS", 1_000_000)
    monkeypatch.setattr(decode_module, "MAX_REDUCED_JPEG_PIXELS", 4_000_000)


def test_reduced_decode_keeps_orientation_and_colour_profile(small_limits):
    icc = wide_gamut_icc()
    data = _marked_jpeg((1600, 1200), mode="RGB", orientation=8, icc=icc)  # 1.92 MP > 1 MP
    decoded = decode_image(data)
    assert decoded.reduction == 2 and (decoded.width, decoded.height) == (600, 800)
    assert decoded.converted_from_profile == "Test Display P3"
    # the white block (top-left in the file) is bottom-left after a 90° counter-clockwise rotation
    assert decoded.rgb[-50, 50].min() > 240
    paint = decoded.rgb[100, 400].astype(int)
    assert paint[0] > 210 and paint[1] < 15  # (200, 30, 30) in the wide gamut → more saturated in sRGB


@pytest.mark.parametrize("progressive", [False, True])
def test_only_single_scan_jpegs_are_decoded_reduced(small_limits, progressive):
    data = _marked_jpeg((1600, 1200), progressive=progressive)
    if progressive:  # libjpeg would buffer the coefficients of the full image
        with pytest.raises(ImageTooLargeError):
            decode_image(data)
    else:
        assert decode_image(data).reduction == 2


def test_jpegs_above_the_reduced_limit_are_refused(small_limits, pillow_must_not_parse):
    with pytest.raises(ImageTooLargeError):
        decode_image(_marked_jpeg((2400, 1800)))  # 4.3 MP > 4 MP


def test_mpo_uses_its_first_image():
    first = Image.new("RGB", (160, 120), (220, 20, 20))
    second = Image.new("RGB", (160, 120), (20, 20, 220))
    buffer = io.BytesIO()
    first.save(buffer, "MPO", save_all=True, append_images=[second])
    decoded = decode_image(buffer.getvalue())
    assert decoded.format == "MPO"
    assert decoded.rgb[60, 80, 0] > 180 and decoded.rgb[60, 80, 2] < 60


def test_heif_uses_its_primary_image():
    pillow_heif = pytest.importorskip("pillow_heif")
    heif = pillow_heif.from_pillow(Image.new("RGB", (128, 96), (220, 20, 20)))
    heif.add_from_pillow(Image.new("RGB", (160, 120), (20, 20, 220)))
    buffer = io.BytesIO()
    try:
        heif.save(buffer, quality=90, primary_index=1)
    except (OSError, RuntimeError, ValueError) as error:  # pragma: no cover - wheel without an HEVC encoder
        pytest.skip(f"no HEIF encoder: {error}")
    decoded = decode_image(buffer.getvalue())
    assert decoded.format == "HEIF"
    assert (decoded.width, decoded.height) == (160, 120)
    assert decoded.rgb[60, 80, 2] > 180 and decoded.rgb[60, 80, 0] < 60


def wide_gamut_icc(description: str = "Test Display P3", gamma: float = 2.2) -> bytes:
    """Minimal ICC v2 display profile with the Display P3 primaries (built here – no third-party profile)."""

    def s15(value: float) -> bytes:
        return struct.pack(">i", round(value * 65536))

    def xyz(values) -> bytes:
        return b"XYZ " + bytes(4) + b"".join(s15(v) for v in values)

    white = np.array([0.3127 / 0.3290, 1.0, (1 - 0.3127 - 0.3290) / 0.3290])
    primaries = np.array([[x / y, 1.0, (1 - x - y) / y] for x, y in ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))]).T
    rgb_to_xyz = primaries * np.linalg.solve(primaries, white)
    bradford = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    d50 = np.array([0.9642, 1.0, 0.8249])
    to_d50 = np.linalg.inv(bradford) @ np.diag((bradford @ d50) / (bradford @ white)) @ bradford @ rgb_to_xyz
    text = description.encode("ascii") + b"\0"
    curve = b"curv" + bytes(4) + struct.pack(">IH", 1, round(gamma * 256)) + bytes(2)
    tags = [
        (b"desc", b"desc" + bytes(4) + struct.pack(">I", len(text)) + text + bytes(78)),
        (b"wtpt", xyz(d50)),
        (b"rXYZ", xyz(to_d50[:, 0])),
        (b"gXYZ", xyz(to_d50[:, 1])),
        (b"bXYZ", xyz(to_d50[:, 2])),
        (b"rTRC", curve),
        (b"gTRC", curve),
        (b"bTRC", curve),
        (b"cprt", b"text" + bytes(4) + b"CC0 test profile\0"),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, body = b"", b""
    for signature, data in tags:
        body += bytes(-(offset + len(body)) % 4)
        table += signature + struct.pack(">II", offset + len(body), len(data))
        body += data
    body += bytes(-len(body) % 4)
    header = (
        struct.pack(">I", offset + len(body)) + b"lcms" + bytes([2, 0x10, 0, 0]) + b"mntrRGB XYZ "
        + struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acsp" + bytes(24) + struct.pack(">I", 0)
        + s15(0.9642) + s15(1.0) + s15(0.8249) + bytes(48)
    )  # fmt: skip
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + table + body


def _with_profile(image: Image.Image, fmt: str, icc: bytes, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, icc_profile=icc, **options)
    return buffer.getvalue()


def test_colour_profile_survives_alpha_and_palette_flattening():
    icc = wide_gamut_icc()
    rgb = Image.new("RGB", (100, 100), (200, 30, 30))
    rgba = rgb.convert("RGBA")
    rgba.putpixel((0, 0), (0, 0, 0, 0))  # one transparent pixel: libwebp keeps the alpha channel
    variants = {
        "RGB PNG": _with_profile(rgb, "PNG", icc),
        "RGBA PNG": _with_profile(rgba, "PNG", icc),
        "palette PNG": _with_profile(rgb.convert("P", palette=Image.Palette.ADAPTIVE), "PNG", icc),
        "RGBA WebP": _with_profile(rgba, "WEBP", icc, lossless=True),
    }
    decoded = {name: decode_image(data) for name, data in variants.items()}
    reference = decoded["RGB PNG"]
    assert reference.converted_from_profile == "Test Display P3"
    assert tuple(reference.rgb[50, 50]) != (200, 30, 30)  # converted, not read as sRGB
    for name, result in decoded.items():
        assert result.converted_from_profile == "Test Display P3", name
        assert tuple(result.rgb[50, 50]) == tuple(reference.rgb[50, 50]), name
    assert tuple(decoded["RGBA PNG"].rgb[0, 0]) == (255, 255, 255)  # transparent → white


def test_real_photos_are_decoded_unchanged_by_the_header_checks():
    # every format that passes the header checks still decodes exactly as Pillow reads it
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    for fmt in ("PNG", "WEBP"):
        buffer = io.BytesIO()
        Image.fromarray(rgb).save(buffer, fmt, **({"lossless": True} if fmt == "WEBP" else {}))
        assert np.array_equal(decode_image(buffer.getvalue()).rgb, rgb), fmt


# --------------------------------------------------------------------------- floods of segments, padding, junk and chunks


def _noise(fmt: str, size=(96, 72), **options) -> bytes:
    rng = np.random.default_rng(11)
    buffer = io.BytesIO()
    Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(buffer, fmt, **options)
    return buffer.getvalue()


JPEG = _noise("JPEG", quality=90)


def _jpeg_after_soi(insert: bytes) -> bytes:
    """The JPEG with `insert` right after its SOI marker (before APP0, DQT, SOF, …)."""
    return JPEG[:2] + insert + JPEG[2:]


def _jpeg_after_app0(insert: bytes) -> bytes:
    end = 4 + int.from_bytes(JPEG[4:6], "big")
    return JPEG[:end] + insert + JPEG[end:]


@pytest.mark.parametrize(
    "data, reason",
    [
        # Pillow parses these in Python and keeps every segment (40 MB: 11-18 s and +0.7-1.2 GB) …
        (_jpeg_after_soi(b"\xff\xfe\x00\x02" * 4097), "too many JPEG segments"),
        (_jpeg_after_soi(b"\xff\xe1\x00\x03\x00" * 4097), "too many JPEG segments"),
        # … and walks padding and junk byte by byte (40 MB of fill bytes: 26 s)
        (_jpeg_after_soi(b"\xff" * 4097), "padding"),
        (_jpeg_after_soi(b"\xff\xd0" * 4097), "padding"),  # restart markers
        (_jpeg_after_soi(b"\xff\x00" * 4097), "padding"),
        (_jpeg_after_app0(b"\x00" * (64 * 1024 + 1)), "junk"),
        (JPEG[:2] + b"\xff\xe1\xff\xf0" + JPEG[2:200], "truncated"),  # Pillow fails on it as well
    ],
)
def test_jpeg_floods_before_the_first_scan_are_refused_before_pillow_parses_them(pillow_must_not_parse, data, reason):
    with pytest.raises(DecodeError, match=reason) as info:
        decode_image(data)
    assert info.value.code == "decode"


@pytest.mark.parametrize(
    "data",
    [
        _jpeg_after_soi(b"\xff\xfe\x00\x02" * 4000),  # many comments
        _jpeg_after_soi(b"\xff" * 4000),  # fill bytes
        _jpeg_after_soi(b"\xff\xd0" * 100),
        _jpeg_after_app0(b"\x00" * 60_000),  # junk that Pillow and libjpeg skip
        _jpeg_after_app0(b"\xff\xe1\x00\x01"),  # a length below 2: both go on after the length field
    ],
)
def test_jpegs_within_the_budgets_decode_exactly_as_before(data):
    assert np.array_equal(decode_image(data).rgb, decode_image(JPEG).rgb)


def test_restart_markers_and_stuffed_bytes_in_the_scan_do_not_count():
    # the budgets cover the markers before the first scan – entropy-coded data is never walked
    data = _noise("JPEG", (1280, 1024), quality=100, restart_marker_blocks=1)
    scan = data[data.index(b"\xff\xda") :]
    assert sum(scan.count(bytes([0xFF, 0xD0 + n])) for n in range(8)) > 4096
    assert scan.count(b"\xff\x00") > 4096
    assert decode_image(data).rgb.shape == (1024, 1280, 3)


def test_the_jpeg_marker_classification_matches_pillows_table():
    """The header walk must split markers exactly as Pillow 13's JpegImagePlugin.MARKER does:
    a length field (handler not None) or none (handler None). Otherwise an upload can hide a
    flood inside a marker that Pillow walks past but our walk skips as one big "segment"."""
    from PIL import JpegImagePlugin

    length_prefixed = {code & 0xFF for code, (_n, _d, handler) in JpegImagePlugin.MARKER.items() if handler is not None}
    standalone = {code & 0xFF for code, (_n, _d, handler) in JpegImagePlugin.MARKER.items() if handler is None}
    assert decode_module._JPEG_SEGMENT == length_prefixed
    assert decode_module._JPEG_STANDALONE == standalone
    # 0x00 (escaped 0xFF00) and 0xFF (fill) are handled on their own; every other low byte is
    # either a segment, a standalone marker, or a reserved code Pillow raises on – none overlap.
    assert not (decode_module._JPEG_SEGMENT & decode_module._JPEG_STANDALONE)
    reserved = set(range(0x01, 0x100)) - {0xFF} - decode_module._JPEG_SEGMENT - decode_module._JPEG_STANDALONE
    assert reserved == set(range(0x01, 0xC0))  # 0x01..0xBF: "no marker found" in Pillow


def _hidden_flood(marker: int, inner: bytes, count: int) -> bytes:
    """A JPG/JPGn marker (no length field in Pillow) carrying a *valid* length field that spans a
    hidden flood of `count` × `inner`. A walk that (wrongly) reads a length here skips the whole
    flood as one segment; Pillow steps over the marker and then parses the flood."""
    flood = inner * count
    return bytes([0xFF, marker]) + struct.pack(">H", len(flood) + 2) + flood


@pytest.mark.parametrize(
    "marker, inner, reason",
    [
        (0xF0, b"\xff\xfe\x00\x02", "too many JPEG segments"),  # COM segments hidden in a JPG0 marker
        (0xC8, b"\xff\xe1\x00\x03\x00", "too many JPEG segments"),  # APP1 segments hidden in a JPG marker
        (0xFD, b"\xff", "padding"),  # fill bytes hidden in a JPG13 marker
    ],
)
def test_jpeg_floods_hidden_in_extension_markers_are_refused(pillow_must_not_parse, marker, inner, reason):
    # the walk must treat a JPG/JPGn marker as standalone (as Pillow does) and keep counting the
    # flood behind it, not skip it as one length-prefixed segment and hand the flood to Pillow
    with pytest.raises(DecodeError, match=reason) as info:
        decode_image(_jpeg_after_soi(_hidden_flood(marker, inner, 5000)))
    assert info.value.code == "decode"


def test_reserved_jpeg_markers_are_refused(pillow_must_not_parse):
    # 0xFF02 is a reserved marker code Pillow raises "no marker found" on – unreadable
    with pytest.raises(DecodeError, match="reserved JPEG marker"):
        decode_image(_jpeg_after_soi(b"\xff\x02\x00\x04\x00\x00"))


def test_a_jpeg_header_larger_than_the_budget_is_refused(pillow_must_not_parse):
    # valid-looking segments whose payloads Pillow would parse byte by byte (Exif concatenation,
    # long SOF component lists): refused once the header passes the byte budget, well under the
    # segment count. Real phone photos keep the header under 10 KiB.
    big_app1 = b"\xff\xe1\xff\xff" + b"Exif\x00\x00" + b"\x00" * 65527
    count = decode_module._MAX_JPEG_HEADER_BYTES // len(big_app1) + 2
    data = JPEG[:2] + big_app1 * count + JPEG[2:]
    assert count < decode_module._MAX_JPEG_SEGMENTS  # not refused by the segment count
    with pytest.raises(DecodeError, match="JPEG header too large"):
        decode_image(data)


def _png_chunks(png: bytes) -> list[bytes]:
    chunks, pos = [], 8
    while pos < len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        chunks.append(png[pos : pos + 12 + length])
        pos += 12 + length
    return chunks


def _png_with(*, before_idat: bytes = b"", after_idat: bytes = b"", idat_parts: int = 1) -> bytes:
    """A noise PNG with extra chunks and its image data split into `idat_parts` IDAT chunks."""
    chunks = _png_chunks(_noise("PNG"))
    image_data = b"".join(c[8:-4] for c in chunks if c[4:8] == b"IDAT")
    step = -(-len(image_data) // idat_parts)
    idats = b"".join(_chunk(b"IDAT", image_data[i : i + step]) for i in range(0, len(image_data), step))
    ihdr = next(c for c in chunks if c[4:8] == b"IHDR")
    return b"\x89PNG\r\n\x1a\n" + ihdr + before_idat + idats + after_idat + _chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "data",
    [
        # Pillow keeps unknown chunks in memory and reads the ones after IDAT one by one (40 MB: 7 s, +400 MB)
        _png_with(after_idat=_chunk(b"prIv", b"") * 2047),
        _png_with(after_idat=_chunk(b"tEXt", b"k\x00v") * 2047),
        _png_with(before_idat=_chunk(b"IDAT", b"") * 65_536),  # empty image data chunks
    ],
)
def test_png_chunk_floods_are_refused_before_pillow_parses_them(pillow_must_not_parse, data):
    with pytest.raises(DecodeError, match="too many PNG chunks"):
        decode_image(data)


def test_pngs_within_the_chunk_budgets_decode_exactly_as_before():
    reference = decode_image(_noise("PNG")).rgb
    for data in (
        _png_with(after_idat=_chunk(b"tEXt", b"k\x00v") * 2045),  # IHDR + 2045 + IEND = 2047 other chunks
        _png_with(idat_parts=6000),  # tiny IDAT chunks (libpng writes 8 KB ones)
    ):
        assert np.array_equal(decode_image(data).rgb, reference)


def _webp_with_chunks(count: int) -> bytes:
    """A VP8X WebP (EXIF forces the extended layout) with `count` unknown chunks after VP8X."""
    exif = Image.Exif()
    exif[0x0131] = "test"  # Software
    data = _noise("WEBP", lossless=True, exif=exif.tobytes())
    assert data[12:16] == b"VP8X"
    body = b"WEBP" + data[12:30] + (b"XYZW" + bytes(4)) * count + data[30:]
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_webp_chunk_floods_are_refused_before_pillow_parses_them(pillow_must_not_parse):
    with pytest.raises(DecodeError, match="too many WebP chunks"):
        decode_image(_webp_with_chunks(256))


def test_webps_with_a_few_extra_chunks_decode_exactly_as_before():
    assert np.array_equal(decode_image(_webp_with_chunks(16)).rgb, decode_image(_webp_with_chunks(0)).rgb)
