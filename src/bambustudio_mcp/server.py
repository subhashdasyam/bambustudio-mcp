from __future__ import annotations

import asyncio
import json
from importlib.resources import files

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .service import Service

FILAMENT_INSTRUCTIONS = (
    "Before print recommendations or slices, confirm the filament material, brand, exact product/variant and color. "
    "Ask about drying history and calibration. "
    "Ask only for missing details. Do not infer the actual spool from a saved profile. "
    "Use the client's web search to find official manufacturer specifications for that exact product. Cite the source URLs. "
    "Never substitute specifications for another product or variant. "
    "If exact specifications cannot be verified or web search is unavailable, use a Generic profile from list_profiles. "
    "Match the confirmed material, printer and nozzle. "
    "If the material is unknown or no compatible Generic profile exists, ask the user before proceeding. "
    "Identify values from manufacturer specifications, profiles and calibration. Label Generic values as unverified starting points. "
    "Check saved filament settings against the confirmed spool before slicing."
)

INSTRUCTIONS = f"""Prepare saved BambuStudio projects; never control a printer. Inspect the project and options first. Confirm the actual printer, every active nozzle and filament. Ask for intended loads, critical surfaces and time constraints when missing. {FILAMENT_INSTRUCTIONS} Read bambustudio://quality-guide and call get_print_guidance before recommendations. Record user declarations with record_print_context. Resolve missing inputs and hardware/material mismatches. Use explicit revisions for changes. Compare alternatives against an unchanged baseline. Review get_job inspection or call inspect_slice for existing sliced archives. Check commands separately from saved settings. Use matched physical samples for fit and finish decisions. Geometry and slicer estimates do not prove strength or finish. Read bambustudio://guide before recommendations. Studio's unsaved GUI state is not synchronized. Poll jobs until terminal status. Preserve the original and export to a new filename."""


def create_server(service=None):
    service = service or Service()
    mcp = FastMCP("BambuStudio", instructions=INSTRUCTIONS)
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=read)
    async def get_environment() -> dict:
        """Report the Studio runtime, supported version, profiles location, and honest configuration coverage."""
        return await asyncio.to_thread(service.runtime.environment)

    @mcp.tool(annotations=read)
    def list_profiles(query: str = "", kind: str | None = None) -> dict:
        """Find machine, process, or filament profiles. Use exact names or paths when importing models."""
        return {"profiles": service.runtime.list_profiles(query, kind)}

    @mcp.tool(annotations=write)
    async def open_project(path: str, machine_profile: str | None = None, process_profile: str | None = None,
                           filament_profiles: list[str] | None = None) -> dict:
        """Copy a saved 3MF into an isolated workspace, or import a model with explicit profiles through Studio."""
        return await asyncio.to_thread(service.open_project, path, machine_profile, process_profile, filament_profiles)

    @mcp.tool(annotations=read)
    def inspect_project(project_id: str, revision: str | None = None) -> dict:
        """Inspect objects, volumes, resource IDs, instances, plates, bounds, and revision history."""
        return service.inspect_project(project_id, revision)

    @mcp.tool(annotations=read)
    def list_options(query: str = "", scope: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        """Search the full versioned FFF configuration catalog; inspect types, units, ranges, defaults and scopes."""
        return service.catalog.list(query, scope, offset, limit)

    @mcp.tool(annotations=read)
    def get_settings(project_id: str, revision: str | None = None, scope: str = "project", target: dict | None = None,
                     keys: list[str] | None = None) -> dict:
        """Read settings and their origin. Scopes: project, plate, object, volume, height_range, filament.

        target selects plate_index (1-based), object_id, volume_id, min_z/max_z, or filament_index (0-based).
        Use keys to bound the response. Studio-normalized settings are returned by completed slice jobs.
        """
        return service.get_settings(project_id, revision, scope, target, keys)

    @mcp.tool(annotations=write)
    def record_print_context(project_id: str, revision: str, context: dict) -> dict:
        """Replace user-declared hardware, filament, calibration and physical observations for this project.

        Read bambustudio://print-context for the exact input shape. This records declarations, not verified measurements.
        It does not edit the 3MF. Observations apply only to this revision; optional job_id must match a succeeded slice.
        """
        return service.record_print_context(project_id, revision, context)

    @mcp.tool(annotations=read)
    def get_print_guidance(project_id: str, revision: str | None = None) -> dict:
        """Return deterministic missing-input questions, context/settings mismatches and sourced rules for selected concerns.

        Select concerns from bambustudio://quality-guide. Inputs are user declarations; no physical quality is inferred.
        """
        return service.get_print_guidance(project_id, revision)

    @mcp.tool(annotations=read)
    async def inspect_slice(project_id: str, revision: str | None = None, job_id: str | None = None) -> dict:
        """Inspect embedded G-code targets, object layers and feature feeds without running it.

        With job_id inspect that exact succeeded revision; otherwise inspect the saved project, including older sliced imports.
        Unknown firmware branches and mappings remain unknown. Targets do not establish actual temperatures or speeds.
        """
        return await asyncio.to_thread(service.inspect_slice, project_id, revision, job_id)

    @mcp.tool(annotations=write)
    async def apply_settings(project_id: str, revision: str, changes: dict, scope: str = "project", target: dict | None = None) -> dict:
        """Validate and apply an atomic batch to a new revision. Uses JSON numbers, booleans, enums and arrays.

        Percentage strings such as '15%' are allowed only on percent types. A filament-scope change selects
        one existing filament_index; project-scope vector values must be complete arrays. Never overwrites the source.
        """
        return await asyncio.to_thread(service.apply_settings, project_id, revision, changes, scope, target)

    @mcp.tool(annotations=write)
    async def restore_revision(project_id: str, revision: str, restore: str) -> dict:
        """Restore a historical or candidate revision as a new head; preserve all previous revisions.

        revision is the current head for concurrency protection; restore is the revision to copy.
        """
        return await asyncio.to_thread(service.restore_revision, project_id, revision, restore)

    @mcp.tool(annotations=read)
    async def analyze_geometry(project_id: str, revision: str | None = None, overhang_angle: float = 45.0) -> dict:
        """Measure geometry, sampled thickness, contact and downward-facing overhang regions; save SVG previews.

        Coordinates are world millimetres. Thickness is sampled geometry, not mechanical strength.
        Overhang angle is the downward-face threshold from horizontal in degrees (0..90).
        """
        return await asyncio.to_thread(service.analyze_geometry, project_id, revision, overhang_angle)

    @mcp.tool(annotations=write)
    async def edit_regions(project_id: str, revision: str, edits: list[dict]) -> dict:
        """Apply spatial edits atomically to a new revision. Read bambustudio://spatial-edits for exact shapes.

        Actions: rotate, paint, modifier_box, remove_modifier, height_range, layer_heights.
        Paint edits a shared mesh resource, affecting every instance of that resource.
        """
        return await asyncio.to_thread(service.edit_regions, project_id, revision, edits)

    @mcp.tool(annotations=write)
    async def start_slice(project_id: str, revision: str | None = None, plate_index: int = 0, timeout_seconds: int = 1800) -> dict:
        """Queue a detached Studio slice. Returns immediately; poll get_job. 0 selects all plates; positive indexes are 1-based."""
        return await asyncio.to_thread(service.start_slice, project_id, revision, plate_index, timeout_seconds)

    @mcp.tool(annotations=read)
    def get_job(job_id: str) -> dict:
        """Read persistent slice or comparison status, log tail, estimates, and output locations."""
        return service.get_job(job_id)

    @mcp.tool(annotations=write)
    def cancel_job(job_id: str) -> dict:
        """Cancel a queued or running job and terminate its Studio subprocesses."""
        return service.cancel_job(job_id)

    @mcp.tool(annotations=write)
    async def compare_candidates(project_id: str, revision: str, candidates: list[dict], constraints: dict | None = None) -> dict:
        """Compare unchanged baseline with 1..7 alternatives, each {name, changes} or {name, revision}.

        Returns immediately with a persistent comparison job; poll get_job. Numeric constraints:
        max_time_seconds, max_filament_g. Spatial candidates use revisions made with
        edit_regions. Reports time/material estimates and tradeoffs; strength/finish remain unverified.
        """
        return await service.compare_candidates(project_id, revision, candidates, constraints)

    @mcp.tool(annotations=write)
    def export_project(project_id: str, revision: str, destination: str, job_id: str | None = None) -> dict:
        """Export to a new filename. Supply a succeeded job_id to export that exact revision with slicing artifacts."""
        return service.export_project(project_id, revision, destination, job_id)

    @mcp.tool(annotations=write)
    async def open_in_studio(project_id: str, revision: str | None = None) -> dict:
        """Launch the saved revision in Studio for review. This does not synchronize or replace unsaved GUI state."""
        return await asyncio.to_thread(service.open_in_studio, project_id, revision)

    @mcp.resource("bambustudio://guide")
    def guide() -> str:
        """Evidence-led print preparation procedure and reference sources."""
        return files("bambustudio_mcp.data").joinpath("guide.md").read_text()

    @mcp.resource("bambustudio://spatial-edits")
    def spatial_edits() -> str:
        """Spatial edit inputs, units, and example requests."""
        return files("bambustudio_mcp.data").joinpath("spatial-edits.json").read_text()

    @mcp.resource("bambustudio://quality-guide")
    def quality_guide() -> str:
        """Sourced preparation rules, prerequisites, candidate settings and physical acceptance checks."""
        return files("bambustudio_mcp.data").joinpath("quality-guide.json").read_text()

    @mcp.resource("bambustudio://print-context")
    def print_context() -> str:
        """Exact user-declaration input shape and example for record_print_context."""
        return files("bambustudio_mcp.data").joinpath("print-context.json").read_text()

    @mcp.resource("bambustudio://coverage")
    def coverage() -> str:
        return json.dumps(service.catalog.data["coverage"], indent=2)

    @mcp.resource("bambustudio://catalog")
    def catalog() -> str:
        """Complete versioned catalog. Prefer list_options for paginated queries."""
        return json.dumps(service.catalog.data, indent=2)

    @mcp.resource("bambustudio://project/{project_id}")
    def project_report(project_id: str) -> str:
        return json.dumps(service.inspect_project(project_id), indent=2)

    @mcp.prompt()
    def prepare_print(project_id: str, intended_use: str = "unspecified") -> str:
        return f"Read bambustudio://guide and bambustudio://quality-guide. Inspect project {project_id}. Intended use: {intended_use}. {FILAMENT_INSTRUCTIONS} Call get_print_guidance and record confirmed inputs with record_print_context. Resolve missing inputs and mismatches for the actual printer, nozzles and filaments. Obtain missing load, attachment, surface, tolerance and time requirements. Explain geometry observations separately from mechanical hypotheses. Make reversible candidates, compare against baseline, inspect actual slice commands, and export the selected revision. Use matched physical samples and record fit/finish observations for their exact revision. Never claim measured strength or finish without physical evidence."

    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
