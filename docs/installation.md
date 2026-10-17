# Install the server and BambuStudio runtime

[README](../README.md) · [Client setup](clients.md)

This server uses BambuStudio, the desktop slicer. It does not connect to a Bambu Lab printer.
The server runs outside BambuStudio. You do not need to change or compile BambuStudio source code.

## Requirements

| Item | Requirement |
| --- | --- |
| System | Linux x86-64 with the libraries that the selected AppImage requires |
| Python | 3.11 or later |
| Package manager | [uv](https://docs.astral.sh/uv/getting-started/installation/) |
| Client | Claude Code or Codex with MCP support |
| BambuStudio | Exact runtime version `02.08.04.57` |
| Download tools | `curl` and `chmod` for the README commands |

The documented runtime uses the Ubuntu 22.04 AppImage. This is the package used for local verification.
Windows, macOS, ARM, and other release assets do not have equivalent verification here.

## Get the official AppImage

1. Open the [official BambuStudio release](https://github.com/bambulab/BambuStudio/releases/tag/v02.08.04.57).
2. Confirm the tag `v02.08.04.57` and the title **2.8.4 Public Beta**.
3. Expand **Assets**.
4. Select `BambuStudio_ubu22-v02.08.04.57-20260922164607.AppImage`.
5. Save the file in this repository's `runtime/` directory.

Select the AppImage asset, not the source ZIP or source tarball.
The [README quick start](../README.md#-quick-start) gives the equivalent download command.
The [release assets](https://github.com/bambulab/BambuStudio/releases/expanded_assets/v02.08.04.57) also list a SHA-256 digest.

From the repository root, check the downloaded file:

```bash
export BAMBUSTUDIO_MCP_DIR="$(pwd -P)"
export BAMBUSTUDIO_APPIMAGE="BambuStudio_ubu22-v02.08.04.57-20260922164607.AppImage"
(
  cd "$BAMBUSTUDIO_MCP_DIR/runtime" &&
  printf '%s  %s\n' \
    'a98691304ef97cf6ca1b0a419c1031cb42df62d1f2ae784522f5addb35ecd533' \
    "$BAMBUSTUDIO_APPIMAGE" | sha256sum --check
)
```

The expected result is the asset filename followed by `OK`.
The digest applies only to that exact Ubuntu 22.04 asset.

## Use an existing download

Set the repository path first. Replace the example path with your repository path:

```bash
export BAMBUSTUDIO_MCP_DIR="/absolute/path/to/bambustudio-mcp"
export BAMBUSTUDIO_APPIMAGE="BambuStudio_ubu22-v02.08.04.57-20260922164607.AppImage"
mkdir -p "$BAMBUSTUDIO_MCP_DIR/runtime"
cp "$HOME/Downloads/$BAMBUSTUDIO_APPIMAGE" "$BAMBUSTUDIO_MCP_DIR/runtime/"
```

## Extract the runtime

If you already extracted this exact asset, continue to [paths and workspace](#paths-and-workspace).
For a new extraction, run:

```bash
chmod +x "$BAMBUSTUDIO_MCP_DIR/runtime/$BAMBUSTUDIO_APPIMAGE"
(
  cd "$BAMBUSTUDIO_MCP_DIR/runtime" &&
  "./$BAMBUSTUDIO_APPIMAGE" --appimage-extract > extraction.log
)
```

The extraction creates this layout:

```text
bambustudio-mcp/
├── README.md
├── src/
└── runtime/                       # Git ignores this directory
    ├── BambuStudio_ubu22-….AppImage
    ├── extraction.log
    └── squashfs-root/
        ├── AppRun                 # Use this launcher
        ├── bin/bambu-studio
        └── resources/
            ├── profiles/          # Bundled machine, process, and filament profiles
            └── model/
```

Use `AppRun`. It sets the path to the bundled libraries before it starts the binary.
Direct use of `bin/bambu-studio` can cause errors for missing libraries.
Extraction avoids the AppImage FUSE mount. It does not remove the package's other system requirements.

## Paths and workspace

Set these variables in the shell that you use for client registration:

```bash
export BAMBUSTUDIO_EXECUTABLE="$BAMBUSTUDIO_MCP_DIR/runtime/squashfs-root/AppRun"
export BAMBUSTUDIO_PROFILES="$BAMBUSTUDIO_MCP_DIR/runtime/squashfs-root/resources/profiles"
export BAMBUSTUDIO_WORKSPACE="$HOME/.local/share/bambustudio-mcp/workspace"
```

| Variable | Purpose |
| --- | --- |
| `BAMBUSTUDIO_MCP_DIR` | Repository path for the client launch command |
| `BAMBUSTUDIO_EXECUTABLE` | Extracted `AppRun` launcher |
| `BAMBUSTUDIO_PROFILES` | Bundled profile directory inside the extraction |
| `BAMBUSTUDIO_WORKSPACE` | Writable directory for project copies, revisions, jobs, logs, and results |

No separate resource or profile download is necessary.
The server can discover profiles beside a standard packaged executable. An explicit path makes the configuration easier to inspect.

The server creates the workspace automatically. It copies input projects into this directory and retains revision history.
The workspace is not the BambuStudio source checkout or the extracted runtime.
The default, without an explicit variable, is `~/.local/share/bambustudio-mcp`.
The example uses a `workspace/` child to make the location clear.

Keep the workspace outside the repository. This prevents accidental commits of project data and generated results.
The `/runtime/` entry in [.gitignore](../.gitignore) excludes the AppImage and its extracted files.

## Install dependencies and connect

From the repository root, run:

```bash
uv sync --frozen
```

Complete the [Claude Code or Codex registration](clients.md).
Ask the client to call `get_environment`, then `list_profiles`.

## Runtime problems

| Result | Action |
| --- | --- |
| `compatible: false` | Check the executable path and exact version `02.08.04.57`. Read the returned runtime error. |
| A missing library error | Use `squashfs-root/AppRun`. Check the selected AppImage's system requirements. |
| No profiles | Set the profile path to `squashfs-root/resources/profiles`. Call `list_profiles` again. |
| A FUSE error | Extract with `--appimage-extract`. Register the extracted `AppRun` path. |
| A permission error | Apply `chmod +x` to the AppImage before extraction. Check workspace write access. |

Keep this runtime pinned until the server supports another version. Do not replace it with the latest release automatically.
After a path change, update the saved [client registration](clients.md#update-an-existing-registration).
