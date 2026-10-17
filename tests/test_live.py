"""Run with BAMBUSTUDIO_EXECUTABLE and BAMBUSTUDIO_PROFILES configured."""
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from bambustudio_mcp.project import Project, children, metadata
from bambustudio_mcp.runtime import Runtime
from bambustudio_mcp.service import Service

pytestmark = pytest.mark.skipif(not os.environ.get("BAMBUSTUDIO_TEST_MODEL"), reason="Live Studio/model paths not configured")


def wait(service, job):
    end = time.monotonic() + 180
    while job["status"] in {"queued", "running"} and time.monotonic() < end:
        time.sleep(.1)
        job = service.get_job(job["job_id"])
    assert job["status"] == "succeeded", job
    return job


def test_real_studio_roundtrip(tmp_path):
    service = Service(tmp_path / "live", Runtime())
    opened = service.open_project(os.environ["BAMBUSTUDIO_TEST_MODEL"], "Bambu Lab P1S 0.4 nozzle",
                                  "0.20mm Standard @BBL X1C", ["Bambu PLA Basic @BBL P1S 0.4 nozzle"])
    pid, revision = opened["project_id"], opened["revision"]
    baseline = wait(service, service.start_slice(pid, revision))
    obj = opened["project"]["objects"][0]
    mesh = opened["project"]["meshes"][0]
    edited = service.apply_settings(pid, revision, {"wall_loops": 4, "sparse_infill_density": 12})
    edited = service.edit_regions(pid, edited["revision"], [
        {"action": "paint", "resource_id": mesh["resource_id"], "face_indices": [0], "kind": "support", "state": "enforce"},
        {"action": "paint", "resource_id": mesh["resource_id"], "face_indices": [1], "kind": "seam", "state": "block"},
        {"action": "modifier_box", "object_id": obj["id"], "bounds_mm": [[-5, -5, 0], [5, 5, 10]], "changes": {"wall_loops": 5}},
        {"action": "height_range", "target": {"object_id": obj["id"], "min_z": 0, "max_z": 5}, "changes": {"sparse_infill_density": 40}},
        {"action": "layer_heights", "object_id": obj["id"], "profile": [[0, 0.2], [10, 0.12], [30, 0.2]]}
    ])
    sliced = wait(service, service.start_slice(pid, edited["revision"]))
    output = Project(Path(sliced["directory"]) / "sliced.3mf")
    assert output.settings["wall_loops"] == "4"
    assert float(output.settings["sparse_infill_density"].rstrip("%")) == 12
    parts = output.describe()["objects"][0]["volumes"]
    modifier = next(p for p in parts if p["subtype"] == "modifier_part")
    assert modifier["metadata"]["wall_loops"] == "5"
    triangles = children(children(output.meshes()[0]["mesh"], "triangles")[0], "triangle")
    assert triangles[0].get("paint_supports") == "4"
    assert triangles[1].get("paint_seam") == "8"
    assert output.height_lines and children(output.ranges, "object")
    np.testing.assert_allclose(output.describe()["meshes"][0]["bounds_mm"], mesh["bounds_mm"], atol=1e-4)
    assert baseline["result"]["sliced_plates"][0]["total_predication"] > 0
    assert sliced["result"]["sliced_plates"][0]["filaments"][0]["total_used_g"] > 0
    report = service.analyze_geometry(pid, edited["revision"])
    assert len(report["meshes"][0]["sampled_thickness"]) > 0
    direct = tmp_path / "direct"
    original = service._directory(pid) / f"{revision}.3mf"
    service.runtime.run(["--slice", "0", "--export-3mf", "direct.3mf", str(original)], direct)
    reference = json.loads((direct / "result.json").read_text())["sliced_plates"][0]
    # Studio varies generated paths between independent runs. Check estimates and material separately.
    assert reference["total_predication"] == pytest.approx(baseline["result"]["sliced_plates"][0]["total_predication"], rel=.01)
    assert reference["filaments"][0]["total_used_g"] == pytest.approx(baseline["result"]["sliced_plates"][0]["filaments"][0]["total_used_g"], rel=.001)
