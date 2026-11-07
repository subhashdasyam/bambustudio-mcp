"""Deterministic, resolution-aware geometric risk candidates, not a print simulation."""
from __future__ import annotations

import hashlib
import html
import json

import numpy as np
import trimesh

from .catalog import Catalog
from .sections import components, contains, intervals, merge_intervals, outer_loops, overlaps, section

GUIDE_CATEGORIES={"clearances":"print_in_place","floating_islands":"bridges","thin_features":"fine_detail",
                  "faceting":"fine_detail","overhangs":"bridges","cosmetic_faces":"seams","top_surfaces":"tops"}
MAX_CLEARANCE_REGIONS = 4096
ANALYSIS_VERSION = 2


def context_signature(context):
    # Physical observations do not change geometry; face intent and a measured expansion budget do.
    relevant={key:context[key] for key in ("cosmetic_faces","elephant_foot_expansion_mm") if key in context}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def parameters(settings, context):
    catalog = Catalog()

    def positive(key):
        value = catalog.decode(key, settings.get(key))
        values = value if isinstance(value, list) else [value]
        return [float(v) for v in values if isinstance(v, (int, float)) and np.isfinite(v) and v > 0]

    nozzle = positive("nozzle_diameter")
    width = positive("outer_wall_line_width") or positive("line_width") or nozzle
    height = positive("layer_height")
    first = positive("initial_layer_print_height") or height
    first_width = positive("initial_layer_line_width") or width
    missing = [name for name, value in (("nozzle_diameter", nozzle), ("line_width", width), ("layer_height", height)) if not value]
    measured = context.get("elephant_foot_expansion_mm")
    return {"nozzle_diameters_mm": nozzle, "line_width_mm": max(width) if width else None,
            "layer_height_mm": min(height) if height else None,
            "first_layer_height_mm": max(first) if first else None,
            "xy_gap_threshold_mm": 2 * max(width) if width else None,
            "z_gap_threshold_mm": 2 * min(height) if height else None,
            "first_layer_squash_budget_mm": 2 * measured if measured is not None else max(first_width) if first_width else None,
            "squash_evidence": "user-reported per-side expansion" if measured is not None else "heuristic half initial-line-width expansion per side; not a moisture or squish measurement",
            "configured_elefant_foot_compensation_mm": catalog.decode("elefant_foot_compensation", settings.get("elefant_foot_compensation")),
            "line_width_evidence": "saved outer/global width; zero or absent width falls back to nozzle diameter",
            "missing": missing}


def _mesh(vertices, faces):
    unique, inverse = np.unique(faces, return_inverse=True)
    return trimesh.Trimesh(vertices=vertices[unique], faces=inverse.reshape(-1, 3), process=False)


class Findings:
    def __init__(self):
        self.entries = {}
        self.region_index = {}
        self.catalog = Catalog()

    def add(self, key, category, bounds, value, threshold, units, why, design, settings=(), severity="medium", **extra):
        bounds = np.asarray(bounds, dtype=float)
        identity = json.dumps([category, key], sort_keys=True)
        issue_id = "geo-" + hashlib.sha256(identity.encode()).hexdigest()[:14]
        if issue_id in self.entries:
            entry = self.entries[issue_id]
            old = entry["location"]["bbox_mm"]
            bounds = np.array([np.minimum(old[0], bounds[0]), np.maximum(old[1], bounds[1])])
            entry["location"] = {"bbox_mm": bounds.tolist(), "z_range_mm": bounds[:, 2].tolist()}
            entry["occurrences"] += 1
            if isinstance(value, (int, float)):
                entry["measurement"]["value"] = min(value, entry["measurement"]["value"])
            if severity == "high":
                entry["severity"] = severity
            return entry
        entry = {"id": issue_id, "category": category, "quality_guide_category":GUIDE_CATEGORIES.get(category,category), "severity": severity, "status": "open",
                 "evidence": "geometric heuristic", "location": {"bbox_mm": bounds.tolist(), "z_range_mm": bounds[:, 2].tolist()},
                 "measurement": {"value": value, "threshold": threshold, "units": units},
                 "why_it_fails": why, "design_fix": design,
                 "slicer_fix": [{"setting": setting, "scopes": self.catalog.options[setting]["scopes"],
                                 "action": "Compare a compatible candidate and inspect the actual toolpaths; this setting cannot repair CAD geometry."}
                                for setting in settings], "occurrences": 1, **extra}
        self.entries[issue_id] = entry
        return entry


def _gap(findings, parts, a, b, threshold, width, layer_range, first, params, scan_axis, scan_step):
    distance = float(np.linalg.norm(b - a))
    if distance <= 1e-6 or distance >= threshold - 1e-7:
        return
    axis = "z" if abs(b[2] - a[2]) > 1e-6 else "xy"
    low, high = np.minimum(a, b), np.maximum(a, b)
    if axis == "z":
        layer_range = [float(low[2]), float(high[2])]
    low[2], high[2] = min(low[2], layer_range[0]), max(high[2], layer_range[1])
    same = parts[0] == parts[1]
    entry = findings.add([sorted(parts), axis], "clearances", [low, high], distance, threshold, "mm",
                         "Opposing solid boundaries leave a clearance below the requested two-line-width or two-layer-height review threshold. Extrusion spread, seams or a sagging roof can close it.",
                         "Increase measured clearance, reorient the joint, or use self-supporting pin/socket profiles. Test the actual hinge region before a long print.",
                         ("xy_hole_compensation", "elefant_foot_compensation", "seam_position"),
                         severity="high" if distance < (width if axis == "xy" else params["layer_height_mm"]) - 1e-5 else "medium",
                         direction=axis, relation="same-part facing surfaces" if same else "separate parts", parts=sorted(parts), clearance_regions=[])
    coordinates=tuple(np.round(np.r_[np.delete(a,scan_axis),np.delete(b,scan_axis),layer_range],7))
    cache=findings.region_index.setdefault(entry["id"],{})
    key=(scan_axis,coordinates)
    previous=cache.get(key)
    if previous and abs(a[scan_axis]-(previous["a_mm"][scan_axis]+previous["scan_count"]*scan_step))<1e-7:
        previous["scan_count"]+=1
    elif len(entry["clearance_regions"]) < MAX_CLEARANCE_REGIONS:
        region = {"a_mm": a.tolist(), "b_mm": b.tolist(), "z_range_mm": list(layer_range), "gap_mm": distance,
                  "scan_axis":scan_axis,"scan_step_mm":scan_step,"scan_count":1}
        entry["clearance_regions"].append(region)
        cache[key]=region
    else:
        entry["regions_truncated"] = True
    if first and distance < params["first_layer_squash_budget_mm"]:
        findings.add([sorted(parts), axis], "bed_contact", [low, high], distance,
                     params["first_layer_squash_budget_mm"], "mm",
                     "A first-layer joint gap is below the declared or conservative two-sided squish budget. Elephant-foot compensation is not a measurement of actual expansion.",
                     "Add a bottom-edge chamfer and a calibrated first-layer clearance. Print a small hinge coupon first.",
                     ("elefant_foot_compensation", "initial_layer_line_width"), severity="high", subtype="first_layer_gap", parts=sorted(parts))


def _scan_clearances(sections, axis, positions, plane_axis, plane, findings, params, layer_range, first):
    width = params["line_width_mm"]
    axes = [i for i in range(3) if i != plane_axis]
    scan_step=float(positions[1]-positions[0]) if len(positions)>1 else width/2
    for coordinate in positions:
        solid = []
        for part, segments in sections:
            values = intervals(segments, coordinate, axis)
            if values is not None:
                solid.extend((float(a), float(b), part, float(c), float(d)) for a, b, c, d in values)
                if plane_axis==2:
                    for lo,hi,align_lo,align_hi in values:
                        if 1e-6<hi-lo<2*width and min(align_lo,align_hi)>.5:
                            a,b=np.zeros(3),np.zeros(3)
                            a[axis],b[axis]=lo,hi
                            a[1-axis]=b[1-axis]=coordinate
                            a[2],b[2]=layer_range
                            entry=findings.add([part,"section_width"],"thin_features",[a,b],float(hi-lo),2*width,"mm solid section width",
                                         "Opposing boundaries of a solid section are less than two extrusion lines apart. A narrow wall or pin can become a fragile single line or disappear.",
                                         "Thicken the feature or select a compatible smaller physical nozzle.",("detect_thin_wall","wall_generator","line_width"),
                                         severity="high" if hi-lo<width-1e-5 else "medium")
                            if hi-lo<=entry["measurement"]["value"]:
                                start,end=a.copy(),b.copy()
                                start[2]=end[2]=plane
                                entry["minimum_section"]={"a_mm":start.tolist(),"b_mm":end.tolist(),"z_mm":plane,
                                                          "part":part,"boundary_alignment":[float(align_lo),float(align_hi)]}
        union = merge_intervals(solid)
        for left, right in zip(union, union[1:]):
            if min(left[5], right[4]) < .5:
                continue  # Tangent slivers at a cavity's edge are not opposing clearance faces.
            a, b = np.zeros(3), np.zeros(3)
            a[plane_axis] = b[plane_axis] = plane
            a[axes[1-axis]] = b[axes[1-axis]] = coordinate
            a[axes[axis]], b[axes[axis]] = left[1], right[0]
            threshold = params["z_gap_threshold_mm"] if axes[axis] == 2 else params["xy_gap_threshold_mm"]
            _gap(findings, (left[3], right[2]), a, b, threshold, width, layer_range, first, params, axes[1-axis], scan_step)


def _circle(loop):
    center0 = loop.mean(0)
    centered = loop - center0
    fit, _, rank, _ = np.linalg.lstsq(np.c_[2*centered, np.ones(len(loop))], (centered**2).sum(1), rcond=None)
    if rank < 3:
        return None
    radius = np.sqrt(max(0, float(fit[2] + fit[:2] @ fit[:2])))
    if radius <= 1e-8:
        return None
    # A small arc or a thin rectangle can fit a huge circle; a full round profile must span its diameter in both axes.
    if (np.abs(np.ptp(loop,axis=0)/(2*radius)-1)>.1).any():
        return None
    residual = float(np.max(np.abs(np.linalg.norm(centered-fit[:2], axis=1)-radius)) / radius)
    return center0+fit[:2], radius, residual


def _merge_clearance_regions(issue, width, bounds):
    """Coalesce identical runs across adjacent layers/planes, preserving the probe lattice."""
    merged,cache=[],{}
    horizontal=issue["direction"]=="xy"
    for region in issue["clearance_regions"]:
        plane_axis=2 if horizontal else 1-region["scan_axis"]
        key=tuple(np.round(np.r_[np.delete(region["a_mm"],plane_axis),np.delete(region["b_mm"],plane_axis),
                                region["scan_axis"],region["scan_step_mm"],region["scan_count"]],7))
        previous=cache.get(key)
        plane_step=max(width,(bounds[1,plane_axis]-bounds[0,plane_axis])/512)
        if horizontal and previous and abs(previous["z_range_mm"][1]-region["z_range_mm"][0])<1e-6:
            previous["z_range_mm"][1]=region["z_range_mm"][1]
        elif not horizontal and previous and abs(region["a_mm"][plane_axis]-(previous["a_mm"][plane_axis]+previous["plane_count"]*plane_step))<1e-7:
            previous["plane_count"]+=region.get("plane_count",1)
        else:
            if not horizontal:
                region.setdefault("plane_axis",plane_axis)
                region.setdefault("plane_step_mm",plane_step)
                region.setdefault("plane_count",1)
            merged.append(region)
            cache[key]=region
    issue["clearance_regions"]=merged


def _oblique_holes(parts,findings,params,epsilon):
    """Adjacent cylindrical facets suggest a few extra horizontal section directions."""
    votes={}
    for part in parts:
        mesh=part["mesh"];adj=mesh.face_adjacency
        if not len(adj):
            continue
        cross=np.cross(mesh.face_normals[adj[:,0]],mesh.face_normals[adj[:,1]])
        length=np.linalg.norm(cross,axis=1)
        unit=cross/np.maximum(length[:,None],1e-12)
        edge=mesh.vertices[mesh.face_adjacency_edges]
        edge=edge[:,1]-edge[:,0];edge/=np.maximum(np.linalg.norm(edge,axis=1)[:,None],1e-12)
        mask=(length>.001)&(length<.5)&(np.abs(unit[:,2])<.01)&(np.abs((unit*edge).sum(1))>.99)
        angles=np.round(np.degrees(np.arctan2(unit[mask,1],unit[mask,0]))%180).astype(int)
        for angle in angles:
            if min(angle,abs(angle-90),180-angle)>2:
                votes[int(angle)]=votes.get(int(angle),0)+1
    directions=sorted((angle for angle in votes if votes[angle]>=6),key=lambda a:(-votes[a],a))[:4]
    for angle in directions:
        theta=np.radians(angle)
        axis=np.array([np.cos(theta),np.sin(theta),0.])
        rotation=np.array([axis,[-axis[1],axis[0],0],[0,0,1]])
        for part in parts:
            source=part["mesh"]
            mesh=trimesh.Trimesh(vertices=source.vertices@rotation.T,faces=source.faces,process=False)
            step=max(params["line_width_mm"],mesh.extents[0]/256)
            for plane in np.arange(mesh.bounds[0,0]+step/2,mesh.bounds[1,0],step):
                _,loops,_=section(mesh,0,plane,epsilon)
                for i,loop in enumerate(loops):
                    depth=sum(bool(contains(loop[0],other)[0]) for j,other in enumerate(loops) if i!=j)
                    fit=_circle(loop)
                    if fit is None or fit[2]>.035:
                        continue
                    center,radius,error=fit
                    corners=np.array([[plane,y,z] for y in (center[0]-radius,center[0]+radius) for z in (center[1]-radius,center[1]+radius)])@rotation
                    is_hole=bool(depth%2)
                    findings.add([part["id"],angle,np.round(center,1).tolist(),round(radius,1),is_hole],"holes_lying_flat",
                                 [corners.min(0),corners.max(0)],float(2*radius),2*params["line_width_mm"],"mm diameter",
                                 "Repeated round section profiles occur along a facet-inferred horizontal axis. A hole roof can sag into clearance; a pin underside can need support.",
                                 "Use a self-supporting teardrop/diamond roof or reorient the axis vertically.",
                                 ("bridge_speed","enable_overhang_speed","enable_support"),severity="high",diameter_mm=float(2*radius),
                                 axis_direction=axis.tolist(),round_fit_relative_error=float(error),feature="hole" if is_hole else "pin")
    return directions


def _surface_checks(part, findings, params, overhang_angle):
    mesh, name = part["mesh"], part["id"]
    width, height = params["line_width_mm"], params["layer_height_mm"]
    normals, centers = mesh.face_normals, mesh.triangles_center
    top = normals[:,2] > .995
    if top.any():
        points=mesh.triangles[top].reshape(-1,3)
        findings.add(name,"top_surfaces",[points.min(0),points.max(0)],float(mesh.area_faces[top].sum()),0,"mm2 upward planar facets",
                     "Upward surfaces provide a reference region for later top-path and travel inspection. No cosmetic defect is asserted.",
                     "Declare any important top face explicitly if it needs a specified finish.",("ironing_type","seam_position"),severity="info")
    near_bed = max(10*height, 4*width)
    downward = (normals[:, 2] < -np.cos(np.radians(overhang_angle)))
    overhang = downward & (centers[:, 2] > params["first_layer_height_mm"]) & (np.abs(normals[:, 2]) < .995)
    if overhang.any():
        points = mesh.triangles[overhang].reshape(-1, 3)
        findings.add(name, "overhangs", [points.min(0), points.max(0)], float(np.degrees(np.arccos(np.clip(-normals[overhang, 2], 0, 1))).min()),
                     overhang_angle, "degrees underside tilt from horizontal",
                     "Downward facets exceed the requested support-review slope. Material, cooling and the local path determine whether they sag.",
                     "Reorient, use a chamfer, or provide removable support away from moving clearances.",
                     ("enable_overhang_speed", "enable_support"))
    bed_curve = downward & (centers[:, 2] < near_bed) & (centers[:, 2] > height/20) & (np.abs(normals[:, 2]) < .9999)
    if bed_curve.any():
        triangles = mesh.triangles[bed_curve].reshape(-1, 3)
        findings.add(name, "bed_contact", [triangles.min(0), triangles.max(0)], float(centers[bed_curve, 2].min()), near_bed, "mm above bed",
                     "Steep underside facets begin near the bed. A tangent curve can have a tiny first footprint and progressively unsupported edges that curl.",
                     "Use a flat or chamfered base rather than a bottom fillet, or reorient the part.",
                     ("initial_layer_line_width", "elefant_foot_compensation", "enable_support"), severity="high", subtype="bed_tangent_curve")
    ceiling = (normals[:, 2] < -.995) & (centers[:, 2] > params["first_layer_height_mm"])
    if ceiling.any():
        subset = _mesh(mesh.vertices, mesh.faces[ceiling])
        for faces in components(subset):
            points = subset.triangles[faces].reshape(-1, 3)
            bounds = np.array([points.min(0), points.max(0)])
            spans = bounds[1, :2]-bounds[0, :2]
            positive = np.flatnonzero(spans > width/20)
            if len(positive) != 2:
                continue
            axis = int(positive[np.argmin(spans[positive])])
            entry=findings.add(name, "bridges", bounds, float(spans[axis]), 2*width, "mm",
                         "A downward ceiling needs a supported bridge path or support. The bounding-box span is a geometric candidate, not a calibrated bridge limit.",
                         "Shorten the span, add a self-supporting roof/chamfer, or reorient the cavity.",
                         ("bridge_speed", "bridge_flow", "bridge_angle", "enable_support"),
                         direction=(np.eye(3)[axis]).tolist(), span_method="shorter horizontal extent of connected ceiling facets",
                         ceiling_regions=[])
            entry["ceiling_regions"].append({"bbox_mm":bounds.tolist(),"span_mm":float(spans[axis]),"direction":np.eye(3)[axis].tolist()})
            # A grouped bridge issue reports the longest candidate; each local span remains available.
            longest=max(entry["ceiling_regions"],key=lambda region:region["span_mm"])
            entry["measurement"]["value"]=longest["span_mm"]
            entry["direction"]=longest["direction"]
    adjacency = mesh.face_adjacency
    if len(adjacency):
        angle = mesh.face_adjacency_angles
        edge = mesh.vertices[mesh.face_adjacency_edges]
        length = np.linalg.norm(edge[:, 1]-edge[:, 0], axis=1)
        widths = 2*np.minimum(mesh.area_faces[adjacency[:, 0]], mesh.area_faces[adjacency[:, 1]]) / np.maximum(length, 1e-12)
        deviation = widths*np.tan(angle/4)/2
        coarse = (angle > .01) & (angle < np.pi/2) & (deviation > height/4)
        tiny = (angle > .001) & (angle < .1) & (widths < width/2)
        if coarse.sum() >= 4 or tiny.sum() >= 12:
            mask = coarse if coarse.sum() >= 4 else tiny
            triangles = mesh.triangles[np.unique(adjacency[mask])].reshape(-1, 3)
            findings.add(name, "faceting", [triangles.min(0), triangles.max(0)],
                         float(np.median(widths[mask])), width, "mm facet altitude",
                         "Repeated facets have measurable normal changes. Coarse facets can show flat bands; sub-line-width angular steps may not survive extrusion.",
                         "Export a smoother CAD mesh for coarse bands. Replace intentional tiny stop steps with printable geometry rather than expecting the slicer to smooth the CAD.",
                         ("layer_height",), severity="medium" if coarse.sum() >= 4 else "low",
                         angular_steps_degrees={"min": float(np.degrees(angle[mask]).min()), "max": float(np.degrees(angle[mask]).max())},
                         ambiguity="Tessellated smooth curves and intentional stepped faces cannot be distinguished from an STL alone.")
        nz = np.abs(normals[adjacency, 2])
        riser = np.where(nz[:, 0] < .2, adjacency[:, 0], adjacency[:, 1])
        rise = np.ptp(mesh.triangles[riser, :, 2], axis=1)
        steps = (angle > np.pi/3) & (nz.min(1) < .2) & (nz.max(1) > .99) & (rise > height/20) & (rise < 2*height)
        if steps.sum() >= 8:
            points = mesh.triangles[np.unique(adjacency[steps])].reshape(-1, 3)
            findings.add([name, "steps"], "faceting", [points.min(0), points.max(0)], float(np.median(rise[steps])), 2*height, "mm repeated CAD riser height",
                         "Repeated horizontal shelves and narrow vertical risers encode steps in the mesh. A finer slicer layer cannot remove those CAD shelves.",
                         "Replace the stepped profile with a smooth CAD surface and re-export the mesh.", ("layer_height",))
    shallow = (normals[:, 2] > .5) & (normals[:, 2] < .9999)
    terrace = height*np.abs(normals[:, 2])/np.sqrt(np.maximum(1-normals[:, 2]**2, 1e-12))
    shallow &= terrace > 2*width
    if shallow.any():
        triangles = mesh.triangles[shallow].reshape(-1, 3)
        entry = findings.add(name, "faceting", [triangles.min(0), triangles.max(0)], float(terrace[shallow].max()), 2*width, "mm estimated terrace run",
                             "A shallow upper slope has a large horizontal run per nominal layer, so layer steps can remain visible on a rounded top.",
                             "Reorient the cosmetic face or retain a smooth CAD surface.", ("layer_height",), subtype="layer_stair_steps")
        entry["slicer_fix"].append({"edit_regions": "layer_heights", "action": "Compare local finer layers within nozzle limits; review the highlighted Z range."})
    diameter = float(min(mesh.extents[:2]))
    slender_limit = 5*width/height
    if diameter > 0 and mesh.extents[2]/diameter > slender_limit:
        findings.add(name, "thin_features", mesh.bounds, float(mesh.extents[2]/diameter), slender_limit, "height / transverse size",
                     "A tall slender component can flex or wobble during deposition. This shape ratio is a heuristic, not a stiffness calculation.",
                     "Thicken the feature, brace it, shorten it, or orient it with greater bed contact.",
                     ("outer_wall_speed", "outer_wall_acceleration"))


def preflight(project, mesh_reports, context, overhang_angle):
    params = parameters(project.settings, context)
    findings, parts, limits = Findings(), [], []
    for resource in project.meshes():
        if resource["role"] != "normal_part":
            continue
        mesh = trimesh.Trimesh(vertices=resource["vertices"], faces=resource["faces"], process=True)
        for index, faces in enumerate(components(mesh)):
            name = f'{resource["instance"]}:{resource["resource_id"]}:{index}'
            parts.append({"id": name, "mesh": _mesh(mesh.vertices, mesh.faces[faces]),
                          "resource_id": resource["resource_id"], "instance": resource["instance"]})
    if not parts:
        return {"analysis_version":ANALYSIS_VERSION,"context_signature":context_signature(context),"issues": [], "analysis_status": "unknown", "parameters": params, "limitations": ["No normal-part meshes."]}
    if params["missing"]:
        return {"analysis_version":ANALYSIS_VERSION,"context_signature":context_signature(context),"issues": [], "analysis_status": "unknown", "parameters": params,
                "limitations": ["Missing project nozzle/line width/layer height; resolution-dependent checks were not run."]}
    width, height = params["line_width_mm"], params["layer_height_mm"]
    epsilon = max(min(width, height)*1e-5, 1e-8)
    bounds = np.array([np.min([p["mesh"].bounds[0] for p in parts], axis=0), np.max([p["mesh"].bounds[1] for p in parts], axis=0)])
    pairs = []
    for i, a in enumerate(parts):
        for b in parts[i+1:]:
            overlap = np.minimum(a["mesh"].bounds[1], b["mesh"].bounds[1])-np.maximum(a["mesh"].bounds[0], b["mesh"].bounds[0])
            if (overlap > 0).all():
                pairs.append((a["id"], b["id"]))
                findings.add([a["id"], b["id"]], "print_in_place", [np.maximum(a["mesh"].bounds[0], b["mesh"].bounds[0]), np.minimum(a["mesh"].bounds[1], b["mesh"].bounds[1])],
                             float(overlap.min()), 0, "mm bbox overlap",
                             "Separate connected components have overlapping bounding boxes. Treat them as possible interlocking/moving parts until the user confirms intent.",
                             "Check clearance in every analysis layer and test a cut-out hinge/joint region. Bounding boxes alone do not prove mechanical interlock.",
                             ("elefant_foot_compensation", "xy_hole_compensation"), severity="high", parts=[a["id"], b["id"]])
        _surface_checks(a, findings, params, overhang_angle)
    for report in mesh_reports:
        thin = [s for s in report["sampled_thickness"] if s["thickness_mm"] < 2*width]
        if thin and report["role"] == "normal_part":
            points = np.array([s["point_mm"] for s in thin])
            findings.add([report["resource_id"], report["instance"]], "thin_features", [points.min(0), points.max(0)],
                         min(s["thickness_mm"] for s in thin), 2*width, "mm sampled inward thickness",
                         "A sampled solid wall is thinner than two lines. One-line or omitted features can be fragile or disappear from the toolpath.",
                         "Thicken the local wall or pin, or choose an appropriate smaller physical nozzle.",
                         ("detect_thin_wall", "wall_generator", "line_width"))
    first_height=params["first_layer_height_mm"]
    # shortcut: bound expensive analysis on pathological inputs; report partial coverage instead of silently declaring a pass.
    next_z=first_height+height/2
    if bounds[1,2]>next_z+4999*height:
        remaining=4999
        limits.append("Layer scan stopped at 5000 layers. Unscanned height is unknown.")
    else:
        remaining=max(0,int(np.ceil((bounds[1,2]-next_z)/height)))
    z_values=np.r_[min(first_height/2,bounds[1,2]/2),next_z+np.arange(remaining)*height]
    z_values=z_values[z_values<bounds[1,2]]
    previous = []
    sample_step = width/2
    positions = []
    for axis in (0, 1):
        if (bounds[1,axis]-bounds[0,axis])/sample_step>1024:
            values = np.linspace(bounds[0, axis]+sample_step/2, bounds[1, axis]-sample_step/2, 1024)
            limits.append(f"XY scan-line spacing increased on axis {axis}; narrow features between probes can be missed.")
        else:
            values = np.arange(bounds[0, axis]+sample_step/2, bounds[1, axis], sample_step)
        positions.append(values)
    for index, z in enumerate(z_values):
        layers, loops_all = [], []
        z_range = [0 if index == 0 else float(z-height/2), first_height if index==0 else float(z+height/2)]
        for part in parts:
            mesh = part["mesh"]
            if not mesh.bounds[0, 2] < z < mesh.bounds[1, 2]:
                continue
            segments, loops, closed = section(mesh, 2, z, epsilon)
            if not closed:
                limits.append("Open or branching section contours encountered; parity checks are incomplete.")
            if len(segments):
                layers.append((part["id"], segments))
            for loop in outer_loops(loops):
                if index > 0 and not overlaps(loop, previous):
                    low, high = loop.min(0), loop.max(0)
                    findings.add([part["id"], np.round((low+high)/2, 2).tolist()], "floating_islands",
                                 [[*low, z_range[0]], [*high, z_range[1]]], 0, 1, "overlap with previous analysis layer",
                                 "A section region has no material overlap with the preceding layer. It starts in air unless generated support or a different orientation supplies a base.",
                                 "Reorient, connect the region to printable material, or split the design.",
                                 ("enable_support", "support_type"), severity="high")
            loops_all.extend(loops)
        for axis in (0, 1):
            _scan_clearances(layers, axis, positions[1-axis], 2, z, findings, params, z_range, index == 0)
        previous = loops_all
    # Vertical sections expose up/down gaps and circular tunnel profiles without requiring a polygon engine.
    vertical_planes = 0
    for plane_axis in (0, 1):
        span = bounds[1, plane_axis]-bounds[0, plane_axis]
        step = max(width, span/512)
        planes = np.arange(bounds[0, plane_axis]+step/2, bounds[1, plane_axis], step)
        if step > width*1.01:
            limits.append(f"Horizontal-hole planes on axis {plane_axis} are spaced {step:.4g} mm; smaller features can be missed.")
        other_axis = 1-plane_axis
        for plane in planes:
            vertical_planes += 1
            layers = []
            for part in parts:
                mesh = part["mesh"]
                if not mesh.bounds[0, plane_axis] < plane < mesh.bounds[1, plane_axis]:
                    continue
                segments, loops, closed = section(mesh, plane_axis, plane, epsilon)
                if len(segments):
                    layers.append((part["id"], segments))
                for i, loop in enumerate(loops):
                    depth = sum(bool(contains(loop[0], other)[0]) for j, other in enumerate(loops) if i != j)
                    circle = _circle(loop)
                    if circle is None or circle[2] > .035:
                        continue
                    center, radius, residual = circle
                    low, high = np.zeros(3), np.zeros(3)
                    low[plane_axis] = high[plane_axis] = plane
                    low[other_axis], high[other_axis] = center[0]-radius, center[0]+radius
                    low[2], high[2] = center[1]-radius, center[1]+radius
                    is_hole=bool(depth%2)
                    key = [part["id"], plane_axis, np.round(center, 1).tolist(), round(radius, 1),is_hole]
                    entry = findings.add(key, "holes_lying_flat", [low, high], float(2*radius), 2*width, "mm diameter",
                                         "A closed approximately round hole profile has a horizontal axis. Its curved roof loses support near the top and can sag into a pin clearance." if is_hole else
                                         "A closed approximately round external pin profile has a horizontal axis. Its curved underside needs support and can begin with a weak bed-tangent footprint.",
                                         "Use a teardrop or diamond roof, reorient the axis vertically, or finish the bore after printing.",
                                         ("bridge_speed", "enable_overhang_speed", "enable_support"), severity="high",
                                         diameter_mm=float(2*radius), axis_direction=np.eye(3)[plane_axis].tolist(), round_fit_relative_error=float(residual), feature="hole" if is_hole else "pin")
                    entry["slicer_fix"].append({"edit_regions": "height_range", "action": "Review the roof Z range with compatible bridge/overhang controls. XY hole compensation cannot repair an open horizontal roof."}) if entry["occurrences"] == 1 else None
            _scan_clearances(layers, 1, positions[other_axis], plane_axis, plane, findings, params,
                             [0, float(bounds[1, 2])], False)
            for issue in findings.entries.values():
                if issue.get("direction")=="z" and "clearance_regions" in issue:
                    _merge_clearance_regions(issue,width,bounds)
                    findings.region_index.pop(issue["id"],None)
    for index, face in enumerate(context.get("cosmetic_faces", [])):
        low, high = face["bounds_mm"]
        entry=findings.add([face["name"],face["bounds_mm"],face.get("avoid_seams",True),face.get("ironing",False)], "cosmetic_faces", [low, high], 0, 0, "user-declared region",
                     "This region has a declared seam or top-finish requirement. Geometry alone cannot confirm its appearance.",
                     "Choose an orientation with the desired cosmetic face accessible.", ("seam_position", "ironing_type"), severity="info",
                     intent=face, name=face["name"])
        if face.get("ironing"):
            entry["top_samples_mm"]=_top_samples(parts,np.asarray([low,high]),width/2)
    oblique=_oblique_holes(parts,findings,params,epsilon)
    result = list(findings.entries.values())
    for issue in result:
        if "minimum_section" in issue:
            sample=issue["minimum_section"]
            mesh=next(part["mesh"] for part in parts if part["id"]==sample["part"])
            _,_,faces=trimesh.proximity.closest_point_naive(mesh,np.array([sample["a_mm"],sample["b_mm"]]))
            normals=mesh.face_normals[faces]
            sample["surface_normals"]=normals.tolist()
            sample["opposing_parallel_faces"]=bool(normals[0]@normals[1]<-.95)
            if not sample["opposing_parallel_faces"]:
                issue["severity"]="medium"
                issue["why_it_fails"]="The minimum sampled section is a narrow wedge/sliver between nonparallel surfaces, not an established wall thickness. Check its local layer paths before changing the design. Other section samples were not classified."
            issue["affected_bbox_mm"]=issue["location"]["bbox_mm"]
            issue["location"]={"bbox_mm":[sample["a_mm"],sample["b_mm"]],"z_range_mm":[sample["z_mm"]]*2}
        if "clearance_regions" in issue:
            _merge_clearance_regions(issue,width,bounds)
    if any(i.get("regions_truncated") for i in result):
        limits.append(f"Clearance evidence regions capped at {MAX_CLEARANCE_REGIONS} runs per issue. Later unsaved regions cannot be correlated with G-code.")
    return {"analysis_version": ANALYSIS_VERSION, "analysis_status": "partial" if limits else "complete", "parameters": params,
            "context_signature": context_signature(context), "bounds_mm": bounds.tolist(), "issues": result,
            "layer_scan": {"count": len(z_values), "spacing_mm": height, "z_range_mm": [float(z_values[0]), float(z_values[-1])] if len(z_values) else [],
                           "xy_probe_spacing_mm": sample_step, "vertical_section_count": vertical_planes,
                           "additional_horizontal_axis_degrees":oblique,
                           "possible_interlocking_pairs": [list(pair) for pair in pairs]},
            "limitations": list(dict.fromkeys(limits + [
                "All geometric risk classifications are heuristics, not physical validation or guaranteed failures.",
                "Every nominal project-layer sample is checked; GUI adaptive profiles and per-region overrides can produce a different actual layer schedule.",
                "Scan-line probes can miss sub-probe features or diagonal clearances. Hole fits cover X/Y and up to four dominant facet-inferred horizontal axes, not every possible tunnel.",
                "BBox overlap suggests possible interlock, not intended motion. STL tessellation does not identify a designed hinge or stop surface.",
                "Elephant-foot compensation is a contour correction, not measured squish. A proxy budget is explicitly labeled.",
                "World Z=0 is the bed. Modifier and negative-volume meshes are excluded; their boolean effects are not simulated.",
            ]))}


def _top_samples(parts,bounds,spacing):
    low,high=bounds
    area=max(0,float((high[0]-low[0])*(high[1]-low[1])))
    spacing=max(spacing,np.sqrt(area/1024),float(np.max(high[:2]-low[:2]))/1024)
    x=np.arange(low[0]+spacing/2,high[0],spacing)
    y=np.arange(low[1]+spacing/2,high[1],spacing)
    if not len(x) or not len(y):
        return []
    grid=np.array(np.meshgrid(x,y)).reshape(2,-1).T
    samples=[]
    for part in parts:
        mesh=part["mesh"]
        for triangle,normal in zip(mesh.triangles,mesh.face_normals):
            if normal[2]<.995:
                continue
            mask=((grid>=triangle[:,:2].min(0)) & (grid<=triangle[:,:2].max(0))).all(1)
            points=grid[mask]
            if not len(points):
                continue
            points=points[contains(points,triangle[:,:2])]
            z=triangle[0,2]-((points-triangle[0,:2])@normal[:2])/normal[2]
            for p,height in zip(points,z):
                if low[2]-1e-6<=height<=high[2]+1e-6:
                    samples.append([float(p[0]),float(p[1]),float(height)])
    return np.unique(np.round(samples,6),axis=0).tolist() if samples else []


def previews(project, issues):
    triangles = []
    for resource in project.meshes():
        if resource["role"] == "normal_part":
            faces = resource["faces"]
            selected = np.linspace(0, len(faces)-1, min(50000, len(faces)), dtype=int)
            triangles.extend(resource["vertices"][faces[selected]])
    if not triangles:
        return []
    triangles = np.asarray(triangles)
    result = []
    for name, projection in (("isometric", [[.866, -.866, 0], [.5, .5, -1]]), ("top", [[1, 0, 0], [0, -1, 0]]), ("front", [[1, 0, 0], [0, 0, -1]])):
        projection = np.asarray(projection)
        points = triangles @ projection.T
        low = points.min(axis=(0, 1))
        extent = np.maximum(points.max(axis=(0, 1))-low, 1e-6)
        scale = min(880/extent[0], 560/extent[1])
        def coords(v):
            return (np.asarray(v) @ projection.T-low)*scale+30
        elements = []
        view=np.array([1,1,1] if name=="isometric" else [0,0,1] if name=="top" else [0,1,0],dtype=float)
        view/=np.linalg.norm(view)
        normal=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        light=(normal@view)/np.maximum(np.linalg.norm(normal,axis=1),1e-12)
        depth=triangles.mean(1)@view
        for face in np.argsort(depth):
            if light[face]<=1e-6:
                continue
            polygon=coords(triangles[face])
            shade=int(150+70*light[face])
            color=f'rgb({shade-20},{shade},{min(255,shade+8)})'
            elements.append('<polygon points="'+" ".join(f"{x:.2f},{y:.2f}" for x,y in polygon)+f'" fill="{color}" stroke="{color}" stroke-width="0.15"/>')
        for issue in issues:
            bounds = np.asarray(issue["location"]["bbox_mm"])
            corners = np.array([[x,y,z] for x in bounds[:,0] for y in bounds[:,1] for z in bounds[:,2]])
            projected = coords(corners)
            a,b = projected.min(0),projected.max(0)
            color = "#d92d20" if issue["severity"] == "high" else "#d68b00"
            title = html.escape(f'{issue["id"]}: {issue["category"]}; {issue["measurement"]}; heuristic')
            middle=(a+b)/2
            elements.append(f'<g id="{issue["id"]}"><title>{title}</title><circle cx="{middle[0]:.2f}" cy="{middle[1]:.2f}" r="3" fill="{color}"/><rect class="issue-box" x="{a[0]:.2f}" y="{a[1]:.2f}" width="{max(b[0]-a[0],2):.2f}" height="{max(b[1]-a[1],2):.2f}" fill="none" stroke="{color}" stroke-width="1.5"/>')
            for region in issue.get("clearance_regions", [])[::max(1,len(issue.get("clearance_regions", []))//250)]:
                c,d = coords([region["a_mm"],region["b_mm"]])
                elements.append(f'<path d="M {c[0]:.2f} {c[1]:.2f} L {d[0]:.2f} {d[1]:.2f}" stroke="#bb1671" stroke-opacity="0.8" stroke-width="1.3"/>')
            elements.append('</g>')
        result.append({"projection": name, "svg": '<svg xmlns="http://www.w3.org/2000/svg" width="940" height="620" viewBox="0 0 940 620"><style>.issue-box{display:none}g:hover .issue-box,g:target .issue-box{display:inline;stroke-width:3}</style><rect width="940" height="620" fill="white"/><text x="20" y="17" font-size="12">Heuristic risks: red high, amber advisory, magenta gap probes. Hover a marker or select an issue link.</text>'+"".join(elements)+'</svg>'})
    return result
