"""The AutoExperten 3D showroom as eight angle-specific plates.

One physical 3D room (``processor/showroom3d/render_plates.py``, Blender) is
rendered from one camera per exterior shot. Every plate comes with metadata
(``plates.json``) that describes the camera and the room geometry, so the
processor can put the real vehicle into the room in the right perspective:

- ``floorHomography``: floor point (x, y) in metres (z = 0; wall at y = 0,
  room in y < 0) → normalised image (u, v) (0..1, u right, v down), i.e.
  ``[u*w, v*w, w] = H @ [x, y, 1]``;
- ``wallHomography``: wall point (x, z) in metres (y = 0, z up) → (u, v);
- ``vehicle``: where a typical car (the light-blocking proxy of the shadow
  pass) stands in this view – projected bounding box, tyre contacts, target
  width ratio;
- ``wall.brandArea``: the white wall between the slat panels that carries
  the official branding (composited by app/showroom/wall_branding.py).

Files of a plate set (e.g. ``public/presets/autoexperten-standard/``):
``<shot>.jpg`` (empty plate, NO branding), ``<shot>-shadow.png`` (16-bit grey
floor shadow/occlusion multiplier of the proxy car in LINEAR light,
65535 = 1.0 = no shadow), optionally ``<shot>-reflection.png`` (16-bit RGB: the
floor's own glossy reflection of the LEDs and the wall in LINEAR light, signed:
code / 65535 − ``reflectionOffset``; with ``reflectance`` – the measured
specular reflectance of the floor per image row) and ``plates.json``. Loading validates everything strictly – a broken
plate set is reported, never silently replaced.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

#: The eight exterior shots, one plate each.
PLATE_SHOTS = (
    "front_left_45",
    "front",
    "front_right_45",
    "left_side",
    "right_side",
    "rear_left_45",
    "rear",
    "rear_right_45",
)

#: 3/4 shots and the plate of the same front/rear group seen from the other side.
MIRROR_PLATES = {
    "front_left_45": "front_right_45",
    "front_right_45": "front_left_45",
    "rear_left_45": "rear_right_45",
    "rear_right_45": "rear_left_45",
}

CONTACT_LABELS = ("front_left", "front_right", "rear_left", "rear_right")


class PlateSetError(Exception):
    """The plate set (plates.json or one of its files) is missing or invalid."""


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class PlateCamera:
    position: tuple[float, float, float]
    height_m: float
    distance_to_anchor_m: float
    orbit_deg: float
    pitch_down_deg: float
    hfov_deg: float
    focal_px: float
    #: focal length / image width
    focal_norm: float

    def summary(self) -> dict:
        return {
            "heightM": round(self.height_m, 3),
            "distanceToAnchorM": round(self.distance_to_anchor_m, 3),
            "orbitDeg": round(self.orbit_deg, 2),
            "pitchDownDeg": round(self.pitch_down_deg, 2),
            "hfovDeg": round(self.hfov_deg, 2),
        }


@dataclass(frozen=True)
class ProxyContact:
    label: str
    u: float
    v: float
    depth_m: float
    floor: tuple[float, float]


@dataclass(frozen=True)
class BrandArea:
    """Wall rectangle (metres, wall plane y = 0) that carries the branding."""

    x_min: float
    x_max: float
    z_min: float
    z_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.z_max - self.z_min


@dataclass(frozen=True)
class Plate:
    key: str
    image: Path
    #: Floor shadow multiplier of the proxy car (None: no shadow pass rendered).
    shadow: Path | None
    size: tuple[int, int]
    shadow_size: tuple[int, int] | None
    camera: PlateCamera
    horizon_v: float
    floor_h: np.ndarray
    wall_h: np.ndarray
    anchor_floor: tuple[float, float]
    anchor_u: float
    anchor_v: float
    anchor_depth_m: float
    view: str
    heading_deg: float
    target_width_ratio: float
    proxy: dict
    #: Projected typical car (u0, v0, u1, v1), normalised.
    proxy_bbox: tuple[float, float, float, float]
    proxy_contacts: dict[str, ProxyContact]
    wall_albedo: tuple[float, float, float]
    brand_area: BrandArea
    #: The floor's own reflection pass (None: not rendered) and its size.
    reflection: Path | None = None
    reflection_size: tuple[int, int] | None = None
    #: Decoding of the signed reflection pass: linear value = code / 65535 − offset.
    reflection_offset: float = 0.0
    #: Measured floor reflectance: ((v, k), ...) – v normalised image row, k = reflection /
    #: mirrored wall radiance (both linear). Set together with `reflection`.
    reflectance: tuple[tuple[float, float], ...] | None = None

    def reflectance_at(self, v) -> np.ndarray:
        """Floor reflectance at normalised image rows `v` (constant beyond the samples)."""
        if not self.reflectance:
            return np.zeros_like(np.asarray(v, np.float64))
        vs, ks = zip(*self.reflectance)
        return np.interp(np.asarray(v, np.float64), vs, ks)

    # ------------------------------------------------------------ proxy geometry

    @property
    def aspect(self) -> float:
        return self.size[0] / self.size[1]

    @property
    def ground_v(self) -> float:
        """v of the typical car's lowest tyre contact (the plate's ground line)."""
        return max(c.v for c in self.proxy_contacts.values())

    @property
    def is_three_quarter(self) -> bool:
        return self.key in MIRROR_PLATES

    def visible_contacts(self) -> list[ProxyContact]:
        """Proxy contacts on the lower outline (the ones a photo shows), sorted by u."""
        points = sorted(self.proxy_contacts.values(), key=lambda c: c.u)
        width, height = self.size
        hull: list[ProxyContact] = []
        for point in points:  # lower convex hull (max v), as in the vehicle outline
            while len(hull) >= 2:
                a, b = hull[-2], hull[-1]
                cross = (b.u - a.u) * width * (point.v - a.v) * height - (b.v - a.v) * height * (point.u - a.u) * width
                if cross >= 0:
                    hull.pop()
                else:
                    break
            hull.append(point)
        return hull

    def proxy_bbox_px(self, width: int, height: int) -> tuple[float, float, float, float]:
        u0, v0, u1, v1 = self.proxy_bbox
        return u0 * width, v0 * height, u1 * width, v1 * height

    def proxy_aspect(self) -> float:
        u0, v0, u1, v1 = self.proxy_bbox
        return (u1 - u0) * self.size[0] / max((v1 - v0) * self.size[1], 1e-6)

    def proxy_rise_ratio(self) -> float:
        """Height difference of the visible proxy contacts / proxy bbox width (both px)."""
        visible = self.visible_contacts()
        u0, _, u1, _ = self.proxy_bbox
        if len(visible) < 2:
            return 0.0
        rise = (max(c.v for c in visible) - min(c.v for c in visible)) * self.size[1]
        return rise / max((u1 - u0) * self.size[0], 1e-6)

    def expected_near_end(self) -> str | None:
        """Image side ("left"/"right") of the car end that is nearer to the camera.

        Derived from the proxy (3D knowledge): the axle group with the smaller
        mean depth is the near end; its side is where its contacts lie in the
        image. None for views without a clear near end (side, front, rear).
        """
        if not self.is_three_quarter:
            return None
        front = [self.proxy_contacts[k] for k in ("front_left", "front_right")]
        rear = [self.proxy_contacts[k] for k in ("rear_left", "rear_right")]
        near, far = (front, rear) if _mean(c.depth_m for c in front) < _mean(c.depth_m for c in rear) else (rear, front)
        return "left" if _mean(c.u for c in near) < _mean(c.u for c in far) else "right"

    # ------------------------------------------------------------ room geometry

    def floor_to_uv(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        return _apply_h(self.floor_h, x, y)

    def wall_to_uv(self, x, z) -> tuple[np.ndarray, np.ndarray]:
        return _apply_h(self.wall_h, x, z)

    def floor_top(self, width: int, height: int) -> np.ndarray:
        """Per output column: first row (float px) that shows the floor in front of the wall.

        A pixel shows the floor when its viewing ray hits z = 0 in front of the
        wall (y < 0). Rows above (wall, horizon) never get floor shadows.
        Columns without floor return ``height``.
        """
        inv = np.linalg.inv(self.floor_h)
        us = (np.arange(width, dtype=np.float64) + 0.5) / width
        # (x*w, y*w, w) = inv @ (u, v, 1): y*w and w are linear in v
        ay, by, cy = inv[1]
        aw, bw, cw = inv[2]
        top = np.full(width, float(height), np.float64)
        for i, u in enumerate(us):
            lo, hi = 0.0, 1.0
            ok = True
            # constraints: y*w < 0 and w > 0, each linear in v: k*v + d (<0 / >0)
            for k, d, sign in ((by, ay * u + cy, -1.0), (bw, aw * u + cw, 1.0)):
                # want sign*(k*v + d) > 0
                ks, ds = sign * k, sign * d
                if abs(ks) < 1e-12:
                    if ds <= 0:
                        ok = False
                    continue
                root = -ds / ks
                if ks > 0:
                    lo = max(lo, root)
                else:
                    hi = min(hi, root)
            if ok and hi >= 1.0 - 1e-9 and lo < 1.0:
                top[i] = lo * height
        return top.astype(np.float32)

    def floor_mask(self, width: int, height: int) -> np.ndarray:
        top = self.floor_top(width, height)
        rows = np.arange(height, dtype=np.float32)[:, None] + 0.5
        return rows >= top[None, :]

    def summary(self) -> dict:
        return {
            "key": self.key,
            "view": self.view,
            "camera": self.camera.summary(),
            "horizonV": round(self.horizon_v, 4),
            "groundV": round(self.ground_v, 4),
            "targetWidthRatio": self.target_width_ratio,
            "proxyBBox": [round(v, 4) for v in self.proxy_bbox],
            "expectedNearEnd": self.expected_near_end(),
        }


@dataclass(frozen=True)
class PlateSet:
    path: Path
    plates: dict[str, Plate]

    def plate(self, key: str) -> Plate:
        try:
            return self.plates[key]
        except KeyError:
            raise PlateSetError(f"no plate for shot '{key}'") from None

    def files(self) -> list[Path]:
        out = [self.path]
        for plate in self.plates.values():
            out.append(plate.image)
            if plate.shadow is not None:
                out.append(plate.shadow)
            if plate.reflection is not None:
                out.append(plate.reflection)
        return out


def _mean(values) -> float:
    values = list(values)
    return sum(values) / max(len(values), 1)


def _apply_h(h: np.ndarray, a, b) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    x = h[0, 0] * a + h[0, 1] * b + h[0, 2]
    y = h[1, 0] * a + h[1, 1] * b + h[1, 2]
    w = h[2, 0] * a + h[2, 1] * b + h[2, 2]
    return x / w, y / w


# --------------------------------------------------------------------------- loading


def _number(where: str, value, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlateSetError(f"{where} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise PlateSetError(f"{where} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise PlateSetError(f"{where} must be <= {maximum}, got {value}")
    return value


def _vector(where: str, value, length: int) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise PlateSetError(f"{where} must be a list of {length} numbers")
    return tuple(_number(f"{where}[{i}]", v) for i, v in enumerate(value))


def _size(where: str, value) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PlateSetError(f"{where} must be [width, height]")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 16 for v in value):
        raise PlateSetError(f"{where} must contain two whole numbers >= 16")
    return int(value[0]), int(value[1])


def _image_size(where: str, path: Path) -> tuple[int, int]:
    """Size from the file header (cheap) – catches empty, foreign or truncated-at-start files."""
    try:
        with Image.open(path) as image:
            return image.size
    except Exception as error:
        raise PlateSetError(f"{where}: {path.name} is not a readable image ({error})") from None


def _homography(where: str, value) -> np.ndarray:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise PlateSetError(f"{where} must be a 3x3 matrix")
    rows = [_vector(f"{where}[{i}]", row, 3) for i, row in enumerate(value)]
    h = np.array(rows, np.float64)
    if abs(np.linalg.det(h)) < 1e-12:
        raise PlateSetError(f"{where} is singular")
    return h


def _section(where: str, value) -> dict:
    if not isinstance(value, dict):
        raise PlateSetError(f"{where} must be an object")
    return value


def _file(directory: Path, where: str, value) -> Path:
    if not isinstance(value, str) or not value or "/" in value or "\\" in value or value.startswith("."):
        raise PlateSetError(f"{where} must be a plain file name next to plates.json")
    path = directory / value
    if not path.is_file():
        raise PlateSetError(f"{where}: file missing: {path.name}")
    return path


def _reflectance(where: str, value) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise PlateSetError(f"{where} must be a list of at least two [v, k] pairs")
    pairs = tuple(
        (
            _number(f"{where}[{i}][0]", _vector(f"{where}[{i}]", pair, 2)[0], minimum=0.0, maximum=1.0),
            _number(f"{where}[{i}][1]", _vector(f"{where}[{i}]", pair, 2)[1], minimum=0.0, maximum=0.5),
        )
        for i, pair in enumerate(value)
    )
    if any(b[0] <= a[0] for a, b in zip(pairs, pairs[1:])):
        raise PlateSetError(f"{where}: v must increase strictly")
    return pairs


def _reflection(where: str, data: dict, directory: Path, size: tuple[int, int]):
    """Optional floor reflection pass: (file, size, offset, reflectance) or (None, None, 0, None)."""
    if data.get("reflection") is None:
        for key in ("reflectance", "reflectionOffset", "reflectionSize"):
            if data.get(key) is not None:
                raise PlateSetError(f"{where}.{key} needs a reflection pass ({where}.reflection)")
        return None, None, 0.0, None
    path = _file(directory, f"{where}.reflection", data["reflection"])
    actual = _image_size(f"{where}.reflection", path)
    if "reflectionSize" in data and _size(f"{where}.reflectionSize", data["reflectionSize"]) != actual:
        raise PlateSetError(f"{where}: {path.name} is not the size plates.json says (reflectionSize)")
    if abs(actual[0] / actual[1] - size[0] / size[1]) > 0.01 * size[0] / size[1]:
        raise PlateSetError(f"{where}: {path.name} has another aspect ratio than the plate")
    offset = _number(f"{where}.reflectionOffset", data.get("reflectionOffset"), minimum=0.0, maximum=1.0)
    return path, actual, offset, _reflectance(f"{where}.reflectance", data.get("reflectance"))


def parse_plate(key: str, data, directory: Path) -> Plate:
    where = f"plates.{key}"
    data = _section(where, data)
    image = _file(directory, f"{where}.image", data.get("image"))
    shadow = _file(directory, f"{where}.shadow", data["shadow"]) if data.get("shadow") is not None else None
    size = _size(f"{where}.size", data.get("size"))
    shadow_size = _size(f"{where}.shadowSize", data["shadowSize"]) if shadow is not None and "shadowSize" in data else None
    if _image_size(f"{where}.image", image) != size:
        raise PlateSetError(f"{where}: {image.name} is not {size[0]}x{size[1]} as plates.json says")
    if shadow is not None:
        actual = _image_size(f"{where}.shadow", shadow)
        if shadow_size is not None and actual != shadow_size:
            raise PlateSetError(f"{where}: {shadow.name} is not {shadow_size[0]}x{shadow_size[1]} as plates.json says")
    reflection, reflection_size, reflection_offset, reflectance = _reflection(where, data, directory, size)
    cam = _section(f"{where}.camera", data.get("camera"))
    camera = PlateCamera(
        position=_vector(f"{where}.camera.position", cam.get("position"), 3),  # type: ignore[arg-type]
        height_m=_number(f"{where}.camera.heightM", cam.get("heightM"), minimum=0.05, maximum=20),
        distance_to_anchor_m=_number(f"{where}.camera.distanceToAnchorM", cam.get("distanceToAnchorM"), minimum=0.1),
        orbit_deg=_number(f"{where}.camera.orbitDeg", cam.get("orbitDeg"), minimum=-180, maximum=180),
        pitch_down_deg=_number(f"{where}.camera.pitchDownDeg", cam.get("pitchDownDeg"), minimum=-89, maximum=89),
        hfov_deg=_number(f"{where}.camera.hfovDeg", cam.get("hfovDeg"), minimum=5, maximum=150),
        focal_px=_number(f"{where}.camera.focalPx", cam.get("focalPx"), minimum=1e-3),
        focal_norm=_number(f"{where}.camera.focalNorm", cam.get("focalNorm"), minimum=1e-3),
    )
    anchor = _section(f"{where}.anchor", data.get("anchor"))
    vehicle = _section(f"{where}.vehicle", data.get("vehicle"))
    proxy = _section(f"{where}.vehicle.proxy", vehicle.get("proxy"))
    proxy_dims = {
        name: _number(f"{where}.vehicle.proxy.{name}", proxy.get(name), minimum=0.1, maximum=20)
        for name in ("lengthM", "widthM", "heightM", "wheelbaseM", "trackM")
    }
    bbox = _vector(f"{where}.vehicle.proxyBBox", vehicle.get("proxyBBox"), 4)
    if not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise PlateSetError(f"{where}.vehicle.proxyBBox must be [u0, v0, u1, v1] with u0 < u1, v0 < v1")
    contacts_data = _section(f"{where}.vehicle.proxyContacts", vehicle.get("proxyContacts"))
    contacts = {}
    for label in CONTACT_LABELS:
        c = _section(f"{where}.vehicle.proxyContacts.{label}", contacts_data.get(label))
        contacts[label] = ProxyContact(
            label=label,
            u=_number(f"{where}.vehicle.proxyContacts.{label}.u", c.get("u"), minimum=-1, maximum=2),
            v=_number(f"{where}.vehicle.proxyContacts.{label}.v", c.get("v"), minimum=-1, maximum=2),
            depth_m=_number(f"{where}.vehicle.proxyContacts.{label}.depthM", c.get("depthM"), minimum=0.01),
            floor=_vector(f"{where}.vehicle.proxyContacts.{label}.floor", c.get("floor"), 2),  # type: ignore[arg-type]
        )
    wall = _section(f"{where}.wall", data.get("wall"))
    albedo = _vector(f"{where}.wall.albedo", wall.get("albedo"), 3)
    if any(not 0.02 <= a <= 1.0 for a in albedo):
        raise PlateSetError(f"{where}.wall.albedo must be linear values in 0.02..1")
    area = _section(f"{where}.wall.brandArea", wall.get("brandArea"))
    brand_area = BrandArea(*(_number(f"{where}.wall.brandArea.{k}", area.get(k)) for k in ("xMin", "xMax", "zMin", "zMax")))
    if brand_area.width <= 0 or brand_area.height <= 0:
        raise PlateSetError(f"{where}.wall.brandArea must have xMin < xMax and zMin < zMax")
    plate = Plate(
        key=key,
        image=image,
        shadow=shadow,
        size=size,
        shadow_size=shadow_size,
        camera=camera,
        horizon_v=_number(f"{where}.horizonV", data.get("horizonV"), minimum=-2, maximum=3),
        floor_h=_homography(f"{where}.floorHomography", data.get("floorHomography")),
        wall_h=_homography(f"{where}.wallHomography", data.get("wallHomography")),
        anchor_floor=_vector(f"{where}.anchor.floor", anchor.get("floor"), 2),  # type: ignore[arg-type]
        anchor_u=_number(f"{where}.anchor.u", anchor.get("u")),
        anchor_v=_number(f"{where}.anchor.v", anchor.get("v")),
        anchor_depth_m=_number(f"{where}.anchor.depthM", anchor.get("depthM"), minimum=0.01),
        view=str(vehicle.get("view", "")),
        heading_deg=_number(f"{where}.vehicle.headingDeg", vehicle.get("headingDeg")),
        target_width_ratio=_number(
            f"{where}.vehicle.targetWidthRatio", vehicle.get("targetWidthRatio"), minimum=0.2, maximum=0.98
        ),
        proxy=proxy_dims,
        proxy_bbox=bbox,  # type: ignore[arg-type]
        proxy_contacts=contacts,
        wall_albedo=albedo,  # type: ignore[arg-type]
        brand_area=brand_area,
        reflection=reflection,
        reflection_size=reflection_size,
        reflection_offset=reflection_offset,
        reflectance=reflectance,
    )
    _check_geometry(where, plate)
    return plate


def _check_geometry(where: str, plate: Plate) -> None:
    """The homographies must agree with the rest of the metadata (catches swapped/garbled matrices)."""
    u, v = plate.floor_to_uv(plate.anchor_floor[0], plate.anchor_floor[1])
    if not (np.isfinite(u) and np.isfinite(v)) or abs(u - plate.anchor_u) > 0.02 or abs(v - plate.anchor_v) > 0.02:
        raise PlateSetError(f"{where}.floorHomography does not map the anchor to anchor.u/v")
    for contact in plate.proxy_contacts.values():
        cu, cv = plate.floor_to_uv(*contact.floor)
        if abs(cu - contact.u) > 0.02 or abs(cv - contact.v) > 0.02:
            raise PlateSetError(f"{where}.floorHomography does not match proxyContacts.{contact.label}")
    # wall and floor share the line y = 0, z = 0 (the wall/floor junction)
    for x in (-1.0, 1.0):
        fu, fv = plate.floor_to_uv(x, 0.0)
        wu, wv = plate.wall_to_uv(x, 0.0)
        if abs(fu - wu) > 0.01 or abs(fv - wv) > 0.01:
            raise PlateSetError(f"{where}: wallHomography and floorHomography disagree at the wall/floor junction")
    ground = plate.ground_v
    if not 0.3 <= ground <= 1.0:
        raise PlateSetError(f"{where}: the proxy's lowest tyre contact (v={ground:.3f}) is outside the frame")


def load_plate_set(path: Path, shots=PLATE_SHOTS) -> PlateSet:
    """Load and strictly validate a plate set (raises PlateSetError)."""
    if not path.is_file():
        raise PlateSetError(f"plates.json missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlateSetError(f"{path.name} unreadable: {error}") from None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise PlateSetError(f"{path.name}: expected an object with \"version\": 1")
    plates_data = data.get("plates")
    if not isinstance(plates_data, dict):
        raise PlateSetError(f"{path.name}: \"plates\" must be an object")
    missing = [s for s in shots if s not in plates_data]
    if missing:
        raise PlateSetError(f"{path.name}: plates missing for {', '.join(missing)}")
    plates = {key: parse_plate(key, plates_data[key], path.parent) for key in shots}
    aspects = {round(p.aspect, 3) for p in plates.values()}
    if max(aspects) / min(aspects) > 1.01:
        raise PlateSetError(f"{path.name}: plates have different aspect ratios")
    return PlateSet(path=path, plates=plates)
