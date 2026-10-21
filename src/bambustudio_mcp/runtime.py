from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .catalog import Catalog


class Runtime:
    def __init__(self, executable: str | None = None, profiles: str | None = None):
        supplied = executable or os.environ.get("BAMBUSTUDIO_EXECUTABLE", "")
        self.executable = Path(supplied).expanduser().resolve() if supplied else None
        root = profiles or os.environ.get("BAMBUSTUDIO_PROFILES", "")
        if not root and self.executable:
            for parent in [self.executable.parent, self.executable.parent.parent]:
                if (parent / "resources/profiles").is_dir():
                    root = str(parent / "resources/profiles")
                    break
        self.profiles = Path(root).expanduser().resolve() if root else None
        self.catalog = Catalog()

    def environment(self):
        report = {"executable": str(self.executable) if self.executable else None,
                  "supported_version": self.catalog.data["studio_version"], "compatible": False,
                  "profiles": str(self.profiles) if self.profiles else None,
                  "coverage": {k: v for k, v in self.catalog.data["coverage"].items()
                               if k not in {"matrix", "runtime_roundtrip_verified"}}, "transport": "stdio", "platform": "linux"}
        if not self.executable or not self.executable.is_file():
            return dict(report, error="Set BAMBUSTUDIO_EXECUTABLE to Studio or the packaged AppRun launcher")
        try:
            # Studio writes result.json even for --help. Keep the probe outside the caller's directory.
            with tempfile.TemporaryDirectory(prefix="bambu-runtime-") as temporary:
                result = subprocess.run([str(self.executable), "--help"], cwd=temporary,
                                        capture_output=True, text=True, timeout=20)
            match = re.search(r'BambuStudio-([\d.]+)', result.stdout + result.stderr)
            version = match[1] if match else None
            report.update(version=version, compatible=version == report["supported_version"] and result.returncode == 0)
            if not report["compatible"]:
                report["error"] = (result.stderr or result.stdout)[-2000:]
            binary = self.executable.parent / "bin/bambu-studio" if self.executable.name == "AppRun" else self.executable
            if binary.is_file():
                with binary.open("rb") as stream:
                    report["binary_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        except (OSError, subprocess.TimeoutExpired) as error:
            report["error"] = str(error)
        return report

    def require(self):
        report = self.environment()
        if not report["compatible"]:
            raise ValueError(f"Studio runtime is unavailable or not compatible: {report}")
        return report

    def profile_index(self, include_templates=False):
        if not self.profiles or not self.profiles.is_dir():
            return {}
        result = {}
        for path in sorted(self.profiles.glob("*/*/*.json")):
            try:
                data = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") in {"machine", "process", "filament"} and "name" in data:
                result.setdefault(data["name"], []).append(path)
            elif include_templates and "type" not in data:
                result.setdefault(data.get("name", path.stem), []).append(path)
        return result

    def list_profiles(self, query="", kind=None):
        result = []
        for name, paths in self.profile_index().items():
            if query.lower() not in name.lower():
                continue
            for path in paths:
                data = json.loads(path.read_text())
                if kind is None or data["type"] == kind:
                    result.append({"name": name, "type": data["type"], "path": str(path),
                                   "instantiation": data.get("instantiation"), "inherits": data.get("inherits")})
        return result

    def resolve_profile(self, name: str, expected_kind: str):
        index = self.profile_index(include_templates=True)
        explicit = Path(name).expanduser()
        paths = [explicit.resolve()] if explicit.is_file() else index.get(name, [])
        if len(paths) != 1:
            raise ValueError(f"Profile {name!r} missing or ambiguous; use an explicit JSON path")

        def reference(path, name, relation):
            if not isinstance(name, str) or not name:
                raise ValueError(f"Invalid {relation} profile reference: {name!r}")
            nearby = path.parent / (name + ".json")
            candidates = [nearby] if nearby.is_file() else index.get(name, [])
            if len(candidates) != 1:
                raise ValueError(f"Missing or ambiguous {relation} profile: {name}")
            return candidates[0]

        def resolve(path, visited, template=False):
            path = path.resolve()
            if path in visited:
                raise ValueError("Preset inheritance/include cycle")
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get("type", expected_kind if template else None) != expected_kind:
                raise ValueError(f"Expected a {expected_kind} profile: {path}")
            base = {}
            visited = visited | {path}
            parent = data.get("inherits")
            if parent:
                base = resolve(reference(path, parent, "inherited"), visited, template)
            includes = data.get("include", [])
            if isinstance(includes, str):
                includes = [includes]
            if not isinstance(includes, list):
                raise ValueError("Profile include must be a name or list of names")
            # Studio applies includes after inheritance, then explicit child settings.
            for name in includes:
                base.update(resolve(reference(path, name, "included"), visited, template=True))
            base.update(data)
            base.pop("inherits", None)
            base.pop("include", None)
            return base

        return resolve(paths[0], set())

    def run(self, arguments: list[str], directory: Path, timeout=1800):
        self.require()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "studio.log").open("w") as log:
            result = subprocess.run([str(self.executable), "--datadir", str(directory / "studio-data"),
                                     "--outputdir", str(directory), *arguments], cwd=directory,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise ValueError(f"Studio exited {result.returncode}: {(directory / 'studio.log').read_text()[-3000:]}")
        result_file = directory / "result.json"
        if result_file.is_file():
            data = json.loads(result_file.read_text())
            if data.get("return_code") != 0:
                raise ValueError(f"Studio rejected the operation: {data}")
        return result
