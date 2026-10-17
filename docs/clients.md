# Connect Claude Code and Codex

[README](../README.md) · [Installation](installation.md)

Complete the [runtime setup](installation.md) first.
Use the same shell with all four path variables from the README.

Check the variables before registration:

```bash
printf '%s\n' "$BAMBUSTUDIO_MCP_DIR" "$BAMBUSTUDIO_EXECUTABLE" \
  "$BAMBUSTUDIO_PROFILES" "$BAMBUSTUDIO_WORKSPACE"
command -v uv
```

Each path must be absolute and nonempty. The `uv` command must resolve to an executable.
The registration commands store the current values. Later shell changes do not update saved registrations.

## Claude Code

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

Place the name `bambustudio` before `--env`. Claude accepts multiple values after an `--env` option.
The `--scope user` option makes the server available across your projects.
See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Codex

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

Codex stores the server in `~/.codex/config.toml`.
See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Check the connection

1. Start a new client session.
2. Use `/mcp` to inspect the server.
3. Ask the assistant to call `get_environment`.
4. Confirm version `02.08.04.57` and `compatible: true`.
5. Ask the assistant to call `list_profiles`.

The profile query must return profiles from the extracted runtime.
Both clients start the server automatically. You do not need to start it in a separate terminal.
MCP uses stdout for protocol messages. The server sends diagnostics to stderr.

## Update an existing registration

First, inspect the saved entry with your client's `mcp get bambustudio` command.
For an entry from the user-scope commands above, remove it with the applicable command:

```bash
claude mcp remove bambustudio --scope user
```

```bash
codex mcp remove bambustudio
```

Set the correct path variables. Repeat the applicable add command above.
Start a new client session after the change.
Removal affects only the client entry. It does not remove the runtime, projects, or workspace.

## Common connection errors

| Error or symptom | Cause and correction |
| --- | --- |
| `Invalid environment variable format: bambustudio` | Claude consumed the server name as an environment value. Put the name before `--env`. |
| The saved paths are empty | Set the variables before the add command. Remove and add the entry again. |
| The client cannot find `uv` | Use `command -v uv` to check the absolute launch path. Repeat registration after a location change. |
| The repository moved | Set `BAMBUSTUDIO_MCP_DIR` to its new path. Repeat registration. |
| The runtime is incompatible | Use the exact supported AppImage. Read [runtime problems](installation.md#runtime-problems). |
| A model import needs profiles | Call `list_profiles`. Select explicit machine, process, and filament profiles. |

The server works with saved projects. Save GUI changes before you open that project through MCP.
