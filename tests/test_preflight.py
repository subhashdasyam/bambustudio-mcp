import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from bambustudio_mcp.geometry import analyze


def project(mesh, **settings):
    values={"nozzle_diameter":["0.4"],"line_width":"0.4","layer_height":"0.2",
            "initial_layer_line_width":"0.5","elefant_foot_compensation":"0.15"}
    values.update(settings)
    return SimpleNamespace(settings=values,meshes=lambda:[{
        "vertices":mesh.vertices,"faces":mesh.faces,"resource_id":"sample","instance":0,"role":"normal_part"}])


def hole_block(teardrop=False):
    """Square prism with a through-hole, made without boolean or polygon dependencies."""
    count=64
    angles=np.arange(count)*2*np.pi/count
    direction=np.c_[np.cos(angles),np.sin(angles)]
    outer=direction*4/np.abs(direction).max(1)[:,None]
    radius=np.full(count,2.)
    if teardrop:
        mask=(angles>=np.pi/4)&(angles<=3*np.pi/4)
        radius[mask]=2*np.sqrt(2)/(np.sin(angles[mask])+np.abs(np.cos(angles[mask])))
    inner=direction*radius[:,None]
    vertices=[]
    for x in (-5,5):
        for loop in (outer,inner):
            vertices.extend([[x,y,z+4] for y,z in loop])
    faces=[]
    for i in range(count):
        j=(i+1)%count
        # End rings and the two cylindrical/prismatic walls.
        faces.extend([[i,j,count+j],[i,count+j,count+i],
                      [2*count+i,3*count+j,2*count+j],[2*count+i,3*count+i,3*count+j],
                      [i,2*count+j,j],[i,2*count+i,2*count+j],
                      [count+i,count+j,3*count+j],[count+i,3*count+j,3*count+i]])
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True)
    if mesh.volume<0:
        mesh.invert()
    assert mesh.is_watertight
    return mesh


def hinge(gap):
    block=hole_block()
    pin=trimesh.creation.cylinder(radius=2-gap,height=12,sections=64)
    pin.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2,[0,1,0]))
    pin.apply_translation([0,0,4])
    return trimesh.util.concatenate([block,pin])


def test_round_horizontal_hole_and_teardrop_roof():
    round_report=analyze(project(hole_block()))
    holes=[i for i in round_report["issues"] if i["category"]=="holes_lying_flat" and i.get("feature")=="hole"]
    assert holes and holes[0]["diameter_mm"]==pytest.approx(4,abs=.02)
    assert holes[0]["axis_direction"]==[1,0,0]
    teardrop=analyze(project(hole_block(teardrop=True)))
    assert not [i for i in teardrop["issues"] if i["category"]=="holes_lying_flat" and i.get("feature")=="hole"]


def test_long_thin_profile_is_not_a_huge_round_pin():
    from bambustudio_mcp.preflight import _circle
    assert _circle(np.array([[-50,0],[50,0],[50,.2],[-50,.2]])) is None


def test_horizontal_hole_on_oblique_xy_axis():
    mesh=hole_block()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi/4,[0,0,1]))
    report=analyze(project(mesh))
    holes=[i for i in report['issues'] if i['category']=='holes_lying_flat' and i.get('feature')=='hole']
    assert any(abs(i['axis_direction'][0])==pytest.approx(2**-.5,abs=.02) and abs(i['axis_direction'][1])==pytest.approx(2**-.5,abs=.02) for i in holes)


def test_tight_hinge_and_larger_gap_advisory():
    tight=analyze(project(hinge(.2)))
    loose=analyze(project(hinge(.5)))
    assert any(i["category"]=="print_in_place" for i in tight["issues"])
    assert any(i["category"]=="clearances" and i["direction"]=="xy" and i["severity"]=="high" for i in tight["issues"])
    # The requested 2-line-width threshold still gives .5 mm an advisory, not a universal failure.
    assert not [i for i in loose["issues"] if i["category"]=="clearances" and i["direction"]=="xy" and i["severity"]=="high"]
    assert any(i["category"]=="clearances" and i["severity"]=="medium" for i in loose["issues"])


def test_sideways_cylinder_has_bed_tangent_risk():
    cylinder=trimesh.creation.cylinder(radius=3,height=10,sections=48)
    cylinder.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2,[1,0,0]))
    cylinder.apply_translation([0,0,3])
    report=analyze(project(cylinder))
    assert any(i["category"]=="bed_contact" and i.get("subtype")=="bed_tangent_curve" for i in report["issues"])


def test_floating_island_and_no_false_floating_box():
    base=trimesh.creation.box([4,4,2]);base.apply_translation([0,0,1])
    upper=trimesh.creation.box([2,2,2]);upper.apply_translation([0,0,5])
    report=analyze(project(trimesh.util.concatenate([base,upper])))
    assert any(i["category"]=="floating_islands" for i in report["issues"])
    assert not any(i["category"]=="floating_islands" for i in analyze(project(base))["issues"])


def test_stepped_mesh_and_shallow_top_have_distinct_evidence():
    cylinders=[]
    for i in range(8):
        c=trimesh.creation.cylinder(radius=4-i*.25,height=.2,sections=16)
        c.apply_translation([0,0,.1+i*.2]);cylinders.append(c)
    report=analyze(project(trimesh.util.concatenate(cylinders)))
    assert any(i["category"]=="faceting" for i in report["issues"])
    assert all("ambiguity" in i or "CAD" in i["why_it_fails"] or i.get("subtype")=="layer_stair_steps"
               for i in report["issues"] if i["category"]=="faceting")


def test_gap_threshold_scales_with_project_resolution():
    mesh=hinge(.5)
    fine=analyze(project(mesh,line_width="0.2",nozzle_diameter=["0.2"]))
    assert not [i for i in fine["issues"] if i["category"]=="clearances" and i["direction"]=="xy"]
    unknown=project(mesh);unknown.settings={}
    report=analyze(unknown)
    assert report["analysis_status"]=="unknown"
    assert "nozzle_diameter" in report["parameters"]["missing"]


def test_first_layer_schedule_and_constant_gap_region_compression():
    a=trimesh.creation.box([2,2,1]);a.apply_translation([0,0,.5])
    b=a.copy();b.apply_translation([2.2,0,0])
    r=analyze(project(trimesh.util.concatenate([a,b]),layer_height='0.12',initial_layer_print_height='0.2'))
    assert r['layer_scan']['count']==8
    first=[i for i in r['issues'] if i.get('subtype')=='first_layer_gap']
    assert first and all(i['location']['z_range_mm']==[0,.2] for i in first)
    gaps=[i for i in r['issues'] if i['category']=='clearances']
    assert any(region['z_range_mm'][0]==0 and region['z_range_mm'][1]>.9 for i in gaps for region in i['clearance_regions'])
    assert sum(len(i['clearance_regions']) for i in gaps)==1


def test_long_hinge_response_size_and_interlock_bounds(service, opened, monkeypatch):
    import json
    from bambustudio_mcp import service as service_module
    mesh=hinge(.2)
    mesh.apply_scale([130/12,1,1])
    geometry=analyze(project(mesh))
    monkeypatch.setattr(service_module,'analyze',lambda *args,**kwargs:geometry)
    report=service.analyze_geometry(opened['project_id'])
    assert report['analysis_status']=='complete'
    assert len(json.dumps(report).encode())<200_000
    assert all(i['clearance_region_count']<1000 for i in report['issues'] if 'clearance_region_count' in i)
    interlock=next(i for i in report['issues'] if i['category']=='print_in_place')
    assert interlock['location']['bbox_mm']==pytest.approx(np.array([[-130*5/12,-1.8,2.2],[130*5/12,1.8,5.8]]))


def test_clearance_run_cap_reports_partial_coverage(monkeypatch):
    monkeypatch.setattr('bambustudio_mcp.preflight.MAX_CLEARANCE_REGIONS',1)
    report=analyze(project(hinge(.2)))
    assert report['analysis_status']=='partial'
    assert any('capped at 1 runs' in limit for limit in report['limitations'])
    assert all(len(i['clearance_regions'])<=1 for i in report['issues'] if 'clearance_regions' in i)


def test_narrow_wedge_section_is_not_reported_as_a_parallel_thin_wall():
    vertices=np.array([[0,-2,0],[0,-2,2],[1,-2,2],[0,2,0],[0,2,2],[1,2,2]])
    faces=[[0,2,1],[3,4,5],[0,1,4],[0,4,3],[1,2,5],[1,5,4],[2,0,3],[2,3,5]]
    wedge=trimesh.Trimesh(vertices,faces,process=True)
    report=analyze(project(wedge))
    thin=next(i for i in report['issues'] if 'minimum_section' in i)
    assert thin['severity']=='medium' and not thin['minimum_section']['opposing_parallel_faces']
    assert thin['location']['z_range_mm']==[thin['minimum_section']['z_mm']]*2
    wall=trimesh.creation.box([.2,3,2]);wall.apply_translation([0,0,1])
    thin=next(i for i in analyze(project(wall))['issues'] if 'minimum_section' in i)
    assert thin['severity']=='high' and thin['minimum_section']['opposing_parallel_faces']


def test_issue_schema_ids_highlights_and_measured_squish():
    mesh=hinge(.2)
    a=analyze(project(mesh),context={"elephant_foot_expansion_mm":.3})
    b=analyze(project(mesh),context={"elephant_foot_expansion_mm":.3})
    assert [i["id"] for i in a["issues"]]==[i["id"] for i in b["issues"]]
    for issue in a["issues"]:
        assert {"id","category","severity","location","measurement","why_it_fails","design_fix","slicer_fix"}<=issue.keys()
        assert issue["location"]["z_range_mm"]==np.asarray(issue["location"]["bbox_mm"])[:,2].tolist()
        assert issue["id"] in a["previews"][-1]["svg"]
    assert a["parameters"]["first_layer_squash_budget_mm"]==.6


def test_tiiny_stand_real_regression():
    path=Path('/home/onepiece/dev/tiiny/3dpart/tiiny_stand_wide.stl')
    if not path.exists():
        pytest.skip('Local real-world regression STL is not distributed with the package')
    original=hashlib.sha256(path.read_bytes()).digest()
    mesh=trimesh.load_mesh(path)
    report=analyze(project(mesh))
    categories={i['category'] for i in report['issues']}
    assert {'holes_lying_flat','print_in_place','bed_contact','faceting','clearances'}<=categories
    assert any(i.get('subtype')=='first_layer_gap' for i in report['issues'])
    assert any(i.get('subtype')=='bed_tangent_curve' for i in report['issues'])
    assert any(i.get('feature')=='hole' and 3<i['diameter_mm']<4 for i in report['issues'])
    assert all(i['diameter_mm']<20 for i in report['issues'] if i['category']=='holes_lying_flat')
    assert report['layer_scan']['count']==55
    assert hashlib.sha256(path.read_bytes()).digest()==original
