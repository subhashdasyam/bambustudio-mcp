import hashlib
import json
import zipfile

import pytest

from bambustudio_mcp.catalog import Catalog
from bambustudio_mcp.project import Project, children, SETTINGS


def test_atomic_revision_preserves_original(service, opened, project_file):
    before = hashlib.sha256(project_file.read_bytes()).hexdigest()
    changed = service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 4, "sparse_infill_density": 12})
    assert hashlib.sha256(project_file.read_bytes()).hexdigest() == before
    project = Project(changed["path"])
    assert project.settings["wall_loops"] == "4"
    assert project.entries["Metadata/custom.bin"] == b"preserve\x00me"
    assert project.entries["custom/texture.png"] == b"preserve png"
    assert "Metadata/plate_1.gcode" not in project.entries
    assert "Metadata/plate_1.png" not in project.entries
    with pytest.raises(ValueError, match="Stale"):
        service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 5})


def test_invalid_batch_does_not_commit(service, opened):
    with pytest.raises(ValueError):
        service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 5, "layer_height": -1})
    assert service.inspect_project(opened["project_id"])["head"] == opened["revision"]


@pytest.mark.parametrize("key,value", [("wall_loops", True), ("wall_loops", 1.1), ("wall_loops", -1),
    ("layer_height", float('nan')), ("nozzle_diameter", 0.4), ("support_type", "invented"),
    ("sparse_infill_density", 101), ("enable_support", "true"), ("not_a_setting", 1)])
def test_validation(key, value):
    with pytest.raises(ValueError):
        Catalog().validate(key, value)


def test_local_overrides_and_filament(service, opened):
    pid = opened["project_id"]
    a = service.apply_settings(pid, opened["revision"], {"wall_loops": 6}, "object", {"object_id": "2"})
    b = service.apply_settings(pid, a["revision"], {"wall_loops": 8}, "volume", {"object_id": "2", "volume_id": "1"})
    values = service.get_settings(pid, b["revision"], "volume", {"object_id": "2", "volume_id": "1"}, ["wall_loops"])
    assert values["settings"]["wall_loops"] == {"value": 8, "origin": "volume"}
    c = service.apply_settings(pid, b["revision"], {"filament_density": 1.3}, "filament", {"filament_index": 0})
    assert Project(c["path"]).settings["filament_density"] == ["1.3"]
    with pytest.raises(ValueError):
        service.apply_settings(pid, c["revision"], {"nozzle_diameter": [0.6]}, "volume", {"object_id": "2", "volume_id": "1"})


def test_spiral_mode_conflicts(service, opened):
    with pytest.raises(ValueError, match="Spiral"):
        service.apply_settings(opened["project_id"], opened["revision"], {"spiral_mode": True})


def test_archive_traversal_and_duplicate(tmp_path):
    for names in [["../escape"], ["duplicate", "duplicate"]]:
        path = tmp_path / "bad.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            for name in names:
                archive.writestr(name, b"bad")
        with pytest.raises(ValueError, match="Unsafe"):
            Project(path)


def test_xml_entities_rejected(tmp_path):
    path = tmp_path / "entity.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", '<!DOCTYPE model [<!ENTITY x SYSTEM "file:///etc/passwd">]><model>&x;</model>')
    with pytest.raises(Exception, match="EntitiesForbidden"):
        Project(path)


def test_spatial_controls_roundtrip(service, opened):
    a = service.edit_regions(opened["project_id"], opened["revision"], [
        {"action": "paint", "resource_id": "3D/3dmodel.model#1", "face_indices": [0], "kind": "support", "state": "enforce"},
        {"action": "paint", "resource_id": "3D/3dmodel.model#1", "face_indices": [1], "kind": "seam", "state": "block"},
        {"action": "height_range", "target": {"object_id": "2", "min_z": 0, "max_z": 5}, "changes": {"wall_loops": 4}},
        {"action": "layer_heights", "object_id": "2", "profile": [[0, 0.2], [10, 0.12]]},
        {"action": "modifier_box", "object_id": "2", "bounds_mm": [[0, 0, 0], [5, 5, 5]], "changes": {"sparse_infill_density": 40}},
        {"action": "rotate", "instance": 0, "degrees_xyz": [90, 0, 0]}
    ])
    p = Project(a["path"])
    meshes = p.meshes()
    assert len(meshes) == 2
    assert min(m["vertices"][:, 2].min() for m in meshes) == pytest.approx(0)
    triangles = children(children(meshes[0]["mesh"], "triangles")[0], "triangle")
    assert triangles[0].get("paint_supports") == "4"
    assert triangles[1].get("paint_seam") == "8"
    assert p.height_lines == ["object_id=1|0;0.2;10;0.12"]
    assert children(children(p.ranges, "object")[0], "range")[0].get("max_z") == "5"
    modifier_id = p.describe()["objects"][0]["volumes"][-1]["id"]
    b = service.edit_regions(opened["project_id"], a["revision"], [{"action": "remove_modifier", "object_id": "2", "volume_id": modifier_id}])
    assert len(Project(b["path"]).meshes()) == 1


def test_geometry(service, opened):
    report = service.analyze_geometry(opened["project_id"])
    mesh = report["meshes"][0]
    assert mesh["watertight"]
    assert mesh["volume_mm3"] == pytest.approx(1000 / 6)
    assert mesh["connected_components"] == 1
    assert mesh["sampled_thickness"]
    assert mesh["mechanical_strength"].startswith("unknown")


def test_export_refuses_overwrite(service, opened, tmp_path):
    path = tmp_path / "result.3mf"
    service.export_project(opened["project_id"], opened["revision"], str(path))
    with pytest.raises(FileExistsError):
        service.export_project(opened["project_id"], opened["revision"], str(path))


def test_restore_revision(service, opened):
    changed = service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 5})
    restored = service.restore_revision(opened["project_id"], changed["revision"], opened["revision"])
    assert Project(restored["path"]).settings["wall_loops"] == "2"
    assert Project(changed["path"]).settings["wall_loops"] == "5"


def test_point_and_group_serialization():
    c = Catalog()
    assert c.decode("best_object_pos", "0.5,0.5") == [.5, .5]
    assert c.encode("best_object_pos", [.5, .5]) == "0.5,0.5"
    groups = [[[0, 0], [10, 0]], [[20, 20], [30, 20]]]
    encoded = c.encode("extruder_printable_area", groups, json_format=False)
    assert encoded == "0x0,10x0#20x20,30x20"
    assert c.decode("extruder_printable_area", encoded) == groups
    assert c.decode("extruder_printable_area", c.encode("extruder_printable_area", groups)) == groups


def test_serialized_enum_distinction():
    c = Catalog()
    assert c.validate("overhang_threshold_participating_cooling", ["95%"]) == ["95%"]
    with pytest.raises(ValueError):
        c.validate("overhang_threshold_participating_cooling", ["100%"])


def test_printer_credentials_not_returned(project_file, service):
    project = Project(project_file)
    project.settings["printhost_password"] = "test-secret"
    project.dirty_settings = True
    saved = project_file.parent / "credentials.3mf"
    project.save(saved)
    opened = service.open_project(str(saved))
    assert "printhost_password" not in service.get_settings(opened["project_id"])["settings"]


def test_unknown_runtime_version_blocks_writes(service, opened, fake_runtime):
    fake_runtime.executable.write_text('#!/bin/sh\nprintf "BambuStudio-99.0.0:\\n"\n')
    with pytest.raises(ValueError, match="not compatible"):
        service.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 3})


def test_runtime_probe_does_not_write_to_caller(fake_runtime, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert fake_runtime.environment()["compatible"]
    assert not (tmp_path / "result.json").exists()
