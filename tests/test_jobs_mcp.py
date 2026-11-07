import asyncio
import json
import os
import sys
import time
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bambustudio_mcp.server import FILAMENT_INSTRUCTIONS, create_server
from bambustudio_mcp.service import Service


def await_job(service, job, timeout=15):
    end = time.monotonic() + timeout
    while job["status"] in {"queued", "running"} and time.monotonic() < end:
        time.sleep(0.05)
        job = service.get_job(job["job_id"])
    assert job["status"] not in {"queued", "running"}
    return job


def test_detached_job_and_reconnect(service, opened):
    job = service.start_slice(opened["project_id"], opened["revision"])
    reconnected = Service(service.workspace, service.runtime)
    job = await_job(reconnected, job)
    assert job["status"] == "succeeded", job
    assert any(i['id']=='geometry_analysis_missing' for i in job['unresolved_geometry_issues'])
    assert job["result"]["sliced_plates"][0]["total_predication"] == 60
    assert job["inspection"]["status"] == "unknown"
    assert job["artifacts"]["inspection"] == "inspection.json"
    assert json.loads((reconnected.workspace / "jobs" / job["job_id"] / "inspection.json").read_text())["status"] == "unknown"
    snapshot=reconnected.workspace/"jobs"/job["job_id"]/"inspection.json"
    original=snapshot.read_bytes()
    inspection=reconnected.inspect_slice(opened["project_id"], opened["revision"], job["job_id"])
    assert inspection["status"] == "unknown"
    assert Path(inspection["report_path"]).is_file() and snapshot.read_bytes()==original
    changed = reconnected.apply_settings(opened["project_id"], opened["revision"], {"wall_loops": 3})
    with pytest.raises(ValueError, match="exact project revision"):
        reconnected.inspect_slice(opened["project_id"], changed["revision"], job["job_id"])
    with pytest.raises(ValueError, match="exact project revision"):
        reconnected.record_print_context(opened["project_id"], changed["revision"], {"observations": [
            {"kind": "surface_finish", "status": "passed", "notes": "Old sample", "job_id": job["job_id"]}]})


def test_cancel_queued_job(service, opened):
    job = service.start_slice(opened["project_id"], opened["revision"])
    service.cancel_job(job["job_id"])
    assert await_job(service, service.get_job(job["job_id"]))["status"] in {"cancelled", "succeeded", "interrupted"}


def test_running_job_timeout(service, opened):
    from pathlib import Path
    Path(str(service.runtime.executable) + ".sleep").touch()
    job = service.start_slice(opened["project_id"], opened["revision"], timeout_seconds=1)
    assert await_job(service, job)["status"] == "timed_out"


def test_cancel_running_job(service, opened):
    from pathlib import Path
    Path(str(service.runtime.executable) + ".sleep").touch()
    job = service.start_slice(opened["project_id"], opened["revision"])
    end = time.monotonic() + 5
    while job["status"] == "queued" and time.monotonic() < end:
        time.sleep(.05)
        job = service.get_job(job["job_id"])
    service.cancel_job(job["job_id"])
    assert await_job(service, job)["status"] == "cancelled"


def test_completed_worker_state_is_not_overwritten(service, tmp_path, monkeypatch):
    from bambustudio_mcp.common import atomic_json
    job_id = "a" * 32
    directory = service.workspace / "jobs" / job_id
    directory.mkdir(parents=True)
    atomic_json(directory / "state.json", {"job_id": job_id, "status": "running"})
    atomic_json(directory / "process.json", {"pid": 123, "start_ticks": "1"})
    def completed(_pid):
        atomic_json(directory / "state.json", {"job_id": job_id, "status": "succeeded"})
        return None
    monkeypatch.setattr(service, "_process_ticks", completed)
    assert service.get_job(job_id)["status"] == "succeeded"


def test_job_report_excludes_printer_credentials(service, project_file):
    from bambustudio_mcp.project import Project
    project = Project(project_file)
    project.settings["printhost_password"] = "test-secret"
    project.dirty_settings = True
    protected = project_file.parent / "protected.3mf"
    project.save(protected)
    opened = service.open_project(str(protected))
    job = await_job(service, service.start_slice(opened["project_id"]))
    assert job["status"] == "succeeded"
    assert "test-secret" not in json.dumps(job)


def test_revision_specific_geometry_warning_snapshot_and_export(service,opened,tmp_path):
    pid,revision=opened['project_id'],opened['revision']
    geometry=service.analyze_geometry(pid,revision)
    job=await_job(service,service.start_slice(pid,revision))
    assert job['status']=='succeeded'
    assert not any(i['id']=='geometry_analysis_missing' for i in job['unresolved_geometry_issues'])
    assert (service.workspace/'jobs'/job['job_id']/'geometry.json').exists()
    known={i['id'] for i in geometry['issues']}
    assert all(i['id'] in known or i['category']=='analysis' for i in job['unresolved_geometry_issues'])
    changed=service.apply_settings(pid,revision,{'wall_loops':4})
    exported=service.export_project(pid,changed['revision'],str(tmp_path/'unreviewed.3mf'))
    assert any(i['id']=='geometry_analysis_missing' for i in exported['unresolved_geometry_issues'])
    assert (tmp_path/'unreviewed.3mf').exists()  # Warning, not a block.
    service.record_print_context(pid,changed['revision'],{'cosmetic_faces':[]})
    service.analyze_geometry(pid,changed['revision'])
    observed=service.record_print_context(pid,changed['revision'],{'cosmetic_faces':[],
        'observations':[{'kind':'surface_finish','status':'unverified','notes':'A sample has not yet been inspected.'}]})
    assert observed['geometry_analysis_status']!='not_run_or_stale'
    service.record_print_context(pid,changed['revision'],{'cosmetic_faces':[{'name':'important','bounds_mm':[[20,30,0],[30,40,10]]}]})
    assert service.get_print_guidance(pid,changed['revision'])['geometry_analysis_status']=='not_run_or_stale'


def test_geometry_response_keeps_large_probe_arrays_on_disk(service, opened, monkeypatch):
    from mcp.types import CallToolResult, TextContent
    from bambustudio_mcp.preflight import ANALYSIS_VERSION, context_signature

    region = {"a_mm": [0, 0, 0.1], "b_mm": [0.2, 0, 0.1],
              "z_range_mm": [0, 0.2], "gap_mm": 0.2}
    issue = {"id": "geo-test-gap", "category": "clearances", "severity": "high", "status": "open",
             "location": {"bbox_mm": [[0, 0, 0], [0.2, 0, 0.2]]},
             "measurement": {"value": 0.2, "threshold": 0.8}, "clearance_regions": [region] * 53000}
    detailed = {"analysis_version": ANALYSIS_VERSION, "analysis_status": "complete", "context_signature": context_signature({}),
                "issues": [issue], "previews": [{"svg": "<svg/>", "projection": "top"}]}
    monkeypatch.setattr("bambustudio_mcp.service.analyze", lambda *args, **kwargs: detailed)
    report = service.analyze_geometry(opened["project_id"], opened["revision"])
    assert report["issues"][0]["id"] == issue["id"]
    assert report["issues"][0]["clearance_region_count"] == 53000
    assert "clearance_region_sample" not in report["issues"][0]
    assert "clearance_regions" not in report["issues"][0]
    assert "highlight_svg" not in report["issues"][0]
    assert report["previews"] and "#<issue id>" in report["highlight_anchor"]
    assert len(json.loads(Path(report["report_path"]).read_text())["issues"][0]["clearance_regions"]) == 53000
    # Include both MCP representations: a Python-only geometry test missed this transport failure.
    wire = CallToolResult(content=[TextContent(type="text", text=json.dumps(report, indent=2))], structuredContent=report)
    assert len(wire.model_dump_json(by_alias=True).encode()) < 100_000
    job = await_job(service, service.start_slice(opened["project_id"], opened["revision"]))
    assert job["status"] == "succeeded"
    snapshot = json.loads((service.workspace / "jobs" / job["job_id"] / "geometry.json").read_text())
    assert len(snapshot["issues"][0]["clearance_regions"]) == 53000


@pytest.mark.asyncio
async def test_comparison(service, opened):
    job = await service.compare_candidates(opened["project_id"], opened["revision"],
                                             [{"name": "walls", "changes": {"wall_loops": 3}}], {"max_time_seconds": 50})
    finished = await_job(service, job)
    assert finished["status"] == "succeeded", finished
    result = finished["result"]
    assert len(result["candidates"]) == 2
    assert not result["candidates"][1]["meets_measured_constraints"]
    assert service.inspect_project(opened["project_id"])["head"] == opened["revision"]


@pytest.mark.asyncio
async def test_mcp_stdio_protocol(tmp_path, fake_runtime, project_file):
    params = StdioServerParameters(command=sys.executable, args=["-m", "bambustudio_mcp.server"],
                                   env={**os.environ, "BAMBUSTUDIO_WORKSPACE": str(tmp_path / "mcp"),
                                        "BAMBUSTUDIO_EXECUTABLE": str(fake_runtime.executable)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "BambuStudio"
            assert initialized.serverInfo.version == version("bambustudio-mcp")
            assert FILAMENT_INSTRUCTIONS in initialized.instructions
            prompt = await client.get_prompt("prepare_print", {"project_id": "example", "intended_use": "threaded shelf"})
            assert any(FILAMENT_INSTRUCTIONS in message.content.text for message in prompt.messages)
            assert "threaded shelf" in prompt.messages[0].content.text
            tools = await client.list_tools()
            assert {"list_options", "apply_settings", "edit_regions", "start_slice"} <= {t.name for t in tools.tools}
            assert {"record_print_context", "get_print_guidance", "inspect_slice"} <= {t.name for t in tools.tools}
            result = await client.call_tool("list_options", {"query": "wall_loops"})
            assert not result.isError
            assert 'Wall loops' in result.content[0].text
            resource = await client.read_resource("bambustudio://guide")
            assert "physical" in resource.contents[0].text
            assert "Generic PETG for confirmed standard PETG" in resource.contents[0].text
            guide = await client.read_resource("bambustudio://quality-guide")
            assert "threads" in {c["id"] for c in json.loads(guide.contents[0].text)["categories"]}
            opened = await client.call_tool("open_project", {"path": str(project_file)})
            project = json.loads(opened.content[0].text)
            geometry = await client.call_tool("analyze_geometry", {"project_id": project["project_id"]})
            assert not geometry.isError
            assert json.loads(geometry.content[0].text)["report_path"].endswith("-geometry-report.json")
            assert not (await client.call_tool("list_options", {"query": "wall_loops"})).isError
            prepared = await client.call_tool("record_print_context", {"project_id": project["project_id"],
                "revision": project["revision"], "context": {"printer_model": "Bambu Lab P1S", "concerns": ["threads"]}})
            assert not prepared.isError
            assert "filaments" in json.loads(prepared.content[0].text)["missing_inputs"]
            inspected = await client.call_tool("inspect_slice", {"project_id": project["project_id"]})
            assert not inspected.isError
            assert json.loads(inspected.content[0].text)["status"] == "unknown"
            bad = await client.call_tool("get_job", {"job_id": "../../escape"})
            assert bad.isError


@pytest.mark.asyncio
async def test_real_stand_geometry_over_stdio(tmp_path):
    path = Path('/home/onepiece/dev/tiiny/3dpart/tiiny_stand_wide_P1S_PLA_fine.gcode.3mf')
    if not path.exists():
        pytest.skip('Local stand archive is not distributed with the package')
    params = StdioServerParameters(command=sys.executable, args=["-m", "bambustudio_mcp.server"],
                                   env={**os.environ, "BAMBUSTUDIO_WORKSPACE": str(tmp_path / "stand-mcp")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            opened = await client.call_tool("open_project", {"path": str(path)})
            project = json.loads(opened.content[0].text)
            result = await client.call_tool("analyze_geometry", {"project_id": project["project_id"]})
            assert not result.isError
            # Check the complete MCP result, including both text and structured content.
            assert len(result.model_dump_json(by_alias=True).encode()) < 60_000
            report = json.loads(result.content[0].text)
            assert {'holes_lying_flat', 'print_in_place', 'bed_contact', 'faceting', 'clearances'} <= {
                issue['category'] for issue in report['issues']}
            assert any(issue.get('clearance_region_count', 0) > 0 for issue in report['issues'])
            assert all('clearance_region_sample' not in issue for issue in report['issues'])
            assert result.structuredContent==report
            assert result.content[0].text==json.dumps(report,separators=(',',':'))
            assert all('highlight_svg' not in issue for issue in report['issues'])
            assert all('sampled_thickness' not in mesh and 'overhang_face_indices' not in mesh for mesh in report['meshes'])
            assert all('clearance_regions' not in issue for issue in report['issues'])
            detailed = json.loads(Path(report['report_path']).read_text())
            assert [i['id'] for i in detailed['issues']] == [i['id'] for i in report['issues']]
            assert sum(len(i.get('clearance_regions', [])) for i in detailed['issues']) == sum(
                i.get('clearance_region_count', 0) for i in report['issues'])
            assert all(Path(preview['path']).is_file() for preview in report['previews'])
            inspected = await client.call_tool('inspect_slice', {'project_id': project['project_id']})
            assert not inspected.isError
            assert len(inspected.model_dump_json(by_alias=True).encode()) < 60_000
            assert inspected.content[0].text==json.dumps(inspected.structuredContent,separators=(',',':'))
            comparisons = [plate['geometry_comparison'] for plate in json.loads(inspected.content[0].text)['plates']]
            assert comparisons and all(item['status'] == 'observed' for item in comparisons)
            assert all('segments' not in feature for c in comparisons for feature in c['feature_paths'].values())
            ids = {issue['id'] for issue in report['issues']}
            assert all(item['geometry_issue_id'] in ids for c in comparisons for item in c['issue_results'])
            assert any(c['observations'] for c in comparisons)
            results={item['geometry_issue_id']:item for c in comparisons for item in c['issue_results']}
            roofs=[issue for issue in report['issues'] if issue['category']=='bridges']
            assert sum('bridge_path' in results[issue['id']]['kinds'] for issue in roofs)>=2
            margins=[item['clearance_margin'] for item in results.values() if item['category']=='clearances']
            assert all(item['remaining_margin_mm'] is not None for item in margins)
            assert any(item['status']=='bead_overlap_candidate' for item in margins)
            assert all(results[o['geometry_issue_id']]['category'] in {'top_surfaces','cosmetic_faces'}
                       for c in comparisons for o in c['observations'] if o['kind']=='travel_over_finished_top')
            assert not (await client.call_tool('list_options', {'query': 'wall_loops'})).isError
