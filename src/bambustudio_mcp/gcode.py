"""Read commanded print behavior without executing printer instructions."""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
from collections import Counter


SETTINGS = "Metadata/project_settings.config"
SETTING_KEYS = {
    "nozzle_temperature", "nozzle_temperature_initial_layer", "layer_height",
    "initial_layer_print_height", "outer_wall_speed", "inner_wall_speed",
    "outer_wall_acceleration", "inner_wall_acceleration", "small_perimeter_speed",
    "small_perimeter_threshold", "wall_loops", "sparse_infill_density",
    "sparse_infill_pattern", "nozzle_diameter", "filament_type",
}
# shortcut: recognize Bambu feature labels only; expand when other slicers are supported.
OBJECT_FEATURES = {
    "Outer wall", "Inner wall", "Overhang wall", "Gap infill", "Sparse infill",
    "Internal solid infill", "Top surface", "Bottom surface", "Bridge",
    "Internal bridge", "Floating vertical shell", "Ironing",
}
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
WORDS = re.compile(r"([A-Z])\s*(" + NUMBER + r")")
COMMAND = re.compile(r"^(?:N\d+\s*)?([GMT]\d+(?:\.\d+)?)(?=\s|[A-Z]|$)")


def _number(value):
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _summary(values):
    known = {key: count for key, count in values.items() if key is not None}
    return {
        "min": min(known) if known else None,
        "max": max(known) if known else None,
        "most_common": [{"value": value, "moves": count} for value, count in values.most_common(8)],
        "distinct_values": len(values), "unknown_moves": values[None],
    }


def _inspect(stream, settings):
    feature, layer_z, layer_height = None, None, None
    xyz = dict.fromkeys("XYZ", 0.0)
    relative, relative_e, e = False, False, 0.0
    feed = acceleration = temperature = None
    tool, tools = 0, set()
    conditional_depth = 0
    temperature_uncertain = False
    first = None
    features, heights, layers = {}, Counter(), set()
    embedded = {}
    reasons = set()

    for line_number, raw in enumerate(stream, 1):
        if len(raw) > 65536:
            raise ValueError("G-code line exceeds the 64 KiB inspection limit")
        code, _, comment = raw.partition(";")
        comment = comment.strip()
        if not code.strip():
            if comment.startswith("FEATURE: "):
                feature = comment[9:].strip()[:80]
            elif comment.startswith("Z_HEIGHT: "):
                layer_z = _number(comment[10:])
            elif comment.startswith("LAYER_HEIGHT: "):
                layer_height = _number(comment[14:])
            elif " = " in comment:
                key, value = comment.split(" = ", 1)
                if key in SETTING_KEYS:
                    embedded[key] = value[:500]
        code = code.strip().upper()
        if not code:
            continue
        match = COMMAND.match(code)
        if not match:
            reasons.add("Unrecognized executable G-code syntax")
            temperature_uncertain = True
            continue
        command = match[1]
        words = {key: float(value) for key, value in WORDS.findall(code[match.end():])}
        if not all(math.isfinite(value) for value in words.values()):
            raise ValueError("Non-finite G-code parameter")
        if command in {"M622", "M623"}:
            reasons.add("Firmware conditional branches are not evaluated")
            conditional_depth = max(0, conditional_depth + (1 if command == "M622" else -1))
            temperature_uncertain = True
            continue
        if command in {"M620", "M621"}:
            reasons.add("Firmware filament-selection macros are not evaluated")
        if "{" in code or "[" in code:
            reasons.add("Unexpanded expressions are not evaluated")
            temperature_uncertain = True
            continue
        if command.startswith("T"):
            selected = int(command[1:])
            if selected != tool:
                temperature = None
                temperature_uncertain = True
            tool = selected
        elif command in {"G90", "G91"}:
            relative = relative_e = command == "G91"
        elif command in {"M82", "M83"}:
            relative_e = command == "M83"
        elif command == "G92":
            e = words.get("E", e)
            xyz.update({axis: words[axis] for axis in xyz if axis in words})
        elif command in {"M104", "M109"}:
            target = words.get("S", words.get("R"))
            if target is not None:
                temperature = target
                temperature_uncertain = conditional_depth > 0 or words.get("T", tool) != tool
        elif command == "M204":
            acceleration = words.get("S", words.get("P", acceleration))
        elif command in {"G20", "G93", "G95"}:
            reasons.add("Unsupported units or feed mode")
            temperature_uncertain = True
        elif command == "M220" and words.get("S", 100) != 100:
            reasons.add("Firmware feed scaling is not applied to commanded feed summaries")
        elif command in {"G0", "G1", "G2", "G3"}:
            if "F" in words:
                feed = words["F"] / 60
            position = {axis: (xyz[axis] + words[axis] if relative else words[axis])
                        if axis in words else xyz[axis] for axis in xyz}
            delta_e = words.get("E", 0) if relative_e else words.get("E", e) - e
            if "E" in words:
                e = e + words["E"] if relative_e else words["E"]
            moving = position["X"] != xyz["X"] or position["Y"] != xyz["Y"]
            moving |= command in {"G2", "G3"} and any(key in words for key in ("I", "J", "R"))
            xyz = position
            if delta_e <= 0 or not moving:
                continue
            label = feature or "Unlabeled"
            if label not in features and len(features) >= 32:
                label = "Other features"
            entry = features.setdefault(label, {"extrusion_moves": 0, "feeds": Counter(), "accelerations": Counter()})
            entry["extrusion_moves"] += 1
            entry["feeds"][round(feed, 5) if feed is not None else None] += 1
            entry["accelerations"][acceleration] += 1
            if feature not in OBJECT_FEATURES:
                continue
            tools.add(tool)
            if layer_z is not None:
                layers.add(round(layer_z, 6))
            if layer_height is not None:
                heights[round(layer_height, 6)] += 1
            if first is None:
                expected_key = "nozzle_temperature_initial_layer"
                expected = _number(settings.get(expected_key))
                if expected_key not in settings:
                    expected_key = "nozzle_temperature"
                    expected = _number(settings.get(expected_key))
                unknown = temperature_uncertain or conditional_depth > 0 or tool != 0 or expected is None or temperature is None
                first = {"line": line_number, "feature": feature, "z_mm": layer_z,
                         "target_temperature_c": temperature, "expected_temperature_c": expected,
                         "expected_setting": expected_key, "tool": tool,
                         "status": "unknown" if unknown else "mismatch" if abs(temperature - expected) > 0.5 else "matched"}
    if first is None:
        reasons.add("No labeled object extrusion was identified")
    if tools - {0} or len(tools) > 1:
        reasons.add("Multiple or nonzero object tools require a tool-to-filament mapping")
    if first is not None and first["status"] == "unknown":
        reasons.add("First object temperature could not be compared with a single known filament target")
    issues = [{"code": "unsupported_context", "severity": "unknown", "message": value} for value in sorted(reasons)]
    if first is not None and first["status"] == "mismatch":
        issues.append({"code": "first_object_temperature_mismatch", "severity": "warning",
                       "message": "The nozzle target at the first labeled object extrusion differs from the archived first-layer target."})
    return {"status": "unknown" if reasons else "warning" if issues else "observed",
            "issues": issues, "first_object_extrusion": first, "gcode_settings": embedded,
            "object_layers": {"count": len(layers), "z_range_mm": [min(layers), max(layers)] if layers else None,
                              "nominal_height_counts": [{"height_mm": value, "extrusion_moves": count}
                                                        for value, count in heights.most_common(16)]},
            "features": {key: {"extrusion_moves": value["extrusion_moves"],
                               "commanded_feed_mm_s": _summary(value["feeds"]),
                               "commanded_print_acceleration_mm_s2": _summary(value["accelerations"])}
                         for key, value in features.items()}}


def inspect_sliced_project(path):
    """Inspect each embedded plate G-code; never run it or change the archive."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len({item.filename for item in infos}) != len(infos):
            raise ValueError("Duplicate archive entries")
        if sum(item.file_size for item in infos) > 1024 ** 3 or any(item.file_size > 512 * 1024 ** 2 for item in infos):
            raise ValueError("Archive exceeds the 1 GiB total / 512 MiB entry inspection limit")
        settings = json.loads(archive.read(SETTINGS)) if SETTINGS in archive.namelist() else {}
        if not isinstance(settings, dict):
            raise ValueError("Archived project settings must be an object")
        settings = {key: value for key, value in settings.items() if key in SETTING_KEYS}
        plates = []
        for info in infos:
            if re.fullmatch(r"Metadata/plate_\d+\.gcode", info.filename):
                with archive.open(info) as binary, io.TextIOWrapper(binary, encoding="utf-8", errors="replace") as stream:
                    plates.append(dict(path=info.filename, **_inspect(stream, settings)))
    issues = []
    threshold = settings.get("small_perimeter_threshold")
    thresholds = threshold if isinstance(threshold, list) else [threshold]
    if threshold is not None and thresholds and all(_number(value) == 0 for value in thresholds):
        issues.append({"code": "small_perimeter_threshold_disabled", "severity": "warning",
                       "message": "The archived small-perimeter threshold is zero; its dedicated speed rule is disabled."})
    if not plates:
        issues.append({"code": "missing_gcode", "severity": "unknown", "message": "No embedded plate G-code was found."})
    status = "unknown" if not plates or any(p["status"] == "unknown" for p in plates) else "warning" if issues or any(p["issues"] for p in plates) else "observed"
    return {"status": status, "issues": issues, "archive_settings": settings,
            "settings_evidence": "Archived project values; embedded G-code comments are reported separately and are not an effective-settings export.",
            "plates": plates, "limitations": [
                "Temperatures are commanded nozzle targets, not measured temperatures or proof of heating completion.",
                "Feeds and M204 acceleration are commands, not achieved speed or firmware motion limits; counts are per extrusion move, not distance-weighted.",
                "Object layers use nominal slicer comments on recognized object features; support, brim and startup extrusion are excluded.",
                "G90/G91 set XYZ and E modes; M82/M83 override E until the next G90/G91. Unmodeled firmware behavior can differ.",
                "Firmware conditions and tool-to-filament mappings are not evaluated. Wall order and seam precision are not assessed.",
                "This inspection does not establish surface finish, thread fit, strength or a safe print.",
            ]}
