import json
import subprocess
import sys
from pathlib import Path

from bridge_dealer import query_success
from bridge_dealer.dds_native import STRAINS, DDSSolver
from bridge_dealer.v2 import compile_sampler


def test_unlimited_exact_ignores_sample_caps_and_solves_complete_population():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/four_hands_85.json").read_text())
    # An independent 85-deal oracle is obtained from ordinary enumeration tests;
    # this query must also finish despite a max_samples lower than its population.
    result = query_success(raw, exact=True, max_samples=1, budget_seconds=None, seed=1)
    assert result["status"] == "exact" and result["samples"] == 85
    assert result["population_size"] == 85 and result["budget_seconds"] is None
    assert result["max_samples"] is None and result["query_mode"] == "exact"
    assert result["stop_reason"] == "exhausted" and result["unresolved"] == 0
    assert result["within_budget"] is None
    assert all(item["interval"] == [item["probability"]] * 2 for item in result["results"])


def test_unlimited_exact_handles_infeasible_population():
    result = query_success({p: {"hcp": 11} for p in "NESW"}, exact=True)
    assert result["status"] == "infeasible" and result["samples"] == 0
    assert all(item["probability"] is None for item in result["results"])


def test_unlimited_exact_matches_full_known_space():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/two_unknown_cards.json").read_text())
    masks = list(compile_sampler(raw).enumerate_masks())
    solver = DDSSolver(threads=4)
    outcomes = solver.success(masks, declarer=1, strains=STRAINS, level=2)
    expected = [sum(row[i] for row in outcomes) / len(masks) for i in range(5)]
    result = query_success(raw, exact=True, declarer="E", level=2)
    assert result["status"] == "exact" and result["population_size"] == 2
    assert [item["probability"] for item in result["results"]] == expected


def test_cli_unlimited_exact():
    root = Path(__file__).resolve().parents[1]
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_dealer",
            "success",
            "examples/four_hands_85.json",
            "--exact",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["status"] == "exact" and result["samples"] == 85


def test_default_query_targets_10000_in_twenty_seconds():
    import inspect

    defaults = inspect.signature(query_success).parameters
    assert defaults["budget_seconds"].default == 20
    assert defaults["min_samples"].default == 10000
    assert defaults["threads"].default == 8


def test_exact_progress_and_final_result_are_reported_separately():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/four_hands_85.json").read_text())
    progress = []
    result = query_success(raw, exact=True, on_progress=progress.append)
    assert progress and progress[-1]["samples"] == 85
    assert progress[-1]["type"] == "done"
    assert all(p["method"] == "enumeration" and p["elapsed_seconds"] >= 0 for p in progress)
    assert result["status"] == "exact"
