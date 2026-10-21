import hashlib
import json
import zipfile

import pytest

from bambustudio_mcp.gcode import inspect_sliced_project


def sliced(tmp_path, gcode, settings=None, second_plate=None):
    path = tmp_path / "sliced.gcode.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings or {
            "nozzle_temperature_initial_layer": ["245"], "nozzle_temperature": ["250"],
        }))
        archive.writestr("Metadata/plate_1.gcode", gcode)
        if second_plate:
            archive.writestr("Metadata/plate_2.gcode", second_plate)
    return path


OBJECT = "; FEATURE: Outer wall\n; Z_HEIGHT: 0.2\n; LAYER_HEIGHT: 0.2\n"


def test_first_object_temperature_excludes_prime_and_support(tmp_path):
    path = sliced(tmp_path, """M83
M109 S205
; FEATURE: Custom
G1 X10 E1 F1200
; FEATURE: Support
G1 X20 E1
""" + OBJECT + "G1 X30 E1\n")
    before = hashlib.sha256(path.read_bytes()).digest()
    report = inspect_sliced_project(path)
    first = report["plates"][0]["first_object_extrusion"]
    assert first["target_temperature_c"] == 205
    assert first["expected_temperature_c"] == 245
    assert first["status"] == "mismatch"
    assert first["feature"] == "Outer wall"
    assert report["status"] == "warning"
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_startup_cleaning_temperature_does_not_trigger_mismatch(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S205\nG1 X10 E1\nM104 S245\n" + OBJECT + "G1 X20 E1 F2400\n")
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "matched"
    assert report["status"] == "observed"


def test_absolute_relative_extrusion_g92_arcs_and_modal_commands(tmp_path):
    path = sliced(tmp_path, "M109 S245\nG90\nM82\nG92 E10\nM204 P800 T9000\n" + OBJECT + """G1 X1 E11 F1200
G1 E10
G1 X2 E10
G92 E0
G2 X3 I0.5 E1
M83
M204 S500
G3 I0.5 J0 E.2 F1800
G0 X4 E.1
G91
G1 X1 E.1
G90
M82
G92 E0
G1 X6 E1
""")
    feature = inspect_sliced_project(path)["plates"][0]["features"]["Outer wall"]
    assert feature["extrusion_moves"] == 6
    assert feature["commanded_feed_mm_s"]["most_common"] == [
        {"value": 30, "moves": 4}, {"value": 20, "moves": 2},
    ]
    assert feature["commanded_print_acceleration_mm_s2"]["most_common"] == [
        {"value": 500, "moves": 4}, {"value": 800, "moves": 2},
    ]


def test_support_layers_do_not_pollute_object_layer_heights(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\n" + OBJECT + """G1 X1 E1 F1200
; FEATURE: Support interface
; Z_HEIGHT: 0.200244
; LAYER_HEIGHT: 0.000244
G1 X2 E1
; Z_HEIGHT: 0.31
; LAYER_HEIGHT: 0.109756
G1 X3 E1
; FEATURE: Inner wall
; Z_HEIGHT: 0.4
; LAYER_HEIGHT: 0.2
G1 X4 E1
""")
    layers = inspect_sliced_project(path)["plates"][0]["object_layers"]
    assert layers == {"count": 2, "z_range_mm": [0.2, 0.4],
                      "nominal_height_counts": [{"height_mm": 0.2, "extrusion_moves": 2}]}


@pytest.mark.parametrize("context", ["M622 J1\nM104 S245\nM623\n", "T1\nM104 S245\n", "M104 S{temperature}\n"])
def test_unsupported_context_is_unknown_not_a_pass(tmp_path, context):
    path = sliced(tmp_path, "M83\nM104 S205\n" + context + OBJECT + "G1 X1 E1 F1200\n")
    report = inspect_sliced_project(path)
    assert report["status"] == "unknown"
    assert report["plates"][0]["first_object_extrusion"]["status"] == "unknown"


def test_small_perimeter_warning_settings_filter_and_multiple_plates(tmp_path):
    gcode = "; small_perimeter_threshold = 0\nM83\nM109 S245\n" + OBJECT + "G1 X1 E1 F1200\n"
    path = sliced(tmp_path, gcode, {"small_perimeter_threshold": ["0"],
                                  "nozzle_temperature_initial_layer": ["245"],
                                  "printhost_password": "private"}, second_plate=gcode)
    report = inspect_sliced_project(path)
    assert len(report["plates"]) == 2
    assert report["issues"][0]["code"] == "small_perimeter_threshold_disabled"
    assert "printhost_password" not in report["archive_settings"]
    assert report["plates"][0]["gcode_settings"]["small_perimeter_threshold"] == "0"


def test_unlabeled_prime_is_not_an_object_and_missing_evidence_is_unknown(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\nG1 X1 E1 F1200\n")
    report = inspect_sliced_project(path)
    assert report["status"] == "unknown"
    assert report["plates"][0]["first_object_extrusion"] is None


def test_multiple_filament_targets_are_not_assumed_to_be_tool_zero(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\n" + OBJECT + "G1 X1 E1 F1200\n",
                  {"nozzle_temperature": ["245", "260"]})
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "unknown"


def test_tool_change_does_not_reuse_previous_tools_temperature(tmp_path):
    path = sliced(tmp_path, "M83\nT1\nM109 S245\nT0\n" + OBJECT + "G1 X1 E1 F1200\n")
    first = inspect_sliced_project(path)["plates"][0]["first_object_extrusion"]
    assert first["target_temperature_c"] is None
    assert first["status"] == "unknown"


def test_compact_gcode_and_inline_comments(tmp_path):
    path = sliced(tmp_path, "M83\nM109S245;M104 S205\n" + OBJECT + "G1X1E.2F1200; prime\n")
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "matched"


def test_missing_gcode_is_unknown(tmp_path):
    path = tmp_path / "empty.3mf"
    with zipfile.ZipFile(path, "w"):
        pass
    assert inspect_sliced_project(path)["issues"][0]["code"] == "missing_gcode"
