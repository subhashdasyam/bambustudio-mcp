# Prepare a print with evidence

[README](../README.md) · [Tool reference](reference.md)

The client supplies print advice. The server supplies geometry observations, supported edits, and BambuStudio slice estimates.
A successful slice proves that the runtime accepted that project. It does not prove mechanical strength or surface quality.

## Give the assistant useful input

Supply these details:

| Input | Example |
| --- | --- |
| Saved project or model | Absolute path to a `.3mf` or `.stl` file |
| Filament | Material, brand, exact product/variant, color, drying history and calibration |
| Printer and nozzle | Bambu Lab P1S, 0.4 mm nozzle |
| Purpose | A bracket that holds a downward load |
| Constraints | Screw locations, contact faces, load direction, and expected load |
| Critical geometry | Hole diameters, fit surfaces, minimum clearances |
| Visible surfaces | Front face, top face, acceptable seam locations |
| Priorities | Strength first, then finish, with a two-hour time limit |

For a saved 3MF, inspect the retained configuration first.
For a raw model, find compatible machine, process, and filament profiles with `list_profiles`.
Do not infer the load direction or material properties from the mesh alone.

Before print recommendations or slices, the assistant asks for missing filament details.
The assistant uses the client's web search to find official manufacturer specifications for the exact product/variant.
It cites source URLs for temperature, drying, cooling and speed guidance.
Specifications for another product, such as Rapid PETG, do not establish settings for standard PETG.

If exact specifications cannot be verified or web search is unavailable, use a matching Generic material profile.
Select a compatible profile with `list_profiles`. Label its values as unverified starting points for calibration.
Keep any known limits from the spool label. Ask the user if the material or a compatible Generic profile is unknown.
For saved projects, check that the filament settings match the actual spool before a slice.

For an STL import, the server uses the selected profiles to create a 3MF project in the workspace.
Export the configured result to a new `.3mf` file with `export_project`.
The original STL stays unchanged. The exported 3MF contains the geometry and print configuration for BambuStudio.

Use `get_print_guidance` to receive explicit missing fields and sourced checks for the selected quality concerns.
Read `bambustudio://print-context`, then use `record_print_context` for the actual printer, each active nozzle, and every assigned spool.
Include firmware, build plate, enclosure conditions, intended use and each spool's exact feeding path.
Record unavailable brand or product details as `unknown`. Leave unavailable numeric calibration measurements unset.
Use physical observations for the tested revision. Do not transfer a finish result to a different revision.

## Use a baseline and a small comparison

1. Call `get_environment` to check the runtime.
2. Call `open_project` to create a project copy.
3. Read `bambustudio://guide` for the preparation procedure.
4. Call `inspect_project` to find object and volume identifiers.
5. Read the current values with `get_settings`.
   Call `get_print_guidance` and record confirmed context. Resolve relevant setup mismatches before recommendations.
6. Call `analyze_geometry` to inspect the dimensions and sampled geometry.
7. Find applicable controls with `list_options`.
8. Apply a candidate with `apply_settings` or `edit_regions`.
9. Call `compare_candidates` with the baseline revision and selected alternatives.
10. Poll `get_job` until the job reaches a terminal state.
    Review its `inspection` report separately from Studio success. Use `inspect_slice` for older sliced imports.
11. Export the selected revision to a new filename with `export_project`.
12. Review the saved export in BambuStudio.

`compare_candidates` includes the unchanged baseline and one to seven alternatives.
Each alternative starts from the same baseline revision.
Global alternatives use `{name, changes}`. Spatial alternatives use `{name, revision}` from earlier edits.
The numeric constraints are `max_time_seconds` and `max_filament_g`.
The client handles qualitative strength and finish requirements separately.

## Ask for a reason for each change

> Inspect my saved bracket project. Identify thin regions, overhangs, and bed contact limits.
> Explain each observation separately from assumptions about strength.
> Compare the baseline with one orientation change and one local reinforcement alternative.
> Keep the material and nozzle profiles constant. Show estimated time and filament for every candidate.
> Explain each compromise. Do not label any candidate mechanically stronger without physical evidence.

The assistant can reason about layer direction, walls, infill, support contact, seams, and local reinforcement.
The server does not perform finite element analysis or calculate certified load capacity.
Thickness samples can miss narrow features. A watertight mesh does not prove a suitable print orientation.

## Keep revisions and slice results consistent

Use the revision from the latest tool result for each edit.
The server rejects an edit against an old head revision.
`restore_revision` copies a historical revision into a new head. It retains the previous history.

Each slice uses an immutable project copy. Only one slice runs at a time per workspace.
The default slice deadline is 30 minutes. Jobs retain their results after a client disconnect.
Use `cancel_job` to cancel a job. Start a new job after an interrupted worker.

For slice artifacts in an export, supply a successful `job_id` for that exact project revision.
The export destination must not exist. The server does not overwrite the original project.
`open_in_studio` opens a saved revision. It does not synchronize unsaved GUI changes.

## Check the physical result

1. Print repeated baseline and candidate samples with the same material and machine conditions.
2. Apply representative loads in the intended direction.
3. Measure deflection or failure load with a repeatable method.
4. Measure critical dimensions and fit.
5. Inspect the same visible surfaces under the same light.
6. Record actual print time and filament use.

Choose a candidate from those measurements and your stated priorities.
Time and material estimates alone cannot establish strength, finish, or fatigue life.
