from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np
from defusedxml.ElementTree import fromstring

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
ET.register_namespace("", CORE)
ET.register_namespace("p", PRODUCTION)
SETTINGS = "Metadata/project_settings.config"
MODEL_CONFIG = "Metadata/model_settings.config"
HEIGHTS = "Metadata/layer_heights_profile.txt"
RANGES = "Metadata/layer_config_ranges.xml"
STALE = (".gcode", ".gcode.md5", ".png")


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def children(element, name):
    return [child for child in element if local(child) == name]


def metadata(element):
    return {e.get("key"): e.get("value", e.text or "") for e in children(element, "metadata") if e.get("key")}


def set_metadata(element, key, value):
    existing = [e for e in children(element, "metadata") if e.get("key") == key]
    for e in existing:
        element.remove(e)
    if value is not None:
        ET.SubElement(element, "metadata", key=key, value=value)


def matrix(text=None):
    result = np.eye(4)
    if text:
        values = [float(n) for n in text.split()]
        if len(values) != 12 or not np.isfinite(values).all():
            raise ValueError("Invalid 3MF transform")
        result[:3, :] = np.array(values).reshape(4, 3).T
    return result


def matrix_text(value):
    return " ".join(format(float(n), ".12g") for n in value[:3, :].T.flat)


class Project:
    def __init__(self, path: Path):
        self.entries = {}
        self.infos = {}
        with zipfile.ZipFile(path) as archive:
            total = 0
            for info in archive.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                pure = PurePosixPath(name)
                if name.startswith("/") or ".." in pure.parts or "\\" in name or name in self.entries:
                    raise ValueError("Unsafe or duplicate project archive entry")
                total += info.file_size
                if total > 1024 ** 3 or info.file_size > 512 * 1024 ** 2:
                    raise ValueError("Project archive exceeds the 1 GiB uncompressed / 512 MiB entry limit")
                self.entries[name] = archive.read(info)
                self.infos[name] = info
        self.models = {name: fromstring(data) for name, data in self.entries.items() if name.endswith(".model")}
        if "3D/3dmodel.model" not in self.models:
            raise ValueError("Project has no main 3MF model")
        self.main = self.models["3D/3dmodel.model"]
        if self.main.get("unit", "millimeter") != "millimeter":
            raise ValueError("Normalize this project's units to millimetres in Studio before editing")
        self.settings = json.loads(self.entries.get(SETTINGS, b"{}"))
        if not isinstance(self.settings, dict):
            raise ValueError("Project settings must be a JSON object")
        self.config = fromstring(self.entries.get(MODEL_CONFIG, b"<config/>"))
        self.ranges = fromstring(self.entries.get(RANGES, b"<objects/>"))
        self.dirty_models = set()
        self.dirty_config = False
        self.dirty_ranges = False
        self.dirty_settings = False
        self.dirty_heights = False
        self.height_lines = self.entries.get(HEIGHTS, b"").decode().splitlines()
        self._check_references()

    def _resources(self, path):
        return {obj.get("id"): obj for r in children(self.models[path], "resources") for obj in children(r, "object")}

    def _check_references(self):
        self.meshes()  # Reject invalid component references and cycles before any write.

    def meshes(self):
        meshes = []

        def visit(path, oid, transform, chain, instance, role="normal_part"):
            key = f"{path}#{oid}"
            if key in chain or len(chain) > 64:
                raise ValueError("Cyclic or excessively nested 3MF components")
            try:
                obj = self._resources(path)[oid]
            except KeyError:
                raise ValueError(f"Missing component resource {key}") from None
            for mesh in children(obj, "mesh"):
                vertices = np.array([[float(v.get(a, "0")) for a in "xyz"]
                                     for container in children(mesh, "vertices") for v in children(container, "vertex")])
                faces = np.array([[int(t.get(a)) for a in ["v1", "v2", "v3"]]
                                  for container in children(mesh, "triangles") for t in children(container, "triangle")], dtype=int)
                if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
                    raise ValueError("Invalid mesh vertices")
                if faces.ndim != 2 or faces.shape[1] != 3 or (faces < 0).any() or (faces >= len(vertices)).any():
                    raise ValueError("Invalid mesh triangles")
                world = np.c_[vertices, np.ones(len(vertices))] @ transform.T
                meshes.append({"resource_id": key, "instance": instance, "vertices": world[:, :3],
                               "faces": faces, "transform": transform, "mesh": mesh, "role": role})
            for container in children(obj, "components"):
                for component in children(container, "component"):
                    subpath = component.get(f"{{{PRODUCTION}}}path", path).lstrip("/")
                    if subpath not in self.models:
                        raise ValueError(f"Missing model path {subpath}")
                    config = next((o for o in children(self.config, "object") if o.get("id") == oid), None) if path == "3D/3dmodel.model" else None
                    part = next((p for p in children(config, "part") if p.get("id") == component.get("objectid")), None) if config is not None else None
                    subrole = part.get("subtype", "normal_part") if part is not None else role
                    visit(subpath, component.get("objectid"), transform @ matrix(component.get("transform")), chain | {key}, instance, subrole)

        build = children(self.main, "build")
        for index, item in enumerate(children(build[0], "item") if build else []):
            visit("3D/3dmodel.model", item.get("objectid"), matrix(item.get("transform")), set(), index)
        return meshes

    def describe(self):
        return {"objects": [{"id": obj.get("id"), "metadata": metadata(obj),
                             "volumes": [{"id": p.get("id"), "subtype": p.get("subtype"), "metadata": metadata(p)}
                                         for p in children(obj, "part")]} for obj in children(self.config, "object")],
                "plates": [{"index": i + 1, "metadata": metadata(p)} for i, p in enumerate(children(self.config, "plate"))],
                "meshes": [{"resource_id": m["resource_id"], "instance": m["instance"], "role": m["role"], "triangles": len(m["faces"]),
                            "bounds_mm": [m["vertices"].min(axis=0).tolist(), m["vertices"].max(axis=0).tolist()]}
                           for m in self.meshes()],
                "configuration_keys": sorted(self.settings), "archive_entries": len(self.entries)}

    def target(self, scope, target):
        if scope == "plate":
            index = target.get("plate_index")
            plates = children(self.config, "plate")
            if not isinstance(index, int) or not 1 <= index <= len(plates):
                raise ValueError("Select an existing 1-based plate_index")
            return plates[index - 1]
        objects = children(self.config, "object")
        selected = [o for o in objects if o.get("id") == str(target.get("object_id"))]
        if len(selected) != 1:
            raise ValueError("Select an existing object_id from inspect_project")
        obj = selected[0]
        if scope == "object":
            return obj
        if scope == "volume":
            parts = [p for p in children(obj, "part") if p.get("id") == str(target.get("volume_id"))]
            if len(parts) != 1:
                raise ValueError("Select an existing volume_id")
            return parts[0]
        if scope == "height_range":
            zmin, zmax = target.get("min_z"), target.get("max_z")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (zmin, zmax)) or not 0 <= zmin < zmax:
                raise ValueError("Height range requires 0 <= min_z < max_z in millimetres")
            ordinal = objects.index(obj) + 1
            ro = next((o for o in children(self.ranges, "object") if o.get("id") == str(ordinal)), None)
            if ro is None:
                ro = ET.SubElement(self.ranges, "object", id=str(ordinal))
            for region in children(ro, "range"):
                lo, hi = float(region.get("min_z")), float(region.get("max_z"))
                if (lo, hi) == (zmin, zmax):
                    return region
                if max(lo, zmin) < min(hi, zmax):
                    raise ValueError("Height ranges cannot overlap")
            return ET.SubElement(ro, "range", min_z=str(zmin), max_z=str(zmax))
        raise ValueError("Unsupported configuration scope")

    def get_settings(self, catalog, scope="project", target=None):
        target = target or {}
        result = {}
        for key, definition in catalog.options.items():
            if "default" in definition and definition["scopes"]:
                result[key] = {"value": catalog.decode(key, definition["default"]), "origin": "default"}
        for key, value in self.settings.items():
            if key in catalog.options and not catalog.options[key]["scopes"]:
                continue
            result[key] = {"value": catalog.decode(key, value), "origin": "project"}
        if scope == "filament":
            index = target.get("filament_index")
            if not isinstance(index, int) or index < 0:
                raise ValueError("filament_index must be a nonnegative, zero-based index")
            return {k: dict(v, value=v["value"][index]) for k, v in result.items()
                    if k in catalog.options and "filament" in catalog.options[k]["scopes"]
                    and isinstance(v["value"], list) and index < len(v["value"])}
        if scope != "project":
            if scope in {"volume", "height_range"}:
                result.update({k: {"value": catalog.decode(k, v), "origin": "object"}
                               for k, v in metadata(self.target("object", target)).items() if k in catalog.options})
            element = self.target(scope, target)
            overrides = {e.get("opt_key"): e.text for e in children(element, "option")} if scope == "height_range" else metadata(element)
            plate_alias = {"bed_type": "curr_bed_type"}
            for key, value in overrides.items():
                key = plate_alias.get(key, key)
                if key in catalog.options:
                    result[key] = {"value": catalog.decode(key, value), "origin": scope}
        return result

    def apply(self, catalog, changes, scope="project", target=None):
        target = target or {}
        effective = self.get_settings(catalog, scope, target)
        diff = []
        for key, value in changes.items():
            if scope == "filament":
                old = catalog.decode(key, self.settings.get(key, catalog.options.get(key, {}).get("default")))
                index = target["filament_index"]
                if not isinstance(old, list) or index >= len(old):
                    raise ValueError(f"{key}: filament_index outside the existing assignment")
                combined = list(old)
                combined[index] = value
                catalog.validate(key, combined, scope)
                self.settings[key] = catalog.encode(key, combined)
                self.dirty_settings = True
            else:
                catalog.validate(key, value, scope)
                if scope == "project":
                    self.settings[key] = catalog.encode(key, value)
                    self.dirty_settings = True
                else:
                    element = self.target(scope, target)
                    encoded = catalog.encode(key, value, json_format=False)
                    if scope == "height_range":
                        for e in children(element, "option"):
                            if e.get("opt_key") == key:
                                element.remove(e)
                        ET.SubElement(element, "option", opt_key=key).text = encoded
                        self.dirty_ranges = True
                    else:
                        if scope == "plate":
                            encoded = ("true" if value else "false") if isinstance(value, bool) else encoded
                            if key in {"first_layer_print_sequence", "other_layers_print_sequence"}:
                                encoded = " ".join(map(str, value))
                        set_metadata(element, "bed_type" if key == "curr_bed_type" and scope == "plate" else key, encoded)
                        self.dirty_config = True
            diff.append({"key": key, "before": effective.get(key), "requested": value, "scope": scope})
        merged = {k: v["value"] for k, v in self.get_settings(catalog, scope, target).items()}
        catalog.validate_combination(merged)
        return diff

    def rotate(self, instance, degrees):
        if not isinstance(instance, int) or len(degrees) != 3 or not np.isfinite(degrees).all():
            raise ValueError("Rotation requires instance index and three finite XYZ angles in degrees")
        items = children(children(self.main, "build")[0], "item")
        if not 0 <= instance < len(items):
            raise ValueError("Invalid instance index")
        value = matrix(items[instance].get("transform"))
        rotation = np.eye(4)
        for axis, angle in enumerate(np.radians(degrees)):
            c, s = math.cos(angle), math.sin(angle)
            r = np.eye(4)
            a, b = [i for i in range(3) if i != axis]
            r[a, a] = r[b, b] = c
            r[a, b], r[b, a] = (-s, s) if axis != 1 else (s, -s)
            rotation = r @ rotation
        value = rotation @ value
        items[instance].set("transform", matrix_text(value))
        meshes = [m for m in self.meshes() if m["instance"] == instance]
        value[2, 3] -= min(float(m["vertices"][:, 2].min()) for m in meshes)
        items[instance].set("transform", matrix_text(value))
        self.dirty_models.add("3D/3dmodel.model")

    def paint(self, resource_id, face_indices, state, kind):
        if state not in {"clear", "enforce", "block"} or kind not in {"support", "seam"}:
            raise ValueError("Choose support/seam and clear/enforce/block")
        path, oid = resource_id.rsplit("#", 1)
        obj = self._resources(path).get(oid)
        if obj is None or not children(obj, "mesh"):
            raise ValueError("Select a mesh resource_id")
        triangles = children(children(children(obj, "mesh")[0], "triangles")[0], "triangle")
        attribute = "paint_supports" if kind == "support" else "paint_seam"
        # Studio's unsplit-face encoding: 2 zero split bits followed by the 2-bit state.
        encoded = {"clear": None, "enforce": "4", "block": "8"}[state]
        for index in face_indices:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(triangles):
                raise ValueError("Face index outside mesh")
            if encoded is None:
                triangles[index].attrib.pop(attribute, None)
            else:
                triangles[index].set(attribute, encoded)
        self.dirty_models.add(path)
        return len(set(face_indices))

    def set_layer_heights(self, object_id, profile, min_height, max_height):
        obj = self.target("object", {"object_id": object_id})
        if len(profile) < 2 or any(not isinstance(p, list) or len(p) != 2 for p in profile):
            raise ValueError("Provide at least two [z_mm, layer_height_mm] points")
        last = -1.0
        for z, height in profile:
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (z, height)) or z <= last or z < 0 or not min_height <= height <= max_height:
                raise ValueError("Layer profile needs increasing nonnegative Z and heights within nozzle limits")
            last = z
        ordinal = children(self.config, "object").index(obj) + 1
        prefix = f"object_id={ordinal}|"
        self.height_lines = [line for line in self.height_lines if not line.startswith(prefix)]
        self.height_lines.append(prefix + ";".join(str(v) for pair in profile for v in pair))
        self.dirty_heights = True

    def modifier_box(self, catalog, object_id, bounds, changes):
        bounds = np.asarray(bounds, dtype=float)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or (bounds[0] >= bounds[1]).any():
            raise ValueError("Modifier box requires positive bounds in object coordinates, in millimetres")
        if not changes:
            raise ValueError("A modifier requires at least one local setting")
        for key, value in changes.items():
            catalog.validate(key, value, "volume")
        config = self.target("object", {"object_id": object_id})
        resources = children(self.main, "resources")[0]
        parent = self._resources("3D/3dmodel.model").get(str(object_id))
        if parent is None:
            raise ValueError("Object must be a main-model resource")
        next_id = max(int(o.get("id")) for root in self.models.values() for r in children(root, "resources") for o in children(r, "object")) + 1
        components = children(parent, "components")
        if not components:
            original = children(parent, "mesh")
            if len(original) != 1:
                raise ValueError("Object has no printable mesh")
            leaf = ET.SubElement(resources, f"{{{CORE}}}object", id=str(next_id), type="model")
            parent.remove(original[0])
            leaf.append(original[0])
            container = ET.SubElement(parent, f"{{{CORE}}}components")
            ET.SubElement(container, f"{{{CORE}}}component", objectid=str(next_id))
            for part in children(config, "part"):
                if part.get("id") == str(object_id):
                    part.set("id", str(next_id))
            next_id += 1
        else:
            container = components[0]
        obj = ET.SubElement(resources, f"{{{CORE}}}object", id=str(next_id), type="model")
        mesh = ET.SubElement(obj, f"{{{CORE}}}mesh")
        vertices = ET.SubElement(mesh, f"{{{CORE}}}vertices")
        for x, y, z in [(x, y, z) for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]]:
            ET.SubElement(vertices, f"{{{CORE}}}vertex", x=str(x), y=str(y), z=str(z))
        triangles = ET.SubElement(mesh, f"{{{CORE}}}triangles")
        faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                 (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
        for a, b, c in faces:
            ET.SubElement(triangles, f"{{{CORE}}}triangle", v1=str(a), v2=str(b), v3=str(c))
        ET.SubElement(container, f"{{{CORE}}}component", objectid=str(next_id))
        part = ET.SubElement(config, "part", id=str(next_id), subtype="modifier_part")
        set_metadata(part, "name", "MCP reinforcement region")
        for key, value in changes.items():
            set_metadata(part, key, catalog.encode(key, value, json_format=False))
        self.dirty_config = True
        self.dirty_models.add("3D/3dmodel.model")
        return str(next_id)

    def remove_modifier(self, object_id, volume_id):
        config = self.target("object", {"object_id": object_id})
        part = self.target("volume", {"object_id": object_id, "volume_id": volume_id})
        if part.get("subtype") != "modifier_part":
            raise ValueError("Only modifier volumes can be removed")
        parent = self._resources("3D/3dmodel.model")[str(object_id)]
        matches = [c for container in children(parent, "components") for c in children(container, "component")
                   if c.get("objectid") == str(volume_id)]
        if len(matches) != 1:
            raise ValueError("Modifier component reference is ambiguous")
        children(parent, "components")[0].remove(matches[0])
        config.remove(part)
        # Keep the now-unreferenced resource to preserve any shared references from other objects.
        self.dirty_config = True
        self.dirty_models.add("3D/3dmodel.model")

    def save(self, path, invalidate=True):
        entries = dict(self.entries)
        for name in self.dirty_models:
            entries[name] = ET.tostring(self.models[name], encoding="utf-8", xml_declaration=True)
        if self.dirty_settings:
            entries[SETTINGS] = json.dumps(self.settings, indent=2).encode()
        if self.dirty_config:
            entries[MODEL_CONFIG] = ET.tostring(self.config, encoding="utf-8", xml_declaration=True)
        if self.dirty_ranges:
            entries[RANGES] = ET.tostring(self.ranges, encoding="utf-8", xml_declaration=True)
        if self.dirty_heights:
            entries[HEIGHTS] = ("\n".join(self.height_lines) + "\n").encode()
        if invalidate:
            invalidated = {k for k in entries if re.fullmatch(r"Metadata/(?:plate_\d+.*|top_\d+|pick_\d+|thumbnail.*|model.*)\.(?:gcode(?:\.md5)?|png)", k)
                           or k in {"Metadata/slice_info.config", "Metadata/filament_sequence.json"}}
            entries = {k: v for k, v in entries.items() if k not in invalidated}
            if MODEL_CONFIG in entries:
                root = fromstring(entries[MODEL_CONFIG])
                cleared = False
                for plate in children(root, "plate"):
                    for element in children(plate, "metadata"):
                        if element.get("key") in {"gcode_file", "thumbnail_file", "top_file", "pick_file", "pattern_bbox_file"}:
                            element.set("value", "")
                            cleared = True
                if cleared:
                    entries[MODEL_CONFIG] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            # Remove references to invalidated previews and slice artifacts as well as their contents.
            for name, data in list(entries.items()):
                if name.endswith(".rels"):
                    root = fromstring(data)
                    removed = False
                    for relation in list(root):
                        target = relation.get("Target", "")
                        base = str(PurePosixPath(name).parent.parent) if name != "_rels/.rels" else ""
                        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                        if resolved in invalidated:
                            root.remove(relation)
                            removed = True
                    if removed:
                        entries[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        path = Path(path)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".3mf")
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            Project(Path(tmp))
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
