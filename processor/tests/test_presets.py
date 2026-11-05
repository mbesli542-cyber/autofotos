"""The preset JSON is validated strictly – a typo must never silently fall back to defaults."""

import dataclasses
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from app.presets import (
    PresetConfigError,
    QualityConfig,
    ReflectionConfig,
    ShadowConfig,
    load_preset,
    parse_preset,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESET = json.loads((REPO_ROOT / "public/presets/autoexperten-standard.json").read_text(encoding="utf-8"))


def _with(path: list, value):
    data = copy.deepcopy(PRESET)
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


def test_the_shipped_preset_is_valid_and_uses_the_plate_set():
    preset = parse_preset(PRESET)
    assert preset.plates == "autoexperten-haendler/plates.json"  # the dealer's studio (shipped look)
    assert preset.branding.enabled and preset.branding.logo.file == "official/AutoExperten_Logo.png"
    assert preset.quality.max_upscale == pytest.approx(1.5)
    assert preset.quality.min_source_long_edge == 1600
    assert preset.quality.min_vehicle_width_ratio == pytest.approx(0.45)
    assert preset.placement_for("front") == preset.placement  # no per-shot overrides needed


def test_quality_defaults_match_the_shipped_preset():
    """The gate as before – only the "photo from above" factors are scaled to the dealer studio's
    camera (the same absolute thresholds; test_showroom_variants.py checks the relation)."""
    assert parse_preset(PRESET).quality == dataclasses.replace(
        QualityConfig(), high_view_rise_factor=1.42, high_view_aspect_factor=0.88
    )


def test_the_shipped_grounding_and_reflection_are_the_dealer_studio_medium_values():
    """The shipped preset is "Schatten mittel" of the dealer's studio (variants/autoexperten-haendler.json):
    lighter, warm (neutral-tint) shadows than the code defaults, the car's reflection near the car."""
    preset = parse_preset(PRESET)
    defaults = ShadowConfig()
    assert preset.shadow == dataclasses.replace(
        defaults, contact_opacity=0.9, crease_opacity=0.8, underbody_opacity=0.8, edge_opacity=0.4,
        underbody_falloff=0.1, ambient_opacity=0.3, ambient_reach=0.4, tint=(1.0, 1.0, 1.0), footprint_reach=0.3,
    )  # fmt: skip
    assert preset.reflection == dataclasses.replace(
        ReflectionConfig(), strength=1.5, max_reflectance=0.3, fade=0.5, footprint_reach=0.3
    )
    # the car's reflection uses the plate's measured floor reflectance, capped (lacquer, not a mirror)
    assert preset.reflection.enabled and preset.reflection.max_reflectance <= 0.3
    # darkest deep under the car, lighter at the floor line; never pure black
    assert preset.shadow.edge_opacity < preset.shadow.underbody_opacity < 1.0
    assert preset.shadow.min_floor_light >= 0.02
    # lighter and shorter than the code defaults (the earlier 3D-room look) – the dealer found even
    # stronger values on his plate "far too black" (edge 0.7, falloff 0.25 m, ambient 0.6)
    for name in ("contact_opacity", "crease_opacity", "underbody_opacity", "underbody_falloff", "ambient_opacity", "ambient_reach"):
        assert getattr(preset.shadow, name) < getattr(defaults, name), name
    assert preset.shadow.edge_opacity <= defaults.edge_opacity


def test_the_dealer_look_fields_default_to_the_earlier_behaviour():
    """shadow.tint / shadow.footprintReach / reflection.footprintReach: the shipped preset writes them
    out (tint 1 1 1, footprint limit 0.3 m); a preset without them gets the earlier behaviour
    (cooler deep shadows, no footprint limit)."""
    assert PRESET["shadow"]["tint"] == [1.0, 1.0, 1.0] and PRESET["shadow"]["footprintReach"] == 0.3
    assert PRESET["reflection"]["footprintReach"] == 0.3
    data = copy.deepcopy(PRESET)
    for section, key in (("shadow", "tint"), ("shadow", "footprintReach"), ("reflection", "footprintReach")):
        del data[section][key]
    preset = parse_preset(data)
    assert preset.shadow.tint == (1.0, 1.0, 0.95) and preset.shadow.footprint_reach == 0.0
    assert preset.reflection.footprint_reach == 0.0
    shadow = parse_preset(_with(["shadow", "tint"], [1, 1, 1])).shadow
    assert shadow.tint == (1.0, 1.0, 1.0) and all(isinstance(v, float) for v in shadow.tint)
    assert parse_preset(_with(["shadow", "footprintReach"], 0.0)).shadow.footprint_reach == 0.0
    assert parse_preset(_with(["reflection", "footprintReach"], 0.0)).reflection.footprint_reach == 0.0


def test_missing_reflection_section_uses_the_defaults():
    data = _with(["reflection"], KeyError)
    assert parse_preset(data).reflection == ReflectionConfig()


def test_shot_overrides_are_still_possible():
    preset = parse_preset(_with(["shotPlacement", "front"], {"maxHeightRatio": 0.7}))
    assert preset.placement_for("front").max_height_ratio == pytest.approx(0.7)
    assert preset.placement_for("rear") == preset.placement


@pytest.mark.parametrize(
    "path, value",
    [
        (["background", "plates"], 5),
        (["background", "plates"], "../secret/plates.json"),  # outside presets/
        (["background", "plates"], "/etc/plates.json"),
        (["background", "image"], "autoexperten-standard-showroom.jpg"),  # the old single master
        (["background", "floorHorizon"], 0.6),
        (["background", "fallbackImage"], "fallback/x.jpg"),
        (["background"], KeyError),
        (["placment"], {"maxHeightRatio": 0.6}),  # typo in a section name
        (["placement", "widthRatio"], 0.8),  # now comes from the plate
        (["placement", "maxHeightRatio"], "0.6"),
        (["shotPlacement", "frnt"], {"maxHeightRatio": 0.6}),  # unknown shot
        (["shotPlacement", "front"], None),
        (["branding", "enabled"], "false"),  # string instead of boolean
        (["branding", "city", "color"], "#40F"),
        (["branding", "logo"], "official/AutoExperten_Logo.png"),
        (["branding", "logo", "top"], 1.4),
        (["shadow", "color"], [34, 25, 18]),  # v1 field, grounding v2 has no tint colour
        (["shadow", "contactWidth"], 1.2),  # v2 rule (× the visible tyre width) – sizes are physical now
        (["shadow", "edgeOpacity"], 0.95),  # lighter at the floor line than deep under the car
        (["shadow", "contactOpacity"], 1.4),
        (["shadow", "minFloorLight"], 0.0),  # pure black holes
        (["shadow", "ambientReach"], 5.0),  # metres – would darken the whole foreground again
        (["shadow", "underbodyFalloff"], 0.0),
        (["shadow"], [0.9]),
        (["shadow", "tint"], [1.0, 1.0, 0.5]),  # a strong colour cast
        (["shadow", "tint"], [0.9, 0.9, 0.9]),  # relative to the most darkened channel (max 1)
        (["shadow", "tint"], [1.0, 1.0]),
        (["shadow", "tint"], "neutral"),
        (["shadow", "footprintReach"], 0.01),  # 0 (off) or a sensible floor distance
        (["shadow", "footprintReach"], 3.0),
        (["shadow", "footprintReach"], -0.3),
        (["reflection", "footprintReach"], 5.0),
        (["reflection", "opacity"], 0.14),  # v2 field – the reflectance is measured on the plate now
        (["reflection", "strength"], 2.0),  # a mirror, not a lacquer reflection
        (["reflection", "maxReflectance"], 0.5),
        (["reflection", "defaultReflectance"], 0.4),
        (["reflection", "fade"], 0.1),
        (["reflection", "fade"], 3.0),
        (["reflection", "blurRate"], 0.5),
        (["reflection", "blurRate"], -0.01),
        (["reflection", "enabled"], "yes"),
        (["reflection", "strenght"], 1.0),  # typo / unknown field
        (["reflection"], 0.1),
        (["quality", "maxUpscale"], 0.8),  # below 1 would mean "always shrink"
        (["quality", "maxUpscal"], 1.5),  # typo
        (["quality", "minSourceLongEdge"], 1600.5),
        (["quality", "minVehicleWidthRatio"], 45),  # percent instead of fraction
        (["quality", "minRiseFactor"], 3.0),
        (["quality"], [1, 2]),
        (["quality", "maxUpscale"], float("nan")),  # json.loads accepts NaN and Infinity
        (["quality", "maxUpscale"], float("inf")),
        (["reflection", "strength"], float("nan")),
        (["output", "aspect"], [4, float("nan")]),
    ],
)
def test_invalid_values_are_rejected_with_a_clear_error(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(path, value))


def test_broken_json_is_a_configuration_error(settings):
    (settings.presets_dir / "autoexperten-standard.json").write_text('{"id": "x",, }', encoding="utf-8")
    with pytest.raises(PresetConfigError, match="autoexperten-standard.json"):
        load_preset(settings, "autoexperten_standard")


@pytest.mark.parametrize(
    "path, value",
    [
        (["branding", "city", "top"], 0.90),  # city line moved down to the floor
        (["placement", "maxHeightRatio"], 0.1),
    ],
)
def test_layouts_that_leave_no_room_for_the_vehicle_are_rejected(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(path, value))


# --------------------------------------------------------------------------- PROC-3: values that broke jobs


@pytest.mark.parametrize(
    "path, value",
    [
        (["output", "aspect"], [4, 0]),  # ZeroDivisionError in every exterior job
        (["output", "aspect"], [0, 3]),
        (["output", "aspect"], [4.5, 3]),  # silently became 4:3
        (["output", "aspect"], [1, 3]),  # 1:3 – no listing image
        (["output", "aspect"], [7, 2]),
        (["output", "aspect"], [4]),
        (["output", "longEdgeMax"], 0),  # ZeroDivisionError
        (["output", "longEdgeMin"], 100),
        (["output", "longEdgeMin"], 5000),  # above longEdgeMax 2400
        (["output", "longEdgeMax"], 9000),
        (["output", "jpegQuality"], 0),
        (["output", "jpegQuality"], 101),
        (["placement", "minMargin"], 0.6),  # ValueError('invalid scale') in every exterior job
        (["placement", "minMargin"], 0.25),
        (["shotPlacement", "front"], {"minMargin": 0.3}),
        (["branding", "mount", "shadowBlur"], 1.0),  # > 7 GB while branding a plate
        (["branding", "mount", "shadowBlur"], 0.05),
        (["branding", "mount", "shadowOffset"], 0.5),
        (["branding", "city", "capHeight"], 0.5),
        (["branding", "city", "tracking"], 40.0),
        (["branding", "logo", "centerX"], 5.0),  # logo drawn off the wall = silently missing
        (["branding", "logo", "maxWidth"], 0.0),
        (["branding", "logo", "opacity"], 0.2),
        (["branding", "logo", "file"], "../secret/logo.png"),
        (["branding", "logo", "file"], "AutoExperten_Logo.png"),  # not the official folder
        (["branding", "logo", "file"], "official/AutoExperten_Logo.svg"),
        (["quality", "borderPx"], 100_000),  # every photo "cropped" → endless retakes
    ],
)
def test_values_that_would_break_jobs_are_configuration_errors(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(path, value))


@pytest.mark.parametrize(
    "path, value",
    [
        (["output", "aspect"], [3, 2]),
        (["output", "longEdgeMin"], 320),
        (["output", "longEdgeMax"], 8000),
        (["placement", "minMargin"], 0.2),
        (["shotPlacement", "front"], {"minMargin": 0.0}),
        (["branding", "mount", "shadowBlur"], 0.02),
        (["branding", "logo", "opacity"], 0.5),
        (["branding", "logo", "file"], "official/AutoExperten_Logo_2027.png"),
        (["quality", "borderPx"], 50),
    ],
)
def test_sensible_values_at_the_limits_are_accepted(path, value):
    parse_preset(_with(path, value))


def test_branding_limits_do_not_apply_when_branding_is_off():
    data = _with(["branding", "enabled"], False)
    data["branding"]["logo"]["file"] = "elsewhere/logo.png"
    assert parse_preset(data).branding.enabled is False


# --------------------------------------------------------------------------- PROC-4: plate cache


def _edit_plates(path: Path, change) -> None:
    import os

    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    stat = path.stat()
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))  # a later mtime even on coarse clocks


def test_branded_plates_follow_an_edited_plates_json(settings):
    from app.presets import BackgroundProvider

    preset = load_preset(settings, "autoexperten_standard")
    provider = BackgroundProvider(settings)
    before = provider.get(preset, "front_left_45", 1200, 900)
    assert provider.get(preset, "front_left_45", 1200, 900) is before  # cached

    def change(data):
        plate = data["plates"]["front_left_45"]
        plate["vehicle"]["targetWidthRatio"] = 0.5
        plate["wall"]["brandArea"]["zMax"] = 3.2

    _edit_plates(settings.presets_dir / preset.plates, change)
    after = provider.get(preset, "front_left_45", 1200, 900)
    fresh = BackgroundProvider(settings).get(preset, "front_left_45", 1200, 900)
    assert after is not before
    assert after.plate is provider.plate_set(preset).plate("front_left_45")  # never mixed with old plate data
    assert after.plate.target_width_ratio == pytest.approx(0.5)
    assert after.plate.brand_area.z_max == pytest.approx(3.2)
    assert after.branding.boxes == fresh.branding.boxes != before.branding.boxes
    assert np.array_equal(after.rgb, fresh.rgb)


# --------------------------------------------------------------------------- PROC-MISS-1: configuration check for /health


def test_config_error_is_none_for_the_shipped_configuration(settings):
    from app.presets import BackgroundProvider

    assert BackgroundProvider(settings).config_error(load_preset(settings, "autoexperten_standard")) is None


@pytest.mark.parametrize("logo", ["missing", "corrupt", "empty"])
def test_config_error_reports_an_unusable_official_logo(settings, logo):
    from PIL import Image

    from app.presets import BackgroundProvider

    path = settings.brand_dir / "official" / "AutoExperten_Logo.png"
    if logo == "missing":
        path.unlink()
    elif logo == "corrupt":
        path.write_bytes(b"\x89PNG\r\n broken")
    else:
        Image.new("RGBA", (40, 20), (0, 0, 0, 0)).save(path)
    error = BackgroundProvider(settings).config_error(load_preset(settings, "autoexperten_standard"))
    assert error and "official logo" in error


def test_config_error_reports_an_output_aspect_that_does_not_fit_the_plates(settings):
    from app.presets import BackgroundProvider

    path = settings.presets_dir / "autoexperten-standard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["output"]["aspect"] = [3, 2]
    path.write_text(json.dumps(data), encoding="utf-8")
    error = BackgroundProvider(settings).config_error(load_preset(settings, "autoexperten_standard"))
    assert error and "3:2" in error and "640x480" in error


def test_an_output_aspect_that_does_not_fit_the_plates_is_a_configuration_error_not_a_missing_showroom(settings):
    from app.presets import BackgroundProvider, ShowroomUnavailableError

    provider = BackgroundProvider(settings)
    shipped = load_preset(settings, "autoexperten_standard")
    path = settings.presets_dir / "autoexperten-standard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["output"]["aspect"] = [16, 9]
    path.write_text(json.dumps(data), encoding="utf-8")
    preset = load_preset(settings, "autoexperten_standard")
    calls = {
        # the pipeline asks for the plate set first – the job fails before the segmentation
        "plate_set": lambda: provider.plate_set(preset),
        "get": lambda: provider.get(preset, "front", 1600, 900),
        # an output size that does not fit usable plates is no missing showroom either
        "get with another size": lambda: provider.get(shipped, "front", 1600, 900),
    }
    for name, call in calls.items():
        with pytest.raises(PresetConfigError) as info:
            call()
        assert not isinstance(info.value, ShowroomUnavailableError), name
    assert "does not match the showroom plates" in provider.config_error(preset)
    assert provider.master_error(preset) is None and provider.source(preset) == "plates"
    assert provider.plate_set(shipped) is not None  # the shipped 4:3 output still fits


# --------------------------------------------------------------------------- branding texts (canvas size)


@pytest.mark.parametrize(
    "path, value",
    [
        (["branding", "city", "text"], "SCHWETZINGEN " * 15),  # 195 characters: a 2.6 GB text canvas
        (["branding", "website", "text"], "w" * 61),
        (["branding", "phone", "text"], "+49 6202\n9262357"),  # one line per text
        (["branding", "phone", "text"], "+49 6202 9262357"),  # Unicode line separator
        # within the capHeight/tracking limits, but about 2.6 wall widths wide (~1 GB per plate)
        (["branding", "city"], {"capHeight": 0.1, "tracking": 2.0}),
        # very wide glyphs measured with the real font: arrows (2.68 em) and U+2031 (1.74 em) –
        # these slipped under the old "1 em per character" bound and needed a multi-GB canvas
        (["branding", "city"], {"text": "\U0001f852" * 18, "capHeight": 0.1, "tracking": 0.0}),
        (["branding", "website"], {"text": "‱" * 18, "capHeight": 0.1, "tracking": 0.0}),
    ],
)
def test_branding_texts_too_long_or_too_wide_for_the_wall_are_configuration_errors(path, value):
    with pytest.raises(PresetConfigError, match="one line of at most 60 characters|too wide for the brand wall"):
        parse_preset(_with(path, value))


def test_branding_text_width_is_measured_with_the_bundled_font_not_one_em_per_character():
    # 18 arrows and 18 capital letters are the same length at the same cap height, but each arrow
    # is ~2.68 em wide against ~0.76 em for the letter – only measuring the real font rejects the
    # arrows (which would need a multi-GB render canvas) while the letters still fit.
    arrows = _with(["branding", "city"], {"text": "\U0001f852" * 18, "capHeight": 0.1, "tracking": 0.0})
    letters = _with(["branding", "city"], {"text": "H" * 18, "capHeight": 0.1, "tracking": 0.0})
    with pytest.raises(PresetConfigError, match="too wide for the brand wall"):
        parse_preset(arrows)
    parse_preset(letters)  # same length and cap height, ordinary glyphs – accepted


@pytest.mark.parametrize(
    "path, value",
    [
        (["branding", "website", "text"], "www." + "a" * 53 + ".de"),  # 60 characters at the shipped size
        (["branding", "city"], {"capHeight": 0.1, "tracking": 0.0}),  # big letters that still fit the wall
        (["branding", "city", "text"], "MANNHEIM-KÄFERTAL"),
        (["branding", "phone", "text"], "+49 6202 9262357"),  # no-break spaces (pasted numbers)
        (["branding", "phone", "text"], ""),  # no phone line
    ],
)
def test_sensible_branding_texts_are_accepted(path, value):
    parse_preset(_with(path, value))


def test_the_branding_text_bound_matches_the_wall_of_the_shipped_plates():
    """A text at the width bound is about as wide as the brand wall – the bound refuses
    texts that would be clipped, not texts that fit."""
    from PIL import ImageFont

    from app.presets import _CAP_HEIGHT_EM, MAX_TEXT_WIDTH
    from app.showroom.branding import FONT_DIR, FONT_FILES
    from app.showroom.plates import load_plate_set

    plates = load_plate_set(REPO_ROOT / "public/presets" / parse_preset(PRESET).plates).plates.values()
    wall = min(p.brand_area.width / p.brand_area.height for p in plates)  # wall width in wall heights
    font = ImageFont.truetype(str(FONT_DIR / FONT_FILES["medium"]), 1000, layout_engine=ImageFont.Layout.BASIC)
    assert font.getbbox("H")[3] - font.getbbox("H")[1] == pytest.approx(_CAP_HEIGHT_EM * 1000, rel=0.02)
    for text in ("www.autoexperten-rn.de", "+49 6202 9262357", "SCHWETZINGEN"):
        average_em = font.getlength(text) / 1000 / len(text)  # Inter: about 0.6 em per character
        assert 0.4 * wall < MAX_TEXT_WIDTH * average_em < 1.25 * wall, text
