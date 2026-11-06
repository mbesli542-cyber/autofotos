"""Automatic glass masks with SAM 2.1 (app/pipeline/glass_mask.py): the search on a synthetic cockpit
with a FAKE prompted segmenter that answers point prompts from a set of labelled objects (the
three smallest objects under the point, the union of the largest objects under a many-point
prompt), the mirror rule, the bump trim, the detector seeds and the wiring. One integration test
runs the real graphs when they are built."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import PROCESSOR_ROOT, Settings
from app.pipeline import glass_mask as gm
from app.pipeline.detection import Instance
from app.pipeline.glass_backdrop import GlassMask
from app.pipeline.sam2 import SAM2_REGISTRY, LOW_RES_SIZE, Sam2Embedding, Sam2Segmenter, ensure_built_model
from app.pipeline.segmentation import ModelUnavailableError

W, H = 1600, 1200
VIEW = (200, 60, 1400, 460)  # the windscreen: bright outside
SKY = (200, 60, 1400, 250)
GROUND = (200, 250, 1400, 460)
CAR = (500, 260, 900, 440)
MIRROR = (700, 60, 900, 170)  # black rear-view mirror hanging from the headliner
DASH = (0, 460, 1600, 800)  # light-grey dash top below the glass


def _rect(box) -> np.ndarray:
    mask = np.zeros((H, W), bool)
    x0, y0, x1, y1 = box
    mask[y0:y1, x0:x1] = True
    return mask


def make_scene(*, view_includes_mirror: bool = False, glass: bool = True):
    """(rgb uint8, objects {name: bool mask}, truth bool mask) of a synthetic cockpit: a dark
    interior, a bright windscreen with sky, ground and a grey car, a black mirror, a light-grey
    dash. ``view_includes_mirror``: SAM's view of the pane swallows the mirror (as it often does),
    ``glass=False``: a door-and-seat photo without any glass."""
    rgb = np.full((H, W, 3), 28, np.uint8)
    objects: dict[str, np.ndarray] = {}
    rgb[DASH[1] : DASH[3], :] = 92
    objects["dash"] = _rect(DASH)
    rgb[800:, :] = 24
    objects["console"] = _rect((0, 800, 1600, 1200))
    objects["pillar_left"] = _rect((0, 60, 200, 460))
    objects["pillar_right"] = _rect((1400, 60, 1600, 460))
    objects["headliner"] = _rect((0, 0, 1600, 60))
    truth = np.zeros((H, W), bool)
    if glass:
        rgb[SKY[1] : SKY[3], SKY[0] : SKY[2]] = (205, 208, 214)
        rgb[GROUND[1] : GROUND[3], GROUND[0] : GROUND[2]] = (168, 166, 160)
        rgb[CAR[1] : CAR[3], CAR[0] : CAR[2]] = (150, 152, 150)
        rgb[MIRROR[1] : MIRROR[3], MIRROR[0] : MIRROR[2]] = (12, 12, 14)
        mirror = _rect(MIRROR)
        objects["mirror"] = mirror
        objects["sky"] = _rect(SKY) & ~mirror
        objects["ground"] = _rect(GROUND)
        objects["car"] = _rect(CAR)
        objects["view"] = _rect(VIEW) if view_includes_mirror else _rect(VIEW) & ~mirror
        truth = _rect(VIEW) & ~mirror
    else:
        rgb[300:900, 300:1300] = 120  # a light-grey seat in the middle
        objects["seat"] = _rect((300, 300, 1300, 900))
    rng = np.random.default_rng(5)
    rgb = np.clip(rgb.astype(np.float32) + rng.normal(0, 2, rgb.shape), 0, 255).astype(np.uint8)
    return rgb, objects, truth


class FakeSam:
    """A prompted segmenter that knows labelled objects: a point prompt (multimask) returns the three
    smallest objects under the point, a many-point prompt (single mask) the union of the largest
    object under each point – nested like SAM's part / object / scene candidates."""

    name = "fake-sam"

    def __init__(self, objects: dict[str, np.ndarray]):
        self.objects = objects
        self.prompts: list[list[tuple[float, float, int]]] = []

    def encode(self, rgb: np.ndarray) -> Sam2Embedding:
        zeros = lambda *shape: np.zeros(shape, np.float32)  # noqa: E731
        return Sam2Embedding(zeros(1, 256, 64, 64), zeros(1, 32, 256, 256), zeros(1, 64, 128, 128), rgb.shape[1], rgb.shape[0])

    def _under(self, x: float, y: float) -> list[np.ndarray]:
        xi, yi = int(np.clip(x, 0, W - 1)), int(np.clip(y, 0, H - 1))
        return sorted((m for m in self.objects.values() if m[yi, xi]), key=lambda m: int(m.sum()))

    @staticmethod
    def _logits(masks: list[np.ndarray]) -> np.ndarray:
        out = np.empty((len(masks), LOW_RES_SIZE, LOW_RES_SIZE), np.float32)
        for k, mask in enumerate(masks):
            small = cv2.resize(mask.astype(np.float32), (LOW_RES_SIZE, LOW_RES_SIZE), interpolation=cv2.INTER_AREA) > 0.5
            out[k] = np.where(small, 8.0, -8.0)
        return out

    def decode(self, embedding, points, box, *, multimask):
        self.prompts.append(list(points))
        positives = [(x, y) for x, y, label in points if label == 1]
        if multimask:
            under = self._under(*positives[0]) or [np.zeros((H, W), bool)]
            while len(under) < 3:
                under.append(under[-1])
            masks = under[:3]
        else:
            union = np.zeros((H, W), bool)
            for x, y in positives:
                under = self._under(x, y)
                if under:
                    union |= under[-1]
            masks = [union]
        return self._logits(masks), np.full(len(masks), 0.9, np.float32), 1.0


class FakeDetector:
    def __init__(self, instances):
        self.instances = instances

    def detect(self, rgb, window=None):
        return list(self.instances)


def _instance(label, score, box):
    return Instance(label, score, tuple(float(v) for v in box), np.zeros((4, 4), np.float32))


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(a & b)) / float(max(np.count_nonzero(a | b), 1))


# --------------------------------------------------------------------------- the search


def test_a_bright_pane_in_a_dark_frame_is_one_pane_without_the_dash():
    rgb, objects, truth = make_scene()
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is not None and len(result.mask.panes) == 1
    region = result.mask.alpha >= 0.5
    assert _iou(region, truth) > 0.9
    # nothing below the glass: the dash top stays the original
    assert not region[VIEW[3] + 12 :, :].any()
    # the black mirror is not glass
    my0, my1 = MIRROR[1] + 8, MIRROR[3] - 8
    assert not region[my0:my1, MIRROR[0] + 8 : MIRROR[2] - 8].any()
    assert result.mask.source == "sam2" and result.mask.panes[0].view is None
    assert result.mask.alpha.dtype == np.float32 and 0.0 <= result.mask.alpha.min() and result.mask.alpha.max() <= 1.0
    verdicts = {s["verdict"] for s in result.seeds}
    assert "outside" in verdicts and ("low" in verdicts or "bottom" in verdicts)


def test_the_mirror_is_cut_out_even_when_the_view_swallows_it():
    rgb, objects, truth = make_scene(view_includes_mirror=True)
    sam = FakeSam(objects)
    result = gm.find_glass(sam, rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is not None
    region = result.mask.alpha >= 0.5
    assert _iou(region, truth) > 0.88
    assert not region[MIRROR[1] + 8 : MIRROR[3] - 8, MIRROR[0] + 8 : MIRROR[2] - 8].any()
    checks = [c for view in result.views for c in view.get("mirrorChecks", [])]
    assert any(c["mirror"] for c in checks)


def test_a_photo_without_glass_gives_none():
    rgb, objects, _ = make_scene(glass=False)
    sam = FakeSam(objects)
    result = gm.find_glass(sam, rgb, "driver_seat", gm.GlassMaskConfig())
    assert result.mask is None
    assert all(s["verdict"] != "outside" for s in result.seeds)
    assert result.prompts <= len(result.seeds)


def test_a_seat_photo_through_the_provider_is_none_and_a_cockpit_is_a_mask():
    rgb, objects, _ = make_scene(glass=False)
    assert gm.sam_glass_masks(FakeSam(objects))(rgb, "driver_seat") is None
    rgb, objects, truth = make_scene()
    mask = gm.sam_glass_masks(FakeSam(objects))(rgb, "cockpit")
    assert isinstance(mask, GlassMask) and mask.alpha.shape == (H, W)
    assert _iou(mask.alpha >= 0.5, truth) > 0.9


def test_detector_seeds_come_from_cars_outside_only():
    rgb, objects, truth = make_scene()
    detector = FakeDetector(
        [
            _instance("car", 0.9, CAR),  # the car seen through the glass
            _instance("car", 0.4, (0, 0, W, H)),  # the photographed car itself (whole frame)
            _instance("car", 0.6, (1250, 300, 1300, 330)),  # a tiny reflection
            _instance("car", 0.9, (300, 900, 900, 1150)),  # below the seed rows
            _instance("laptop", 0.9, (1000, 500, 1300, 700)),
        ]
    )
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig(), detector)
    assert len(result.detector_seeds) == 1
    x, y = result.detector_seeds[0]
    scale = 512 / W
    assert abs(x - 0.5 * (CAR[0] + CAR[2]) * scale) <= 1 and abs(y - 0.5 * (CAR[1] + CAR[3]) * scale) <= 1
    assert result.mask is not None and _iou(result.mask.alpha >= 0.5, truth) > 0.9


def test_a_pane_that_is_just_a_detected_car_is_no_pane():
    """A crop that cuts the window away around a car: SAM's object is the car, the gate would pass
    (bright, no dark structure) – the pane's box matches the car's box, so it is dropped."""
    rgb, objects, _ = make_scene()
    objects = {name: mask for name, mask in objects.items() if name in ("car", "dash", "console")}
    detector = FakeDetector([_instance("car", 0.9, CAR)])
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig(min_inner_pane=0.0), detector)
    assert result.mask is None
    assert any(p["verdict"] == "just_a_car" for p in result.panes)


def test_a_pane_that_covers_only_part_of_a_car_is_incomplete():
    rgb, objects, _ = make_scene()
    # SAM's objects end at a diagonal: the right part of the pane is never found
    cut = np.zeros((H, W), bool)
    cut[:, : (CAR[0] + CAR[2]) // 2] = True
    objects = {name: (mask & cut if name in ("sky", "ground", "car", "view") else mask) for name, mask in objects.items()}
    detector = FakeDetector([_instance("car", 0.9, CAR)])
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig(), detector)
    assert result.mask is None
    assert any(p["verdict"] == "incomplete" for p in result.panes)


def test_a_pane_that_cuts_a_detected_car_at_its_lower_edge_is_incomplete_and_vetoes():
    """The windscreen pane ends above the lower body of a car seen through it (SAM's objects stop at
    a row through the car) while the detector's INSTANCE MASK shows the whole visible car: the pane
    covers it only in part – measured on the mask, not on the loose box – so the pane is incomplete
    and the photo gets no mask at all (the window continues beyond the pane)."""
    rgb, objects, _ = make_scene()
    cut_row = (CAR[1] + CAR[3]) // 2
    cut = np.zeros((H, W), bool)
    cut[:cut_row, :] = True
    objects = {name: (mask & cut if name in ("sky", "ground", "car", "view") else mask) for name, mask in objects.items()}
    car_logits = np.where(_rect(CAR), 8.0, -8.0).astype(np.float32)
    detector = FakeDetector([Instance("car", 0.9, tuple(float(v) for v in CAR), car_logits)])
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig(), detector)
    assert result.mask is None and result.veto == "incomplete"
    incomplete = [p for p in result.panes if p["verdict"] == "incomplete"]
    assert incomplete and incomplete[0]["carCoverage"] < gm.GlassMaskConfig().detector_coverage
    # the whole car in the pane passes with the same detector
    rgb, objects, truth = make_scene()
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig(), detector)
    assert result.mask is not None and result.panes[0]["carCoverage"] == 1.0
    assert _iou(result.mask.alpha >= 0.5, truth) > 0.9


def test_a_bright_region_next_to_a_pane_that_is_not_interior_vetoes_the_photo():
    """The pane ends early (SAM's objects stop at a column) and the rest of the windscreen stays
    bright beside it without any interior evidence: the photo is unsure – no mask. The same bright
    region covered by an interior seed's mask (a light panel reaching far down) never vetoes."""
    rgb, objects, _ = make_scene()
    cut_col = 1100
    cut = np.zeros((H, W), bool)
    cut[:, :cut_col] = True
    partial = {name: (mask & cut if name in ("sky", "ground", "car", "view") else mask) for name, mask in objects.items()}
    result = gm.find_glass(FakeSam(partial), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is None and result.veto == "adjacent"
    blob = next(b for b in result.unassigned_blobs if b["veto"] == "adjacent")
    assert blob["adjacent"] and not blob["top"] and blob["interior"] < 0.5 and blob["share"] >= gm.GlassMaskConfig().adjacent_veto
    # the rest of the windscreen is light interior (a seed's mask reaching far down) – the pane stands
    partial["panel_right"] = _rect((cut_col, VIEW[1], VIEW[2], 800))
    result = gm.find_glass(FakeSam(partial), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is not None and result.veto is None and len(result.mask.panes) == 1
    assert all(b["veto"] is None for b in result.unassigned_blobs)
    # switched off: the early pane alone is returned
    del partial["panel_right"]
    result = gm.find_glass(FakeSam(partial), rgb, "cockpit", gm.GlassMaskConfig(adjacent_veto=0.0))
    assert result.mask is not None and result.veto is None


def test_a_second_bright_window_the_search_cannot_resolve_vetoes_the_photo():
    """One window in the showroom and the next still in the car park would be a bad result: a bright
    region at the top that is no pane makes the provider return nothing."""
    rgb, objects, _ = make_scene()
    rgb[0:360, 1440:1600] = (200, 202, 206)  # a second window (behind a pillar) SAM knows no object for
    objects = {name: mask for name, mask in objects.items() if name not in ("pillar_right", "headliner")}
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is None
    assert any(share >= gm.GlassMaskConfig().unassigned_veto for share in result.unassigned)
    # the same photo with the region resolved as an object is a two-pane mask
    objects["second"] = _rect((1440, 0, 1600, 360))
    result = gm.find_glass(FakeSam(objects), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.mask is not None and len(result.mask.panes) == 2


# --------------------------------------------------------------------------- rules


def test_looks_like_mirror_wants_a_compact_blob_in_the_upper_middle_of_the_view():
    cfg = gm.GlassMaskConfig()
    view_box = (20, 10, 500, 160)
    blob = np.zeros((288, 512), bool)
    blob[14:36, 230:300] = True  # compact, aspect 3.2, upper middle: 0.9 % of the frame
    assert gm.looks_like_mirror(blob, view_box, cfg)
    low = np.zeros_like(blob)
    low[120:142, 230:300] = True  # too low in the view
    assert not gm.looks_like_mirror(low, view_box, cfg)
    side = np.zeros_like(blob)
    side[14:36, 30:100] = True  # at the edge of the view
    assert not gm.looks_like_mirror(side, view_box, cfg)
    ring = blob.copy()
    ring[18:32, 240:290] = False  # hollow: not compact
    assert not gm.looks_like_mirror(ring, view_box, cfg)
    big = np.zeros_like(blob)
    big[10:120, 100:420] = True  # a windscreen, not a mirror
    assert not gm.looks_like_mirror(big, view_box, cfg)


def test_bump_trim_cuts_what_hangs_below_the_pane_bottom():
    cfg = gm.GlassMaskConfig()
    pane = np.zeros((288, 512), bool)
    pane[20:60, 40:470] = True  # a strip whose bottom is at row 59
    pane[60:95, 200:240] = True  # a bump of dash top hanging 35 rows below it
    trimmed = gm._trim_bumps(pane, cfg)
    assert trimmed[20:60, 40:470].all()
    assert not trimmed[70:, :].any()
    sloped = np.zeros((288, 512), bool)
    for x in range(40, 470):  # a windscreen seen at an angle: its bottom edge is a gentle slope
        sloped[20 : 60 + int(0.08 * (x - 40)), x] = True
    assert np.array_equal(gm._trim_bumps(sloped, cfg), sloped)


def test_config_defaults_keep_the_mirror_and_pane_rules_within_their_meaning():
    cfg = gm.GlassMaskConfig()
    assert 0 < cfg.mirror_min_area < cfg.mirror_max_area < 0.1
    assert 0 < cfg.feather_px <= 3.0
    assert cfg.seed_rows <= 0.6 and cfg.max_pane_centroid <= 0.5
    assert cfg.detector_min_area < cfg.detector_max_area


# --------------------------------------------------------------------------- wiring


def test_settings_choose_the_glass_mask_source(monkeypatch):
    monkeypatch.delenv("PROCESSOR_GLASS_MASKS", raising=False)
    assert Settings.from_env().glass_masks == "sam2"
    monkeypatch.setenv("PROCESSOR_GLASS_MASKS", "none")
    assert Settings.from_env().glass_masks == "none"


def test_the_app_wires_the_sam_provider_lazily(settings):
    from app.main import create_app

    app = create_app(replace(settings, glass_masks="sam2"), segmenter=_StubSegmenter(), warm_up=False)
    manager = app.state.manager
    assert manager.glass_masks is not None
    app = create_app(replace(settings, glass_masks="none"), segmenter=_StubSegmenter(), warm_up=False)
    assert app.state.manager.glass_masks is None


class _StubSegmenter:
    name = "stub"
    loaded = True
    detector = None

    def segment(self, rgb):  # pragma: no cover - never called here
        raise AssertionError("not used")


# --------------------------------------------------------------------------- integration


MODELS_DIR = Path(os.environ.get("PROCESSOR_MODELS_DIR") or PROCESSOR_ROOT / "models")


def test_the_built_graphs_run_the_search_and_never_take_the_dash():
    """Runs only with the built SAM 2.1 graphs in the models folder (scripts/export_sam2.py). The
    flat synthetic scene is no photo for SAM (its largest masks swallow the whole flat frame, so most
    seeds read as "huge"): what is checked is that the real graphs run through the search with the
    seeds' verdicts recorded and that whatever comes out never covers the dash or the mirror – the
    dealer's photos are evaluated outside the test suite."""
    spec = SAM2_REGISTRY["sam2.1-hiera-small"]
    try:
        for filename, sha256 in ((spec.encoder_filename, spec.encoder_sha256), (spec.decoder_filename, spec.decoder_sha256)):
            ensure_built_model(filename, sha256, MODELS_DIR, name=spec.name)
    except ModelUnavailableError as error:
        pytest.skip(f"SAM 2.1 graphs not built: {error}")
    rgb, _, _ = make_scene()
    result = gm.find_glass(Sam2Segmenter(spec, MODELS_DIR), rgb, "cockpit", gm.GlassMaskConfig())
    assert result.prompts >= 2 and "encode" in result.timings_ms and "views" in result.timings_ms
    assert {s["verdict"] for s in result.seeds} & {"outside", "huge", "low", "dark"}
    if result.mask is not None:
        region = result.mask.alpha >= 0.5
        assert not region[VIEW[3] + 24 :, :].any()
        assert not region[MIRROR[1] + 8 : MIRROR[3] - 8, MIRROR[0] + 8 : MIRROR[2] - 8].any()
