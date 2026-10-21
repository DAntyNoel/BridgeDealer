"""Check the worker protocol and publication timing independently of DDS speed."""

import json
import sys
from time import perf_counter, sleep

import pytest

from bridge_dealer import query_success, success
from bridge_dealer.web import Jobs

# Deliberately leave gaps longer than a second between native checkpoints.
# Checkpoints must publish promptly; display polling must not pace the worker.
STREAMING_WORKER = """
import json, sys, time
request = json.load(sys.stdin)
exact = request['exact']
def emit(n, kind='checkpoint'):
    value = dict(type=kind, samples=n, successes=[n // 2], generated=n,
                 proposed=n, method='enumeration' if exact else 'monte_carlo',
                 population_size=6 if exact else None,
                 sampling_seconds=0, dds_seconds=0)
    if kind == 'done':
        value['stop_reason'] = 'exhausted' if exact else 'sample_cap'
    print(json.dumps(value), flush=True)
emit(0)
time.sleep(0.05)
emit(1)
time.sleep(0.05)
emit(2)
time.sleep(1.1)
emit(4)
time.sleep(1.1)
emit(6, 'done')
"""


@pytest.mark.parametrize("exact", [False, True])
def test_target_publishes_immediately_then_tracks_completed_batches(monkeypatch, exact):
    monkeypatch.setattr(
        success, "_worker_command", lambda: [sys.executable, "-c", STREAMING_WORKER]
    )
    progress = []
    result = query_success(
        {},
        strains=["NT"],
        exact=exact,
        min_samples=2,
        max_samples=6,
        budget_seconds=5,
        on_progress=progress.append,
    )
    live = [p["result"] for p in progress if p.get("result", {}).get("running")]
    assert "result" not in progress[0] and progress[0]["samples"] == 0
    assert [p["samples"] for p in live] == [2, 4]
    assert live[0]["samples"] == 2 and live[0]["elapsed_seconds"] < 0.8
    assert live[-1]["samples"] >= 4  # The target did not stop the computation.
    assert all(p["status"] == "running" and p["stop_reason"] == "running" for p in live)
    for snapshot in live:
        item = snapshot["results"][0]
        n = snapshot["samples"]
        assert item["samples"] == n and item["successes"] == n // 2
        if exact:
            assert item["probability"] is None
            assert item["interval"] == [n // 2 / 6, (n // 2 + 6 - n) / 6]
            assert snapshot["inference"] == "census_bounds"
        else:
            assert item["probability"] == 0.5
            assert item["interval"][0] <= 0.5 <= item["interval"][1]
    assert live[0]["samples"] == 2  # Earlier snapshots are immutable.
    assert result["samples"] == 6 and not result["running"]
    assert result["status"] == ("exact" if exact else "estimated")
    assert progress[-1]["type"] == "done" and not progress[-1]["result"]["running"]


def test_backend_publishes_new_batches_without_waiting_for_display_poll(monkeypatch):
    code = STREAMING_WORKER.replace("time.sleep(1.1)", "time.sleep(0.02)")
    monkeypatch.setattr(success, "_worker_command", lambda: [sys.executable, "-c", code])
    progress = []
    result = query_success(
        {},
        strains=["NT"],
        min_samples=2,
        max_samples=6,
        budget_seconds=5,
        on_progress=progress.append,
    )
    live = [p["result"] for p in progress if p.get("result", {}).get("running")]
    assert [p["samples"] for p in live] == [2, 4]
    assert live[1]["elapsed_seconds"] - live[0]["elapsed_seconds"] < 0.5
    assert result["samples"] == 6


@pytest.mark.parametrize("exact", [False, True])
def test_job_keeps_running_with_visible_results_and_cancels(monkeypatch, exact):
    monkeypatch.setattr(
        success, "_worker_command", lambda: [sys.executable, "-c", STREAMING_WORKER]
    )
    jobs = Jobs()
    try:
        identity = jobs.start(
            {
                "contracts": [{"declarer": "N", "level": 3, "strain": "NT"}],
                "exact": exact,
                "min_samples": 2,
                "max_samples": 6,
                "budget_seconds": 5,
            }
        )
        deadline = perf_counter() + 3
        while perf_counter() < deadline:
            job = jobs.snapshot(identity)
            if job.get("result"):
                break
            assert job["state"] == "running"
            sleep(0.01)
        else:
            pytest.fail("No early result was published")
        assert job["state"] == "running" and job["result"]["running"]
        assert job["result"]["samples"] == 2
        assert "result" not in job["progress"]  # Do not duplicate full results in HTTP JSON.
        assert jobs.snapshot(identity)["result"] == job["result"]
        jobs.cancel(identity)
        jobs.worker.join(timeout=2)
        final = jobs.snapshot(identity)
        assert final["state"] == "cancelled"
        assert final["result"]["status"] == "cancelled"
        assert not final["result"]["running"] and final["result"]["samples"] >= 2
        if exact:
            assert final["result"]["inference"] == "census_bounds"
    finally:
        jobs.close()


def test_budget_after_target_preserves_latest_result(monkeypatch):
    code = STREAMING_WORKER[: STREAMING_WORKER.index("time.sleep(1.1)")]
    code += "time.sleep(60)\n"
    monkeypatch.setattr(success, "_worker_command", lambda: [sys.executable, "-c", code])
    progress = []
    result = query_success(
        {},
        strains=["NT"],
        min_samples=2,
        max_samples=100,
        budget_seconds=1,
        on_progress=progress.append,
    )
    assert any(p.get("result", {}).get("running") for p in progress)
    assert result["stop_reason"] == "time_budget" and result["worker_terminated"]
    assert result["status"] == "estimated" and not result["running"]
    assert result["samples"] == 2 and result["within_budget"]


@pytest.mark.parametrize(
    ("example", "target", "population"),
    [("four_hands_85", 2, None), ("two_unknown_cards", 1, 2)],
)
def test_real_exact_target_precedes_exhaustion_and_ignores_caps(example, target, population):
    from pathlib import Path

    raw = json.loads((Path(__file__).parents[1] / f"examples/{example}.json").read_text())
    progress = []
    result = query_success(
        raw,
        exact=True,
        min_samples=target,
        max_samples=1,
        budget_seconds=None,
        on_progress=progress.append,
    )
    live = next(p["result"] for p in progress if p.get("result", {}).get("running"))
    assert live["samples"] == target and live["status"] == "running"
    assert live["population_size"] == population
    assert all(item["probability"] is None for item in live["results"])
    assert result["status"] == "exact"
    assert result["samples"] == (85 if population is None else population)
    assert result["budget_seconds"] is None and result["max_samples"] is None
