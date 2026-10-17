"""Extract the pinned source's FFF schema; use executable-exported defaults when present.

This deliberately records unresolved expressions. It never evaluates C++ as Python.
Run the compiled exporter against a matching Studio build for authoritative parity.
"""
import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path


def uncomment(text):
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0][0] in "\"'" else "\n" * m[0].count("\n"), text)


def strings(text):
    return [ast.literal_eval(m[0]) for m in re.finditer(r'"(?:\\.|[^"\\])*"', text)]


def numeric(text):
    text = text.strip().replace("true", "True").replace("false", "False")
    tree = ast.parse(text, mode="eval")
    if any(not isinstance(n, (ast.Expression, ast.Constant, ast.UnaryOp, ast.USub, ast.UAdd,
                             ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Load)) for n in ast.walk(tree)):
        raise ValueError(text)
    return eval(compile(tree, "<constant>", "eval"), {"__builtins__": {}}, {})


def generate(source, defaults, evidence=None):
    raw = (source / "src/libslic3r/PrintConfig.cpp").read_text()
    text = uncomment(raw)
    start = text.index("void PrintConfigDef::init_common_params()")
    end = text.index("void PrintConfigDef::init_extruder_option_keys()")
    body = text[start:end]
    for group in ("filament_extruder_override_keys", "filament_overhang_override_keys"):
        loop = body.index(f"for (auto& opt_key : {group})")
        opening = body.index("{", loop)
        depth = 0
        for token in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]', body[opening:]):
            if token[0] == "{":
                depth += 1
            elif token[0] == "}":
                depth -= 1
                if depth == 0:
                    body = body[:loop] + body[opening + token.end():]
                    break
    adds = list(re.finditer(r'this->add\("([^\"]+)",\s*(co\w+)\)', body))
    enums = {}
    for m in re.finditer(r'static\s+(?:const\s+)?t_config_enum_values s_keys_map_(\w+)\s*(?:=\s*)?\{([\s\S]*?)\n\};', text):
        enums[m[1]] = {key: re.sub(r'\bint\(|\)|\s', '', symbol) for key, symbol in
                      re.findall(r'\{\s*"([^\"]+)"\s*,\s*([^}\n]+)\}', m[2])}
    options = {}
    unresolved = []
    header = uncomment((source / "src/libslic3r/PrintConfig.hpp").read_text())
    scope_keys = {}
    for match in re.finditer(r'PRINT_CONFIG_CLASS_DEFINE\(\s*(\w+),([\s\S]*?)\n\)', header):
        scope_keys[match[1]] = set(re.findall(r'\(\(\w+,\s*(\w+)\)\)', match[2]))
    local_keys = scope_keys.get("PrintObjectConfig", set()) | scope_keys.get("PrintRegionConfig", set())
    region_keys = scope_keys.get("PrintRegionConfig", set())
    for index, match in enumerate(adds):
        block = body[match.end():adds[index + 1].start() if index + 1 < len(adds) else len(body)]
        key, kind = match[1], match[2][2:]
        option = {"type": kind, "nullable": False, "scopes": ["project"],
                  "source_line": raw[:raw.index('"' + key + '"', raw.index("void PrintConfigDef::init_common_params()"))].count("\n") + 1}
        if key in local_keys or key == "extruder":
            option["scopes"].append("object")
        if key in region_keys or key == "extruder":
            option["scopes"] += ["volume", "height_range"]
        if key.startswith("filament_"):
            option["scopes"].append("filament")
        # PlateConfig is a separate persisted schema; only its defined keys are writable there.
        if key in {"curr_bed_type", "print_sequence", "spiral_mode", "first_layer_print_sequence", "other_layers_print_sequence", "other_layers_print_sequence_nums"}:
            option["scopes"].append("plate")
        for field, dest in [("label", "label"), ("tooltip", "description"), ("category", "category"),
                            ("sidetext", "units"), ("ratio_over", "ratio_over")]:
            vals = re.findall(r'def->' + field + r'\s*=\s*([\s\S]*?);', block)
            if vals:
                option[dest] = "".join(strings(vals[-1]))
        for field in ["min", "max", "readonly", "nullable"]:
            vals = re.findall(r'def->' + field + r'\s*=\s*([^;]+);', block)
            if vals:
                try:
                    option[field] = numeric(vals[-1].replace("max_temp", "360"))
                except (ValueError, SyntaxError):
                    unresolved.append(f"{key}.{field}: {vals[-1]}")
        option["enum_values"] = [s for expr in re.findall(r'def->enum_values.(?:push_back|emplace_back)\(([^;]+)\);', block) for s in strings(expr)]
        enum_class = re.search(r'enum_keys_map\s*=\s*&ConfigOptionEnum<(\w+)>', block)
        if enum_class and not option["enum_values"]:
            option["enum_values"] = list(enums.get(enum_class[1], {}))
        if enum_class:
            option["serialized_enum_values"] = list(enums.get(enum_class[1], {}))
        direct_map = re.search(r'enum_keys_map\s*=\s*&s_keys_map_(\w+)', block)
        if direct_map and not option["enum_values"]:
            option["enum_values"] = list(enums.get(direct_map[1], {}))
        copied = re.search(r'def->enum_values\s*=\s*(\w+)->enum_values', block)
        if copied:
            declaration = re.search(r'auto\s+' + copied[1] + r'\s*=\s*def\s*=\s*this->add\("([^\"]+)"', body)
            if declaration:
                option["enum_values"] = options[declaration[1]]["enum_values"]
        if enum_class and option["enum_values"]:
            mapped = enums.get(enum_class[1], {})
            # A subset in the UI is a real restriction, except where Studio's own UI label
            # differs from its serialized value (100% cooling is serialized as 95%).
            option["serialized_enum_values"] = [v for v in option["enum_values"] if v in mapped] if all(v in mapped for v in option["enum_values"]) else list(mapped)
        alias = re.search(r'def->aliases\s*=\s*\{([^}]+)\}', block)
        option["aliases"] = strings(alias[1]) if alias else []
        default = re.search(r'set_default_value\(new ConfigOption\w+(?:<\w+>)?\s*([({][\s\S]*?)\);', block)
        if key in defaults:
            option["default"] = defaults[key]
            option["default_evidence"] = "executable_export"
        elif default:
            expr = default[1][1:-1].strip()
            try:
                if kind in {"Enum", "Enums"} and enum_class:
                    normalized = re.sub(r'\bint\(|\)|\s', '', expr).split("::")[-1]
                    if kind == "Enums":
                        option["default"] = [k for k, v in enums[enum_class[1]].items() if v.split("::")[-1] == normalized]
                        if not option["default"]:
                            raise ValueError(normalized)
                    else:
                        option["default"] = next(k for k, v in enums[enum_class[1]].items() if v.split("::")[-1] == normalized)
                elif kind in {"String", "Strings"}:
                    values = strings(expr)
                    option["default"] = values if kind == "Strings" else "".join(values)
                elif kind in {"Point", "Points", "PointsGroups", "Point3", "IntsGroups"}:
                    raise ValueError("coordinate default requires compiled exporter")
                else:
                    values = [numeric(v) for v in expr.split(",") if v.strip()]
                    vector = kind in {"Floats", "Ints", "Bools", "Percents", "FloatsOrPercents"}
                    if vector and default[1].startswith("(") and len(values) == 2:
                        values = [values[1]] * int(values[0])
                    option["default"] = values if vector else values[0]
                option["default_evidence"] = "source_constant"
            except (ValueError, SyntaxError, StopIteration, KeyError, IndexError):
                option["default_expression"] = expr
        if "Nullable" in block.split("set_default_value", 1)[-1]:
            option["nullable"] = True
        options[key] = option
        if key.startswith("printhost_") or key in {"print_host", "print_host_webui", "printer_host", "host_type", "preset_names", "preset_name"}:
            option["scopes"] = []
            option["excluded_reason"] = "printer communication is outside this server's scope"
    for group in ["filament_extruder_override_keys", "filament_overhang_override_keys"]:
        match = re.search(r'\b' + group + r'\s*=\s*\{([^}]+)\}', text)
        for key in strings(match[1]):
            original = options[key.removeprefix("filament_")]
            options[key] = dict(original, nullable=True, scopes=["project", "filament"], generated_from=key.removeprefix("filament_"))
            if key in defaults:
                options[key]["default"] = defaults[key]
                options[key]["default_evidence"] = "executable_export"
    version = re.search(r'set\(SLIC3R_VERSION "([^\"]+)"', (source / "version.inc").read_text())[1]
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    missing_defaults = [k for k, v in options.items() if "default" not in v]
    if missing_defaults == ["extruder"]:
        options["extruder"]["default_evidence"] = "Studio has no static default; inherit the object's assignment"
        missing_defaults = []
    evidence = evidence or {}
    preserved = evidence.get("baseline_roundtrip", {}).get("unchanged", [])
    normalized = evidence.get("baseline_roundtrip", {}).get("normalized", [])
    matrix = {key: {"discovered": True, "read_implemented": bool(value["scopes"]),
                    "write_implemented": bool(value["scopes"]) and not value.get("readonly", False),
                    "validation": "schema and selected dependencies; Studio checks during slicing",
                    "baseline_persistence": "preserved" if key in preserved else "normalized" if key in normalized else "unverified",
                    "all_values_verified": False} for key, value in options.items()}
    return {"studio_version": version, "source_revision": revision,
            "source_sha256": hashlib.sha256(raw.encode()).hexdigest(), "options": dict(sorted(options.items())),
            "coverage": {"discovered": len(options), "metadata_source": "source_extraction",
                         "unresolved_metadata": unresolved, "missing_defaults": missing_defaults,
                         "runtime_roundtrip_verified": preserved, "runtime_normalized": normalized,
                         "matrix": matrix, "complete_release": False}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--defaults", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    data = generate(args.source.resolve(), json.loads(args.defaults.read_text()) if args.defaults else {},
                    json.loads(args.evidence.read_text()) if args.evidence else {})
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps({k: v for k, v in data["coverage"].items() if k not in {"matrix", "runtime_roundtrip_verified"}}, indent=2))
