"""Derive portrait (upright phone) test photos from the landscape regression photos.

The regression set has no real portrait car photos. Upright exterior photos made
the pipeline merge background objects into the car (the car is a thin band of a
tall frame – see app/pipeline/segmentation.py, ``segment_vehicle``), so these
derived frames reproduce that situation from the cached regression photos:

- ``<case>_9x16`` / ``<case>_3x4``: the landscape photo at full frame width,
  centred in a 2160 × 3840 (9:16) or 2160 × 2880 (3:4) portrait frame; the space
  above and below is filled with the photo's own top band (sky, trees) and bottom
  band (ground, snow), mirrored back and forth (``BAND`` of the photo height – real
  scene texture, no bars, no second car) – the car stays an unchanged band;
- ``<case>_9x16_1080``: the 9:16 frame at 1080 × 1920 (a 1080p video-stream
  capture held upright) – expected ``source_resolution_too_low`` with the
  "hold the phone sideways" message (also the 2160 px frames of a car that fills
  only about half of the width, see ``DEFAULT_CASES``);
- ``<case>_crop3x4`` (front/rear views, where the car is narrower than the frame):
  a real 3:4 portrait crop of the photo centred on the car;
- ``<case>_crop3x4_flip`` (``FLIP_CASES``): that crop mirrored horizontally – the car's
  sides swapped, which BiRefNet does not treat symmetrically (front_crop3x4 loses both
  mirrors in its window pass and gets them from the whole frame; the mirrored crop keeps
  both heads but loses their bases, and its whole frame loses both mirrors).

``requiredParts`` of a source case (points inside e.g. the side mirrors, which an
accepted result must contain) are carried over into the derived frames' coordinates.

Writes the JPEGs plus ``portrait_set.json`` (a manifest for
``scripts/visual_regression.py --manifest … --cache-dir …``) into ``--out``.
The photos carry the source photo's licence (CC BY / CC BY-SA / CC0): never
commit them; credit the authors when sharing.

Usage (from processor/)::

    .venv/bin/python scripts/make_portrait_cases.py --out /opt/ae-regression/portrait
    .venv/bin/python scripts/visual_regression.py --manifest /opt/ae-regression/portrait/portrait_set.json \\
        --cache-dir /opt/ae-regression/portrait --label portrait
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

PROCESSOR_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROCESSOR_ROOT / "tests" / "visual" / "regression_set.json"
#: Landscape cases turned into portrait frames → expected outcome of the 9:16 and 3:4
#: frames (a car filling only ~53 % of the width needs more than 1.5× upscaling once
#: the frame is only 2160 px wide: rejected with the "hold the phone sideways" message;
#: side_right: the window pass leaves out the hubcap that is glued to the landscape photo's
#: rear, and the X-Trail's own bonnet edge in front of the snowy car behind is kept),
#: and the cases that also get a real portrait crop.
DEFAULT_CASES = {
    "side_left": "accept",
    "sedan_fl45": "accept",
    "old_hardlight": "source_resolution_too_low",
    "side_right": "accept",
}
CROP_CASES = ("front",)
#: Crop cases that also get the mirrored crop → its expectation, the warning codes the accepted
#: result must carry and the required parts NOT carried over (substring of the part name):
#: front_crop3x4_flip keeps both mirror heads, no BiRefNet pass of it keeps their bases (the
#: whole frame loses both mirrors) – accepted with the hint to check the result.
FLIP_CASES = {
    "front": {"expected": "accept", "expectedWarnings": ["vehicle_part_uncertain"], "dropParts": "base"},
}
WIDTH = 2160
#: Share of the photo height used as sky (top) and ground (bottom) texture.
BAND = 0.12
CREDIT_KEYS = ("title", "author", "authorUrl", "license", "licenseUrl", "landingUrl")


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))


def _mirrored(band: np.ndarray, rows: int, *, upwards: bool) -> np.ndarray:
    """``rows`` rows of ``band`` mirrored back and forth, continuing it seamlessly
    above (``upwards``) or below the band."""
    pieces, flip, total = [], True, 0
    while total < rows:
        piece = band[::-1] if flip else band
        pieces.append(piece)
        total += piece.shape[0]
        flip = not flip
    stack = np.concatenate(pieces, axis=0)
    return stack[:rows][::-1] if upwards else stack[:rows]


def padded_portrait(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    """The photo at ``width``, centred in a width × height frame, its own sky band
    mirrored above and its ground band mirrored below."""
    h, w = rgb.shape[:2]
    scaled = cv2.resize(rgb, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    sh = scaled.shape[0]
    top = (height - sh) // 2
    bottom = height - sh - top
    if top < 0:
        raise ValueError("the scaled photo is taller than the portrait frame")
    band = max(2, int(round(BAND * sh)))
    above = _mirrored(scaled[:band], top, upwards=True)
    below = _mirrored(scaled[sh - band :], bottom, upwards=False)
    return np.concatenate([above, scaled, below], axis=0)


def crop_portrait(rgb: np.ndarray, centre_x: float) -> np.ndarray | None:
    """A 3:4 portrait crop of the full photo height around ``centre_x`` (None if too narrow)."""
    h, w = rgb.shape[:2]
    cw = int(round(h * 3 / 4))
    if cw > w:
        return None
    x0 = int(np.clip(round(centre_x - cw / 2), 0, w - cw))
    return rgb[:, x0 : x0 + cw]


def save(rgb: np.ndarray, path: Path) -> str:
    Image.fromarray(rgb).save(path, quality=92, subsampling=0)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="output directory (photos + portrait_set.json)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, help="regression photo cache (default: manifest cacheDirDefault)")
    parser.add_argument(
        "--cases",
        default=",".join(DEFAULT_CASES),
        help="landscape cases for the padded frames (expected outcome: DEFAULT_CASES, otherwise accept)",
    )
    parser.add_argument("--crop-cases", default=",".join(CROP_CASES), help="cases for the real 3:4 portrait crop")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cache = args.cache_dir or Path(manifest.get("cacheDirDefault") or "/opt/ae-regression/images")
    by_id = {c["id"]: c for c in manifest["cases"]}
    args.out.mkdir(parents=True, exist_ok=True)
    cases = []

    def parts(source: dict, scale: float, dx: float, dy: float, size: tuple[int, int]) -> dict:
        """The source's required parts in the derived frame (scaled, then shifted)."""
        moved = []
        for part in source.get("requiredParts") or []:
            x, y = part["point"]
            px, py = int(round(x * scale + dx)), int(round(y * scale + dy))
            if 0 <= px < size[0] and 0 <= py < size[1]:
                moved.append({"part": part["part"], "point": [px, py]})
        return {"requiredParts": moved} if moved else {}

    def entry(
        source: dict,
        case_id: str,
        file: str,
        digest: str,
        size: tuple[int, int],
        expected: str,
        how: str,
        extra: dict | None = None,
    ):
        cases.append(
            {
                "id": case_id,
                "caseId": case_id,
                "category": "portrait",
                "shotKey": source["shotKey"],
                "expected": expected,
                "file": file,
                "sha256": digest,
                "width": size[0],
                "height": size[1],
                "derivedFrom": source["id"],
                "derivation": how,
                **(extra or {}),
                **{k: source[k] for k in CREDIT_KEYS if source.get(k)},
            }
        )

    for case_id in [c for c in args.cases.split(",") if c]:
        source = by_id.get(case_id)
        if source is None:
            print(f"unknown case {case_id}", file=sys.stderr)
            return 2
        rgb = load_rgb(cache / source["file"])
        h, w = rgb.shape[:2]
        for name, height in (("9x16", 3840), ("3x4", 2880)):
            frame = padded_portrait(rgb, WIDTH, height)
            file = f"{case_id}_{name}.jpg"
            how = f"full photo scaled to {WIDTH} px wide, centred in a {WIDTH}x{height} frame, sky/ground mirrored"
            expected = DEFAULT_CASES.get(case_id, "accept")
            scale, top = WIDTH / w, (height - round(h * WIDTH / w)) // 2
            extra = parts(source, scale, 0, top, (WIDTH, height))
            entry(source, f"{case_id}_{name}", file, save(frame, args.out / file), (WIDTH, height), expected, how, extra)
            if name == "9x16":
                small = cv2.resize(frame, (1080, 1920), interpolation=cv2.INTER_AREA)
                file = f"{case_id}_9x16_1080.jpg"
                entry(
                    source,
                    f"{case_id}_9x16_1080",
                    file,
                    save(small, args.out / file),
                    (1080, 1920),
                    "source_resolution_too_low",
                    "the 9:16 frame at 1080x1920 (upright 1080p camera stream)",
                    parts(source, scale * 0.5, 0, top * 0.5, (1080, 1920)),
                )
    for case_id in [c for c in args.crop_cases.split(",") if c]:
        source = by_id.get(case_id)
        if source is None:
            print(f"unknown case {case_id}", file=sys.stderr)
            return 2
        rgb = load_rgb(cache / source["file"])
        crop = crop_portrait(rgb, rgb.shape[1] / 2)
        if crop is None:
            continue
        file = f"{case_id}_crop3x4.jpg"
        size = (crop.shape[1], crop.shape[0])
        x0 = int(np.clip(round(rgb.shape[1] / 2 - crop.shape[1] / 2), 0, rgb.shape[1] - crop.shape[1]))  # as crop_portrait
        entry(source, f"{case_id}_crop3x4", file, save(np.ascontiguousarray(crop), args.out / file), size, "accept",
              "3:4 portrait crop of the full photo height, centred", parts(source, 1.0, -x0, 0, size))  # fmt: skip
        flip = FLIP_CASES.get(case_id)
        if flip is not None:
            file = f"{case_id}_crop3x4_flip.jpg"
            moved = parts(source, 1.0, -x0, 0, size).get("requiredParts", [])
            kept = [
                {"part": f"{p['part']} (mirrored crop)", "point": [size[0] - 1 - p["point"][0], p["point"][1]]}
                for p in moved
                if flip["dropParts"] not in p["part"]
            ]
            extra = {"requiredParts": kept} if kept else {}
            if flip.get("expectedWarnings"):
                extra["expectedWarnings"] = list(flip["expectedWarnings"])
            mirrored = np.ascontiguousarray(crop[:, ::-1])
            entry(source, f"{case_id}_crop3x4_flip", file, save(mirrored, args.out / file), size, flip["expected"],
                  "the centred 3:4 portrait crop, mirrored horizontally (the car's sides swapped)", extra)  # fmt: skip

    out = {
        "version": 1,
        "description": "Portrait frames derived from the regression photos by scripts/make_portrait_cases.py",
        "cacheDirDefault": str(args.out),
        "cases": cases,
    }
    (args.out / "portrait_set.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for case in cases:
        print(f"{case['id']:<24} {case['width']}x{case['height']}  expected {case['expected']}")
    print(f"manifest: {args.out / 'portrait_set.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
