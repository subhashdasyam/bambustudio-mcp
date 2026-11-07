"""Correlate commanded paths with preflight regions. Never infer physical fusion."""
from __future__ import annotations

import math
import hashlib

import numpy as np
from rtree import index

from .sections import clearance_probes, segment_distance
from .catalog import Catalog

# Pinned GCodeWriter.hpp rounds XYZ/IJ to three decimals; radius comparisons need the accumulated rounding budget.
COORDINATE_TOLERANCE_MM = 3 * 10**-3
MARGIN_REVIEW_MM = 0.1


def capsule_interval(c, d, a, b, radius):
    """Distance interval along c..d covered by a round-ended a..b bead footprint."""
    c,d,a,b=(np.asarray(p,dtype=float)[:2] for p in (c,d,a,b))
    length=float(np.linalg.norm(d-c))
    if length<1e-12:
        return None
    v=(d-c)/length
    intervals=[]
    for point in (a,b):
        offset=c-point
        along=float(offset@v)
        disc=radius**2-float(offset@offset)+along**2
        if disc>=-1e-10:
            root=math.sqrt(max(0,disc))
            intervals.append((-along-root,-along+root))
    delta=b-a
    span=float(np.linalg.norm(delta))
    if span>1e-12:
        u=delta/span
        normal=np.array([-u[1],u[0]])
        low,high=0.,length
        for offset,slope,lower,upper in ((float((c-a)@u),float(v@u),0.,span),
                                        (float((c-a)@normal),float(v@normal),-radius,radius)):
            if abs(slope)<1e-12:
                if not lower<=offset<=upper:
                    low,high=1.,0.
            else:
                x,y=sorted(((lower-offset)/slope,(upper-offset)/slope))
                low,high=max(low,x),min(high,y)
        if low<=high:
            intervals.append((low,high))
    intervals=[(max(0.,lo),min(length,hi)) for lo,hi in intervals if max(0.,lo)<=min(length,hi)]
    return (min(lo for lo,_ in intervals),max(hi for _,hi in intervals)) if intervals else None


def union_intervals(intervals):
    result=[]
    for lo,hi in sorted(intervals):
        if hi<=lo+1e-8:
            continue
        if result and lo<=result[-1][1]+1e-8:
            result[-1][1]=max(result[-1][1],hi)
        else:
            result.append([lo,hi])
    return result


def uncovered_intervals(intervals, covered):
    result=union_intervals(intervals)
    for start,end in union_intervals(covered):
        pieces=[]
        for lo,hi in result:
            if end<=lo or start>=hi:
                pieces.append([lo,hi])
            else:
                if lo<start: pieces.append([lo,start])
                if end<hi: pieces.append([end,hi])
        result=pieces
    return result


def box_interval(a, b, bounds):
    low, high = np.asarray(bounds)
    delta = b-a
    enter, leave = 0., 1.
    for axis in range(3):
        if abs(delta[axis]) < 1e-12:
            if a[axis] < low[axis]-1e-6 or a[axis] > high[axis]+1e-6:
                return None
        else:
            t = sorted(((low[axis]-a[axis])/delta[axis], (high[axis]-a[axis])/delta[axis]))
            enter, leave = max(enter,t[0]), min(leave,t[1])
    return (enter,leave) if enter<=leave else None


def box_crossing(a,b,bounds):
    return box_interval(a,b,bounds) is not None


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
        self.deposited=[]
        self.deposition_index=index.Index()
        self.travel_candidates=[]
        self.last_print_z=None
        self.gap_edges={}
        self.event_paths={}
        self.shared_paths={}
        self.travelled = True
        self.width = self.report.get("parameters",{}).get("line_width_mm")
        self.layer_height = self.report.get("parameters",{}).get("layer_height_mm")
        self.emitted_width=None
        self.first_height=self._positive("initial_layer_print_height") or self.layer_height
        self.first_width=self._positive("initial_layer_line_width")
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
            for region in (probe for run in issue.get("clearance_regions",[]) for probe in clearance_probes(run)):
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

    def _positive(self,key):
        try:
            value=float(self.settings.get(key))
            return value if math.isfinite(value) and value>0 else None
        except (ValueError,TypeError):
            return None

    def bead_width(self,feature,z):
        if self.emitted_width:
            return self.emitted_width
        if self.first_height and z<=self.first_height+COORDINATE_TOLERANCE_MM and self.first_width:
            return self.first_width
        key={"Outer wall":"outer_wall_line_width","Inner wall":"inner_wall_line_width",
             "Top surface":"top_surface_line_width","Sparse infill":"sparse_infill_line_width",
             "Internal solid infill":"internal_solid_infill_line_width","Support":"support_line_width"}.get(feature)
        return (self._positive(key) if key else None) or self.width

    def unknown(self, message):
        self.reasons.add(message)

    def record(self, issue_id, kind, a, b, line, feature, evidence="observed_toolpath"):
        key = (issue_id,kind)
        length = float(np.linalg.norm(b-a))
        event = self.events.setdefault(key,{"geometry_issue_id":issue_id,"kind":kind,"evidence":evidence,
                                          "count":0,"total_length_mm":0.,"z_range_mm":[float(a[2]),float(a[2])],"examples":[]})
        event["count"] += 1
        event["total_length_mm"] += length
        path_id=hashlib.sha256(repr((line,tuple(a),tuple(b))).encode()).hexdigest()[:14]
        self.event_paths.setdefault(key,set()).add(path_id)
        self.shared_paths.setdefault((path_id,kind),[length,set()])[1].add(issue_id)
        event["z_range_mm"] = [min(event["z_range_mm"][0],float(min(a[2],b[2]))),max(event["z_range_mm"][1],float(max(a[2],b[2])))]
        if len(event["examples"]) < 20:
            direction = (b-a)/length if length else b-a
            event["examples"].append({"path_id":path_id,"line":line,"z_mm":float(b[2]),"feature":feature,"length_mm":length,
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
            points=np.array([a,b])
            self._segment(a,b,extruding,feature,line)
        if extruding and feature not in {"Support","Support interface","Brim","Skirt"} and self.top_lines:
            if len(self.deposited)<200000:
                radius=self.bead_width(feature,float(b[2]))/2
                low,high=points.min(0),points.max(0)
                self.deposition_index.insert(len(self.deposited),(low[0]-radius,low[1]-radius,high[0]+radius,high[1]+radius))
                self.deposited.append((points,radius,line))
            else:
                self.top_paths_truncated=True

    def _segment(self,a,b,extruding,feature,line):
        low,high = np.minimum(a,b),np.maximum(a,b)
        if extruding:
            width=self.bead_width(feature,float(b[2]))
            radius=width/2
            height=self.layer_height or 0.
            self.last_print_z=float(b[2])
            hit_ids=set()
            search_low=low-np.array([radius+MARGIN_REVIEW_MM,radius+MARGIN_REVIEW_MM,height])
            search_high=high+np.array([radius+MARGIN_REVIEW_MM,radius+MARGIN_REVIEW_MM,0])
            for item in self.index.intersection(tuple(search_low)+tuple(search_high)):
                issue_id,region = self.gaps[item]
                c,d = np.asarray(region["a_mm"]),np.asarray(region["b_mm"])
                if abs(d[2]-c[2]) > 1e-6:
                    # Vertical gaps are intervals: only commands inside the open Z interval count.
                    inside = min(b[2],a[2]) > min(c[2],d[2])+1e-5 and max(b[2],a[2]) < max(c[2],d[2])-1e-5
                    near = segment_distance(a[:2],b[:2],c[None,:2])[0] < self.width/20
                    hit = inside and near
                    gap=float(abs(d[2]-c[2]))
                    floor,ceiling=float(low[2]-height),float(high[2])
                    lo,hi=sorted((float(c[2]),float(d[2])))
                    close=segment_distance(a[:2],b[:2],c[None,:2])[0]<=radius
                    seen_left=close and floor<=lo+MARGIN_REVIEW_MM and ceiling>=lo-MARGIN_REVIEW_MM
                    seen_right=close and floor<=hi+MARGIN_REVIEW_MM and ceiling>=hi-MARGIN_REVIEW_MM
                    consumed_left=max(0.,ceiling-lo) if seen_left and floor<=lo+1e-6 else 0.
                    consumed_right=max(0.,hi-floor) if seen_right and ceiling>=hi-1e-6 else 0.
                    interior=close and floor>lo+1e-6 and ceiling<hi-1e-6
                    layer=None
                else:
                    z_low,z_high=region["z_range_mm"]
                    hit = z_low+1e-6 < b[2] <= z_high+1e-6 and gap_crossing(a,b,c,d,COORDINATE_TOLERANCE_MM)
                    if high[2]-height>=z_high-1e-6 or low[2]<=z_low+1e-6:
                        continue
                    gap=float(np.linalg.norm(d[:2]-c[:2]))
                    distances=segment_distance(a[:2],b[:2],np.array([c[:2],d[:2]]))
                    seen_left,seen_right=distances<=radius+MARGIN_REVIEW_MM
                    interval=capsule_interval(c,d,a,b,radius)
                    consumed_left=interval[1] if seen_left and interval and interval[0]<=1e-6 else 0.
                    consumed_right=gap-interval[0] if seen_right and interval and interval[1]>=gap-1e-6 else 0.
                    interior=interval is not None and interval[0]>1e-6 and interval[1]<gap-1e-6 and interval[1]>interval[0]+1e-6
                    layer=round(float(b[2]),6)
                if seen_left or seen_right or interior:
                    edges=self.gap_edges.setdefault((item,layer),{"left":None,"right":None,"gap_mm":gap,"z_mm":float(b[2]),"width_mm":width,"interior_obstruction":False})
                    for side,seen,consumed in (("left",seen_left,consumed_left),("right",seen_right,consumed_right)):
                        if seen and (edges[side] is None or consumed>edges[side]):
                            edges[side]=consumed
                            edges["z_mm"]=float(b[2])
                    edges["interior_obstruction"]|=interior
                    edges["width_mm"]=max(edges["width_mm"],width)
                if hit:
                    hit_ids.add(issue_id)
            for issue_id in sorted(hit_ids):
                self.record(issue_id,"extrusion_in_clearance",a,b,line,feature)
            if feature in {"Bridge","Internal bridge","Overhang wall"}:
                matched=[]
                for issue in self.issues:
                    if issue["category"] not in {"bridges","holes_lying_flat","overhangs","floating_islands","bed_contact"}:
                        continue
                    for region in issue.get("ceiling_regions",[{"bbox_mm":issue["location"]["bbox_mm"]}]):
                        bounds=np.array(region["bbox_mm"],dtype=float)
                        bounds[0]-=[radius,radius,COORDINATE_TOLERANCE_MM]
                        bounds[1]+=[radius,radius,height+COORDINATE_TOLERANCE_MM]
                        if box_crossing(a,b,bounds):
                            matched.append(issue)
                            break
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
                    self.top_index.insert(len(self.top_lines),(low[0]-radius,low[1]-radius,high[0]+radius,high[1]+radius))
                    self.top_lines.append((a,b,width))
                else:
                    self.top_paths_truncated=True
            self.travelled=False
        else:
            self.travelled=True
            if np.linalg.norm(b[:2]-a[:2])<1e-8 or self.last_print_z is None or max(abs(a[2]-self.last_print_z),abs(b[2]-self.last_print_z))>COORDINATE_TOLERANCE_MM:
                return
            intervals={}
            for item in self.top_index.intersection((low[0],low[1],high[0],high[1])):
                c,d,top_width=self.top_lines[item]
                if abs(b[2]-c[2])>COORDINATE_TOLERANCE_MM:
                    continue
                interval=capsule_interval(a,b,c,d,top_width/2)
                if interval:
                    for issue in self.issues:
                        if issue["category"] not in {"cosmetic_faces","top_surfaces"}:
                            continue
                        bounds=np.array(issue["location"]["bbox_mm"],dtype=float)
                        bounds[0,2]-=COORDINATE_TOLERANCE_MM
                        bounds[1,2]+=(self.layer_height or 0)+COORDINATE_TOLERANCE_MM
                        span=box_interval(a,b,bounds)
                        if span:
                            length=float(np.linalg.norm(b[:2]-a[:2]))
                            lo,hi=max(interval[0],span[0]*length),min(interval[1],span[1]*length)
                            if lo<hi:
                                intervals.setdefault(issue["id"],[]).append((lo,hi))
            if intervals:
                self.travel_candidates.append((a,b,line,intervals))

    def result(self):
        if self.top_paths_truncated:
            self.unknown("Top/deposition cache reached its segment limit; exposed-top travel was not evaluated.")
        else:
            for a,b,line,regions in self.travel_candidates:
                low,high=np.minimum(a,b),np.maximum(a,b)
                covered=[]
                remaining=union_intervals([interval for intervals in regions.values() for interval in intervals])
                for item in self.deposition_index.intersection((low[0],low[1],high[0],high[1])):
                    points,radius,later=self.deposited[item]
                    if later<=line or points[:,2].min()<=b[2]+COORDINATE_TOLERANCE_MM:
                        continue
                    for c,d in zip(points,points[1:]):
                        interval=capsule_interval(a,b,c,d,radius)
                        if interval:
                            covered.append(interval)
                            remaining=uncovered_intervals(remaining,[interval])
                            if not remaining: break
                    if not remaining: break
                direction=(b-a)/np.linalg.norm(b[:2]-a[:2])
                exposed={issue_id:uncovered_intervals(intervals,covered) for issue_id,intervals in regions.items()}
                boundaries=sorted({edge for intervals in exposed.values() for interval in intervals for edge in interval})
                for lo,hi in zip(boundaries,boundaries[1:]):
                    if hi-lo<1e-8: continue
                    middle=(lo+hi)/2
                    for issue_id,intervals in exposed.items():
                        if not any(start<=middle<=end for start,end in intervals): continue
                        self.record(issue_id,"travel_over_finished_top",a+lo*direction,a+hi*direction,line,"Travel",
                                    "no_z_hop_and_no_later_deposition_over_this_xy_interval")
        margins={}
        for (index,_),edges in self.gap_edges.items():
            issue_id,region=self.gaps[index]
            both=edges["left"] is not None and edges["right"] is not None
            remaining=edges["gap_mm"]-(edges["left"] or 0.)-(edges["right"] or 0.)
            if edges["interior_obstruction"]:
                remaining=min(remaining,0.)
            entry=margins.setdefault(issue_id,{"both_sides_samples":0,"one_side_samples":0,"remaining_margin_mm":None,
                                               "review_threshold_mm":MARGIN_REVIEW_MM,"status":"unknown_missing_boundary_paths",
                                               "evidence":"nominal bead envelope; not proof of fusion"})
            entry["both_sides_samples" if both else "one_side_samples"]+=1
            if (both or edges["interior_obstruction"]) and (entry["remaining_margin_mm"] is None or remaining<entry["remaining_margin_mm"]):
                entry.update(remaining_margin_mm=remaining,z_mm=edges["z_mm"],line_width_mm=edges["width_mm"],
                             probe_a_mm=region["a_mm"],probe_b_mm=region["b_mm"],
                             status="bead_overlap_candidate" if remaining<0 else "bead_obstruction_candidate" if edges["interior_obstruction"] else "low_margin" if remaining<MARGIN_REVIEW_MM-1e-6 else "estimated_margin")
        unique={}
        for (_,kind),(length,ids) in self.shared_paths.items():
            total=unique.setdefault(kind,{"unique_paths":0,"total_length_mm":0.,"shared_paths":0})
            total["unique_paths"]+=1
            total["total_length_mm"]+=length
            total["shared_paths"]+=int(len(ids)>1)
        for (issue_id,kind),event in self.events.items():
            paths=self.event_paths[(issue_id,kind)]
            shared=[self.shared_paths[(path,kind)][1] for path in paths if len(self.shared_paths[(path,kind)][1])>1]
            event["shared_path_count"]=len(shared)
            event["shared_with_geometry_issue_ids"]=sorted(set().union(*shared)-{issue_id}) if shared else []
            event["totals_include_shared_paths"]=bool(shared)
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
            if issue["category"]=="clearances":
                linked[-1]["clearance_margin"]=margins.get(issue["id"],{"status":"unknown_no_nearby_paths","remaining_margin_mm":None})
                if not matched and linked[-1]["clearance_margin"].get("remaining_margin_mm") is not None:
                    linked[-1]["status"]="unknown" if self.reasons else linked[-1]["clearance_margin"]["status"]
                    linked[-1]["kinds"].append("bead_margin_estimate")
        return {"status":"unknown" if self.reasons else "observed", "observations":list(self.events.values())+coverage,
                "feature_paths":{name:{"gcode_moves":len(value["lines"]),"linearized_segments":value["linearized_segments"],
                                       "commanded_length_mm":value["path_length_mm"],"longest_segment_mm":value["longest_segment_mm"],
                                       "segments":value["segments"],"segments_truncated":value["linearized_segments"]>len(value["segments"])} for name,value in self.feature_lengths.items()},
                "issue_results":linked,"unique_path_totals":unique,"cosmetic_requirements":"declared" if any(i["category"]=="cosmetic_faces" for i in self.issues) else "unknown: no cosmetic regions declared",
                "coordinate_frame":{"status":"aligned" if self.offset is not None else "unknown","applied_extruder_offset_mm":self.offset.tolist() if self.offset is not None else None},
                "unmapped_arc_examples":self.unknown_arcs,
                "unknown_reasons":sorted(self.reasons),"limitations":[
                    "Clearance observations prove commanded material paths enter sampled nominal voids, not that a physical joint fused.",
                    "Bead edges use emitted LINE_WIDTH or saved feature/first-layer width and layer height. Actual spread and roof sag remain unverified; positive margin does not clear a geometric risk.",
                    "Arc subdivision uses project line width; endpoint/radius comparisons allow the pinned writer's 0.003 mm rounding budget.",
                    "Outer-wall starts after travel are seam candidates, not exact firmware seam detection.",
                    "Travel is clipped to exposed top bead footprints at the current print Z without Z-hop, excluding all later higher-layer bead footprints. This is not proof of nozzle contact or scratches.",
                    "Ironing coverage applies only to sampled intended top points and known spacing, not every point of a continuous face.",
                ]}
