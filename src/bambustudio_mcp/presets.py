"""Reusable process/material presets, independent of project-local settings."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .catalog import Catalog
from .preset_storage import (backup_directory, digest, directory, document, encode, preset_name,
                             read_file, save_backup, write_file, write_lock)

IDENTITY = {"name", "type", "from", "inherits", "include", "instantiation", "version", "setting_id",
            "filament_id", "print_settings_id", "filament_settings_id", "id",
            "compatible_printers_condition", "compatible_prints_condition"}
COMPATIBILITY = {"compatible_printers", "compatible_prints"}
ACTIONS = {"list", "read", "derive", "validate", "create", "update", "backups", "restore"}
WRITES = {"create", "update", "restore"}
MAX_INDEX_BYTES = 64 * 1024 * 1024


def editable_setting(key, effective, catalog):
    if (key in IDENTITY | COMPATIBILITY or key not in effective or key not in catalog.options or "gcode" in key
            or key.startswith(("printhost", "machine_", "printer_", "extruder_", "printable_")) or key == "nozzle_diameter"):
        raise ValueError(f"Setting is not an editable process/material option: {key}")


def configured_targets(config=None):
    if config is None:
        path = os.environ.get("BAMBUSTUDIO_PRESET_TARGETS")
        if not path:
            return {}
        source = Path(path).expanduser()
        with directory(source.parent) as fd:
            config = document(read_file(fd, source.name))
    if not isinstance(config, dict) or set(config) != {"targets"} or not isinstance(config["targets"], dict):
        raise ValueError("Preset configuration requires a targets object")
    if len(config["targets"]) > 64:
        raise ValueError("At most 64 preset targets are supported")
    result = {}
    for name, original in config["targets"].items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or not isinstance(original, dict):
            raise ValueError("Invalid configured target")
        target = dict(original)
        transport = target.setdefault("transport", "local")
        target.setdefault("layout", "desktop")
        target.setdefault("writable", False)
        if target["layout"] not in {"desktop", "api"} or not isinstance(target["writable"], bool):
            raise ValueError("Target layout/writable declaration is invalid")
        if transport == "local":
            if set(target) - {"transport", "layout", "writable", "root", "system_roots"}:
                raise ValueError("Unknown local preset target field")
            roots = target.setdefault("system_roots", [])
            if not isinstance(roots, list) or len(roots) > 16 or "root" not in target:
                raise ValueError("Declare a root and a list of vendor system_roots")
            paths = [target["root"], *roots]
        elif transport == "ssh":
            if set(target) - {"transport", "layout", "writable", "host", "remote_target", "remote_config"}:
                raise ValueError("Unknown SSH preset target field; use private OpenSSH configuration for credentials")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target.get("host", "")):
                raise ValueError("SSH host must be a configured OpenSSH alias")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", target.get("remote_target", "")):
                raise ValueError("Invalid remote target ID")
            paths = [target.get("remote_config", "")]
        else:
            raise ValueError("Preset transport must be local or ssh")
        for path in paths:
            if not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts or any(ord(c) < 32 for c in path):
                raise ValueError("Configured paths must be absolute without traversal/control characters")
        result[name] = target
    return result


class Presets:
    def __init__(self, config=None, local_only=False):
        self.config = config
        self.local_only = local_only
        self.catalog = Catalog()

    @property
    def targets(self):
        # A bad optional preset configuration must not prevent the core slicer server from starting.
        return configured_targets(self.config)

    def list_targets(self):
        return {"targets": [{"id": name, **{key: target[key] for key in ("transport", "layout", "writable")}}
                            for name, target in sorted(self.targets.items())],
                "configuration_env": "BAMBUSTUDIO_PRESET_TARGETS"}

    def run(self, target, action, **arguments):
        targets = self.targets
        if action not in ACTIONS or target not in targets:
            raise ValueError("Unknown preset action or configured target")
        spec = targets[target]
        dry_run = arguments.get("dry_run", True)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be boolean")
        if action in WRITES and not dry_run and not spec["writable"]:
            raise ValueError("Preset target is read-only")
        if spec["transport"] == "ssh":
            if self.local_only:
                raise ValueError("The storage agent cannot forward to another SSH target")
            from .preset_agent import remote_request
            return remote_request(spec, {"action": action, **arguments})
        if arguments.get("kind") not in {"process", "filament"}:
            raise ValueError("Preset kind must be process or filament")
        method = {"list": self._list, "read": self._read, "derive": self._derive, "validate": self._validate,
                  "create": self._create, "update": self._update, "backups": self._backups, "restore": self._restore}[action]
        return method(spec, **arguments)

    @staticmethod
    def _folder(target, kind):
        category = kind if target["layout"] == "desktop" else {"process": "presets", "filament": "filaments"}[kind]
        return Path(target["root"]) / category

    def _index(self, target, kind):
        result = {}
        total_bytes = 0
        roots = [(self._folder(target, kind), True)] + [(Path(root) / kind, False) for root in target["system_roots"]]
        for root, user in roots:
            with directory(root) as fd:
                names = sorted(name for name in os.listdir(fd) if name.endswith(".json") and not name.startswith("."))
                if len(names) > 10000:
                    raise ValueError("Preset directory exceeds 10000 JSON files")
                for filename in names:
                    raw = read_file(fd, filename)
                    total_bytes += len(raw)
                    if total_bytes > MAX_INDEX_BYTES:
                        raise ValueError("Preset index exceeds the 64 MiB input budget")
                    data = document(raw)
                    name = data.get("name", Path(filename).stem)
                    if not isinstance(name, str) or not name or data.get("type", kind) != kind:
                        raise ValueError("Preset name/type does not match its category")
                    if name in result:
                        raise ValueError(f"Ambiguous internal preset name: {name}")
                    result[name] = {"data": data, "raw": raw, "revision": digest(raw), "user": user, "filename": filename}
                    if len(result)>10000:
                        raise ValueError("Preset target exceeds 10000 indexed profiles")
        return result

    def _resolve(self, index, name, kind, active=(), budget=None):
        budget = [0] if budget is None else budget
        budget[0] += 1
        if budget[0] > 2048:
            raise ValueError("Preset ancestry exceeds the resolution budget")
        if not isinstance(name, str) or name not in index:
            raise ValueError(f"Missing preset parent/include: {name}")
        if name in active or len(active) >= 64:
            raise ValueError("Preset inheritance/include cycle or excessive depth")
        entry = index[name]
        data = entry["data"]
        includes = data.get("include", [])
        if isinstance(includes, str):
            includes = [includes]
        if not isinstance(includes, list) or len(includes)>64 or not all(isinstance(item, str) for item in includes):
            raise ValueError("Preset include must be a name or list of names")
        parent = data.get("inherits", "")
        if not isinstance(parent, str):
            raise ValueError("Preset inherits must be a name")
        effective, lineage = {}, []
        for reference in ([parent] if parent else []) + includes:
            values, ancestors = self._resolve(index, reference, kind, (*active, name), budget)
            effective.update(values)
            lineage.extend(ancestors)
        effective.update(data)
        effective.pop("inherits", None)
        effective.pop("include", None)
        lineage.append({"name": name, "revision": entry["revision"]})
        return effective, lineage

    def _list(self, target, kind, query="", offset=0, limit=50):
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative and limit must be 1..100")
        index = self._index(target, kind)
        matches = [{"name": name, "kind": kind, "origin": "user" if item["user"] else "vendor",
                    "inherits": item["data"].get("inherits"), "revision": item["revision"]}
                   for name, item in sorted(index.items()) if query.lower() in name.lower()]
        return {"presets": matches[offset:offset+limit], "total": len(matches), "offset": offset}

    def _read(self, target, kind, name, keys=None):
        index = self._index(target, kind)
        effective, lineage = self._resolve(index, name, kind)
        if keys is not None and (not isinstance(keys, list) or len(keys) > 50 or not all(isinstance(key, str) for key in keys)):
            raise ValueError("Select at most 50 setting keys")
        settings = {key: value for key, value in effective.items() if key not in IDENTITY | COMPATIBILITY}
        return {"name": name, "kind": kind, "origin": "user" if index[name]["user"] else "vendor",
                "revision": index[name]["revision"], "resolution_revision": digest(json.dumps(lineage, sort_keys=True).encode()),
                "inherits": index[name]["data"].get("inherits", ""), "compatible_printers": effective.get("compatible_printers", []),
                "compatible_prints": effective.get("compatible_prints", []),
                "compatibility_conditions_present": any(bool(effective.get(key)) for key in ("compatible_printers_condition", "compatible_prints_condition")),
                "setting_count": len(settings), "lineage": list({item["name"]: item for item in lineage}.values()),
                "settings": {key: settings[key] for key in (keys or []) if key in settings},
                "value_format": "Bambu JSON strings/vectors; request keys explicitly to keep client results compact"}

    def _changes(self, kind, effective, changes):
        if not isinstance(changes, dict) or len(changes) > 50:
            raise ValueError("changes must contain at most 50 typed settings")
        result, diff = dict(effective), []
        for key, value in changes.items():
            if key in COMPATIBILITY:
                previous = effective.get(key, [])
                if not isinstance(value, list) or not value or not all(isinstance(item, str) and item in previous for item in value):
                    raise ValueError("Compatibility edits may only narrow an existing nonempty list")
                encoded = value
            else:
                editable_setting(key, effective, self.catalog)
                self.catalog.validate(key, value)
                encoded = self.catalog.encode(key, value)
            diff.append({"key": key, "before": effective.get(key), "after": encoded})
            result[key] = encoded
        decoded = {key: self.catalog.decode(key, value) for key, value in result.items() if key in self.catalog.options}
        self.catalog.validate_combination(decoded)
        return result, diff

    def _candidate(self, target, kind, base, name, changes, expected_base_revision=None):
        preset_name(name)
        index = self._index(target, kind)
        effective, lineage = self._resolve(index, base, kind)
        printers = effective.get("compatible_printers")
        if (index[base]["data"].get("name") != base or effective.get("type") != kind
                or str(index[base]["data"].get("instantiation")).lower() != "true"
                or not isinstance(printers, list) or not printers or not all(isinstance(item, str) and item for item in printers)):
            raise ValueError("Derive from a concrete preset with explicit printer/nozzle compatibility")
        base_revision = digest(json.dumps(lineage, sort_keys=True).encode())
        if expected_base_revision is not None and expected_base_revision != base_revision:
            raise ValueError("Stale preset ancestry; preview the derivation again")
        values, diff = self._changes(kind, effective, changes)
        if target["layout"] == "desktop":
            candidate = {"inherits": base, **{key: values[key] for key in changes}}
        else:
            candidate = dict(values, inherits="")
        candidate.update(type=kind, name=name, **{"from": "User", "instantiation": "true"})
        candidate.pop("setting_id", None)
        candidate.pop("id", None)
        if "version" in effective:
            candidate["version"] = effective["version"]
        identity = "print_settings_id" if kind == "process" else "filament_settings_id"
        candidate[identity] = name if kind == "process" else [name]
        if kind == "filament" and "filament_id" in effective:
            candidate["filament_id"] = effective["filament_id"]
        return candidate, {"name": name, "kind": kind, "base": base, "base_revision": base_revision,
                           "compatible_printers": values["compatible_printers"], "changes": diff,
                           "validation": "catalog-checked overrides and resolved ancestry; GUI/physical behavior unverified"}

    def _derive(self, target, kind, base, name, changes):
        _, report = self._candidate(target, kind, base, name, changes)
        return dict(report, dry_run=True)

    def _validate(self, target, kind, name):
        index = self._index(target, kind)
        effective, lineage = self._resolve(index, name, kind)
        if effective.get("type") != kind:
            raise ValueError("Select a concrete process or filament preset")
        self.catalog.validate_combination({key: self.catalog.decode(key, value) for key, value in effective.items() if key in self.catalog.options})
        problems = []
        for key, value in index[name]["data"].items():
            if key not in self.catalog.options or key in IDENTITY | COMPATIBILITY:
                continue
            try:
                self.catalog.validate(key, self.catalog.decode(key, value))
            except (ValueError, TypeError) as error:
                problems.append({"key": key, "message": str(error)})
        return {"name": name, "kind": kind, "revision": index[name]["revision"], "resolved_ancestors": len(lineage),
                "catalog_issues": problems, "catalog_compatible": not problems,
                "compatible_printers": effective.get("compatible_printers", []),
                "unknown_setting_count": len(set(effective)-set(self.catalog.options)-IDENTITY-COMPATIBILITY),
                "validation": "structure and ancestry checked; inherited vendor values are preserved, not certified"}

    def _create(self, target, kind, base, name, changes, dry_run=True, expected_base_revision=None):
        candidate, report = self._candidate(target, kind, base, name, changes, expected_base_revision)
        if name in self._index(target, kind):
            raise ValueError("Preset name already exists")
        raw = encode(candidate)
        if not dry_run:
            with directory(self._folder(target, kind)) as fd, write_lock(fd):
                # Another cooperating creator may have used a different filename for the same internal name.
                if name in self._index(target, kind):
                    raise ValueError("Preset name already exists")
                candidate, report = self._candidate(target, kind, base, name, changes, expected_base_revision)
                raw = encode(candidate)
                write_file(fd, name + ".json", raw)
        return dict(report, dry_run=dry_run, revision=digest(raw), written=not dry_run)

    def _user(self, target, kind, name):
        index = self._index(target, kind)
        if name not in index or not index[name]["user"] or index[name]["data"].get("from") != "User":
            raise ValueError("Only existing user presets can be updated or restored")
        return index, index[name]

    def _replace(self, target, kind, name, entry, raw, expected_revision, dry_run):
        if not re.fullmatch(r"[0-9a-f]{64}", expected_revision or "") or entry["revision"] != expected_revision:
            raise ValueError("Stale preset revision; read it again before updating")
        backup = digest(entry["raw"])
        if not dry_run:
            with directory(self._folder(target, kind)) as fd, write_lock(fd):
                old = read_file(fd, entry["filename"])
                if digest(old) != expected_revision:
                    raise ValueError("Stale preset revision; another writer changed this preset")
                save_backup(fd, name, old)
                write_file(fd, entry["filename"], raw, replace=True)
        return {"name": name, "kind": kind, "dry_run": dry_run, "written": not dry_run,
                "previous_revision": expected_revision, "revision": digest(raw), "backup_id": backup if not dry_run else None}

    def _update(self, target, kind, name, changes, expected_revision, dry_run=True):
        index, entry = self._user(target, kind, name)
        effective, _ = self._resolve(index, name, kind)
        values, diff = self._changes(kind, effective, changes)
        candidate = {**entry["data"], **{key: values[key] for key in changes}}
        raw = encode(candidate)
        return dict(self._replace(target, kind, name, entry, raw, expected_revision, dry_run), changes=diff)

    def _backups(self, target, kind, name, offset=0, limit=50):
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("offset/limit outside supported range")
        self._user(target, kind, name)
        with directory(self._folder(target, kind)) as fd:
            try:
                with backup_directory(fd, name) as backups:
                    names = sorted(file for file in os.listdir(backups) if re.fullmatch(r"[a-f0-9]{64}\.json", file))
                    selected = names[offset:offset+limit]
                    for file in selected:
                        if digest(read_file(backups, file)) != file[:-5]:
                            raise ValueError("Backup checksum mismatch")
            except FileNotFoundError:
                names, selected = [], []
        return {"name": name, "total": len(names), "backups": [{"id": file[:-5]} for file in selected]}

    def _restore(self, target, kind, name, backup_id, expected_revision, dry_run=True):
        index, entry = self._user(target, kind, name)
        if not re.fullmatch(r"[a-f0-9]{64}", backup_id):
            raise ValueError("Invalid backup ID")
        with directory(self._folder(target, kind)) as fd, backup_directory(fd, name) as backups:
            raw = read_file(backups, backup_id + ".json")
        data = document(raw)
        if digest(raw) != backup_id or data.get("name") != name or data.get("type") != kind or data.get("from") != "User":
            raise ValueError("Backup checksum or identity mismatch")
        candidate_index = dict(index)
        candidate_index[name] = dict(entry, data=data)
        self._resolve(candidate_index, name, kind)
        return self._replace(target, kind, name, entry, raw, expected_revision, dry_run)
