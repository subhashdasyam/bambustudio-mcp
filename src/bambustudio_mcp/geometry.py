from __future__ import annotations

import numpy as np
import trimesh


def analyze(project, overhang_angle=45.0, sample_count=64):
    if not 0 < overhang_angle < 90 or not 1 <= sample_count <= 256:
        raise ValueError("Overhang angle must be 0..90 degrees; samples must be 1..256")
    reports = []
    previews = []
    for resource in project.meshes():
        mesh = trimesh.Trimesh(vertices=resource["vertices"], faces=resource["faces"], process=False)
        bottom = float(mesh.bounds[0, 2])
        centers = mesh.triangles_center
        bed = np.abs(centers[:, 2] - bottom) < 0.02
        overhang = (mesh.face_normals[:, 2] < -np.cos(np.radians(overhang_angle))) & ~bed
        indices = np.flatnonzero(overhang)
        thickness = []
        if mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0:
            selected = np.linspace(0, len(mesh.faces) - 1, min(sample_count, len(mesh.faces)), dtype=int)
            epsilon = max(float(mesh.extents.max()) * 1e-7, 1e-6)
            origins = centers[selected] - mesh.face_normals[selected] * epsilon
            try:
                locations, rays, _ = mesh.ray.intersects_location(origins, -mesh.face_normals[selected], multiple_hits=False)
                for location, ray in zip(locations, rays):
                    distance = float(np.linalg.norm(location - origins[ray]) + epsilon)
                    thickness.append({"face_index": int(selected[ray]), "thickness_mm": distance,
                                      "point_mm": centers[selected[ray]].tolist()})
            except ModuleNotFoundError:
                pass
        adjacency = mesh.face_adjacency
        parents = list(range(len(mesh.faces)))
        def root(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index
        for a, b in adjacency:
            parents[root(int(a))] = root(int(b))
        component_count = len({root(i) for i in range(len(parents))})
        reports.append({"resource_id": resource["resource_id"], "instance": resource["instance"], "role": resource["role"],
                        "bounds_mm": mesh.bounds.tolist(), "dimensions_mm": mesh.extents.tolist(),
                        "watertight": bool(mesh.is_watertight), "consistent_winding": bool(mesh.is_winding_consistent),
                        "connected_components": component_count, "volume_mm3": float(mesh.volume) if mesh.is_watertight else None,
                        "bed_contact_area_mm2": float(mesh.area_faces[bed].sum()),
                        "overhang_area_mm2": float(mesh.area_faces[overhang].sum()),
                        "overhang_face_indices": indices[:2000].tolist(), "overhang_selection_truncated": len(indices) > 2000,
                        "sampled_thickness": sorted(thickness, key=lambda x: x["thickness_mm"]),
                        "thickness_method": "sampled inward face-normal ray distance; not minimum feature thickness",
                        "mechanical_strength": "unknown; requires material, load case and physical validation"})
        # A static SVG preview needs no display server, browser, or rendering service.
        face_sample = np.linspace(0, len(mesh.faces) - 1, min(1500, len(mesh.faces)), dtype=int)
        triangles = mesh.triangles[face_sample]
        projection = np.array([[0.866, -0.866, 0.0], [0.5, 0.5, -1.0]])
        points = triangles @ projection.T
        low = points.min(axis=(0, 1))
        extent = np.maximum(points.max(axis=(0, 1)) - low, 1e-6)
        points = (points - low) * min(560 / extent[0], 420 / extent[1]) + 20
        polygons = []
        for face, coords in zip(face_sample, points):
            color = "#e77447" if overhang[face] else "#a5c6cf"
            polygons.append(f'<polygon points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in coords)}" fill="{color}" stroke="#30434b" stroke-width="0.3"><title>face {int(face)}</title></polygon>')
        previews.append({"resource_id": resource["resource_id"], "instance": resource["instance"],
                         "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="460" viewBox="0 0 600 460">' + "".join(polygons) + '</svg>'})
    return {"coordinate_system": "project world coordinates in millimetres", "meshes": reports, "previews": previews,
            "limitations": ["Overhang geometry does not predict bridge success or support removability.",
                            "Thickness samples can miss narrow features; they are not a stress analysis.",
                            "No strength or finish improvement is physically validated."]}
