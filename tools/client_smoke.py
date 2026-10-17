"""Exercise a real client using only this MCP server; keep raw transcripts outside the repository."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client", choices=["claude", "codex"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bambu-client-") as temporary:
        root = Path(temporary)
        exported = root / "client-result.3mf"
        environment = {"BAMBUSTUDIO_EXECUTABLE": os.environ["BAMBUSTUDIO_EXECUTABLE"],
                       "BAMBUSTUDIO_WORKSPACE": str(root / "workspace")}
        if os.environ.get("BAMBUSTUDIO_PROFILES"):
            environment["BAMBUSTUDIO_PROFILES"] = os.environ["BAMBUSTUDIO_PROFILES"]
        server = {"command": sys.executable, "args": ["-m", "bambustudio_mcp.server"], "env": environment}
        config = root / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"bambustudio": server}}))
        prompt = ("Test the BambuStudio MCP server using its tools only. Call get_environment and verify compatibility. "
                  f"Open the saved project at {str(Path(args.project).resolve())!r} using open_project. "
                  "Apply wall_loops=3 to a new project revision. Start a slice for that revision and use get_job "
                  "until it succeeds. Export that sliced revision using the succeeded job_id to "
                  f"{str(exported)!r}. Do not control a printer or launch the GUI. "
                  "Report the actual tool results. Do not stop after listing tools.")
        if args.client == "claude":
            command = ["claude", "--strict-mcp-config", "--mcp-config", str(config), "--tools", "",
                       "--allowedTools", "mcp__bambustudio__*", "--no-session-persistence", "--setting-sources", "",
                       "--max-turns", "15", "--output-format", "stream-json", "--verbose", "-p", prompt]
        else:
            # JSON string literals are valid TOML basic strings; each override remains one argv element.
            command = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--json", "--sandbox", "workspace-write",
                       "-c", 'mcp_servers.bambustudio.command=' + json.dumps(sys.executable),
                       "-c", 'mcp_servers.bambustudio.args=' + json.dumps(server["args"])]
            for key, value in environment.items():
                command += ["-c", f'mcp_servers.bambustudio.env.{key}=' + json.dumps(value)]
            command += [prompt]
        transcript = root / "transcript.jsonl"
        evidence = {"client": args.client, "workflow_succeeded": False}
        try:
            with transcript.open("w") as output:
                completed = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, cwd=root, timeout=args.timeout)
            evidence["exit_code"] = completed.returncode
            if exported.is_file():
                from bambustudio_mcp.project import Project
                project = Project(exported)
                evidence["wall_loops"] = project.settings.get("wall_loops")
                evidence["has_sliced_gcode"] = any(name.endswith(".gcode") for name in project.entries)
                evidence["workflow_succeeded"] = evidence["wall_loops"] == "3" and evidence["has_sliced_gcode"]
            messages = transcript.read_text()
            evidence["observed_tools"] = [name for name in ["get_environment", "open_project", "apply_settings", "start_slice", "get_job", "export_project"] if name in messages]
            if not evidence["workflow_succeeded"]:
                # Never copy raw account, endpoint, or transcript details into durable evidence.
                evidence["limitation"] = "Client did not complete the workflow; inspect the temporary transcript during debugging."
        except subprocess.TimeoutExpired:
            evidence["limitation"] = "Client timed out before producing the configured, sliced project."
        from bambustudio_mcp.common import atomic_json
        atomic_json(Path(args.output), evidence)
        print(json.dumps(evidence, indent=2))
        if not evidence["workflow_succeeded"]:
            # Keep only the last diagnostic message in the terminal, not the durable report.
            print("Diagnostic tail:", transcript.read_text()[-1800:], file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
