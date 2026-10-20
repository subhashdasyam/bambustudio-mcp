# Tool and project reference

[README](../README.md) · [Print preparation](usage.md)

The server exposes 16 tools over MCP stdio. Tool discovery supplies the current input schemas.

## Tools

| Tool | Purpose |
| --- | --- |
| `get_environment` | Report the runtime, compatibility, profiles path, and catalog coverage |
| `list_profiles` | Find machine, process, and filament profiles |
| `open_project` | Copy a saved 3MF or import a model with explicit profiles |
| `inspect_project` | Inspect objects, volumes, instances, plates, bounds, and revisions |
| `list_options` | Search option types, units, ranges, defaults, enums, and scopes |
| `get_settings` | Read values and their origin at a selected scope |
| `apply_settings` | Validate a batch and create a new revision |
| `restore_revision` | Copy a historical revision into a new head |
| `analyze_geometry` | Measure geometry and create SVG previews |
| `edit_regions` | Apply orientation, paint, modifiers, height ranges, or layer profiles |
| `start_slice` | Start a detached BambuStudio slice job |
| `get_job` | Read persistent job status, log excerpts, estimates, and output paths |
| `cancel_job` | Cancel a job and terminate its Studio subprocesses |
| `compare_candidates` | Compare the baseline with one to seven alternatives |
| `export_project` | Export a revision to a new destination |
| `open_in_studio` | Open a saved revision in the BambuStudio GUI |

## Resources and prompt

| URI | Contents |
| --- | --- |
| `bambustudio://guide` | Preparation procedure and reference sources |
| `bambustudio://spatial-edits` | Exact edit shapes, units, and examples |
| `bambustudio://coverage` | Catalog provenance, restrictions, and verification status |
| `bambustudio://catalog` | Full versioned catalog |
| `bambustudio://project/{project_id}` | Project inspection report |

The `prepare_print` prompt accepts `project_id` and an optional `intended_use`.
It guides inspection, reversible alternatives, comparison, and export.
It also requests missing filament details and directs manufacturer research through the client's web search.
If exact specifications cannot be verified or web search is unavailable, it directs the assistant to a compatible Generic profile.

## Values and scopes

Use JSON numbers, booleans, enum strings, and arrays.
Single-nozzle vector options still require arrays. Nullable vectors accept `null` elements.
Percent options accept numbers or percentage strings.
Float-or-percent options preserve the difference between `10` and `"10%"`.
Use the serialized enum value from `list_options`.

| Scope | Target fields |
| --- | --- |
| `project` | No target |
| `object` | `object_id` |
| `volume` | `object_id`, `volume_id` |
| `height_range` | `object_id`, `min_z`, `max_z` |
| `plate` | `plate_index`, starting at 1 |
| `filament` | `filament_index`, starting at 0 |

Only Studio's persisted plate fields accept plate overrides.
The server rejects unsupported scopes and protected fields. The catalog does not imply unrestricted write access to all 603 options.

For `start_slice`, `plate_index: 0` selects all plates. Positive indexes start at 1.
This selection differs from the target required for a plate override.

## Spatial edits

Read `bambustudio://spatial-edits` before an edit.
The same examples exist in [spatial-edits.json](../src/bambustudio_mcp/data/spatial-edits.json).

| Action | Behavior |
| --- | --- |
| `rotate` | Apply relative XYZ angles about the project origin, then place the selected instance on the bed |
| `paint` | Enforce, block, or clear support paint or seam paint on selected faces |
| `modifier_box` | Add a box modifier with local configuration values |
| `remove_modifier` | Remove a selected modifier volume |
| `height_range` | Apply configuration values within a height range |
| `layer_heights` | Replace an object's variable layer profile |

Paint selects original face indexes or a box in world millimeters.
Modifier boxes use object coordinates in millimeters.
Paint affects every instance of the shared mesh resource. It replaces earlier subface paint on selected faces.
Unselected faces retain their paint.

Rotation does not move other instances.
A layer profile replaces the complete previous profile. Its Z values must increase from zero or a positive value.
Layer heights must obey the nozzle limits.

## Geometry and archive limits

Geometry reports cover dimensions, components, face orientation, watertightness, conditional volume, sampled thickness, overhangs, and bed contact.
SVG files provide static geometry previews. These reports do not calculate stress or strength.

The server accepts millimeter projects only. Archive limits are 1 GiB total and 512 MiB per entry.
The server rejects cyclic components, malformed references, and unknown runtime versions.
It preserves unknown project entries. After edits, it removes known stale slice artifacts and previews.
Studio regenerates artifacts where the runtime supports them.

Read the [verification limits](verification.md) before you interpret coverage or slice results.
