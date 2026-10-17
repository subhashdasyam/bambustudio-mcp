"""Reproducible live evidence without private models or identifiers in the report."""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from bambustudio_mcp.common import atomic_json
from bambustudio_mcp.project import Project, children
from bambustudio_mcp.runtime import Runtime
from bambustudio_mcp.service import Service


def wait(service, job):
    deadline = time.monotonic() + 180
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(.1)
        job = service.get_job(job["job_id"])
    if job["status"] != "succeeded":
        raise RuntimeError(job)
    return job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    service = Service(args.workspace)
    opened = service.open_project(args.model, "Bambu Lab P1S 0.4 nozzle", "0.20mm Standard @BBL X1C",
                                  ["Bambu PLA Basic @BBL P1S 0.4 nozzle"])
    pid, revision = opened["project_id"], opened["revision"]
    baseline = wait(service, service.start_slice(pid, revision))
    source = Project(service._directory(pid) / f"{revision}.3mf")
    exported = Project(Path(baseline["directory"]) / "sliced.3mf")
    unchanged, normalized, absent = [], [], []
    for key, value in source.settings.items():
        if key not in service.catalog.options:
            continue
        if key not in exported.settings:
            absent.append(key)
        elif service.catalog.decode(key, value) == service.catalog.decode(key, exported.settings[key]):
            unchanged.append(key)
        else:
            normalized.append(key)
    obj, mesh = opened["project"]["objects"][0], opened["project"]["meshes"][0]
    edited = service.apply_settings(pid, revision, {"wall_loops": 4, "sparse_infill_density": 12})
    edited = service.edit_regions(pid, edited["revision"], [
        {"action": "paint", "resource_id": mesh["resource_id"], "face_indices": [0], "kind": "support", "state": "enforce"},
        {"action": "paint", "resource_id": mesh["resource_id"], "face_indices": [1], "kind": "seam", "state": "block"},
        {"action": "modifier_box", "object_id": obj["id"], "bounds_mm": [[-5,-5,0],[5,5,10]], "changes": {"wall_loops": 5}},
        {"action": "height_range", "target": {"object_id": obj["id"], "min_z": 0, "max_z": 5}, "changes": {"sparse_infill_density": 40}},
        {"action": "layer_heights", "object_id": obj["id"], "profile": [[0,.2],[10,.12],[30,.2]]}])
    finished = wait(service, service.start_slice(pid, edited["revision"]))
    final = Project(Path(finished["directory"]) / "sliced.3mf")
    modifiers = [p for p in final.describe()["objects"][0]["volumes"] if p["subtype"] == "modifier_part"]
    triangles = children(children(final.meshes()[0]["mesh"], "triangles")[0], "triangle")
    checks = {"wall_loops": final.settings["wall_loops"] == "4",
              "modifier_preserved": len(modifiers) == 1 and modifiers[0]["metadata"].get("wall_loops") == "5",
              "support_paint_preserved": triangles[0].get("paint_supports") == "4",
              "seam_paint_preserved": triangles[1].get("paint_seam") == "8",
              "layer_profile_preserved": bool(final.height_lines),
              "height_range_preserved": bool(children(final.ranges, "object"))}
    assert all(checks.values()), checks
    comparison_job = asyncio.run(service.compare_candidates(pid, revision, [{"name":"extra_walls", "changes":{"wall_loops":4}}]))
    comparison = wait(service, comparison_job)["result"]
    environment = service.runtime.environment()
    evidence = {"studio_version": environment["version"], "binary_sha256": environment["binary_sha256"],
                "source_revision": service.catalog.data["source_revision"], "catalog_options": len(service.catalog.options),
                "baseline_roundtrip": {"unchanged": unchanged, "normalized": normalized, "absent": absent},
                "spatial_checks": checks, "comparison": [{k:v for k,v in c.items() if k not in {"revision","job_id"}}
                                                          for c in comparison["candidates"]],
                "physical_strength_validated": False, "physical_finish_validated": False}
    atomic_json(Path(args.output), evidence)
    print(json.dumps({"roundtrip_unchanged":len(unchanged),"roundtrip_normalized":len(normalized),"absent":absent,"checks":checks},indent=2))


if __name__ == "__main__":
    main()
