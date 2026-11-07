import copy
import json

from bambustudio_mcp.reports import geometry_summary, inspection_summary
from bambustudio_mcp.server import compact_result


def test_shared_guidance_fixes_and_raw_arrays_stay_in_full_report():
    fix={"setting":"bridge_speed","action":"Compare a candidate","scopes":["project"]}
    issue={"id":"one","category":"bridges","why_it_fails":"Roof can sag","design_fix":"Reorient",
           "slicer_fix":[fix],"highlight_svg":["/tmp/top.svg#one"],"ceiling_regions":[{"bbox_mm":[[0,0,0],[1,1,1]]}]}
    report={"issues":[issue,{**issue,"id":"two"}],"meshes":[{
        "overhang_face_indices":list(range(2000)),"sampled_thickness":[{"thickness_mm":.123456789}]}],
        "previews":[{"path":"/tmp/top.svg","projection":"top"}]}
    original=copy.deepcopy(report)
    summary=geometry_summary(report,"/tmp/full.json")
    assert report==original
    assert len(summary['issue_guidance'])==len(summary['slicer_fixes'])==1
    assert summary['issues'][0]['guidance_ref']==summary['issues'][1]['guidance_ref']
    assert summary['issues'][0]['slicer_fix']==['bridge_speed']
    assert summary['issues'][0]['ceiling_region_count']==1
    assert 'highlight_svg' not in summary['issues'][0]
    assert summary['meshes'][0]['minimum_sampled_thickness_mm']==.123457
    assert summary['meshes'][0]['overhang_face_count']==2000
    text=compact_result(summary).content[0].text
    assert text==json.dumps(summary,separators=(',',':'))


def test_different_modifier_instructions_are_not_lost_in_shared_table():
    issues=[{'id':str(i),'slicer_fix':[{'edit_regions':'height_range','action':action}]} for i,action in enumerate(('roof','top','roof'))]
    summary=geometry_summary({'issues':issues},'/tmp/report.json')
    assert len(summary['slicer_fixes'])==2
    assert summary['issues'][0]['slicer_fix']==summary['issues'][2]['slicer_fix']
    assert summary['issues'][0]['slicer_fix']!=summary['issues'][1]['slicer_fix']


def test_inspection_summary_keeps_counts_and_leaves_complete_paths_untouched():
    report={'plates':[{'geometry_comparison':{
        'feature_paths':{'Bridge':{'gcode_moves':2,'segments':[{'line':1},{'line':2}]}},
        'observations':[{'kind':'bridge_path','count':2,'examples':[{'line':1}]}],
        'unmapped_arc_examples':[]}}]}
    original=copy.deepcopy(report)
    summary=inspection_summary(report,'/tmp/inspection.json')
    comparison=summary['plates'][0]['geometry_comparison']
    assert comparison['feature_paths']['Bridge']=={'gcode_moves':2,'saved_segment_count':2}
    assert comparison['observations'][0]['saved_example_count']==1
    assert report==original
    assert inspection_summary(summary,'/tmp/inspection.json')==summary
