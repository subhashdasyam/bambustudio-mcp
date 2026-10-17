# 🧩 BambuStudio MCP

### Less menu diving. More informed slices.

![Platform: Linux x86-64](https://img.shields.io/badge/platform-Linux_x86--64-222222)
![Transport: MCP stdio](https://img.shields.io/badge/MCP-stdio-7c3aed)
![Clients: Claude Code and Codex](https://img.shields.io/badge/clients-Claude_Code_%2B_Codex-0d9488)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

Give Claude Code or Codex the tools to inspect a part, change its print configuration, and compare actual BambuStudio slices.

> **This MCP server is for BambuStudio, the desktop slicer. It is NOT for Bambu Lab printer control.**
> It runs outside BambuStudio and does not change BambuStudio source code.
> It works with saved projects, not unsaved changes in the GUI.

[Quick start](#-quick-start) · [Example requests](#-give-your-assistant-a-print-brief) · [Documentation](#-go-deeper) · [Evidence](docs/verification.md)

## ✨ What it does for your print

Ask for a stronger bracket, fewer supports, or a cleaner visible face. Give the assistant your material, load directions, and priorities.

The assistant proposes changes. The server applies supported edits to project copies and runs BambuStudio for time and filament estimates.
The source project stays unchanged.

| Your goal | Tools that help |
| --- | --- |
| Understand the part | Dimensions, overhangs, bed contact, sampled thickness, and SVG previews |
| Plan reinforcement | Orientation, walls, infill, object overrides, and local box modifiers |
| Refine the finish | Layer heights, seam paint, support paint, and process options |
| Compare the cost | The unchanged baseline plus up to seven alternatives, with time and filament estimates |
| Keep useful experiments | Project revisions, history restore, and export to a new file |
| Search the controls | A versioned catalog of 603 source-derived FFF options, with types, units, ranges, and scope rules |

**Strength and surface quality need physical checks.** Geometry and slice estimates do not prove either result.
The catalog has explicit restrictions. It does not mean that every GUI control or option combination has complete verification.

The assistant supplies advice through Claude Code or Codex. The server supplies local tools and slice results.
It needs no separate model service. Your chosen client still determines how it sends data to its model provider.

## 🚀 Quick start

### 1. Prepare the server

Use Linux x86-64, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Install Claude Code or Codex before its registration step.

Open a terminal in this repository. Run these commands in the same shell:

```bash
export BAMBUSTUDIO_MCP_DIR="$(pwd -P)"
uv sync --frozen
mkdir -p "$BAMBUSTUDIO_MCP_DIR/runtime"
```

### 2. Get BambuStudio from GitHub Releases

The supported runtime is **BambuStudio `02.08.04.57` (2.8.4 Public Beta)**.
The commands use the Ubuntu 22.04 AppImage from the [official release](https://github.com/bambulab/BambuStudio/releases/tag/v02.08.04.57).
Other Studio versions fail the server's version check.

Download the AppImage:

```bash
export BAMBUSTUDIO_APPIMAGE="BambuStudio_ubu22-v02.08.04.57-20260922164607.AppImage"
curl --fail --location \
  "https://github.com/bambulab/BambuStudio/releases/download/v02.08.04.57/$BAMBUSTUDIO_APPIMAGE" \
  --output "$BAMBUSTUDIO_MCP_DIR/runtime/$BAMBUSTUDIO_APPIMAGE"
```

Extract the AppImage into `runtime/squashfs-root`:

```bash
chmod +x "$BAMBUSTUDIO_MCP_DIR/runtime/$BAMBUSTUDIO_APPIMAGE"
(
  cd "$BAMBUSTUDIO_MCP_DIR/runtime" &&
  "./$BAMBUSTUDIO_APPIMAGE" --appimage-extract > extraction.log
)
```

The AppImage contains the executable, resources, and profiles. No separate profile download is necessary.
The repository ignores `/runtime/` through [.gitignore](.gitignore).

Already have the AppImage? Use the [instructions for a local download](docs/installation.md#use-an-existing-download).
For asset checksums and runtime problems, read the [installation guide](docs/installation.md).

### 3. Set the runtime and workspace paths

```bash
export BAMBUSTUDIO_EXECUTABLE="$BAMBUSTUDIO_MCP_DIR/runtime/squashfs-root/AppRun"
export BAMBUSTUDIO_PROFILES="$BAMBUSTUDIO_MCP_DIR/runtime/squashfs-root/resources/profiles"
export BAMBUSTUDIO_WORKSPACE="$HOME/.local/share/bambustudio-mcp/workspace"
```

Use **`AppRun`**, which sets the path to the bundled libraries.
The workspace holds project copies, revisions, logs, and slice results. The server creates it automatically.
Keep the workspace separate from the extracted runtime. See [paths and workspace](docs/installation.md#paths-and-workspace).

### 4. Connect your assistant

Run the command for your client in the same shell.
The command stores the current paths in the client configuration.

<details open>
<summary><strong>Claude Code</strong></summary>

```bash
claude mcp add bambustudio --scope user --transport stdio \
  --env "BAMBUSTUDIO_EXECUTABLE=$BAMBUSTUDIO_EXECUTABLE" \
  --env "BAMBUSTUDIO_PROFILES=$BAMBUSTUDIO_PROFILES" \
  --env "BAMBUSTUDIO_WORKSPACE=$BAMBUSTUDIO_WORKSPACE" \
  -- "$(command -v uv)" --directory "$BAMBUSTUDIO_MCP_DIR" \
  run --frozen bambustudio-mcp

claude mcp get bambustudio
claude mcp list
```

Keep `bambustudio` before `--env`. Claude accepts multiple values after each `--env` option.
The `--scope user` option makes the server available across your projects.

</details>

<details open>
<summary><strong>Codex</strong></summary>

```bash
codex mcp add bambustudio \
  --env "BAMBUSTUDIO_EXECUTABLE=$BAMBUSTUDIO_EXECUTABLE" \
  --env "BAMBUSTUDIO_PROFILES=$BAMBUSTUDIO_PROFILES" \
  --env "BAMBUSTUDIO_WORKSPACE=$BAMBUSTUDIO_WORKSPACE" \
  -- "$(command -v uv)" --directory "$BAMBUSTUDIO_MCP_DIR" \
  run --frozen bambustudio-mcp

codex mcp get bambustudio
codex mcp list
```

</details>

Start a new client session. Ask the assistant:

> Call the BambuStudio `get_environment` tool. Show the runtime version, compatibility result, and profiles path.

The expected version is `02.08.04.57`. The compatibility result must be `true`.
Ask for `list_profiles` to check access to the bundled profiles.
The client starts the server automatically. You do not need a separate server terminal.

For a previous registration or a connection error, read the [client guide](docs/clients.md).

## 🎯 Give your assistant a print brief

### A bracket with a job to do

> Open `/absolute/path/bracket.3mf`. Use PETG and the current 0.4 mm nozzle profile.
> The screws hold the back face. A downward load acts on the front arm.
> Preserve the holes. Compare the current project with two alternatives.
> Consider orientation, walls, local reinforcement, infill, and supports.
> Show time and filament estimates. Explain strength assumptions. Export my selected revision to a new filename.

### A visible surface that matters

> Inspect `/absolute/path/enclosure.3mf`. The front face is visible.
> Compare seam placement, layer heights, and support choices for that face.
> Show the time cost of each alternative. List the surface checks I must do on a physical print.

### A time budget with a baseline

> Open `/absolute/path/fixture.3mf`. Keep the mounting surfaces and holes unchanged.
> Compare the baseline with three alternatives under 7,200 seconds of estimated print time.
> Explain each compromise. Keep the baseline if no candidate meets the limit.

The workflow is **inspect → revise → slice → compare → export → review in BambuStudio**.
For raw STL imports, select explicit machine, process, and filament profiles with `list_profiles`.
Saved 3MF projects retain their configuration.

## 📚 Go deeper

| Guide | Contents |
| --- | --- |
| [Installation](docs/installation.md) | Official AppImage, extraction, checksums, profiles, workspace, and runtime problems |
| [Claude Code and Codex](docs/clients.md) | CLI setup, saved paths, connection checks, and the `--env` error |
| [Print preparation](docs/usage.md) | Useful input, candidate comparisons, revisions, and physical checks |
| [Tool reference](docs/reference.md) | All 16 tools, resources, scopes, value types, and spatial edits |
| [Verification and limits](docs/verification.md) | Recorded evidence, repeatable checks, coverage gaps, and development checks |

## 🔎 Evidence, without the magic claims

The recorded implementation checks report **33 passes**, a real BambuStudio round trip, and completed Claude Code and Codex workflows.
See the [evidence and its limits](docs/verification.md) before you interpret these results.

The complete-coverage release gate remains open. A compiled metadata comparison still needs a complete Studio development build.
No physical strength or finish measurements support the current results.

## License

[AGPL-3.0-or-later](LICENSE). Read the [third-party notices](NOTICE.md) for catalog provenance and dependency notices.
BambuStudio comes from its upstream release. This repository does not distribute the Studio executable.
