# Reusable process and filament presets

[README](../README.md) · [Tool reference](reference.md)

Project settings belong to one saved 3MF. Reusable presets belong to a configured library and can supply settings to many projects.
This implementation recreates the storage feature described in [commit 27177d7](https://github.com/unf0rg0tt3n/bambustudio-mcp/commit/27177d75923ce59d4b13c5222b0ad1f253e034cd).
It uses this server's catalog, compact MCP results and project revision workflow.

Process presets contain print settings, including support-material slot assignments. Filament presets describe one material.
An AMS tray number is not a project filament slot. Confirm the actual printer, nozzle, spool and calibration before applying advice.
Machine profiles, executable G-code edits and printer control are outside these tools.

## Configure storage

Copy [the example configuration](../examples/preset-targets.json) to a private location outside the repository.
Replace its paths with real absolute paths and set:

```bash
export BAMBUSTUDIO_PRESET_TARGETS=/absolute/private/targets.json
```

No configuration means no preset targets. A malformed optional configuration returns a preset-tool error; the core slicer tools remain available.
The tools accept target IDs, not arbitrary hosts or storage paths. Target listings omit private paths and SSH aliases.

Each local target declares `root`, `system_roots`, `layout` and optional `writable` (default `false`).
Create the library directories before use. Vendor roots contain `process/` and `filament/` with the actual installation's defaults.
Do not point a writable user root at a vendor resource directory.

| Layout | Process files | Filament files | Output |
| --- | --- | --- | --- |
| `desktop` | `root/process/` | `root/filament/` | User overrides inheriting an installed base by internal name |
| `api` | `root/presets/` | `root/filaments/` | A resolved user preset with empty `inherits` |

The API layout is a filesystem convention for services that store profiles this way. There is no HTTP upload or API-server integration.
Such services may need their own normalization, import or cache reload. Compatibility with a particular external API is not claimed.

Use the actual active Studio account/profile directory for desktop storage. No automatic account-directory discovery is attempted.
Run storage operations with the UID that consumes the presets. New and replacement files use mode `0600`;
directory write access from a different SSH/container account does not make those files readable by Studio or an API service.
Paths must have no symlink ancestors; files must be regular and have one hard link.
The implementation requires POSIX directory descriptors, advisory locking and atomic filesystem operations.
Native Windows paths and unverified network/cloud-sync filesystem semantics are unsupported.

## Preview, create and update

1. Call `list_preset_targets`, then `list_presets(target, kind, query, offset, limit)`.
2. Read a concrete base with `read_preset`. Its default response gives identity, compatibility and ancestry.
   Request up to 50 `keys` to read resolved Bambu JSON values. Untyped templates cannot be used as concrete derivation bases.
3. Call `derive_preset(target, kind, base, name, changes)` for a typed change preview.
   Changes use JSON numbers, booleans and arrays; stored values retain Bambu's string/vector format.
4. Call `create_preset` with the same arguments. It defaults to `dry_run=true`.
   For an actual write, use `dry_run=false` and enable the target's `writable` flag.
   Supply the preview's `base_revision` as `expected_base_revision` to reject changed ancestry.
5. Read the resulting preset. To update it, pass its content `revision` as `expected_revision` to `update_preset`.

New names cannot replace an existing internal name or filename. Existing vendor profiles are not writable through these tools.
Printer compatibility changes may narrow an existing list; they cannot expand it or remove its restrictions.
Unknown or out-of-range edited options fail catalog validation. Untouched vendor defaults remain intact.
Inheritance uses internal names, including support-material names containing `/`, while new filenames reject separators and hidden names.
Includes apply after inherited values and before the child's own values. Missing parents, duplicate names and cycles fail explicitly.

Example API shapes, not print recommendations:

```json
{"target":"desktop","kind":"process","base":"0.20mm Standard @BBL X1C","name":"My process","changes":{"wall_loops":4}}
```

```json
{"target":"desktop","kind":"filament","base":"Generic PETG HF @BBL X1C","name":"My calibrated material","changes":{"nozzle_temperature":[245]}}
```

Choose a base whose declared printer/nozzle list includes the actual machine. A profile name does not establish material calibration.
Use `validate_preset` to inspect ancestry and catalog issues in a stored file. Structural validity does not establish GUI or physical behavior.

## Backup and restore

Actual updates save exact prior bytes under the selected category's `.preset-backups/`, indexed by content checksum.
Use `list_preset_backups` and `restore_preset` with a backup ID and the current content revision.
Restore defaults to a preview, verifies checksum and identity, resolves the saved ancestry, and backs up current bytes before replacement.
Backups cover the user file, not the vendor parents. A later change to those parents can change the restored effective settings.
There is no automatic backup pruning or transaction spanning several profiles or targets.

Dry-runs do not create locks, directories, files or backups in a preset library.
Writes use anchored directory descriptors, a cooperating-writer lock, durable temporary files and atomic installation with readback.
Close Studio and stop competing/cloud writers before actual edits. Advisory locks cannot stop unrelated applications.
Reopen Studio and verify the preset appears in the intended account and printer filter; some versions may need manual JSON import.
These tools do not stop applications, synchronize GUI memory, update cloud metadata or resolve cloud conflicts.

## Apply selected settings to a project

`apply_preset` previews an explicit list of setting keys against an exact project revision.
It requires the saved machine profile to appear in the preset's compatibility list and checks saved nozzle layer-height limits.
Declared compatible-process lists are checked too. Nonempty compatibility expressions are preserved but not evaluated;
automatic application rejects them rather than claiming a match.
Filament application also requires an existing zero-based `filament_index` and an unambiguous one-material vector mapping.
Other filament slots and all unselected settings remain unchanged. Ambiguous extruder-versus-material vectors are rejected.

The default preview creates no project revision. With `dry_run=false`, the normal project lock and stale-revision check apply.
The tool creates a new revision, records the source preset and its ancestry fingerprint, and invalidates the old slice.
Supply `read_preset`'s `resolution_revision` as `expected_preset_revision` to guard against source changes.
Run geometry analysis again before slicing. This operation does not change the original model or the reusable preset.

## SSH and containers

SSH targets declare a private OpenSSH `host` alias, `remote_config`, `remote_target` and matching layout.
Install this package on the authorized POSIX storage host so `python3 -m bambustudio_mcp.preset_agent` is available.
That helper reads one bounded JSON request, performs an allowed local storage action, replies and exits.
It is not another MCP server and cannot forward requests onward. Both target configurations must permit actual writes.

Configure the alias, dedicated account/key and independently verified `known_hosts` outside the repository.
SSH uses batch mode, strict host-key checking, disabled forwarding and bounded input/output/time.
Only the fixed module command and administrator-configured remote path enter the SSH command line; operation data travels on stdin.
There is no automatic retry after a timeout: a write might have committed, so read back first.

The [container example](../compose.example.yaml) uses stdio with no exposed web port or Docker socket.
Its image includes OpenSSH and a named unprivileged account. Build arguments `MCP_UID` and `MCP_GID` can match the mount owner's IDs.
Set `PRESET_TARGETS_CONFIG`, `PRESET_ROOT` and `BAMBU_DEFAULTS` to existing host paths, then launch with:

```bash
podman compose -f compose.example.yaml run --rm -T mcp
```

Docker Compose can use the same file. The target configuration uses container paths from the example mounts.
Mount SSH configuration read-only only when needed. Grant access to the selected libraries, not a whole home directory.
The image does not contain Studio or its profiles. Preset storage does not need Studio, while project import/application/slicing still require the configured runtime.

## Evidence and limits

Tests use original Bambu profiles pinned by URL and checksum in `tests/fixtures/preset_profiles/manifest.json`.
They cover both layouts, actual ancestry/includes, identities, previews, revision conflicts, backup/restore, unsafe files,
concurrent creation, the real helper through simulated SSH transport, MCP calls and project application.
The live process-preset test applies selected values and checks an actual Studio slice without modifying its source model.
No desktop GUI import, particular remote-host connection, cloud synchronization or physical print is established by these tests.
