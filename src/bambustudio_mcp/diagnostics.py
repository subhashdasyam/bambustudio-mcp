"""Version-pinned native messages; geometry remains separate heuristic evidence."""
from __future__ import annotations

import json
import re
from importlib.resources import files


def warning_guide():
    return json.loads(files("bambustudio_mcp.data").joinpath("warning-guide.json").read_text())


def classify(message):
    lines = [" ".join(line.casefold().split()) for line in message.splitlines()]
    normalized = "\n".join(lines)
    return [key for key, rule in warning_guide()["rules"].items()
            if any(phrase in lines for phrase in rule["phrases"]) or re.search(rule["pattern"], normalized, re.M)]


def collect_native(directory):
    """Read complete job artifacts, not the last 4 KB shown by get_job."""
    records = {}

    def add(message, source, plate=None):
        if not isinstance(message, str) or not message.strip():
            return
        message = message.strip()
        key = (plate, " ".join(message.split()))
        if key in records:
            if source not in records[key]["sources"]:
                records[key]["sources"].append(source)
            return
        records[key] = {"message": message, "plate_index": plate,
                        "rule_ids": classify(message), "sources": [source]}

    result_path = directory / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text())
        if not isinstance(result, dict) or not isinstance(result.get("sliced_plates", []), list):
            raise ValueError("Native result must contain an object and a sliced_plates array")
        for plate in result.get("sliced_plates", []):
            if not isinstance(plate, dict):
                raise ValueError("Native sliced_plates entries must be objects")
            add(plate.get("warning_message"), "result.json", plate.get("id"))
        if result.get("return_code"):
            add(result.get("error_string"), "result.json", result.get("plate_index") or None)
    log_path = directory / "studio.log"
    pending, plate = None, None
    if log_path.is_file():
        with log_path.open(errors="replace") as stream:
            for line in stream:
                # Studio's native messages may span several unprefixed lines.
                if pending is not None and not re.match(r"^\[\d{4}-\d\d-\d\d", line):
                    pending += line
                    continue
                if pending is not None:
                    add(pending, "studio.log", plate)
                    pending = None
                match = re.search(r"plate (\d+): found (?:NON_CRITICAL )?slicing warnings: (.*)", line)
                if match:
                    plate = int(match[1])
                    pending = re.sub(r", no_check=[^,\s]+\s*$", "", match[2]) + "\n"
                else:
                    match = re.search(r"got (?:warnings|error when validate): (.*)", line)
                    if match:
                        pending, plate = match[1] + "\n", None
            if pending is not None:
                add(pending, "studio.log", plate)
    return {"status": "observed" if records else "not_observed", "messages": list(records.values()),
            "result_present": result_path.is_file(), "log_present": log_path.is_file()}


def native_summary(report, path):
    messages = [{**item, "message": item["message"][:800], "message_truncated": len(item["message"]) > 800}
                for item in report.get("messages", [])[:5]]
    guide = warning_guide()
    keys = {key for item in messages for key in item["rule_ids"]}
    return {**{k: v for k, v in report.items() if k != "messages"}, "messages": messages,
            "message_count": len(report.get("messages", [])), "report_path": str(path),
            "rules": {key: value for key, value in guide["rules"].items() if key in keys},
            "coverage": "Absence from CLI artifacts does not prove absence in the GUI or physical print."}
