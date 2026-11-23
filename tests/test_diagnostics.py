import json
import time
from pathlib import Path

import pytest

from bambustudio_mcp.catalog import Catalog
from bambustudio_mcp.common import atomic_json
from bambustudio_mcp.diagnostics import classify, collect_native, native_summary, warning_guide
from bambustudio_mcp.server import INSTRUCTIONS, compact_result


FLOATING = "It seems object test part has floating regions. Please re-orient the object or enable support generation."
ENFORCERS = "Support enforcers are used but support is not enabled. Please enable support."


def test_rules_are_pinned_and_use_real_catalog_settings():
    catalog, guide = Catalog(), warning_guide()
    assert guide["studio_version"] == catalog.data["studio_version"]
    assert guide["source_revision"] == catalog.data["source_revision"]
    for rule in guide["rules"].values():
        assert rule["categories"] and rule["next_steps"]
        assert all(guide["source_revision"] in url for url in rule["sources"])
        assert all(key in catalog.options and catalog.options[key]["scopes"] for key in rule["settings"])
    assert classify(FLOATING) == ["floating_regions"]
    assert classify(ENFORCERS) == ["support_enforcers_disabled"]
    assert classify("Unexpected error 123") == []
    assert classify('Model "floating regions" has an unrelated problem.') == []
    assert classify("No floating regions were detected.") == []
    assert classify("Das Objekt hat schwebende Bereiche") == []
    assert "diagnose_warning first" in INSTRUCTIONS


def test_native_collection_retains_multiple_plates_and_messages_before_log_tail(tmp_path):
    atomic_json(tmp_path / "result.json", {"return_code": 0, "sliced_plates": [
        {"id": 1, "warning_message": FLOATING}, {"id": 2, "warning_message": FLOATING}]})
    (tmp_path / "studio.log").write_text(
        f"[2026-11-23 01:00:00] [warning] got warnings: {ENFORCERS}\n"
        f"[2026-11-23 01:00:01] [warning] plate 1: found NON_CRITICAL slicing warnings: {FLOATING}\n"
        "[2026-11-23 01:00:02] [warning] plate 1: found NON_CRITICAL slicing warnings: Unsupported overhangs were detected.\n"
        'Model "test part" has 3 overhang region(s).\n'
        "[2026-11-23 01:00:03] [info] done\n" + "x" * 6000)
    report = collect_native(tmp_path)
    assert len(report["messages"]) == 4
    floating = [m for m in report["messages"] if m["rule_ids"] == ["floating_regions"]]
    assert [m["plate_index"] for m in floating] == [1, 2]
    assert floating[0]["sources"] == ["result.json", "studio.log"]
    assert 'Model "test part"' in report["messages"][-1]["message"]
    assert any(m["rule_ids"] == ["support_enforcers_disabled"] for m in report["messages"])
    assert native_summary(report, tmp_path / "diagnostics.json")["message_count"] == 4


def test_failed_native_result_and_unrecognized_warning(tmp_path):
    atomic_json(tmp_path / "result.json", {"return_code": 1, "error_string": "Unrecognized Studio failure", "plate_index": 2})
    report = collect_native(tmp_path)
    assert report["messages"][0] == {"message": "Unrecognized Studio failure", "rule_ids": [],
                                      "plate_index": 2, "sources": ["result.json"]}
    assert collect_native(tmp_path / "absent")["status"] == "not_observed"


@pytest.mark.parametrize("result", [[], {"sliced_plates": {}}, {"sliced_plates": [None]}])
def test_malformed_native_result_is_a_controlled_error(tmp_path, result):
    atomic_json(tmp_path / "result.json", result)
    with pytest.raises(ValueError, match="Native"):
        collect_native(tmp_path)


def test_in_progress_and_comparison_jobs_are_not_claimed_as_reproduction(service, opened):
    job_id, directory = make_job(service, opened, status="running")
    assert service.diagnose_warning(opened["project_id"], opened["revision"], FLOATING, job_id)["reproduction"] == "incomplete"
    atomic_json(directory / "request.json", {"kind": "comparison"})
    with pytest.raises(ValueError, match="individual slice"):
        service.diagnose_warning(opened["project_id"], opened["revision"], FLOATING, job_id)


def test_diagnosis_reads_scoped_evidence_without_editing_and_keeps_unknowns(service, opened):
    pid, revision = opened["project_id"], opened["revision"]
    path = service._directory(pid) / f"{revision}.3mf"
    original = path.read_bytes()
    result = service.diagnose_warning(pid, revision, FLOATING, target={"object_id": "2", "plate_index": 1},
                                      studio_version="02.08.04.57")
    assert result["reported_warning"]["rule_ids"] == ["floating_regions"]
    assert result["reproduction"] == "not_run"
    assert result["settings_evidence"]["project"]["enable_support"]["value"] is False
    assert result["settings_evidence"]["objects"][0]["object_id"] == "2"
    assert result["geometry"]["status"] == "not_run_or_stale"
    assert Path(result["report_path"]).is_file()
    assert path.read_bytes() == original
    unknown = service.diagnose_warning(pid, revision, "Unknown native message", studio_version="99.0")
    assert unknown["reproduction"] == "unknown_message" and not unknown["rules"]
    assert unknown["gui_version_matches_rules"] is False
    assert "matching Studio version" in unknown["unknown_message_action"]


def make_job(service, opened, message=FLOATING, plate=1, status="succeeded"):
    job_id = "d" * 32
    directory = service.workspace / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / "state.json", {"job_id": job_id, "project_id": opened["project_id"],
                                            "revision": opened["revision"], "status": status})
    atomic_json(directory / "request.json", {"environment": {"version": "02.08.04.57"}})
    atomic_json(directory / "result.json", {"return_code": 0, "sliced_plates": [{"id": plate, "warning_message": message}]})
    return job_id, directory


def test_reproduction_requires_exact_revision_and_matching_plate(service, opened):
    pid, revision = opened["project_id"], opened["revision"]
    job_id, directory = make_job(service, opened)
    report = service.diagnose_warning(pid, revision, FLOATING, job_id, {"plate_index": 1})
    assert report["reproduction"] == "warning_class_observed"
    assert report["job"]["studio_version"] == "02.08.04.57"
    assert (directory / "diagnostics.json").is_file()
    # A different plate's message is not confirmation on this one.
    atomic_json(directory / "result.json", {"return_code": 0, "sliced_plates": [{"id": 2, "warning_message": FLOATING}]})
    report = service.diagnose_warning(pid, revision, FLOATING, job_id, {"plate_index": 1})
    assert report["reproduction"] == "not_observed"
    changed = service.apply_settings(pid, revision, {"wall_loops": 3})
    with pytest.raises(ValueError, match="exact project revision"):
        service.diagnose_warning(pid, changed["revision"], FLOATING, job_id)
    # Historical exact revisions are still diagnosable after the head changes.
    assert service.diagnose_warning(pid, revision, FLOATING, job_id)["revision"] == revision


@pytest.mark.parametrize("kwargs", [dict(), {"warning_text": 123}, {"warning_text": "a" * 4001},
    {"warning_text": FLOATING, "target": {"object_id": "missing"}},
    {"warning_text": FLOATING, "target": {"plate_index": True}},
    {"warning_text": FLOATING, "target": {"plate_index": 0}},
    {"warning_text": FLOATING, "target": {"unknown": 1}}])
def test_diagnosis_validates_inputs(service, opened, kwargs):
    with pytest.raises(ValueError):
        service.diagnose_warning(opened["project_id"], opened["revision"], **kwargs)


def test_failed_worker_still_collects_warnings(service, opened, fake_runtime):
    executable = fake_runtime.executable
    executable.write_text(executable.read_text().replace(
        "pathlib.Path('sliced.3mf').write_bytes(pathlib.Path('input.3mf').read_bytes())",
        f"print('got warnings: {ENFORCERS}'); sys.exit(1)"))
    job = service.start_slice(opened["project_id"], opened["revision"])
    end = time.monotonic() + 15
    while job["status"] in {"queued", "running"} and time.monotonic() < end:
        time.sleep(.05)
        job = service.get_job(job["job_id"])
    assert job["status"] == "failed"
    assert job["native_diagnostics"]["messages"][0]["rule_ids"] == ["support_enforcers_disabled"]
    assert Path(job["native_diagnostics"]["report_path"]).is_file()
    assert service.diagnose_warning(opened["project_id"], opened["revision"], ENFORCERS,
                                     job["job_id"])["reproduction"] == "warning_class_observed"


def test_diagnostics_summary_is_bounded_and_full_messages_stay_on_disk(service, opened):
    job_id, directory = make_job(service, opened)
    rules = warning_guide()["rules"]
    text = "\n".join(rule["phrases"][0] for rule in rules.values())
    atomic_json(directory / "result.json", {"return_code": 0, "sliced_plates": [
        {"id": i + 1, "warning_message": text + "x" * 4000} for i in range(50)]})
    result = service.diagnose_warning(opened["project_id"], opened["revision"], FLOATING, job_id)
    assert len(compact_result(result).model_dump_json(by_alias=True).encode()) < 60_000
    assert result["native_diagnostics"]["message_count"] == 50
    assert len(result["native_diagnostics"]["messages"]) == 5
    full = json.loads((directory / "diagnostics.json").read_text())
    assert len(full["messages"]) == 50 and len(full["messages"][0]["message"]) > 4000
