"""Client summaries; complete measurements and paths stay in the saved reports."""
from __future__ import annotations

import copy


def _client_numbers(value):
    if isinstance(value,float):
        return round(value,6)
    if isinstance(value,dict):
        return {k:_client_numbers(v) for k,v in value.items()}
    if isinstance(value,list):
        return [_client_numbers(v) for v in value]
    return value


def geometry_summary(report, path):
    result = copy.deepcopy({k:v for k,v in report.items() if k not in {"issues", "meshes"}})
    fixes, guidance, references = {}, {}, {}
    issues = []
    for original in report["issues"]:
        issue = {k:v for k,v in original.items() if k not in {
            "clearance_regions", "highlight_svg", "slicer_fix", "why_it_fails", "design_fix", "top_samples_mm", "ceiling_regions"}}
        for field in ("clearance_regions", "top_samples_mm", "ceiling_regions"):
            if field in original:
                issue[field.removesuffix("s")+"_count"] = len(original[field])
        text = (original.get("why_it_fails"), original.get("design_fix"))
        if text not in references:
            key = f"g{len(references)+1}"
            references[text] = key
            guidance[key] = dict(zip(("why_it_fails", "design_fix"), text))
        issue["guidance_ref"] = references[text]
        issue["slicer_fix"] = []
        for fix in original.get("slicer_fix", []):
            key = fix.get("setting") or "edit_regions:"+fix["edit_regions"]
            if key in fixes and fixes[key]!=fix:
                key=next((name for name,value in fixes.items() if value==fix),key+f":{len(fixes)+1}")
            fixes.setdefault(key, fix)
            issue["slicer_fix"].append(key)
        issues.append(issue)
    meshes=[]
    for original in report.get("meshes", []):
        mesh={k:v for k,v in original.items() if k not in {"sampled_thickness", "overhang_face_indices"}}
        samples=original.get("sampled_thickness", [])
        mesh["sampled_thickness_count"]=len(samples)
        mesh["minimum_sampled_thickness_mm"]=min((s["thickness_mm"] for s in samples),default=None)
        mesh["overhang_face_count"]=len(original.get("overhang_face_indices", []))
        meshes.append(mesh)
    return _client_numbers({**result, "issues":issues, "meshes":meshes, "slicer_fixes":fixes, "issue_guidance":guidance,
            "highlight_anchor":"Append #<issue id> to a highlighted preview path.", "report_path":str(path)})


def inspection_summary(report, path):
    result=copy.deepcopy(report)
    for plate in result.get("plates", []):
        comparison=plate.get("geometry_comparison", {})
        for feature in comparison.get("feature_paths", {}).values():
            if "segments" in feature:
                feature["saved_segment_count"]=len(feature.pop("segments"))
        for observation in comparison.get("observations", []):
            if "examples" in observation:
                observation["saved_example_count"]=len(observation.pop("examples"))
        if "unmapped_arc_examples" in comparison:
            comparison["unmapped_arc_example_count"]=len(comparison.pop("unmapped_arc_examples"))
    result["report_path"]=str(path)
    return _client_numbers(result)
