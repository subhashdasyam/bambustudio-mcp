from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .catalog import Catalog
from .common import atomic_json, identifier, locked
from .geometry import analyze
from .project import Project, children, metadata
from .runtime import Runtime


class Service:
    def __init__(self, workspace=None, runtime=None):
        self.workspace = Path(workspace or os.environ.get("BAMBUSTUDIO_WORKSPACE", "~/.local/share/bambustudio-mcp")).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime = runtime or Runtime()
        self.catalog = Catalog()

    def _directory(self, project_id):
        path = self.workspace / "projects" / identifier(project_id)
        if not (path / "project.json").is_file():
            raise ValueError("Unknown project_id")
        return path

    def _load(self, project_id, revision=None):
        directory = self._directory(project_id)
        state = json.loads((directory / "project.json").read_text())
        revision = revision or state["head"]
        identifier(revision)
        path = directory / f"{revision}.3mf"
        if not path.is_file():
            raise ValueError("Unknown project revision")
        return directory, state, revision, Project(path)

    def _commit(self, directory, state, parent, project, changes, invalidate=True):
        if state["head"] != parent:
            raise ValueError("Stale revision; inspect the current project and retry")
        revision = uuid.uuid4().hex
        project.save(directory / f"{revision}.3mf", invalidate=invalidate)
        atomic_json(directory / f"{revision}.json", {"revision": revision, "parent": parent, "changes": changes})
        state["head"] = revision
        atomic_json(directory / "project.json", state)
        return {"project_id": state["project_id"], "revision": revision, "parent": parent, "changes": changes,
                "slice_valid": False, "path": str(directory / f"{revision}.3mf")}

    def open_project(self, path, machine_profile=None, process_profile=None, filament_profiles=None):
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Input file does not exist")
        project_id, revision = uuid.uuid4().hex, uuid.uuid4().hex
        directory = self.workspace / "projects" / project_id
        directory.mkdir(parents=True)
        working = directory / f"{revision}.3mf"
        profiles = {}
        for kind, name in [("machine", machine_profile), ("process", process_profile)]:
            if name:
                profiles[kind] = self.runtime.resolve_profile(name, kind)
        profiles["filament"] = [self.runtime.resolve_profile(name, "filament") for name in (filament_profiles or [])]
        if source.suffix.lower() == ".3mf" and not any([machine_profile, process_profile, filament_profiles]):
            project = Project(source)
            shutil.copyfile(source, working)
        else:
            self.runtime.require()
            if source.suffix.lower() != ".3mf" and not (machine_profile and process_profile and filament_profiles):
                raise ValueError("Model import requires explicit machine, process, and filament profiles")
            inputs = directory / "import"
            inputs.mkdir()
            arguments = []
            general = []
            for kind in ("machine", "process"):
                if kind in profiles:
                    name = inputs / f"{kind}.json"
                    atomic_json(name, profiles[kind])
                    general.append(str(name))
            if general:
                arguments += ["--load-settings", ";".join(general)]
            filaments = []
            for i, profile in enumerate(profiles["filament"]):
                name = inputs / f"filament-{i}.json"
                atomic_json(name, profile)
                filaments.append(str(name))
            if filaments:
                arguments += ["--load-filaments", ";".join(filaments)]
            arguments += ["--export-3mf", "imported.3mf", str(source)]
            self.runtime.run(arguments, inputs)
            shutil.copyfile(inputs / "imported.3mf", working)
            project = Project(working)
        state = {"project_id": project_id, "head": revision, "source": str(source)}
        atomic_json(directory / "project.json", state)
        atomic_json(directory / f"{revision}.json", {"revision": revision, "parent": None, "changes": []})
        return dict(state, revision=revision, project=project.describe())

    def inspect_project(self, project_id, revision=None):
        directory, state, revision, project = self._load(project_id, revision)
        return {"project_id": project_id, "revision": revision, "head": state["head"], "project": project.describe(),
                "history": [json.loads(p.read_text()) for p in sorted(directory.glob("*.json")) if len(p.stem) == 32]}

    def get_settings(self, project_id, revision=None, scope="project", target=None, keys=None):
        _, _, revision, project = self._load(project_id, revision)
        values = project.get_settings(self.catalog, scope, target)
        if keys is not None:
            values = {k: v for k, v in values.items() if k in keys}
        return {"revision": revision, "scope": scope, "settings": values,
                "evidence": "project and catalog; Studio effective export is available after slicing"}

    def restore_revision(self, project_id, revision, restore):
        self.runtime.require()
        directory = self._directory(project_id)
        with locked(directory / "project.lock"):
            _, state, _, _ = self._load(project_id, revision)
            _, _, _, previous = self._load(project_id, restore)
            return self._commit(directory, state, revision, previous, [{"restored_from": restore}])

    def apply_settings(self, project_id, revision, changes, scope="project", target=None):
        self.runtime.require()
        directory = self._directory(project_id)
        with locked(directory / "project.lock"):
            directory, state, _, project = self._load(project_id, revision)
            if revision != state["head"]:
                raise ValueError("Stale revision")
            if not changes:
                raise ValueError("Provide at least one setting change")
            diff = project.apply(self.catalog, changes, scope, target)
            return self._commit(directory, state, revision, project, diff)

    def analyze_geometry(self, project_id, revision=None, overhang_angle=45.0):
        directory, _, revision, project = self._load(project_id, revision)
        report = analyze(project, overhang_angle)
        previews = report.pop("previews")
        for i, preview in enumerate(previews):
            path = directory / f"{revision}-view-{i}.svg"
            path.write_text(preview.pop("svg"))
            preview["path"] = str(path)
        report.update(revision=revision, previews=previews)
        atomic_json(directory / f"{revision}-geometry-report.json", report)
        return report

    def edit_regions(self, project_id, revision, edits):
        self.runtime.require()
        directory = self._directory(project_id)
        with locked(directory / "project.lock"):
            directory, state, _, project = self._load(project_id, revision)
            if revision != state["head"]:
                raise ValueError("Stale revision")
            if not edits:
                raise ValueError("Provide at least one spatial edit")
            for edit in edits:
                action = edit["action"]
                if action == "rotate":
                    project.rotate(edit["instance"], edit["degrees_xyz"])
                elif action == "paint":
                    indices = edit.get("face_indices")
                    if indices is None:
                        import numpy as np
                        mesh = next(m for m in project.meshes() if m["resource_id"] == edit["resource_id"] and m["instance"] == edit.get("instance", 0))
                        centers = mesh["vertices"][mesh["faces"]].mean(axis=1)
                        bounds = np.asarray(edit["bounds_mm"], dtype=float)
                        if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or (bounds[0] > bounds[1]).any():
                            raise ValueError("Selection bounds require [minimum_xyz, maximum_xyz] in world millimetres")
                        indices = np.flatnonzero(((centers >= bounds[0]) & (centers <= bounds[1])).all(axis=1)).tolist()
                    project.paint(edit["resource_id"], indices, edit["state"], edit["kind"])
                elif action == "layer_heights":
                    settings = {k: v["value"] for k, v in project.get_settings(self.catalog).items()}
                    project.set_layer_heights(edit["object_id"], edit["profile"], min(settings["min_layer_height"]), max(settings["max_layer_height"]))
                elif action == "height_range":
                    project.apply(self.catalog, edit["changes"], "height_range", edit["target"])
                elif action == "modifier_box":
                    project.modifier_box(self.catalog, edit["object_id"], edit["bounds_mm"], edit["changes"])
                elif action == "remove_modifier":
                    project.remove_modifier(edit["object_id"], edit["volume_id"])
                else:
                    raise ValueError(f"Unknown spatial action: {action}")
            return self._commit(directory, state, revision, project, edits)

    def start_slice(self, project_id, revision=None, plate_index=0, timeout_seconds=1800):
        environment = self.runtime.require()
        directory, _, revision, _ = self._load(project_id, revision)
        if isinstance(plate_index, bool) or not isinstance(plate_index, int) or plate_index < 0:
            raise ValueError("plate_index must be 0 for all plates or a positive Studio plate index")
        if not 1 <= timeout_seconds <= 86400:
            raise ValueError("timeout_seconds must be 1..86400")
        job_id = uuid.uuid4().hex
        job = self.workspace / "jobs" / job_id
        job.mkdir(parents=True)
        shutil.copyfile(directory / f"{revision}.3mf", job / "input.3mf")
        atomic_json(job / "request.json", {"executable": str(self.runtime.executable), "project_id": project_id,
                                           "revision": revision, "plate_index": plate_index, "timeout_seconds": timeout_seconds,
                                           "environment": environment})
        atomic_json(job / "state.json", {"job_id": job_id, "project_id": project_id, "revision": revision, "status": "queued"})
        with (job / "worker.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "bambustudio_mcp.worker", str(job)],
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        atomic_json(job / "process.json", {"pid": process.pid, "start_ticks": self._process_ticks(process.pid)})
        return self.get_job(job_id)

    @staticmethod
    def _process_ticks(pid):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return None if fields[0] == "Z" else fields[19]
        except (OSError, IndexError):
            return None

    def get_job(self, job_id):
        job = self.workspace / "jobs" / identifier(job_id)
        if not (job / "state.json").is_file():
            raise ValueError("Unknown job_id")
        state = json.loads((job / "state.json").read_text())
        if state["status"] in {"queued", "running"} and (job / "process.json").exists():
            process = json.loads((job / "process.json").read_text())
            current = self._process_ticks(process["pid"])
            if current is None or current != process["start_ticks"]:
                state = json.loads((job / "state.json").read_text())
                if state["status"] in {"queued", "running"}:
                    state.update(status="cancelled" if (job / "cancelled").exists() else "interrupted",
                                 error="Worker no longer exists; start a new slice if needed")
                    atomic_json(job / "state.json", state)
        state["directory"] = str(job)
        if (job / "studio.log").exists():
            with (job / "studio.log").open("rb") as log:
                log.seek(max(0, (job / "studio.log").stat().st_size - 4000))
                state["log_tail"] = log.read().decode(errors="replace")
        return state

    def cancel_job(self, job_id):
        state = self.get_job(job_id)
        job = self.workspace / "jobs" / identifier(job_id)
        if state["status"] not in {"queued", "running"}:
            return state
        (job / "cancelled").touch()
        if state.get("active_job_id"):
            self.cancel_job(state["active_job_id"])
        process = json.loads((job / "process.json").read_text())
        if self._process_ticks(process["pid"]) == process["start_ticks"]:
            try:
                os.killpg(process["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        return self.get_job(job_id)

    async def compare_candidates(self, project_id, revision, candidates, constraints=None):
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 7:
            raise ValueError("Provide 1..7 alternatives; the baseline is added automatically")
        self.runtime.require()
        self._load(project_id, revision)
        constraints = constraints or {}
        if set(constraints) - {"max_time_seconds", "max_filament_g"}:
            raise ValueError("Only max_time_seconds and max_filament_g are measured constraints")
        from .common import finite_number
        for value in constraints.values():
            if finite_number(value) < 0:
                raise ValueError("Comparison limits must be nonnegative")
        for candidate in candidates:
            if candidate.get("revision") and candidate.get("changes"):
                raise ValueError("Use either a candidate revision or setting changes")
            if candidate.get("edits"):
                raise ValueError("Create spatial revisions with edit_regions first")
            if candidate.get("revision"):
                self._load(project_id, candidate["revision"])
            for key, value in candidate.get("changes", {}).items():
                self.catalog.validate(key, value)
        job_id = uuid.uuid4().hex
        job = self.workspace / "jobs" / job_id
        job.mkdir(parents=True)
        atomic_json(job / "request.json", {"kind": "comparison", "executable": str(self.runtime.executable),
                    "project_id": project_id, "revision": revision, "candidates": candidates, "constraints": constraints})
        atomic_json(job / "state.json", {"job_id": job_id, "project_id": project_id, "revision": revision,
                                          "kind": "comparison", "status": "queued"})
        with (job / "worker.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "bambustudio_mcp.worker", str(job)],
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        atomic_json(job / "process.json", {"pid": process.pid, "start_ticks": self._process_ticks(process.pid)})
        return self.get_job(job_id)

    async def _evaluate_candidates(self, project_id, revision, candidates, constraints, comparison_directory):
        directory, _, _, baseline = self._load(project_id, revision)
        results = []
        for candidate in [{"name": "baseline", "changes": {}}] + candidates:
            candidate_revision = revision
            if candidate.get("changes") or candidate.get("edits"):
                temporary = Project(directory / f"{revision}.3mf")
                temporary.apply(self.catalog, candidate.get("changes", {}))
                if candidate.get("edits"):
                    raise ValueError("Create spatial candidate revisions with edit_regions, then pass their revision")
                candidate_revision = uuid.uuid4().hex
                temporary.save(directory / f"{candidate_revision}.3mf")
                atomic_json(directory / f"{candidate_revision}.json", {"revision": candidate_revision, "parent": revision, "changes": candidate.get("changes", {})})
            if candidate.get("revision"):
                _, _, candidate_revision, _ = self._load(project_id, candidate["revision"])
            job = self.start_slice(project_id, candidate_revision)
            state_file = comparison_directory / "state.json"
            state = json.loads(state_file.read_text())
            state.update(status="running", active_job_id=job["job_id"], completed_candidates=results)
            atomic_json(state_file, state)
            while job["status"] in {"queued", "running"}:
                await asyncio.sleep(0.2)
                if (comparison_directory / "cancelled").exists():
                    self.cancel_job(job["job_id"])
                    raise InterruptedError("Comparison cancelled")
                job = self.get_job(job["job_id"])
            plates = job.get("result", {}).get("sliced_plates", [])
            duration = sum(p.get("total_predication", 0) for p in plates) if plates else None
            grams = sum(f.get("total_used_g", 0) for p in plates for f in p.get("filaments", [])) if plates else None
            meets = duration is not None and grams is not None and job["status"] == "succeeded"
            for key, actual in [("max_time_seconds", duration), ("max_filament_g", grams)]:
                if key in (constraints or {}) and (actual is None or actual > constraints[key]):
                    meets = False
            results.append({"name": candidate.get("name", candidate_revision), "revision": candidate_revision,
                            "job_id": job["job_id"], "status": job["status"], "estimated_time_seconds": duration,
                            "estimated_filament_g": grams, "meets_measured_constraints": meets,
                            "strength": "unverified", "surface_finish": "unverified", "plates": plates})
        baseline_time = results[0]["estimated_time_seconds"]
        for candidate in results:
            duration = candidate["estimated_time_seconds"]
            difference = duration - baseline_time if duration is not None and baseline_time is not None else None
            candidate["time_difference_seconds"] = difference
            candidate["time_difference_inconclusive"] = difference is None or abs(difference) <= max(1.0, (baseline_time or 0) * .01)
        return {"baseline_revision": revision, "candidates": results,
                "selection": "Client selects using requirements and these measured estimates; no strength score is computed."}

    def export_project(self, project_id, revision, destination, job_id=None):
        directory, _, revision, _ = self._load(project_id, revision)
        source = directory / f"{revision}.3mf"
        if job_id:
            job = self.get_job(job_id)
            if job["status"] != "succeeded" or job["revision"] != revision or job["project_id"] != project_id:
                raise ValueError("Slice job must have succeeded for this exact project revision")
            source = self.workspace / "jobs" / job_id / "sliced.3mf"
        destination = Path(destination).expanduser().absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output)
        return {"path": str(destination), "revision": revision, "sliced": bool(job_id)}

    def open_in_studio(self, project_id, revision=None):
        self.runtime.require()
        directory, _, revision, _ = self._load(project_id, revision)
        log_path = directory / "gui-launch.log"
        with log_path.open("a") as log:
            subprocess.Popen([str(self.runtime.executable), str(directory / f"{revision}.3mf")],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return {"revision": revision, "launched": True, "state_sync": "none; opening a saved revision does not synchronize unsaved GUI state"}
