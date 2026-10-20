import copy
import itertools
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

import pytest

from bridge_dealer import Conditions, Deal, compile_sampler, query_success
from bridge_dealer.cards import hand_text
from bridge_dealer.dds_native import STRAINS, DDSSolver, library_path
from bridge_dealer.query_sampling import Proposals, matches_masks
from bridge_dealer.success import confidence_sequence

REFERENCES = [
    (
        "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3",
        [[5, 8, 5, 8], [6, 6, 6, 6], [5, 7, 5, 7], [7, 5, 7, 5], [6, 6, 6, 6]],
    ),
    (
        "E:QJT5432.T.6.QJ82 .J97543.K7532.94 87.A62.QJT4.AT75 AK96.KQ8.A98.K63",
        [[4, 9, 4, 9], [10, 2, 10, 2], [8, 3, 8, 3], [6, 7, 6, 7], [9, 3, 9, 3]],
    ),
    (
        "N:73.QJT.AQ54.T752 QT6.876.KJ9.AQ84 5.A95432.7632.K6 AKJ9842.K.T8.J93",
        [[3, 10, 3, 10], [9, 4, 9, 4], [8, 4, 8, 4], [3, 9, 3, 9], [4, 8, 4, 8]],
    ),
]


@pytest.fixture(scope="module")
def native():
    try:
        path = library_path()
    except FileNotFoundError:
        pytest.skip("local DDS library not installed")
    return DDSSolver(path, threads=4)


def masks_of(deal):
    return tuple(sum(1 << c for c in h) for h in deal.hands)


def test_dds_thresholds_against_420_official_cells(native):
    masks = [masks_of(Deal.from_pbn(pbn)) for pbn, _ in REFERENCES]
    for declarer in range(4):
        for level in range(1, 8):
            actual = native.success(masks, declarer=declarer, strains=STRAINS, level=level)
            expected = [
                tuple(row[declarer] >= 6 + level for row in table) for _, table in REFERENCES
            ]
            assert actual == expected


def test_exact_full_deal_query(base, native):
    raw = {p: {"predeal": hand_text(base.hand(p))} for p in "NESW"}
    started = perf_counter()
    result = query_success(raw, budget_seconds=3, level=1, seed=42)
    assert perf_counter() - started < 3
    assert result["within_budget"] and result["status"] == "exact"
    assert result["samples"] == 1
    assert [r["probability"] for r in result["results"]] == [0, 0, 0, 1, 0]
    assert all(r["interval"] == [r["probability"]] * 2 for r in result["results"])


def test_tiny_space_exact_query_matches_independent_dds(native):
    path = Path(__file__).resolve().parents[1] / "examples/two_unknown_cards.json"
    raw = json.loads(path.read_text())
    masks = list(compile_sampler(raw).enumerate_masks())
    expected = native.success(masks, declarer=1, strains=STRAINS, level=2)
    result = query_success(raw, declarer="E", level=2, budget_seconds=3, seed=7)
    assert result["status"] == "exact" and result["population_size"] == len(masks)
    assert [r["successes"] for r in result["results"]] == [
        sum(row[i] for row in expected) for i in range(5)
    ]


def test_conditioned_query_and_fixed_count_reproducibility(base, native):
    raw = {
        "N": {"predeal": hand_text(base.hand("N"))},
        "E": {"shape_any": list(base.shape("E")), "hcp": [8, 15]},
    }
    options = dict(
        seed=20261020, max_samples=40, min_samples=1, budget_seconds=4, strains="NT,S", level=3
    )
    a, b = query_success(raw, **options), query_success(raw, **options)
    assert a["status"] == b["status"] == "estimated"
    assert a["stop_reason"] == b["stop_reason"] == "sample_cap"
    assert a["results"] == b["results"]
    assert a["strategy"] == "joint_shape_rejection"
    assert a["samples"] == 40 and a["proposed"] >= 40


def test_joint_shapes_with_hcp_against_exhaustive_oracle(base, eight_unknown, brute_force):
    raw = copy.deepcopy(eight_unknown)
    for seat in "NESW":
        raw[seat]["shape_any"] = list(base.shape(seat))
        raw[seat]["hcp"] = [max(0, base.hcp(seat) - 2), base.hcp(seat) + 2]
    conditions = Conditions.from_dict(raw)
    expected = {masks_of(d) for d in brute_force if conditions.matches(d)}
    proposal = Proposals(conditions, seed=17, compile_deadline=perf_counter() + 1)
    counts = Counter()
    for _ in range(30000):
        candidate = proposal.next()
        if candidate is not None:
            assert candidate in expected and matches_masks(conditions, candidate)
            counts[candidate] += 1
    assert set(counts) == expected
    mean = sum(counts.values()) / len(counts)
    assert all(abs(n - mean) < max(mean * 0.7, 10) for n in counts.values())


def test_unfinished_matrix_compile_falls_back_to_entire_space(base):
    raw = {"N": {"shape_any": [4, 3, 3, 3]}}
    proposal = Proposals(Conditions.from_dict(raw), seed=1, compile_deadline=0)
    assert not proposal.cdf and proposal.strategy == "uniform_predeal_rejection"
    for _ in range(1000):
        masks = proposal.next()
        if masks:
            assert matches_masks(proposal.conditions, masks)


@pytest.mark.parametrize("active", ("N", "NE"))
def test_hcp_point_groups_exact_counts_and_samples(base, eight_unknown, brute_force, active):
    raw = copy.deepcopy(eight_unknown)
    for seat in active:
        raw[seat]["hcp"] = base.hcp(seat)
    conditions = Conditions.from_dict(raw)
    expected = {masks_of(d) for d in brute_force if conditions.matches(d)}
    proposal = Proposals(conditions, seed=1, compile_deadline=perf_counter() + 2)
    assert proposal.strategy == "hcp_point_groups"
    assert proposal.population_size == len(expected)
    assert all(proposal.next() in expected for _ in range(1000))


def test_rare_37_hcp_is_constructed_without_rejection():
    conditions = Conditions.from_dict({"N": {"hcp": 37}})
    proposal = Proposals(conditions, seed=1, compile_deadline=perf_counter() + 1)
    assert proposal.strategy == "hcp_point_groups"
    # N has all twelve AKQ plus one of four jacks; the other 39 cards are free.
    assert proposal.population_size == 4 * math.factorial(39) // math.factorial(13) ** 3
    assert all(matches_masks(conditions, proposal.next()) for _ in range(100))


def test_timed_out_hcp_compile_does_not_sample_a_partial_table():
    conditions = Conditions.from_dict({"N": {"hcp": [10, 20]}, "E": {"hcp": [10, 20]}})
    proposal = Proposals(conditions, seed=42, compile_deadline=0)
    assert proposal.strategy == "uniform_predeal_rejection" and not proposal.cdf
    assert any(proposal.next() is not None for _ in range(100))


def test_rare_hcp_plus_shape_selects_stronger_proposal():
    conditions = Conditions.from_dict({"N": {"hcp": 37, "shape_any": [4, 3, 3, 3]}})
    proposal = Proposals(conditions, seed=4, compile_deadline=perf_counter() + 2)
    assert proposal.strategy == "hcp_point_groups_rejection"
    assert all(proposal.next() is not None for _ in range(100))


def test_four_hcp_constraints_use_subset_then_reject():
    conditions = Conditions.from_dict({"N": {"hcp": 37}, **{p: {"hcp": 1} for p in "ESW"}})
    proposal = Proposals(conditions, seed=4, compile_deadline=perf_counter() + 2)
    assert proposal.strategy == "hcp_point_groups" and proposal.population_size is None
    accepted = [m for _ in range(1000) if (m := proposal.next()) is not None]
    assert len(accepted) > 100
    assert all(matches_masks(conditions, m) for m in accepted)


def test_infeasible_is_not_zero_percent(base):
    raw = {p: {"predeal": hand_text(base.hand(p))} for p in "NESW"}
    raw["N"]["hcp"] = base.hcp("N") + 1
    result = query_success(raw, budget_seconds=2)
    assert result["status"] == "infeasible" and not result["samples"]
    assert all(r["probability"] is None and r["interval"] is None for r in result["results"])


def test_rejection_timeout_is_unknown():
    raw = {p: {"hcp": 11} for p in "NESW"}
    result = query_success(raw, budget_seconds=2)
    assert result["status"] == "infeasible"


def test_slow_worker_is_killed_and_checkpoint_preserved(monkeypatch):
    from bridge_dealer import success

    code = (
        "import json,time; "
        "print(json.dumps(dict(type='checkpoint',samples=2,successes=[1],generated=3,"
        "proposed=3,method='monte_carlo',population_size=None,sampling_seconds=0,"
        "dds_seconds=0)),flush=True); time.sleep(60)"
    )
    monkeypatch.setattr(success, "_worker_command", lambda: [sys.executable, "-c", code])
    started = perf_counter()
    result = query_success({}, strains=["NT"], budget_seconds=0.6)
    assert perf_counter() - started < 0.8
    assert result["within_budget"] and result["worker_terminated"]
    assert result["status"] == "insufficient_samples"
    assert result["samples"] == 2 and result["unresolved"] == 1
    assert result["results"][0]["probability"] == 0.5


def test_missing_dds_is_explicit_error(tmp_path):
    result = query_success({}, dds_library=tmp_path / "missing", budget_seconds=2)
    assert result["status"] == "error" and result["errors"]


def test_confidence_sequence_covers_all_prefixes_with_exact_binomial_probability():
    # Exhaustive probability of ANY false exclusion through n=10, not a
    # simulation or comparison with the implementation's own formula.
    for probability in (0.1, 0.5, 0.9):
        miss = 0.0
        for outcomes in itertools.product((0, 1), repeat=10):
            successes, excluded = 0, False
            for n, outcome in enumerate(outcomes, 1):
                successes += outcome
                lo, hi = confidence_sequence(successes, n, 0.05)
                excluded |= not lo <= probability <= hi
            if excluded:
                miss += probability ** sum(outcomes) * (1 - probability) ** (10 - sum(outcomes))
        assert miss <= 0.05 + 1e-12
    assert confidence_sequence(0, 0, 0.05) == [0, 1]


@pytest.mark.parametrize(
    "options",
    [
        {"budget_seconds": 0},
        {"budget_seconds": math.inf},
        {"budget_seconds": True},
        {"level": 8},
        {"level": True},
        {"strains": []},
        {"strains": ["invalid"]},
        {"seed": 2**32},
        {"max_samples": 0},
        {"threads": 33},
        {"confidence": 1},
    ],
)
def test_invalid_queries(options):
    with pytest.raises(ValueError):
        query_success({}, **options)


def test_cli_cold_start_with_default_ten_second_budget(native):
    root = Path(__file__).resolve().parents[1]
    started = perf_counter()
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_dealer",
            "success",
            "examples/v2_fixed_one.json",
            "--seed",
            "20261020",
            "--strains",
            "S,H,D,C,NT",
            "--level",
            "3",
            "--budget",
            "10",
            "--min-samples",
            "100",
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=10,
    )
    elapsed = perf_counter() - started
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert elapsed <= 10 and result["within_budget"]
    assert result["samples"] >= 100 and len(result["results"]) == 5
