"""Detached worker: persists jobs across MCP client disconnects."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import zipfile
from pathlib import Path

from .common import atomic_json, locked


def run(directory: Path):
    request = json.loads((directory / "request.json").read_text())
    state = json.loads((directory / "state.json").read_text())
    child = None

    def interrupted(_signal, _frame):
        raise InterruptedError("Job cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if request.get("kind") == "comparison":
            import asyncio
            from .runtime import Runtime
            from .service import Service
            service = Service(directory.parent.parent, Runtime(request["executable"]))
            result = asyncio.run(service._evaluate_candidates(request["project_id"], request["revision"],
                                  request["candidates"], request["constraints"], directory))
            state.update(status="succeeded", progress=100, result=result)
            return
        with locked(directory.parent / "slice.lock"):
            if (directory / "cancelled").exists():
                raise InterruptedError("Job cancelled before start")
            state.update(status="running", progress=None)
            atomic_json(directory / "state.json", state)
            command = [request["executable"], "--datadir", str(directory / "studio-data"), "--outputdir", str(directory),
                       "--slice", str(request["plate_index"]), "--export-3mf", "sliced.3mf",
                       "--export-settings", str(directory / "effective-settings.json"), str(directory / "input.3mf")]
            with (directory / "studio.log").open("w") as log:
                child = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
                code = child.wait(timeout=request["timeout_seconds"])
            result_file = directory / "result.json"
            result = json.loads(result_file.read_text()) if result_file.is_file() else None
            if code or result is None or result.get("return_code") != 0 or not (directory / "sliced.3mf").is_file():
                raise ValueError(f"Studio did not produce a valid slice (exit={code}, result={result})")
            state.update(status="succeeded", progress=100, result=result,
                         artifacts={"project": "sliced.3mf", "settings": "effective-settings.json", "log": "studio.log"})
            effective = json.loads((directory / "effective-settings.json").read_text())
            from .catalog import Catalog
            from .project import Project
            catalog = Catalog()
            requested = Project(directory / "input.3mf").settings
            state["normalizations"] = [{"key": key, "requested": value, "effective": effective.get(key)}
                                      for key, value in requested.items()
                                      if key in catalog.options and catalog.options[key]["scopes"]
                                      and catalog.decode(key, value) != catalog.decode(key, effective.get(key))]
            state["normalization_evidence"] = "Studio export; local settings remain in the exported project"
            from .gcode import inspect_sliced_project
            try:
                inspection = inspect_sliced_project(directory / "sliced.3mf")
            except (ValueError, OSError, zipfile.BadZipFile) as error:
                inspection = {"status": "unknown", "issues": [{"code": "inspection_failed", "message": str(error)}]}
            atomic_json(directory / "inspection.json", inspection)
            state["inspection"] = inspection
            state["artifacts"]["inspection"] = "inspection.json"
    except (InterruptedError, subprocess.TimeoutExpired) as error:
        if request.get("kind") == "comparison":
            latest = json.loads((directory / "state.json").read_text())
            if latest.get("active_job_id"):
                service.cancel_job(latest["active_job_id"])
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        state.update(status="cancelled" if isinstance(error, InterruptedError) else "timed_out", error=str(error))
    except Exception as error:
        state.update(status="failed", error=str(error))
    finally:
        atomic_json(directory / "state.json", state)


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve())
