import json

import pytest

from bambustudio_mcp.runtime import Runtime


@pytest.fixture
def profiles(tmp_path):
    root = tmp_path / "profiles"

    def write(name, data, vendor="vendor", kind="machine"):
        path = root / vendor / kind / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    return Runtime(profiles=str(root)), write


def test_profile_merge_precedence(profiles):
    runtime, write = profiles
    write("base", {"type": "machine", "name": "base", "inherited": 1, "value": "base"})
    write("first", {"value": "first", "explicit": "first"})
    write("second", {"value": "second", "explicit": "second"})
    write("printer", {"type": "machine", "name": "printer", "inherits": "base",
                      "include": ["first", "second"], "explicit": "child"})

    result = runtime.resolve_profile("printer", "machine")
    assert result == {"type": "machine", "name": "printer", "inherited": 1,
                      "value": "second", "explicit": "child"}


def test_nested_untyped_templates_and_string_include(profiles):
    runtime, write = profiles
    write("start", {"machine_start_gcode": "M109 S[nozzle_temperature_initial_layer]"})
    write("templates", {"include": "start", "machine_end_gcode": "M104 S0"})
    write("printer", {"type": "machine", "name": "printer", "include": ["templates"]})

    result = runtime.resolve_profile("printer", "machine")
    assert result["machine_start_gcode"] == "M109 S[nozzle_temperature_initial_layer]"
    assert result["machine_end_gcode"] == "M104 S0"
    assert [profile["name"] for profile in runtime.list_profiles()] == ["printer"]


@pytest.mark.parametrize("relation", ["inherits", "include"])
def test_missing_profile_reference(profiles, relation):
    runtime, write = profiles
    write("printer", {"type": "machine", "name": "printer", relation: "missing"})
    with pytest.raises(ValueError, match="Missing or ambiguous"):
        runtime.resolve_profile("printer", "machine")


@pytest.mark.parametrize("relations", [("inherits", "inherits"), ("include", "include"),
                                       ("inherits", "include")])
def test_profile_dependency_cycles(profiles, relations):
    runtime, write = profiles
    write("first", {"type": "machine", "name": "first", relations[0]: "second"})
    write("second", {"type": "machine", "name": "second", relations[1]: "first"})
    with pytest.raises(ValueError, match="cycle"):
        runtime.resolve_profile("first", "machine")


def test_ambiguous_profile_and_explicit_path(profiles):
    runtime, write = profiles
    first = write("printer", {"type": "machine", "name": "printer"})
    write("printer", {"type": "machine", "name": "printer"}, vendor="other")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        runtime.resolve_profile("printer", "machine")
    assert runtime.resolve_profile(str(first), "machine")["name"] == "printer"


def test_ambiguous_includes_and_local_preference(profiles):
    runtime, write = profiles
    write("template", {"machine_start_gcode": "first"}, vendor="first")
    write("template", {"machine_start_gcode": "second"}, vendor="second")
    write("printer", {"type": "machine", "name": "printer", "include": "template"})
    with pytest.raises(ValueError, match="Missing or ambiguous included"):
        runtime.resolve_profile("printer", "machine")
    write("template", {"machine_start_gcode": "local"})
    assert runtime.resolve_profile("printer", "machine")["machine_start_gcode"] == "local"


def test_profile_and_template_kind_checks(profiles):
    runtime, write = profiles
    template = write("template", {"machine_start_gcode": "start"})
    filament = write("filament", {"type": "filament", "name": "filament"})
    for path in (template, filament):
        with pytest.raises(ValueError, match="Expected a machine profile"):
            runtime.resolve_profile(str(path), "machine")
    write("printer", {"type": "machine", "name": "printer", "include": "filament"})
    with pytest.raises(ValueError, match="Expected a machine profile"):
        runtime.resolve_profile("printer", "machine")


@pytest.mark.parametrize("include", [True, {}, [False], [""]])
def test_invalid_include_reference(profiles, include):
    runtime, write = profiles
    write("printer", {"type": "machine", "name": "printer", "include": include})
    with pytest.raises(ValueError, match="include"):
        runtime.resolve_profile("printer", "machine")


def test_filament_template_inheritance(profiles):
    runtime, write = profiles
    write("base", {"type": "filament", "name": "base", "nozzle_temperature": ["250"]}, kind="filament")
    write("common", {"fan_max_speed": ["40"]}, kind="filament")
    write("template", {"inherits": "common", "filament_flow_ratio": ["0.95"]}, kind="filament")
    write("petg", {"type": "filament", "name": "petg", "inherits": "base",
                   "include": ["template"]}, kind="filament")
    result = runtime.resolve_profile("petg", "filament")
    assert result["nozzle_temperature"] == ["250"]
    assert result["fan_max_speed"] == ["40"]
    assert result["filament_flow_ratio"] == ["0.95"]
