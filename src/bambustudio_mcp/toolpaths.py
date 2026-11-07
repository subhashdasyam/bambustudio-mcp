"""Correlate commanded paths with preflight regions. Never infer physical fusion."""
from __future__ import annotations

import math

import numpy as np
from rtree import index

from .sections import segment_distance
from .catalog import Catalog

# Pinned GCodeWriter.hpp rounds XYZ/IJ to three decimals; radius comparisons need the accumulated rounding budget.
COORDINATE_TOLERANCE_MM = 3 * 10**-3


def box_crossing(a, b, bounds):
    low, high = np.asarray(bounds)
    delta = b-a
    enter, leave = 0., 1.
    for axis in range(3):
        if abs(delta[axis]) < 1e-12:
            if a[axis] < low[axis]-1e-6 or a[axis] > high[axis]+1e-6:
                return False
        else:
            t = sorted(((low[axis]-a[axis])/delta[axis], (high[axis]-a[axis])/delta[axis]))
            enter, leave = max(enter,t[0]), min(leave,t[1])
    return enter <= leave


def gap_crossing(a, b, c, d, margin=0.):
    """Intersection with the open interior of a measured XY clearance probe."""
    a,b,c,d = np.asarray(a)[:2],np.asarray(b)[:2],np.asarray(c)[:2],np.asarray(d)[:2]
    gap_length=float(np.linalg.norm(d-c))
    if margin:
        if gap_length<=2*margin:
            return False
        direction=(d-c)/gap_length
        c,d=c+margin*direction,d-margin*direction
    r,s = b-a,d-c
    cross = lambda x,y: float(x[0]*y[1]-x[1]*y[0])
    denominator = cross(r,s)
    if abs(denominator) < 1e-10:
        if abs(cross(c-a,r)) > 1e-8 or r@r < 1e-12:
            return False
        t = sorted((float((c-a)@r/(r@r)),float((d-a)@r/(r@r))))
        return max(0,t[0]+1e-5) < min(1,t[1]-1e-5)
    t,u = cross(c-a,s)/denominator,cross(c-a,r)/denominator
    return -1e-8 <= t <= 1+1e-8 and 1e-5 < u < 1-1e-5


def arc_points(a, b, words, clockwise, resolution):
    if "R" in words:
        delta=b[:2]-a[:2]
        chord=float(np.linalg.norm(delta));radius=abs(words["R"])
        if chord<1e-10 or chord>2*radius+1e-6:
            return None
        middle=(a[:2]+b[:2])/2
        side=np.array([-delta[1],delta[0]])/chord*np.sqrt(max(0,radius**2-chord**2/4))
        candidates=(middle+side,middle-side)
        center=None
        for candidate in candidates:
            start=math.atan2(*(a[:2]-candidate)[::-1]);end=math.atan2(*(b[:2]-candidate)[::-1])
            sweep=(start-end if clockwise else end-start)%(2*math.pi)
            if (sweep<=np.pi+1e-6)==(words["R"]>=0):
                center=candidate;break
        if center is None:
            return None
    elif any(k in words for k in ("I","J")):
        center = a[:2]+[words.get("I",0),words.get("J",0)]
    else:
        return None
    radius = float(np.linalg.norm(a[:2]-center))
    if radius < 1e-10 or abs(np.linalg.norm(b[:2]-center)-radius) > COORDINATE_TOLERANCE_MM:
        return None
    start = math.atan2(*(a[:2]-center)[::-1])
    end = math.atan2(*(b[:2]-center)[::-1])
    sweep = (start-end if clockwise else end-start) % (2*math.pi)
    if sweep < 1e-10:
        sweep = 2*math.pi
    count = max(2,math.ceil(radius*sweep/resolution))
    if count > 4096:
        return None
    direction=-1 if clockwise else 1
    critical=((np.arange(4)*np.pi/2-start)*direction)%(2*np.pi)
    distances=np.unique(np.r_[np.linspace(0,sweep,count+1),critical[(critical>0)&(critical<sweep)]])
    angles = start+direction*distances
    return np.c_[center[0]+radius*np.cos(angles),center[1]+radius*np.sin(angles),a[2]+(b[2]-a[2])*distances/sweep]


class ToolpathComparison:
    def __init__(self, report, settings):
        self.report = report or {}
        self.issues = self.report.get("issues",[])
        self.settings = settings
        self.reasons = set()
        self.events = {}
        self.feature_lengths = {}
        self.unknown_arcs = []
        self.gaps = []
        self.top_lines = []
        self.top_index = index.Index()
        self.top_paths_truncated = False
        self.travelled = True
        self.width = self.report.get("parameters",{}).get("line_width_mm")
        self.layer_height = self.report.get("parameters",{}).get("layer_height_mm")
        offsets=Catalog().decode("extruder_offset",settings.get("extruder_offset"))
        self.offset=None
        if offsets and all(len(v)==2 and np.isfinite(v).all() for v in offsets) and all(np.allclose(v,offsets[0],rtol=0,atol=1e-8) for v in offsets):
            self.offset=np.array([*offsets[0],0.],dtype=float)
        else:
            self.reasons.add("A single common extruder offset is unavailable; nozzle-to-model frame mapping is unknown.")
        if self.report.get("coordinate_frame_unknown"):
            self.reasons.add(self.report["coordinate_frame_unknown"])
            self.offset=None
        if report and report.get("analysis_status","complete")!="complete":
            self.reasons.add("Geometry analysis has partial or unknown coverage; absence of a path observation is not a clearance pass.")
        self.top_samples = {}
        self.covered = {}
        if not report:
            self.reasons.add("No geometry analysis for this revision was supplied.")
        if not self.width:
            self.reasons.add("No project-derived line width is available for geometry correlation.")
        prop = index.Property()
        prop.dimension = 3
        self.index = index.Index(properties=prop)
        for issue in self.issues:
            for region in issue.get("clearance_regions",[]):
                a,b = np.array(region["a_mm"]),np.array(region["b_mm"])
                low,high = np.minimum(a,b),np.maximum(a,b)
                low[2],high[2] = region["z_range_mm"]
                i = len(self.gaps)
                self.gaps.append((issue["id"],region))
                self.index.insert(i,tuple(low)+tuple(high))
            if issue.get("intent",{}).get("ironing"):
                samples = np.asarray(issue.get("top_samples_mm",[]),dtype=float).reshape(-1,3)
                self.top_samples[issue["id"]] = samples
                self.covered[issue["id"]] = np.zeros(len(samples),dtype=bool)

    def unknown(self, message):
        self.reasons.add(message)

    def record(self, issue_id, kind, a, b, line, feature, evidence="observed_toolpath"):
        key = (issue_id,kind)
        length = float(np.linalg.norm(b-a))
        event = self.events.setdefault(key,{"geometry_issue_id":issue_id,"kind":kind,"evidence":evidence,
                                          "count":0,"total_length_mm":0.,"z_range_mm":[float(a[2]),float(a[2])],"examples":[]})
        event["count"] += 1
        event["total_length_mm"] += length
        event["z_range_mm"] = [min(event["z_range_mm"][0],float(min(a[2],b[2]))),max(event["z_range_mm"][1],float(max(a[2],b[2])))]
        if len(event["examples"]) < 20:
            direction = (b-a)/length if length else b-a
            event["examples"].append({"line":line,"z_mm":float(b[2]),"feature":feature,"length_mm":length,
                                      "direction":direction.tolist(),"start_mm":a.tolist(),"end_mm":b.tolist()})

    def move(self, a, b, extruding, feature, line, command, words, conditional=False):
        if not self.width or self.offset is None:
            return
        a,b = np.array(a,dtype=float)+self.offset,np.array(b,dtype=float)+self.offset
        if not extruding and not self.top_lines:
            self.travelled=True
            return
        if conditional:
            self.unknown("Conditional motion was skipped; firmware branch execution is unknown.")
            return
        if command in {"G2","G3"}:
            points = arc_points(a,b,words,command=="G2",self.width/10)
            if points is None:
                self.unknown("Unsupported or excessively large arc was not correlated; no chord approximation was used.")
                if len(self.unknown_arcs)<10:
                    self.unknown_arcs.append({"line":line,"command":command,"start_mm":a.tolist(),"end_mm":b.tolist(),
                                              "arc_words":{k:words[k] for k in ("I","J","R") if k in words}})
                return
            for c,d in zip(points,points[1:]):
                self._segment(c,d,extruding,feature,line)
        else:
            self._segment(a,b,extruding,feature,line)

    def _segment(self,a,b,extruding,feature,line):
        low,high = np.minimum(a,b),np.maximum(a,b)
        if extruding:
            hit_ids=set()
            for item in self.index.intersection(tuple(low)+tuple(high)):
                issue_id,region = self.gaps[item]
                c,d = np.asarray(region["a_mm"]),np.asarray(region["b_mm"])
                if abs(d[2]-c[2]) > 1e-6:
                    # Vertical gaps are intervals: only commands inside the open Z interval count.
                    inside = min(b[2],a[2]) > min(c[2],d[2])+1e-5 and max(b[2],a[2]) < max(c[2],d[2])-1e-5
                    near = segment_distance(a[:2],b[:2],c[None,:2])[0] < self.width/20
                    hit = inside and near
                else:
                    z_low,z_high=region["z_range_mm"]
                    hit = z_low+1e-6 < b[2] <= z_high+1e-6 and gap_crossing(a,b,c,d,COORDINATE_TOLERANCE_MM)
                if hit:
                    hit_ids.add(issue_id)
            for issue_id in sorted(hit_ids):
                self.record(issue_id,"extrusion_in_clearance",a,b,line,feature)
            if feature in {"Bridge","Internal bridge","Overhang wall"}:
                matched = [i for i in self.issues if i["category"] in {"bridges","holes_lying_flat","overhangs","floating_islands","bed_contact"} and box_crossing(a,b,i["location"]["bbox_mm"])]
                for issue in matched:
                    self.record(issue["id"],"bridge_path" if "bridge" in feature.lower() else "overhang_perimeter",a,b,line,feature)
                length=float(np.linalg.norm(b-a))
                total=self.feature_lengths.setdefault(feature,{"lines":set(),"linearized_segments":0,"path_length_mm":0.,"longest_segment_mm":0.,"segments":[]})
                total["lines"].add(line)
                total["linearized_segments"]+=1
                total["path_length_mm"]+=length
                total["longest_segment_mm"]=max(total["longest_segment_mm"],length)
                if len(total["segments"])<10000:
                    total["segments"].append({"line":line,"z_mm":float(b[2]),"length_mm":length,"direction":((b-a)/length).tolist() if length else [0,0,0],
                                              "start_mm":a.tolist(),"end_mm":b.tolist(),"geometry_issue_ids":[i["id"] for i in matched]})
            for issue in self.issues:
                if issue["category"] != "cosmetic_faces":
                    continue
                if feature=="Outer wall" and self.travelled and issue.get("intent",{}).get("avoid_seams",True):
                    if box_crossing(a,a,issue["location"]["bbox_mm"]):
                        self.record(issue["id"],"seam_start_candidate",a,a,line,feature,"heuristic_outer_wall_start_after_travel")
                if feature=="Ironing" and issue["id"] in self.top_samples:
                    samples = self.top_samples[issue["id"]]
                    if len(samples):
                        spacing = self.settings.get("ironing_spacing")
                        try:
                            spacing = float(spacing)
                        except (ValueError,TypeError):
                            spacing = None
                        if not spacing or spacing<=0:
                            self.unknown("Ironing spacing is unavailable; full sampled coverage is unknown.")
                        else:
                            if not self.layer_height:
                                self.unknown("Layer height is unknown for mapping intended CAD tops to emitted layer ceilings.")
                            height=self.layer_height or 0
                            near = (segment_distance(a[:2],b[:2],samples[:,:2]) <= spacing/2+1e-6) & (samples[:,2]<=b[2]+COORDINATE_TOLERANCE_MM) & (samples[:,2]>=b[2]-height-COORDINATE_TOLERANCE_MM)
                            self.covered[issue["id"]] |= near
                    bounds=np.array(issue["location"]["bbox_mm"],dtype=float)
                    bounds[1,2]+=self.layer_height or 0
                    if box_crossing(a,b,bounds):
                        self.record(issue["id"],"ironing_in_intended_region",a,b,line,feature)
            if feature=="Top surface":
                if len(self.top_lines)<20000:
                    self.top_index.insert(len(self.top_lines),(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])))
                    self.top_lines.append((a,b))
                else:
                    self.top_paths_truncated=True
            self.travelled=False
        else:
            self.travelled=True
            if np.linalg.norm(b[:2]-a[:2])<1e-8:
                return
            for item in self.top_index.intersection((low[0],low[1],high[0],high[1])):
                c,d=self.top_lines[item]
                if min(a[2],b[2]) < max(c[2],d[2])-1e-5:
                    continue
                if gap_crossing(a,b,c,d):
                    for issue in self.issues:
                        if issue["category"] in {"cosmetic_faces","top_surfaces","faceting"} and box_crossing(c,d,issue["location"]["bbox_mm"]):
                            self.record(issue["id"],"travel_over_finished_top",a,b,line,feature or "Travel")
                    break

    def result(self):
        if self.top_paths_truncated:
            self.unknown("Finished top path cache reached 20000 segments; later crossings can be missed.")
        coverage=[]
        for issue_id,samples in self.top_samples.items():
            count=int(self.covered[issue_id].sum())
            coverage.append({"geometry_issue_id":issue_id,"kind":"ironing_coverage","samples":len(samples),"covered_samples":count,
                             "covered_fraction":count/len(samples) if len(samples) else None,
                             "status":"unknown" if not len(samples) or self.reasons else "sampled_coverage" if count==len(samples) else "incomplete_or_not_observed"})
        linked=[]
        for issue in self.issues:
            matched=[event for (issue_id,_),event in self.events.items() if issue_id==issue["id"]]
            linked.append({"geometry_issue_id":issue["id"],"category":issue["category"],
                           "status":"matched_paths" if matched else "unknown" if self.reasons else "not_observed_in_sampled_paths",
                           "kinds":[event["kind"] for event in matched],"physical_status":"unverified"})
        return {"status":"unknown" if self.reasons else "observed", "observations":list(self.events.values())+coverage,
                "feature_paths":{name:{"gcode_moves":len(value["lines"]),"linearized_segments":value["linearized_segments"],
                                       "commanded_length_mm":value["path_length_mm"],"longest_segment_mm":value["longest_segment_mm"],
                                       "segments":value["segments"],"segments_truncated":value["linearized_segments"]>len(value["segments"])} for name,value in self.feature_lengths.items()},
                "issue_results":linked,"cosmetic_requirements":"declared" if any(i["category"]=="cosmetic_faces" for i in self.issues) else "unknown: no cosmetic regions declared",
                "coordinate_frame":{"status":"aligned" if self.offset is not None else "unknown","applied_extruder_offset_mm":self.offset.tolist() if self.offset is not None else None},
                "unmapped_arc_examples":self.unknown_arcs,
                "unknown_reasons":sorted(self.reasons),"limitations":[
                    "Clearance observations prove commanded material paths enter sampled nominal voids, not that a physical joint fused.",
                    "Paths are nominal centerlines. Bead spread, sag and actual nozzle widths are not simulated.",
                    "Arc subdivision uses project line width; endpoint/radius comparisons allow the pinned writer's 0.003 mm rounding budget.",
                    "Outer-wall starts after travel are seam candidates, not exact firmware seam detection.",
                    "Travel intersections are XY projections over already emitted top paths, not proof of nozzle contact or scratches.",
                    "Ironing coverage applies only to sampled intended top points and known spacing, not every point of a continuous face.",
                ]}
