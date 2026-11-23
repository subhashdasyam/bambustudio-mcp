# Diagnose BambuStudio slice warnings

[README](../README.md) · [Tool reference](reference.md)

Use `diagnose_warning` before researching upstream source for a GUI warning.
The server includes a [versioned warning guide](../src/bambustudio_mcp/data/warning-guide.json)
with explanations, candidate settings, related geometry categories and pinned source URLs.
It recognizes floating regions, floating cantilevers, inactive support enforcers,
incomplete tree-support coverage and empty G-code layers. Other messages remain unknown.

## Start with the exact file and message

Save the GUI project under a new filename, then open that file with `open_project`.
Use `inspect_project` to identify the object and 1-based plate index.
Pass the message and the GUI's reported version:

```json
{
  "project_id": "<project id>",
  "revision": "<saved revision>",
  "warning_text": "It seems object bracket has floating regions. Please re-orient the object or enable support generation.",
  "target": {"object_id": "<object id>", "plate_index": 1},
  "studio_version": "02.08.04.57"
}
```

The response explains the warning, reads relevant saved project and object settings,
shows local volume overrides and project-wide support-paint counts, and links related geometry issues.
`setting_options` gives the catalog's actual editable scopes. Most support controls in this catalog
are project-only. A saved object override can still affect a GUI-loaded project; its presence does
not mean the MCP can edit that key at object scope.
Long object and issue lists are sampled; counts and report paths retain the scope of the full evidence.

`preset_difference_metadata` reports whether GUI preset-difference entries exist and are nonempty.
This helps investigate a saved value that changes when the GUI reloads presets.
Empty metadata alone does not prove that the GUI changed a setting. Confirm the relevant value in
the GUI, save it to a new project, and compare that exact saved file. The tool does not rewrite presets
or synchronize the GUI.

## Reproduce against the saved revision

Run `analyze_geometry` and show its highlighted previews. Then call `start_slice` on the same revision
and plate. Poll `get_job` until terminal status. Its `native_diagnostics` includes recognized warning
explanations automatically, including warnings collected from failed slice jobs.

Call `diagnose_warning` again with the same inputs and that individual slice `job_id`.
Jobs from another project or revision are rejected. A comparison job is not an individual slice job.
The tool reads all native warning records in `studio.log` and the native `result.json`, rather than
searching only the log tail. The CLI result keeps only the last slicing warning for each plate.
Additional messages can survive in the log. Repeated messages from the same plate are deduplicated.

The result distinguishes an observed warning class from an unknown message, incomplete job,
missing reproduction and a warning not observed in the available artifacts. A selected plate must
match an observed native warning to count as reproduction. Unknown-plate validation messages are
retained but cannot confirm the warning on a selected plate.

The native job version and binary hash come from the job's runtime snapshot. The reported GUI
version is separate. A version mismatch means the bundled explanation needs verification for that GUI.
For existing sliced archives, `inspect_slice` supplies G-code evidence; it cannot reconstruct GUI
notifications that were never saved. Re-slice the saved revision to obtain native warning evidence.

## Interpret the result

- Native warnings generally omit feature coordinates. Related issue IDs are geometry candidates,
  not confirmed native warning locations. Names can be duplicated, and native runtime object IDs
  are not assumed to equal saved 3MF IDs.
- “Floating regions” comes from Studio's support-necessity detector. The MCP's floating-island
  section check uses a different algorithm. One can find a risk the other misses.
- Missing native warnings do not establish that a GUI warning is resolved. Unsaved changes,
  preset reloads, local overrides, Studio versions and log coverage can differ.
- Check actual paths with `inspect_slice` after a successful slice. Verify generated support,
  bridge/overhang behavior and joint clearances against the geometry issues before exporting.
- For moving parts or tight fits, compare a small cut-out coupon before a long print. Slice evidence
  does not establish physical fit, support removal, strength or finish.

Full native messages are saved in the job's `diagnostics.json`. Tool results contain up to five
message samples, their counts and the report path. Detailed diagnosis is saved separately in the
project workspace. The server does not change settings as part of diagnosis.

For an unknown or translated message, retain its original text and inspect these local artifacts first.
If source research is still necessary, use the matching Studio version and revision. Do not assume
upstream `master` matches the installed application.
