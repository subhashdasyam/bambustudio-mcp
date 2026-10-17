# Verification and known limits

[README](../README.md) · [Tool reference](reference.md)

## Recorded results

The [evidence directory](../evidence/) contains sanitized results from the implementation session.
These records describe that run. They do not describe a fresh run on your computer.

| Check | Recorded result | Evidence |
| --- | --- | --- |
| Automated suite | 33 passes, no failures, no skips | [verification.json](../evidence/verification.json) |
| Real Studio round trip | Successful | [live-check.json](../evidence/live-check.json) |
| Claude Code workflow | Change, slice, and export completed | [claude.json](../evidence/claude.json) |
| Codex workflow | Change, slice, and export completed | [codex.json](../evidence/codex.json) |
| MCP tool discovery | Inspector report | [inspector.json](../evidence/inspector.json) |
| BambuStudio source | No source changes | [verification.json](../evidence/verification.json) |

The baseline round trip preserved 574 values. Studio normalized one value, `filament_prime_volume`.
These results establish representative integration behavior. They do not establish every option combination or a global optimum.

## Coverage is not complete

The catalog contains 603 source-derived FFF options from source revision `79df7ae30`.
Read-only fields and printer communication fields have explicit restrictions.
The catalog records metadata provenance separately from runtime evidence.

The complete-coverage release gate remains open.
A compiled exporter exists, but its local build stopped at missing Boost development libraries, version 1.83 or later.
The installed Studio runtime and the external server do not need that development build.

The metadata checks do not reproduce every rule in the Studio GUI.
A saved value can differ from the effective value after Studio normalization.
Completed jobs report normalized values from Studio's export. Review those values and slice warnings.

## Repeat the automated checks

From the repository root, install the test dependencies:

```bash
uv sync --frozen --extra test
uv run --frozen pytest -q
```

For the real Studio checks, set the runtime variables from the [installation guide](installation.md).
Set the bundled model path:

```bash
export BAMBUSTUDIO_TEST_MODEL="$BAMBUSTUDIO_MCP_DIR/runtime/squashfs-root/resources/model/Bambu_Cube.stl"
uv run --frozen pytest -q tests/test_live.py
```

Generate a separate report with a new workspace path:

```bash
uv run --frozen python tools/live_check.py "$BAMBUSTUDIO_TEST_MODEL" \
  --workspace /tmp/bambu-mcp-check --output /tmp/bambu-mcp-check.json
```

The live test compares direct CLI and MCP time estimates within 1%.
It compares filament estimates within 0.1%. Independent Studio runs can produce small differences.
Candidate comparisons mark time differences within the larger of one second or 1% of baseline time as inconclusive.

## Inspect the MCP interface

With Node.js and npm available, run:

```bash
npx @modelcontextprotocol/inspector --cli \
  uv --directory "$BAMBUSTUDIO_MCP_DIR" run --frozen bambustudio-mcp \
  --method tools/list
```

The command can download the Inspector package. It checks tool discovery, not print quality.

## Compare compiled metadata

This developer check requires a complete Studio development build of the same version.
The exporter uses an external CMake include. It does not edit Studio source files.

1. Configure the Studio build with `-DCMAKE_PROJECT_INCLUDE=/absolute/path/to/bambustudio-mcp/tools/exporter.cmake`.
2. Build the `bambustudio-mcp-exporter` target.
3. Compare its JSON output with [catalog.json](../src/bambustudio_mcp/data/catalog.json).
4. Resolve differences before a complete-coverage release.

## Results that still need independent checks

- Every supported option combination and every Studio dependency rule.
- Visual review of exports in the Studio GUI.
- Physical strength, layer adhesion, fatigue life, dimensions, and surface quality.
- Platforms and release assets outside the documented Linux runtime.

Use repeated baseline and candidate prints for physical claims.
See the [physical result procedure](usage.md#check-the-physical-result).
