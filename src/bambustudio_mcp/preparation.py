"""Validate user declarations and select evidence-linked print preparation rules."""
from __future__ import annotations

import json
import math
from importlib.resources import files
from urllib.parse import urlsplit

from .common import finite_number


def quality_guide():
    return json.loads(files("bambustudio_mcp.data").joinpath("quality-guide.json").read_text())


def validate_context(context):
    def fields(value, allowed, name):
        if not isinstance(value, dict) or set(value) - set(allowed):
            raise ValueError(f"{name}: expected an object with fields {', '.join(allowed)}")

    def text(value, name):
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError(f"{name}: expected nonempty text, at most 4000 characters")

    setup_fields = ["printer_model", "firmware_version", "build_plate", "environment", "intended_use"]
    fields(context, setup_fields + ["nozzles", "filaments", "concerns", "observations"], "context")
    for key in setup_fields:
        if key in context:
            text(context[key], key)
    if "nozzles" in context:
        if not isinstance(context["nozzles"], list) or not 1 <= len(context["nozzles"]) <= 16:
            raise ValueError("nozzles must contain 1..16 active nozzle declarations in project order")
        for nozzle in context["nozzles"]:
            fields(nozzle, ["diameter_mm", "material", "variant"], "nozzle")
            if "diameter_mm" in nozzle and finite_number(nozzle["diameter_mm"]) <= 0:
                raise ValueError("nozzle.diameter_mm must be positive")
            for key in ("material", "variant"):
                if key in nozzle:
                    text(nozzle[key], f"nozzle.{key}")
    if "filaments" in context:
        if not isinstance(context["filaments"], list) or not 1 <= len(context["filaments"]) <= 64:
            raise ValueError("filaments must contain 1..64 filament declarations in project assignment order")
        for filament in context["filaments"]:
            fields(filament, ["material", "brand", "product", "color", "drying", "feeding_path", "specification_url", "calibration"], "filament")
            for key, value in filament.items():
                if key != "calibration":
                    text(value, key)
                if key == "specification_url":
                    url = urlsplit(value)
                    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                        raise ValueError("specification_url must be an HTTP(S) URL without credentials")
            calibration = filament.get("calibration", {})
            fields(calibration, ["temperature_c", "max_volumetric_speed_mm3_s", "flow_ratio", "flow_dynamics"], "calibration")
            for key, value in calibration.items():
                if key == "flow_dynamics":
                    text(value, key)
                elif finite_number(value) <= 0:
                    raise ValueError(f"calibration.{key} must be positive")
    ids = {category["id"] for category in quality_guide()["categories"]}
    if "concerns" in context:
        concerns = context["concerns"]
        if not isinstance(concerns, list) or not concerns or any(not isinstance(c, str) or c not in ids for c in concerns):
            raise ValueError("concerns must select categories from bambustudio://quality-guide")
        if len(concerns) != len(set(concerns)):
            raise ValueError("concerns must not contain duplicates")
    observations = context.get("observations", [])
    if not isinstance(observations, list) or len(observations) > 50:
        raise ValueError("observations must be an array with at most 50 entries")
    for observation in observations:
        fields(observation, ["kind", "status", "notes", "job_id"], "observation")
        if observation.get("kind") not in {"thread_fit", "surface_finish", "strength"}:
            raise ValueError("observation.kind must be thread_fit, surface_finish or strength")
        if observation.get("status") not in {"passed", "failed", "unverified"}:
            raise ValueError("observation.status must be passed, failed or unverified")
        text(observation.get("notes"), "observation.notes")
        if "job_id" in observation:
            from .common import identifier
            text(observation["job_id"], "observation.job_id")
            identifier(observation["job_id"])
    return context


def prepare(catalog, settings, context):
    guide = quality_guide()
    missing, warnings, plans = [], [], []
    unknown = {"unknown", "unspecified"}
    for key in ("printer_model", "firmware_version", "build_plate", "environment", "intended_use", "nozzles", "filaments", "concerns"):
        if key not in context or (isinstance(context[key], str) and context[key].strip().lower() in unknown):
            missing.append(key)
    configured_printer = settings.get("printer_model")
    if context.get("printer_model") and configured_printer and context["printer_model"] != configured_printer:
        warnings.append({"code": "printer_mismatch", "declared": context["printer_model"], "configured": configured_printer})
    nozzles = catalog.decode("nozzle_diameter", settings.get("nozzle_diameter"))
    declared = context.get("nozzles", [])
    for index, nozzle in enumerate(declared):
        for key in ("diameter_mm", "material", "variant"):
            if key not in nozzle or (isinstance(nozzle[key], str) and nozzle[key].strip().lower() in unknown):
                missing.append(f"nozzles[{index}].{key}")
    if declared and nozzles:
        if len(declared) != len(nozzles) or any(n.get("diameter_mm") is not None and (size is None or not math.isclose(n["diameter_mm"], size, rel_tol=1e-6)) for n, size in zip(declared, nozzles)):
            warnings.append({"code": "nozzle_mismatch", "declared_mm": [n.get("diameter_mm") for n in declared], "configured_mm": nozzles,
                             "action": "Confirm every active nozzle before a recommendation."})
    assigned = settings.get("filament_type", settings.get("filament_settings_id", []))
    if isinstance(assigned, list) and assigned and context.get("filaments") and len(assigned) != len(context["filaments"]):
        warnings.append({"code": "filament_assignment_count_mismatch", "declared": len(context["filaments"]), "configured": len(assigned)})
    for index, filament in enumerate(context.get("filaments", [])):
        prefix = f"filaments[{index}]"
        for key in ("material", "brand", "product", "color", "drying", "feeding_path", "calibration"):
            if key not in filament or (key == "material" and filament[key].strip().lower() in unknown):
                missing.append(f"{prefix}.{key}")
        material = filament.get("material", "")
        if isinstance(assigned, list) and index < len(assigned) and "filament_type" in settings and material:
            if material.upper() != str(assigned[index]).upper():
                warnings.append({"code": "material_mismatch", "index": index, "declared": material, "configured": assigned[index]})
        plans.append({"filament_index": index,
                      "specifications": "user_supplied_reference" if filament.get("specification_url") else "exact_product_lookup_required",
                      "search_query": " ".join(filament.get(key, "") for key in ("brand", "product", "material")).strip(),
                      "fallback": f"Select a compatible Generic {material} profile with list_profiles if exact specifications cannot be verified." if material and material.lower() not in unknown else "Confirm the material before selecting a Generic profile.",
                      "evidence": "User declarations and references are not independently verified."})
        calibration = filament.get("calibration", {})
        for key in ("temperature_c", "max_volumetric_speed_mm3_s", "flow_ratio", "flow_dynamics"):
            if key not in calibration:
                missing.append(f"{prefix}.calibration.{key}")
        for measured, option in [("temperature_c", "nozzle_temperature"), ("max_volumetric_speed_mm3_s", "filament_max_volumetric_speed"), ("flow_ratio", "filament_flow_ratio")]:
            values = catalog.decode(option, settings.get(option))
            if measured in calibration and isinstance(values, list) and values:
                # shortcut: variant rows are not filament indexes; resolve full Studio mapping before comparing multiple assignments.
                unambiguous = len(context.get("filaments", [])) == 1 and len(assigned) == 1 and all(v is not None and v == values[0] for v in values)
                if not unambiguous:
                    warnings.append({"code": "calibration_mapping_unknown", "index": index, "key": option,
                                     "candidates": values, "action": "Confirm the filament, extruder and nozzle-variant mapping before comparing calibration."})
                elif not math.isclose(calibration[measured], values[0], rel_tol=1e-6):
                    warnings.append({"code": "calibration_setting_changed", "index": index, "key": option,
                                     "calibrated": calibration[measured], "configured": values[0],
                                     "action": "Review dependent calibrations before reusing the result."})
    selected = [c for c in guide["categories"] if c["id"] in context.get("concerns", [])]
    if "threads" in context.get("concerns", []):
        thresholds = catalog.decode("small_perimeter_threshold", settings.get("small_perimeter_threshold"))
        if thresholds and any(value == 0 for value in thresholds):
            warnings.append({"code": "small_perimeter_slowdown_disabled", "action": "Use a calibrated threshold and verify the emitted critical contours."})
    if settings.get("wall_sequence") == "inner-outer-inner wall" and int(settings.get("wall_loops", 0)) < 3:
        warnings.append({"code": "wall_order_requires_three_walls"})
    if any(catalog.decode("enable_pressure_advance", settings.get("enable_pressure_advance")) or []):
        if str(configured_printer or context.get("printer_model", "")).startswith("Bambu Lab"):
            warnings.append({"code": "generic_pressure_advance_field_not_supported", "action": "Use the calibration procedure supported by the actual Bambu printer."})
    relevant = {key for category in selected for key in category["candidate_keys"]}
    return {"input_status": "incomplete" if missing else "declared", "missing_inputs": missing,
            "questions": [{"field": key, "question": f"Confirm {key} for the actual print setup. Leave unavailable calibration measurements unset."} for key in missing],
            "warnings": warnings, "filament_plans": plans, "prerequisites": guide["prerequisites"],
            "current_settings": {key: {"value": catalog.decode(key, settings[key]), "scopes": catalog.options[key]["scopes"]}
                                 for key in sorted(relevant) if key in settings},
            "available_concerns": [{"id": c["id"], "label": c["label"], "observation_needed": c["observation_needed"]} for c in guide["categories"]],
            "selected_guides": selected,
            "physical_validation": "User observations only. No automatic strength or finish verification.",
            "actions": "Resolve missing inputs and mismatches, inspect slice output, and compare a matched physical sample before a full print."}
