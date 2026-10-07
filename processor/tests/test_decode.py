import io

import numpy as np
import pytest
from PIL import Image

from app.pipeline.decode import DecodeError, decode_image


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
