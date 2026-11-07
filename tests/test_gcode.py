import hashlib
import json
import zipfile

import pytest

from bambustudio_mcp.gcode import inspect_sliced_project


def sliced(tmp_path, gcode, settings=None, second_plate=None):
    path = tmp_path / "sliced.gcode.3mf"
    settings=dict(settings or {"nozzle_temperature_initial_layer":["245"],"nozzle_temperature":["250"]})
    settings.setdefault("extruder_offset",["0x0"])
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))
        archive.writestr("Metadata/plate_1.gcode", gcode)
        if second_plate:
            archive.writestr("Metadata/plate_2.gcode", second_plate)
    return path


OBJECT = "; FEATURE: Outer wall\n; Z_HEIGHT: 0.2\n; LAYER_HEIGHT: 0.2\n"


def test_first_object_temperature_excludes_prime_and_support(tmp_path):
    path = sliced(tmp_path, """M83
M109 S205
; FEATURE: Custom
G1 X10 E1 F1200
; FEATURE: Support
G1 X20 E1
""" + OBJECT + "G1 X30 E1\n")
    before = hashlib.sha256(path.read_bytes()).digest()
    report = inspect_sliced_project(path)
    first = report["plates"][0]["first_object_extrusion"]
    assert first["target_temperature_c"] == 205
    assert first["expected_temperature_c"] == 245
    assert first["status"] == "mismatch"
    assert first["feature"] == "Outer wall"
    assert report["status"] == "warning"
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_startup_cleaning_temperature_does_not_trigger_mismatch(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S205\nG1 X10 E1\nM104 S245\n" + OBJECT + "G1 X20 E1 F2400\n")
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "matched"
    assert report["status"] == "observed"


def test_absolute_relative_extrusion_g92_arcs_and_modal_commands(tmp_path):
    path = sliced(tmp_path, "M109 S245\nG90\nM82\nG92 E10\nM204 P800 T9000\n" + OBJECT + """G1 X1 E11 F1200
G1 E10
G1 X2 E10
G92 E0
G2 X3 I0.5 E1
M83
M204 S500
G3 I0.5 J0 E.2 F1800
G0 X4 E.1
G91
G1 X1 E.1
G90
M82
G92 E0
G1 X6 E1
""")
    feature = inspect_sliced_project(path)["plates"][0]["features"]["Outer wall"]
    assert feature["extrusion_moves"] == 6
    assert feature["commanded_feed_mm_s"]["most_common"] == [
        {"value": 30, "moves": 4}, {"value": 20, "moves": 2},
    ]
    assert feature["commanded_print_acceleration_mm_s2"]["most_common"] == [
        {"value": 500, "moves": 4}, {"value": 800, "moves": 2},
    ]


def test_support_layers_do_not_pollute_object_layer_heights(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\n" + OBJECT + """G1 X1 E1 F1200
; FEATURE: Support interface
; Z_HEIGHT: 0.200244
; LAYER_HEIGHT: 0.000244
G1 X2 E1
; Z_HEIGHT: 0.31
; LAYER_HEIGHT: 0.109756
G1 X3 E1
; FEATURE: Inner wall
; Z_HEIGHT: 0.4
; LAYER_HEIGHT: 0.2
G1 X4 E1
""")
    layers = inspect_sliced_project(path)["plates"][0]["object_layers"]
    assert layers == {"count": 2, "z_range_mm": [0.2, 0.4],
                      "nominal_height_counts": [{"height_mm": 0.2, "extrusion_moves": 2}]}


@pytest.mark.parametrize("context", ["M622 J1\nM104 S245\nM623\n", "T1\nM104 S245\n", "M104 S{temperature}\n"])
def test_unsupported_context_is_unknown_not_a_pass(tmp_path, context):
    path = sliced(tmp_path, "M83\nM104 S205\n" + context + OBJECT + "G1 X1 E1 F1200\n")
    report = inspect_sliced_project(path)
    assert report["status"] == "unknown"
    assert report["plates"][0]["first_object_extrusion"]["status"] == "unknown"


def test_small_perimeter_warning_settings_filter_and_multiple_plates(tmp_path):
    gcode = "; small_perimeter_threshold = 0\nM83\nM109 S245\n" + OBJECT + "G1 X1 E1 F1200\n"
    path = sliced(tmp_path, gcode, {"small_perimeter_threshold": ["0"],
                                  "nozzle_temperature_initial_layer": ["245"],
                                  "printhost_password": "private"}, second_plate=gcode)
    report = inspect_sliced_project(path)
    assert len(report["plates"]) == 2
    assert report["issues"][0]["code"] == "small_perimeter_threshold_disabled"
    assert "printhost_password" not in report["archive_settings"]
    assert report["plates"][0]["gcode_settings"]["small_perimeter_threshold"] == "0"


def test_unlabeled_prime_is_not_an_object_and_missing_evidence_is_unknown(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\nG1 X1 E1 F1200\n")
    report = inspect_sliced_project(path)
    assert report["status"] == "unknown"
    assert report["plates"][0]["first_object_extrusion"] is None


def test_multiple_filament_targets_are_not_assumed_to_be_tool_zero(tmp_path):
    path = sliced(tmp_path, "M83\nM109 S245\n" + OBJECT + "G1 X1 E1 F1200\n",
                  {"nozzle_temperature": ["245", "260"]})
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "unknown"


def test_tool_change_does_not_reuse_previous_tools_temperature(tmp_path):
    path = sliced(tmp_path, "M83\nT1\nM109 S245\nT0\n" + OBJECT + "G1 X1 E1 F1200\n")
    first = inspect_sliced_project(path)["plates"][0]["first_object_extrusion"]
    assert first["target_temperature_c"] is None
    assert first["status"] == "unknown"


def test_compact_gcode_and_inline_comments(tmp_path):
    path = sliced(tmp_path, "M83\nM109S245;M104 S205\n" + OBJECT + "G1X1E.2F1200; prime\n")
    report = inspect_sliced_project(path)
    assert report["plates"][0]["first_object_extrusion"]["status"] == "matched"


def test_missing_gcode_is_unknown(tmp_path):
    path = tmp_path / "empty.3mf"
    with zipfile.ZipFile(path, "w"):
        pass
    assert inspect_sliced_project(path)["issues"][0]["code"] == "missing_gcode"


def geometry_issues(*issues):
    return {"parameters":{"line_width_mm":.4},"issues":list(issues)}


def clearance_issue(a=(1,0,.1),b=(1.2,0,.1)):
    return {"id":"geo-gap","category":"clearances","location":{"bbox_mm":[[.5,-.2,0],[2,.2,.2]]},
            "clearance_regions":[{"a_mm":list(a),"b_mm":list(b),"z_range_mm":[0,.2]}]}


def test_paths_in_nominal_gap_link_to_geometry_id_and_negative_control(tmp_path):
    start="G90\nM83\nG0 X0 Y0 Z.2\n"
    path=sliced(tmp_path,start+OBJECT+"G1 X2 E1 F1200\n")
    result=inspect_sliced_project(path,geometry_issues(clearance_issue()))['plates'][0]['geometry_comparison']
    intrusion=[i for i in result['observations'] if i['kind']=='extrusion_in_clearance']
    assert intrusion[0]['geometry_issue_id']=='geo-gap'
    assert intrusion[0]['examples'][0]['z_mm']==.2
    path=sliced(tmp_path,start+OBJECT+"G1 X.9 E1 F1200\n")
    assert not inspect_sliced_project(path,geometry_issues(clearance_issue()))['plates'][0]['geometry_comparison']['observations']


def test_arc_correlation_follows_arc_instead_of_chord(tmp_path):
    path=sliced(tmp_path,"G90\nM83\nG0 X0 Y0 Z.2\n"+OBJECT+"G3 X2 Y0 I1 J0 E1 F1200\n")
    report=inspect_sliced_project(path,geometry_issues(clearance_issue((.9,-1,.1),(1.1,-1,.1))))
    assert any(o['kind']=='extrusion_in_clearance' for o in report['plates'][0]['geometry_comparison']['observations'])
    path=sliced(tmp_path,"G90\nM83\nG0 X0 Y0 Z.2\n"+OBJECT+"G3 X2 Y0 R1 E1\n")
    report=inspect_sliced_project(path,geometry_issues(clearance_issue((.9,-1,.1),(1.1,-1,.1))))
    assert any(o['kind']=='extrusion_in_clearance' for o in report['plates'][0]['geometry_comparison']['observations'])
    path=sliced(tmp_path,"G90\nM83\nG0 X0 Y0 Z.2\n"+OBJECT+"G3 X2 Y0 R.2 E1\n")
    report=inspect_sliced_project(path,geometry_issues(clearance_issue()))
    assert report['plates'][0]['geometry_comparison']['status']=='unknown'
    assert not report['plates'][0]['geometry_comparison']['observations']


def test_compressed_clearance_run_preserves_probes_without_filling_between_them(tmp_path):
    issue=clearance_issue()
    issue['clearance_regions'][0].update(scan_axis=1,scan_step_mm=1.,scan_count=3)
    for y,expected in [(0,True),(2,True),(.5,False)]:
        code=f'G90\nM83\nG0 X0 Y{y} Z.2\n'+OBJECT+'G1 X2 E1\n'
        c=inspect_sliced_project(sliced(tmp_path,code),geometry_issues(issue))['plates'][0]['geometry_comparison']
        assert any(o['kind']=='extrusion_in_clearance' for o in c['observations'])==expected
    issue=clearance_issue((1,0,.1),(1,0,.3))
    issue['clearance_regions'][0].update(z_range_mm=[.1,.3],scan_axis=1,scan_step_mm=1.,scan_count=3,
                                        plane_axis=0,plane_step_mm=1.,plane_count=3)
    for y,expected in [(2,True),(1.5,False)]:
        code=f'G90\nM83\nG0 X2.5 Y{y} Z.2\n'+OBJECT+'G1 X3.5 E1\n'
        c=inspect_sliced_project(sliced(tmp_path,code),geometry_issues(issue))['plates'][0]['geometry_comparison']
        assert any(o['kind']=='extrusion_in_clearance' for o in c['observations'])==expected


def test_grouped_bridge_matches_local_ceiling_regions(tmp_path):
    issue={'id':'geo-group','category':'bridges','location':{'bbox_mm':[[0,0,0],[10,1,1]]},
           'ceiling_regions':[{'bbox_mm':[[0,0,0],[1,1,1]]},{'bbox_mm':[[9,0,0],[10,1,1]]}]}
    for x,expected in [(0,True),(5,False)]:
        code=f'G90\nM83\nG0 X{x} Y0 Z.2\n; FEATURE: Bridge\nG1 X{x+1} E1\n'
        c=inspect_sliced_project(sliced(tmp_path,code),geometry_issues(issue))['plates'][0]['geometry_comparison']
        assert bool(c['observations'])==expected


def test_extruder_offset_alignment_and_unknown_differing_offsets(tmp_path):
    code="G90\nM83\nG0 X0 Y-2 Z.2\n"+OBJECT+"G1 X2 E1\n"
    report=geometry_issues(clearance_issue())
    path=sliced(tmp_path,code,{'extruder_offset':['0x2']})
    comparison=inspect_sliced_project(path,report)['plates'][0]['geometry_comparison']
    assert comparison['coordinate_frame']['applied_extruder_offset_mm']==[0,2,0]
    assert any(o['kind']=='extrusion_in_clearance' for o in comparison['observations'])
    path=sliced(tmp_path,code,{'extruder_offset':['0x2','1x0']})
    comparison=inspect_sliced_project(path,report)['plates'][0]['geometry_comparison']
    assert comparison['status']=='unknown' and not comparison['observations']


def test_pinned_writer_arc_rounding_budget():
    from bambustudio_mcp.toolpaths import arc_points
    import numpy as np
    a=np.array([121.141,185.756,.32]);b=np.array([120.326,186.726,.32])
    assert arc_points(a,b,{'I':-1.015,'J':-.024},False,.042) is not None
    assert arc_points(a,b+np.array([.1,0,0]),{'I':-1.015,'J':-.024},False,.042) is None


def test_bridge_and_overhang_lengths_link_to_geometry(tmp_path):
    issue={'id':'geo-roof','category':'bridges','location':{'bbox_mm':[[0,0,.1],[4,1,.3]]}}
    gcode="G90\nM83\nG0 X0 Y0 Z.2\n; Z_HEIGHT: 0.2\n; FEATURE: Bridge\nG1 X2 E1 F900\n; FEATURE: Overhang wall\nG1 X4 E1\n"
    result=inspect_sliced_project(sliced(tmp_path,gcode),geometry_issues(issue))['plates'][0]['geometry_comparison']
    assert {i['kind'] for i in result['observations']}=={'bridge_path','overhang_perimeter'}
    assert all(i['total_length_mm']==2 and i['geometry_issue_id']=='geo-roof' for i in result['observations'])
    assert result['feature_paths']['Bridge']['gcode_moves']==1
    assert result['feature_paths']['Bridge']['segments'][0]['direction']==[1,0,0]
    assert result['feature_paths']['Bridge']['segments'][0]['geometry_issue_ids']==['geo-roof']


def test_stationary_unretraction_and_feed_change_are_not_bridge_or_seam_paths(tmp_path):
    issue={'id':'geo-roof','category':'bridges','location':{'bbox_mm':[[0,0,0],[4,1,1]]}}
    code='G90\nM83\nG0 X0 Y0 Z.2\n; FEATURE: Bridge\nG1 E.8\nG1 F1000\n'
    result=inspect_sliced_project(sliced(tmp_path,code),geometry_issues(issue))['plates'][0]['geometry_comparison']
    assert not result['observations'] and not result['feature_paths']


def test_cosmetic_seams_travel_and_sampled_ironing_coverage(tmp_path):
    issue={'id':'geo-face','category':'cosmetic_faces','location':{'bbox_mm':[[0,0,.2],[2,2,.2]]},
           'intent':{'avoid_seams':True,'ironing':True},'top_samples_mm':[[.5,.5,.2],[1.5,.5,.2]]}
    gcode="G90\nM83\nG0 X.2 Y.2 Z.2\n"+OBJECT+"""G1 X1.8 E1
; FEATURE: Top surface
G1 Y1 E1
G0 X2 Y.6
G0 X0 Y.6
G0 X0 Y.5
; FEATURE: Ironing
G1 X2 E.1
"""
    settings={'ironing_spacing':'.2'}
    result=inspect_sliced_project(sliced(tmp_path,gcode,settings),geometry_issues(issue))['plates'][0]['geometry_comparison']
    kinds={i['kind'] for i in result['observations']}
    assert {'seam_start_candidate','travel_over_finished_top','ironing_in_intended_region','ironing_coverage'}<=kinds
    coverage=next(i for i in result['observations'] if i['kind']=='ironing_coverage')
    assert coverage['covered_fraction']==1 and coverage['status']=='sampled_coverage'
    rounded=dict(issue,location={'bbox_mm':[[0,0,.18],[2,2,.18]]},top_samples_mm=[[.5,.5,.18],[1.5,.5,.18]])
    result=inspect_sliced_project(sliced(tmp_path,gcode,settings),geometry_issues(rounded))['plates'][0]['geometry_comparison']
    assert next(i for i in result['observations'] if i['kind']=='ironing_coverage')['covered_fraction']==1
    no_ironing=gcode.split('; FEATURE: Ironing')[0]
    result=inspect_sliced_project(sliced(tmp_path,no_ironing,settings),geometry_issues(issue))['plates'][0]['geometry_comparison']
    assert next(i for i in result['observations'] if i['kind']=='ironing_coverage')['covered_fraction']==0


def test_conditional_motion_does_not_confirm_gap_intrusion(tmp_path):
    gcode="G90\nM83\nG0 X0 Y0 Z.2\n"+OBJECT+"M622 J1\nG1 X2 E1\nM623\n"
    result=inspect_sliced_project(sliced(tmp_path,gcode),geometry_issues(clearance_issue()))['plates'][0]['geometry_comparison']
    assert not result['observations']
    assert result['status']=='unknown'
