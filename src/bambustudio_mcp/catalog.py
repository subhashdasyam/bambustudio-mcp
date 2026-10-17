from __future__ import annotations

import json
import math
import re
from importlib.resources import files


class Catalog:
    def __init__(self):
        self.data = json.loads(files("bambustudio_mcp.data").joinpath("catalog.json").read_text())
        self.options = self.data["options"]

    def list(self, query: str = "", scope: str | None = None, offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("offset must be nonnegative; limit must be 1..200")
        matches = [dict(key=k, **v) for k, v in self.options.items()
                   if query.lower() in (k + " " + v.get("label", "") + " " + v.get("description", "")).lower()
                   and (scope is None or scope in v["scopes"])]
        return {"total": len(matches), "options": matches[offset:offset + limit],
                "catalog_version": self.data["studio_version"], "coverage": {k: v for k, v in self.data["coverage"].items()
                    if k not in {"matrix", "runtime_roundtrip_verified"}}}

    def validate(self, key: str, value, scope: str = "project"):
        if key not in self.options:
            raise ValueError(f"Unknown configuration option: {key}")
        option = self.options[key]
        if scope not in option["scopes"] or option.get("readonly"):
            raise ValueError(f"{key} cannot be changed at {scope} scope")
        kind = option["type"]
        vector = kind in {"Floats", "Ints", "Bools", "Strings", "Percents", "FloatsOrPercents", "Enums", "Points", "PointsGroups", "IntsGroups"}
        if vector and not isinstance(value, list):
            raise ValueError(f"{key} requires an array, including for a single nozzle or filament")
        if kind in {"Points", "Point", "Point3", "PointsGroups", "IntsGroups"}:
            self._points(key, value, kind)
            return value
        values = value if vector else [value]
        for item in values:
            if item is None and option.get("nullable"):
                continue
            scalar = {"Floats": "Float", "Ints": "Int", "Bools": "Bool", "Strings": "String",
                      "Percents": "Percent", "FloatsOrPercents": "FloatOrPercent", "Enums": "Enum"}.get(kind, kind)
            if scalar == "Bool":
                if not isinstance(item, bool):
                    raise ValueError(f"{key} requires boolean values")
            elif scalar == "String":
                if not isinstance(item, str):
                    raise ValueError(f"{key} requires strings")
            elif scalar == "Enum":
                if item not in option.get("serialized_enum_values", option.get("enum_values", [])):
                    raise ValueError(f"{key}: choose one of {option.get('serialized_enum_values', option.get('enum_values', []))}")
            elif scalar in {"Float", "Int", "Percent", "FloatOrPercent"}:
                percent = isinstance(item, str) and item.endswith("%") and scalar in {"Percent", "FloatOrPercent"}
                number = float(item[:-1]) if percent else item
                if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                    raise ValueError(f"{key} requires finite numeric values")
                if scalar == "Int" and not isinstance(number, int):
                    raise ValueError(f"{key} requires integers")
                if option.get("min") is not None and number < option["min"]:
                    raise ValueError(f"{key} is below {option['min']}")
                if option.get("max") is not None and number > option["max"]:
                    raise ValueError(f"{key} exceeds {option['max']}")
            else:
                raise ValueError(f"Unresolved configuration type {kind}; edits are blocked")
        return value

    @staticmethod
    def _points(key, value, kind):
        from .common import finite_number

        if not isinstance(value, list):
            raise ValueError(f"{key} requires coordinate arrays")
        groups = value if kind in {"PointsGroups", "IntsGroups"} else [value]
        for group in groups:
            points = group if kind in {"Points", "PointsGroups"} else [group]
            for point in points:
                if not isinstance(point, list) or (kind != "IntsGroups" and len(point) != (3 if kind == "Point3" else 2)):
                    raise ValueError(f"{key}: incorrect coordinate dimensions")
                for coordinate in point:
                    finite_number(coordinate)
                    if kind == "IntsGroups" and not isinstance(coordinate, int):
                        raise ValueError(f"{key} requires integer groups")

    def decode(self, key, value):
        if value is None:
            return None
        if key not in self.options:
            return value
        kind = self.options[key]["type"]
        if kind in {"Strings", "Floats", "Ints", "Bools", "Percents", "FloatsOrPercents", "Enums"}:
            values = value if isinstance(value, list) else str(value).split(";" if kind == "Strings" else ",")
            return [self._decode_scalar(kind, item) for item in values]
        if kind in {"Point", "Point3", "Points", "PointsGroups", "IntsGroups"}:
            if isinstance(value, list) and (not value or isinstance(value[0], (list, int, float))):
                return value
            if kind in {"Point", "Point3"}:
                return self._decode_point(value)
            if kind in {"PointsGroups", "IntsGroups"}:
                groups = value if isinstance(value, list) else str(value).split("#")
                if kind == "IntsGroups":
                    return [[int(p) for p in g.split(",") if p] for g in groups]
                return [[self._decode_point(p) for p in g.split(",") if p] for g in groups]
            points = [self._decode_point(p) for p in (value if isinstance(value, list) else str(value).split(","))]
            return points if kind == "Points" else points[0]
        return self._decode_scalar(kind, value)

    @staticmethod
    def _decode_point(value):
        return [float(n) for n in re.split("[x,]", str(value))]

    @staticmethod
    def _decode_scalar(kind, value):
        if value is None or value == "nil":
            return None
        if kind in {"Bool", "Bools"}:
            return str(value).lower() in {"1", "true"}
        if kind in {"Int", "Ints"}:
            return int(value)
        if kind in {"Float", "Floats", "Percent", "Percents", "FloatOrPercent", "FloatsOrPercents"}:
            if str(value).endswith("%"):
                return float(str(value)[:-1]) if kind in {"Percent", "Percents"} else value
            return float(value)
        return value

    def encode(self, key, value, json_format=True):
        kind = self.options[key]["type"]
        def scalar(item):
            if item is None:
                return "nil"
            if isinstance(item, bool):
                return "1" if item else "0"
            return str(item)
        if kind in {"Point", "Point3"}:
            return ",".join(map(scalar, value))
        if kind == "Points":
            points = ["x".join(map(scalar, p)) for p in value]
            return points if json_format else ",".join(points)
        if kind in {"PointsGroups", "IntsGroups"}:
            groups = [",".join("x".join(map(scalar, p)) if isinstance(p, list) else scalar(p) for p in g) for g in value]
            return groups if json_format else "#".join(groups)
        if isinstance(value, list):
            if json_format:
                return list(map(scalar, value))
            if kind == "Strings":
                return ";".join(json.dumps(s, ensure_ascii=False) for s in value)
            return ",".join(map(scalar, value))
        return scalar(value)

    @staticmethod
    def validate_combination(settings):
        if settings.get("spiral_mode"):
            expected = {"wall_loops": 1, "sparse_infill_density": 0, "top_shell_layers": 0, "enable_support": False}
            bad = [key for key, value in expected.items() if settings.get(key) != value]
            if bad:
                raise ValueError("Spiral mode conflicts with: " + ", ".join(bad))
        for key in ("layer_height", "initial_layer_print_height"):
            if key in settings and settings[key] <= 0:
                raise ValueError(f"{key} must be greater than zero")
