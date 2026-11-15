"""A bounded, single-request storage helper for explicitly configured SSH targets."""
from __future__ import annotations

import argparse
import json
import selectors
import shlex
import subprocess
import sys
import time

from .preset_storage import MAX_JSON, directory, document, read_file


def remote_request(target, request):
    request = {**request, "target": target["remote_target"], "layout": target["layout"]}
    payload = json.dumps(request, allow_nan=False).encode()
    if len(payload) > MAX_JSON:
        raise ValueError("SSH preset request exceeds 1 MiB")
    command = shlex.join(["python3", "-m", "bambustudio_mcp.preset_agent", "--config", target["remote_config"]])
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no", target["host"], command]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, errors, offset = bytearray(), bytearray(), 0
    deadline = time.monotonic() + 30
    try:
        with selectors.DefaultSelector() as streams:
            streams.register(process.stdin, selectors.EVENT_WRITE, "input")
            streams.register(process.stdout, selectors.EVENT_READ, "output")
            streams.register(process.stderr, selectors.EVENT_READ, "error")
            while streams.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("SSH preset operation timed out; read the target before retrying a write")
                for key, _ in streams.select(min(remaining, 1)):
                    if key.data == "input":
                        offset += key.fileobj.write(payload[offset:offset+4096])
                        key.fileobj.flush()
                        if offset == len(payload):
                            streams.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = key.fileobj.read1(16384)
                        if not chunk:
                            streams.unregister(key.fileobj)
                            continue
                        buffer = output if key.data == "output" else errors
                        buffer.extend(chunk)
                        if len(buffer) > (2*MAX_JSON if key.data == "output" else 16384):
                            raise ValueError("SSH preset response exceeds the transport limit")
        code = process.wait(timeout=max(.01, deadline-time.monotonic()))
        try:
            response = document(output)
        except ValueError:
            response = {}
        if code and "error" not in response:
            # Do not echo private OpenSSH diagnostics/configuration into a model-facing result.
            raise ValueError("SSH preset operation failed; check the configured host/helper and read back before retrying writes")
        if "error" in response:
            raise ValueError("Remote preset operation: " + str(response["error"]))
        if not isinstance(response.get("result"), dict):
            raise ValueError("Invalid storage-agent response")
        return response["result"]
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("SSH preset operation timed out; read back before retrying a write") from error
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def dispatch(presets, request):
    if not isinstance(request, dict):
        raise ValueError("Storage request must be an object")
    request = dict(request)
    layout = request.pop("layout", None)
    target = request.get("target")
    spec = presets.targets.get(target, {})
    if spec.get("transport") != "local" or layout != spec.get("layout"):
        raise ValueError("Agent requires a local target with the declared storage layout")
    return presets.run(**request)


def main():
    from pathlib import Path
    from .presets import Presets

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        path = Path(args.config)
        with directory(path.parent) as fd:
            config = document(read_file(fd, path.name))
        raw = sys.stdin.buffer.read(MAX_JSON + 1)
        result = dispatch(Presets(config, local_only=True), document(raw))
        response = json.dumps({"result": result}, allow_nan=False, separators=(",", ":"))
        if len(response.encode()) > MAX_JSON:
            raise ValueError("Select fewer preset keys or a smaller page")
        print(response)
    except Exception as error:
        message = "Storage I/O failed; check configured paths and permissions" if isinstance(error, OSError) else str(error)[:1000]
        print(json.dumps({"error": message}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
