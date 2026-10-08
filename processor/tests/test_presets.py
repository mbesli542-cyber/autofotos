"""The preset JSON is validated strictly – a typo must never silently fall back to defaults."""

import copy
import json
from pathlib import Path

import pytest

from app.presets import PresetConfigError, load_preset, parse_preset

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESET = json.loads((REPO_ROOT / "public/presets/autoexperten-standard.json").read_text(encoding="utf-8"))


def _with(path: list, value):
    data = copy.deepcopy(PRESET)
    node = data
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


def test_the_shipped_preset_is_valid_and_has_all_exterior_overrides():
    preset = parse_preset(PRESET)
    assert set(preset.shot_placement) == {
        "front_left_45", "front", "front_right_45", "left_side",
        "right_side", "rear_left_45", "rear", "rear_right_45",
    }  # fmt: skip
    assert preset.placement_for("left_side").width_ratio == pytest.approx(0.82)
    assert preset.placement_for("front").width_ratio == pytest.approx(0.60)
    assert preset.placement_for("rear_left_45").width_ratio == pytest.approx(0.80)
    assert preset.placement_for("front").ground_line == preset.placement.ground_line  # inherits the base


@pytest.mark.parametrize(
    "path, value",
    [
        (["background", "floorHorizont"], 0.6),  # typo in the key the README asks to edit
        (["background", "floorHorizon"], 62),  # percent instead of fraction
        (["placment"], {"widthRatio": 0.8}),  # typo in a section name
        (["shotPlacement", "frnt"], {"widthRatio": 0.6}),  # unknown shot
        (["shotPlacement", "front"], {"widthRation": 0.6}),  # typo inside a shot override
        (["shotPlacement", "front"], None),
        (["branding", "enabled"], "false"),  # string instead of boolean
        (["branding", "city", "color"], "#40F"),
        (["branding", "logo"], "official/AutoExperten_Logo.png"),
        (["branding", "logo", "top"], 1.4),
        (["placement", "widthRatio"], "0.8"),
        (["shadow", "color"], [34, 25]),
    ],
)
def test_invalid_values_are_rejected_with_a_clear_error(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(path, value))


def test_broken_json_is_a_configuration_error(settings):
    (settings.presets_dir / "autoexperten-standard.json").write_text('{"id": "x",, }', encoding="utf-8")
    with pytest.raises(PresetConfigError, match="autoexperten-standard.json"):
        load_preset(settings, "autoexperten_standard")
