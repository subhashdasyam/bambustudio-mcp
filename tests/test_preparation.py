import json

import pytest

from bambustudio_mcp.preparation import prepare, quality_guide, validate_context
from bambustudio_mcp.catalog import Catalog
from bambustudio_mcp.service import Service


def context():
    return {"printer_model": "Bambu Lab P1S",
            "moving_parts":"none","hinges":"none","fit_requirements":"Threaded holes","cosmetic_faces":[],"load_direction":"none",
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
    assert set(schema["fields"]["concerns"]) == {c["id"] for c in guide["categories"]}


@pytest.mark.parametrize("printer,count", [("P1S", 1), ("A1", 1), ("X1 Carbon", 1), ("P2S", 1), ("H2D", 2), ("X2D", 2), ("H2C", 2)])
def test_support_paths_match_confirmed_nozzles(printer, count):
    declared = context()
    declared["printer_model"] = f"Bambu Lab {printer}"
    declared["nozzles"] = declared["nozzles"] * count
    declared["concerns"] = ["support_contacts"]
    report = prepare(Catalog(), {"printer_model": declared["printer_model"], "nozzle_diameter": ["0.4"] * count}, declared)
    paths = {p["path"]: p["status"] for p in report["support_paths"]}
    assert paths["separate_support_nozzle"] == ("candidate" if count > 1 else "hardware_required")
    assert paths["single_nozzle_material_changes"] == ("candidate" if count == 1 else "confirm_hardware")


def test_unknown_or_mismatched_nozzles_do_not_confirm_support_hardware():
    declared = context()
    declared["concerns"] = ["support_contacts"]
    for diameters in (None, ["0.4", "0.4"]):
        report = prepare(Catalog(), {"nozzle_diameter": diameters}, declared)
        assert report["support_paths"][2]["status"] == "confirm_hardware"


@pytest.mark.parametrize("height,invalid", [(0.8, True), (0.6, True), (0.4, True), (0.1, False), (0.04, True)])
def test_layer_height_examples_obey_actual_nozzle_limits(height, invalid):
    settings = {"layer_height": str(height), "min_layer_height": ["0.08"], "max_layer_height": ["0.28"]}
    report = prepare(Catalog(), settings, context())
    assert ("layer_height_outside_nozzle_limits" in {w["code"] for w in report["warnings"]}) == invalid


def test_new_remedies_are_reachable_for_p1s_and_other_models():
    declared = context()
    declared["concerns"] = ["visible_walls", "fine_detail", "tops", "flexible_feed", "support_contacts"]
    validate_context(declared)
    for model in ("P1S", "A1 mini", "X2D"):
        declared["printer_model"] = f"Bambu Lab {model}"
        report = prepare(Catalog(), {}, declared)
        ids = {r["id"] for c in report["selected_guides"] for r in c["rules"]}
        assert {"upright_curved_surface", "fine_layers_and_nozzle", "adaptive_curved_tops",
                "low_resistance_flexible_feed", "separate_support_material"} <= ids


def test_missing_design_intent_questions_and_cosmetic_region_validation():
    report=prepare(Catalog(),{}, {})
    questions={q['field']:q['question'] for q in report['questions']}
    assert {'moving_parts','hinges','fit_requirements','cosmetic_faces','load_direction'}<=questions.keys()
    assert report['requirement_guides']['hinges']==['print_in_place','holes_lying_flat','bed_contact']
    validate_context({'cosmetic_faces':[{'name':'top','bounds_mm':[[0,0,2],[2,2,2]],'avoid_seams':True,'ironing':True}],
                      'elephant_foot_expansion_mm':.1})
    for bad in ({'cosmetic_faces':[{'name':'x','bounds_mm':[[0,0,0],[1,1,float('nan')]]}]},
                {'cosmetic_faces':[{'name':'x','bounds_mm':[[1,0,0],[0,1,1]]}]},
                {'cosmetic_faces':[{'name':'x','bounds_mm':[[0,0,0],[1,1,1]],'ironing':'yes'}]},
                {'elephant_foot_expansion_mm':-.1}):
        with pytest.raises(ValueError):validate_context(bad)
