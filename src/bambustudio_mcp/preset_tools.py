"""Reusable preset tools registered separately from project editing tools."""
import asyncio
from typing import Annotated

from mcp.types import CallToolResult, ToolAnnotations


def register(mcp, service, compact):
    presets = service.presets
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
    add = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    @mcp.tool(annotations=read)
    def list_preset_targets() -> Annotated[CallToolResult, dict]:
        """List administrator-configured preset storage targets. Paths, credentials and hosts stay private."""
        return compact(presets.list_targets())

    @mcp.tool(annotations=read)
    async def list_presets(target: str, kind: str, query: str="", offset: int=0, limit: int=50) -> Annotated[CallToolResult, dict]:
        """Page reusable process or filament presets by internal name on a configured storage target."""
        return compact(await asyncio.to_thread(presets.run,target,"list",kind=kind,query=query,offset=offset,limit=limit))

    @mcp.tool(annotations=read)
    async def read_preset(target: str, kind: str, name: str, keys: list[str] | None=None) -> Annotated[CallToolResult, dict]:
        """Read identity, ancestry revision and compatibility. Select up to 50 keys for resolved Bambu JSON values."""
        return compact(await asyncio.to_thread(presets.run,target,"read",kind=kind,name=name,keys=keys))

    @mcp.tool(annotations=read)
    async def derive_preset(target: str, kind: str, base: str, name: str, changes: dict) -> Annotated[CallToolResult, dict]:
        """Preview a reusable preset from an installed concrete base with typed changes. Writes nothing.

        Process presets control support/interface filament slots; material presets describe a single material.
        Confirm printer/nozzle, spool specifications and calibration. Presets are not physical validation.
        """
        return compact(await asyncio.to_thread(presets.run,target,"derive",kind=kind,base=base,name=name,changes=changes))

    @mcp.tool(annotations=read)
    async def validate_preset(target: str, kind: str, name: str) -> Annotated[CallToolResult, dict]:
        """Inspect stored preset structure, ancestry and catalog issues. This does not verify GUI import or print quality."""
        return compact(await asyncio.to_thread(presets.run,target,"validate",kind=kind,name=name))

    @mcp.tool(annotations=add)
    async def create_preset(target: str, kind: str, base: str, name: str, changes: dict, dry_run: bool=True,
                            expected_base_revision: str | None=None) -> Annotated[CallToolResult, dict]:
        """Create a new preset from a base; default is a write-free preview. Never overwrite an existing name.

        Supply the preview's base_revision to reject changed ancestry. Actual writes need a writable configured target.
        Close Studio and stop competing/cloud writers before editing their preset files; reopen/import afterward.
        """
        return compact(await asyncio.to_thread(presets.run,target,"create",kind=kind,base=base,name=name,changes=changes,dry_run=dry_run,expected_base_revision=expected_base_revision))

    @mcp.tool(annotations=write)
    async def update_preset(target: str, kind: str, name: str, changes: dict, expected_revision: str,
                            dry_run: bool=True) -> Annotated[CallToolResult, dict]:
        """Preview/update an existing user preset with a matching content revision. Actual updates first save an exact backup."""
        return compact(await asyncio.to_thread(presets.run,target,"update",kind=kind,name=name,changes=changes,expected_revision=expected_revision,dry_run=dry_run))

    @mcp.tool(annotations=read)
    async def list_preset_backups(target: str, kind: str, name: str, offset: int=0, limit: int=50) -> Annotated[CallToolResult, dict]:
        """Page checksummed pre-update snapshots for a user preset."""
        return compact(await asyncio.to_thread(presets.run,target,"backups",kind=kind,name=name,offset=offset,limit=limit))

    @mcp.tool(annotations=write)
    async def restore_preset(target: str, kind: str, name: str, backup_id: str, expected_revision: str,
                             dry_run: bool=True) -> Annotated[CallToolResult, dict]:
        """Preview/restore exact backup bytes with a current revision guard. Actual restores back up current contents."""
        return compact(await asyncio.to_thread(presets.run,target,"restore",kind=kind,name=name,backup_id=backup_id,expected_revision=expected_revision,dry_run=dry_run))

    @mcp.tool(annotations=add)
    async def apply_preset(project_id: str, revision: str, target: str, kind: str, name: str, keys: list[str],
                           filament_index: int | None=None, dry_run: bool=True,
                           expected_preset_revision: str | None=None) -> Annotated[CallToolResult, dict]:
        """Preview/apply selected reusable settings to a new project revision. The original model and preset stay unchanged.

        Check exact printer/nozzle compatibility. Filament application needs an existing zero-based slot and unambiguous vectors.
        expected_preset_revision is read_preset's resolution_revision. Rerun geometry analysis after an actual change.
        """
        return compact(await asyncio.to_thread(service.apply_preset,project_id,revision,target,kind,name,keys,filament_index,dry_run,expected_preset_revision))
