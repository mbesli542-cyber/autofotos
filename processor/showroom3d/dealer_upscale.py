"""Step 2 of the dealer backdrop: upscale the cleaned plate ONCE to the preset's output size.

The cleaned dealer image (``dealer_clean.py``, 1448 × 1086) is enlarged to the
output size of the preset (2400 × 1800, ×1.657). Two methods are compared:

``lanczos`` (shipped): Lanczos-3 resampling in linear light, then a mild
    unsharp mask on the luminance only (σ 0.8 px, amount 0.35, threshold 0.5
    levels) – no new detail, only the softness of the enlargement is reduced;
``esrgan``: Real-ESRGAN (``RealESRGAN_x2plus`` / ``RealESRGAN_x4plus``,
    BSD-3-Clause code and weights, https://github.com/xinntao/Real-ESRGAN), run on
    CPU in tiles, then Lanczos down to the output size. Needs PyTorch – run it in
    its own virtualenv (see ``public/presets/autoexperten-haendler/README.md``).
    The network is defined below (the RRDBNet layout of the released weights).

Usage (from the repository root)::

    processor/.venv/bin/python processor/showroom3d/dealer_upscale.py lanczos \
        /opt/ae-dealer/clean/clean.png /opt/ae-dealer/up/lanczos.png
    /opt/ae-ai/esrgan-venv/bin/python processor/showroom3d/dealer_upscale.py esrgan \
        /opt/ae-dealer/clean/clean.png /opt/ae-dealer/up/esrgan-x2.png \
        --weights /opt/ae-ai/esrgan/weights/RealESRGAN_x2plus.pth --scale 2
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

OUTPUT_SIZE = (2400, 1800)
#: Unsharp mask of the lanczos method (luminance, output px / fraction / 8-bit levels).
SHARPEN_SIGMA, SHARPEN_AMOUNT, SHARPEN_THRESHOLD = 0.8, 0.35, 0.5
#: SHA-256 of the released Real-ESRGAN weights (GitHub release assets v0.1.0 / v0.2.1).
ESRGAN_WEIGHTS = {
    "RealESRGAN_x4plus.pth": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
    "RealESRGAN_x2plus.pth": "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb",
}


def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _resize_linear(rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Lanczos-3 resampling of an sRGB float image (0..1) in linear light, per channel (float32)."""
    linear = _to_linear(rgb).astype(np.float32)
    channels = [np.asarray(Image.fromarray(np.ascontiguousarray(linear[..., i])).resize(size, Image.LANCZOS)) for i in range(3)]
    return _to_srgb(np.stack(channels, axis=-1))


def _gaussian(image: np.ndarray, sigma: float) -> np.ndarray:
    radius = max(1, int(round(3 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(image, radius, mode="reflect")
    rows = sum(kernel[i] * padded[:, i : i + image.shape[1]] for i in range(len(kernel)))
    return sum(kernel[i] * rows[i : i + image.shape[0], :] for i in range(len(kernel)))


def lanczos(rgb_u8: np.ndarray, size: tuple[int, int] = OUTPUT_SIZE) -> np.ndarray:
    up = _resize_linear(rgb_u8.astype(np.float32) / 255.0, size) * 255.0
    luma = up @ np.array([0.2126, 0.7152, 0.0722])
    detail = luma - _gaussian(luma, SHARPEN_SIGMA)
    detail = np.sign(detail) * np.maximum(np.abs(detail) - SHARPEN_THRESHOLD, 0.0)
    out = up + (SHARPEN_AMOUNT * detail)[..., None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- Real-ESRGAN (optional)


def _rrdbnet(scale: int):
    import torch
    from torch import nn
    from torch.nn import functional as F

    class RDB(nn.Module):
        def __init__(self, feat=64, grow=32):
            super().__init__()
            self.conv1 = nn.Conv2d(feat, grow, 3, 1, 1)
            self.conv2 = nn.Conv2d(feat + grow, grow, 3, 1, 1)
            self.conv3 = nn.Conv2d(feat + 2 * grow, grow, 3, 1, 1)
            self.conv4 = nn.Conv2d(feat + 3 * grow, grow, 3, 1, 1)
            self.conv5 = nn.Conv2d(feat + 4 * grow, feat, 3, 1, 1)

        def forward(self, x):
            act = lambda t: F.leaky_relu(t, 0.2)  # noqa: E731
            x1 = act(self.conv1(x))
            x2 = act(self.conv2(torch.cat((x, x1), 1)))
            x3 = act(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = act(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, feat=64):
            super().__init__()
            self.rdb1, self.rdb2, self.rdb3 = RDB(feat), RDB(feat), RDB(feat)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, feat=64, blocks=23):
            super().__init__()
            in_ch = 3 * (4 if scale == 2 else 1)
            self.conv_first = nn.Conv2d(in_ch, feat, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(feat) for _ in range(blocks)])
            self.conv_body = nn.Conv2d(feat, feat, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(feat, feat, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(feat, feat, 3, 1, 1)
            self.conv_hr = nn.Conv2d(feat, feat, 3, 1, 1)
            self.conv_last = nn.Conv2d(feat, 3, 3, 1, 1)

        def forward(self, x):
            act = lambda t: F.leaky_relu(t, 0.2)  # noqa: E731
            if scale == 2:
                x = F.pixel_unshuffle(x, 2)
            feat = self.conv_first(x)
            feat = feat + self.conv_body(self.body(feat))
            feat = act(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = act(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(act(self.conv_hr(feat)))

    return RRDBNet()


def esrgan(rgb_u8: np.ndarray, weights: Path, scale: int, size=OUTPUT_SIZE, tile: int = 192, pad: int = 16) -> np.ndarray:
    import torch

    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if ESRGAN_WEIGHTS.get(weights.name) != digest:
        raise SystemExit(f"{weights.name}: unknown weights (sha256 {digest})")
    torch.set_num_threads(3)
    net = _rrdbnet(scale)
    state = torch.load(weights, map_location="cpu", weights_only=True)
    net.load_state_dict(state.get("params_ema", state.get("params", state)), strict=True)
    net.eval()
    image = torch.from_numpy(rgb_u8.astype(np.float32) / 255.0).permute(2, 0, 1)[None]
    _, _, h, w = image.shape
    out = torch.zeros((1, 3, h * scale, w * scale))
    with torch.no_grad():
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                y0, x0 = max(0, y - pad), max(0, x - pad)
                y1, x1 = min(h, y + tile + pad), min(w, x + tile + pad)
                # pixel_unshuffle needs even tiles
                y1 -= (y1 - y0) % 2
                x1 -= (x1 - x0) % 2
                res = net(image[:, :, y0:y1, x0:x1]).clamp(0, 1)
                ty1, tx1 = min(h, y + tile), min(w, x + tile)
                out[:, :, y * scale : ty1 * scale, x * scale : tx1 * scale] = res[
                    :, :, (y - y0) * scale : (ty1 - y0) * scale, (x - x0) * scale : (tx1 - x0) * scale
                ]
    big = out[0].permute(1, 2, 0).numpy()
    return np.clip(_resize_linear(big, size) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("method", choices=("lanczos", "esrgan"))
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--scale", type=int, choices=(2, 4), default=2)
    args = parser.parse_args()
    rgb = np.asarray(Image.open(args.source).convert("RGB"))
    if args.method == "lanczos":
        out = lanczos(rgb)
    else:
        if args.weights is None:
            raise SystemExit("--weights is required for esrgan")
        out = esrgan(rgb, args.weights, args.scale)
    args.target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(args.target, optimize=True)
    print(f"{args.method}: {rgb.shape[1]}x{rgb.shape[0]} -> {out.shape[1]}x{out.shape[0]} → {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
