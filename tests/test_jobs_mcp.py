import asyncio
import json
import os
import sys
import time

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
    assert job["result"]["sliced_plates"][0]["total_predication"] == 60


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
async def test_mcp_stdio_protocol(tmp_path, fake_runtime):
    params = StdioServerParameters(command=sys.executable, args=["-m", "bambustudio_mcp.server"],
                                   env={**os.environ, "BAMBUSTUDIO_WORKSPACE": str(tmp_path / "mcp"),
                                        "BAMBUSTUDIO_EXECUTABLE": str(fake_runtime.executable)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "BambuStudio"
            assert FILAMENT_INSTRUCTIONS in initialized.instructions
            prompt = await client.get_prompt("prepare_print", {"project_id": "example", "intended_use": "threaded shelf"})
            assert any(FILAMENT_INSTRUCTIONS in message.content.text for message in prompt.messages)
            assert "threaded shelf" in prompt.messages[0].content.text
            tools = await client.list_tools()
            assert {"list_options", "apply_settings", "edit_regions", "start_slice"} <= {t.name for t in tools.tools}
            result = await client.call_tool("list_options", {"query": "wall_loops"})
            assert not result.isError
            assert 'Wall loops' in result.content[0].text
            resource = await client.read_resource("bambustudio://guide")
            assert "physical" in resource.contents[0].text
            assert "Generic PETG for confirmed standard PETG" in resource.contents[0].text
            bad = await client.call_tool("get_job", {"job_id": "../../escape"})
            assert bad.isError
