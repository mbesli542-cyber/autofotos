"""The preset JSON is validated strictly – a typo must never silently fall back to defaults."""

import copy
import json
from pathlib import Path

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
    assert preset.plates == "autoexperten-standard/plates.json"
    assert preset.branding.enabled and preset.branding.logo.file == "official/AutoExperten_Logo.png"
    assert preset.quality.max_upscale == pytest.approx(1.5)
    assert preset.quality.min_source_long_edge == 1600
    assert preset.quality.min_vehicle_width_ratio == pytest.approx(0.45)
    assert preset.placement_for("front") == preset.placement  # no per-shot overrides needed


def test_quality_defaults_match_the_shipped_preset():
    assert parse_preset(PRESET).quality == QualityConfig()


def test_grounding_and_reflection_defaults_match_the_shipped_preset():
    preset = parse_preset(PRESET)
    assert preset.shadow == ShadowConfig()
    assert preset.reflection == ReflectionConfig()
    # the car's reflection uses the plate's measured floor reflectance, capped (lacquer, not a mirror)
    assert preset.reflection.enabled and preset.reflection.strength == pytest.approx(1.0)
    assert 0.1 <= preset.reflection.max_reflectance <= 0.25
    # darkest deep under the car, lighter at the floor line; never pure black
    assert preset.shadow.edge_opacity < preset.shadow.underbody_opacity < 1.0
    assert preset.shadow.min_floor_light >= 0.02


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
        (["branding", "phone", "top"], 0.90),  # phone number moved down to the floor
        (["placement", "maxHeightRatio"], 0.1),
    ],
)
def test_layouts_that_leave_no_room_for_the_vehicle_are_rejected(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(path, value))
