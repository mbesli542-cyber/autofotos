"""Download the CC0 assets the showroom scene uses (Poly Haven, https://polyhaven.com).

All assets are CC0 (public domain) – no attribution required, credited anyway in
processor/showroom3d/README.md. Nothing generated, no stock dealership photos.

Usage (any Python 3, standard library only):

    python3 processor/showroom3d/fetch_assets.py /opt/showroom-assets
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

API = "https://api.polyhaven.com/files/{asset}"
USER_AGENT = "AutoExpertenShowroomAssets/1.0 (+https://github.com/mbesli542-cyber/autofotos)"

#: (asset id, resolution, texture maps) – the scene expects exactly these files.
TEXTURES = (
    ("laminate_floor_02", "4k", ("Diffuse", "nor_gl", "Rough")),  # lacquered oak plank floor
    ("oak_veneer_01", "2k", ("Diffuse", "nor_gl", "Rough")),  # slat panels (tinted to walnut in the shader)
    ("painted_plaster_wall", "2k", ("Diffuse", "nor_gl", "Rough")),  # brand wall micro structure
)
MODELS = (("island_tree_02", "2k"),)  # olive-like trees in the planters


def _get(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=120)


def _files(asset: str) -> dict:
    with _get(API.format(asset=asset)) as response:
        return json.load(response)


def _download(url: str, target: Path) -> None:
    if target.is_file() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with _get(url) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    tmp.replace(target)
    print(f"  {target} ({target.stat().st_size // 1024} KiB)")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    for asset, res, maps in TEXTURES:
        print(asset)
        files = _files(asset)
        for name in maps:
            url = files[name][res]["jpg"]["url"]
            _download(url, root / asset / url.rsplit("/", 1)[-1])
    for asset, res in MODELS:
        print(asset)
        gltf = _files(asset)["gltf"][res]["gltf"]
        _download(gltf["url"], root / asset / gltf["url"].rsplit("/", 1)[-1])
        for rel, include in gltf.get("include", {}).items():
            _download(include["url"], root / asset / rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
