import json

import pytest

from bambustudio_mcp.preparation import prepare, quality_guide, validate_context
from bambustudio_mcp.catalog import Catalog
from bambustudio_mcp.service import Service


def context():
    return {"printer_model": "Bambu Lab P1S",
            "firmware_version": "test firmware", "build_plate": "Textured PEI",
            "environment": "Closed enclosure; ambient not measured", "intended_use": "Indoor enclosure with threaded holes",
            "nozzles": [{"diameter_mm": 0.4, "material": "hardened steel", "variant": "standard"}],
            "filaments": [{"material": "PETG", "brand": "ELEGOO", "product": "PETG", "color": "black",
                           "drying": "unknown", "feeding_path": "external spool", "calibration": {"temperature_c": 245, "max_volumetric_speed_mm3_s": 8,
                           "flow_ratio": 0.98, "flow_dynamics": "User reports a saved manual calibration"}}],
            "concerns": ["threads", "visible_walls"]}


def test_context_missing_inputs_and_generic_fallback(service, opened):
    report = service.get_print_guidance(opened["project_id"])
    assert report["input_status"] == "incomplete"
    assert {"printer_model", "nozzles", "filaments", "concerns"} <= set(report["missing_inputs"])
    report = service.record_print_context(opened["project_id"], opened["revision"], context())
    assert report["input_status"] == "declared"
    assert report["context_evidence"] == "user_reported"
    assert {g["id"] for g in report["selected_guides"]} == {"threads", "visible_walls"}
    assert "Generic PETG" in report["filament_plans"][0]["fallback"]
    assert report["filament_plans"][0]["specifications"] == "exact_product_lookup_required"
    reconnected = Service(service.workspace, service.runtime)
    assert reconnected.get_print_guidance(opened["project_id"])["context"] == context()
    assert service.inspect_project(opened["project_id"])["head"] == opened["revision"]


def test_mismatches_and_stale_calibration_are_reported():
    settings = {"printer_model": "Bambu Lab A1", "nozzle_diameter": ["0.6"], "filament_type": ["PLA"],
                "nozzle_temperature": ["250"], "filament_max_volumetric_speed": ["10"],
                "filament_flow_ratio": ["1.0"], "small_perimeter_threshold": ["0"],
                "wall_sequence": "inner-outer-inner wall", "wall_loops": "2"}
    report = prepare(Catalog(), settings, context())
    codes = {w["code"] for w in report["warnings"]}
    assert {"printer_mismatch", "nozzle_mismatch", "material_mismatch", "calibration_setting_changed",
            "small_perimeter_slowdown_disabled", "wall_order_requires_three_walls"} <= codes


def test_physical_observations_do_not_transfer_to_another_revision(service, opened):
    declared = context()
    declared["observations"] = [{"kind": "surface_finish", "status": "passed", "notes": "Physical sample inspected under the same light"}]
    report = service.record_print_context(opened["project_id"], opened["revision"], declared)
    assert report["physical_observations"][0]["status"] == "passed"
    changed = service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 3})
    report = service.get_print_guidance(opened["project_id"], changed["revision"])
    assert report["physical_observations"] == []
    assert "observations_from_other_revision" in {w["code"] for w in report["warnings"]}
    with pytest.raises(ValueError, match="Stale"):
        service.record_print_context(opened["project_id"], opened["revision"], declared)


@pytest.mark.parametrize("invalid", [
    {"surprise": True}, {"printer_model": 3}, {"nozzles": [{"diameter_mm": True}]},
    {"nozzles": [{"diameter_mm": float("nan")}]}, {"nozzles": []}, {"filaments": []},
    {"filaments": [{"calibration": {"flow_ratio": -1}}]},
    {"filaments": [{"specification_url": "file:///etc/passwd"}]},
    {"filaments": [{"specification_url": "https://user:secret@example.com/spec"}]},
    {"concerns": ["invented"]}, {"concerns": ["threads", "threads"]},
    {"observations": [{"kind": "surface_finish", "status": "passed"}]},
    {"observations": [{"kind": "surface_finish", "status": "passed", "notes": "test", "job_id": 1}]},
])
def test_context_validation(invalid):
    with pytest.raises(ValueError):
        validate_context(invalid)


def test_multi_nozzle_context_and_material_assignment_order():
    declared = context()
    declared["printer_model"] = "Bambu Lab H2D"
    declared["nozzles"].append({"diameter_mm": 0.6, "material": "hardened steel", "variant": "high flow"})
    declared["filaments"].append({"material": "PLA", "brand": "unknown", "product": "unknown", "color": "unknown", "drying": "unknown"})
    validate_context(declared)
    report = prepare(Catalog(), {"printer_model": "Bambu Lab H2D", "nozzle_diameter": ["0.4", "0.6"], "filament_type": ["PETG", "PLA"]}, declared)
    assert not {w["code"] for w in report["warnings"]} & {"nozzle_mismatch", "material_mismatch", "filament_assignment_count_mismatch"}
    assert "filaments[1].calibration" in report["missing_inputs"]


def test_calibration_vectors_are_not_assumed_to_be_filament_indexes():
    settings = {"filament_type": ["PETG"], "nozzle_temperature": ["245", "275"],
                "filament_max_volumetric_speed": ["8", "20"], "filament_flow_ratio": ["0.98", "0.98"]}
    report = prepare(Catalog(), settings, context())
    unknown = [w["key"] for w in report["warnings"] if w["code"] == "calibration_mapping_unknown"]
    assert set(unknown) == {"nozzle_temperature", "filament_max_volumetric_speed"}
    assert "calibration_setting_changed" not in {w["code"] for w in report["warnings"]}


def test_guide_keys_and_context_resource_match_runtime():
    from importlib.resources import files
    catalog = Catalog()
    guide = quality_guide()
    for entry in guide["prerequisites"] + [r for category in guide["categories"] for r in category["rules"]]:
        assert entry["sources"] and entry["acceptance_checks"]
        assert all(key in catalog.options for key in entry["keys"])
    for category in guide["categories"]:
        assert all(key in catalog.options for key in category["candidate_keys"])
    schema = json.loads(files("bambustudio_mcp.data").joinpath("print-context.json").read_text())
    validate_context(schema["example"])
