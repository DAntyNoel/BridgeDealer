import copy
import os
import subprocess
import sys

import pytest
from conftest import ROOT, oracle_matches

from bridge_dealer import InfeasibleConditionsError, SamplingExhaustedError, random_deals


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_seeded_samples_obey_all_hand_constraints(base, eight_unknown, brute_force, n):
    raw = copy.deepcopy(eight_unknown)
    for seat in "NESW"[:n]:
        raw[seat].update(shape=list(base.shape(seat)), hcp=[base.hcp(seat) - 2, base.hcp(seat) + 2])
    expected = {d for d in brute_force if oracle_matches(raw, d)}
    samples = list(random_deals(raw, seed=42, count=20, max_attempts=10000))
    assert len(samples) == 20
    assert all(deal in expected for deal in samples)
    assert samples == list(random_deals(raw, seed=42, count=20, max_attempts=10000))


def test_any_shape_and_unconstrained_random():
    raw = {"N": {"shape_any": [4, 3, 3, 3], "hcp": [12, 16]}, "S": {"hcp": [8, 12]}}
    samples = list(random_deals(raw, seed=20260922, count=12, max_attempts=10000))
    assert all(oracle_matches(raw, deal) for deal in samples)
    a = list(random_deals({}, seed=1, count=3))
    b = list(random_deals({}, seed=2, count=3))
    assert a != b


def test_reproducibility_in_independent_processes():
    command = [
        sys.executable,
        "-m",
        "bridge_dealer",
        "random",
        str(ROOT / "examples/north_south.json"),
        "--seed",
        "19",
        "--count",
        "5",
    ]
    outputs = []
    for hash_seed in ("1", "937"):
        run = subprocess.run(
            command,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        outputs.append(run.stdout)
    assert outputs[0] == outputs[1]
    assert outputs[0].count('[Deal "') == 5


def test_infeasible_and_budget_exhaustion_are_different():
    impossible = {seat: {"hcp": 11} for seat in "NESW"}
    with pytest.raises(InfeasibleConditionsError):
        next(random_deals(impossible, seed=0))
    samples = random_deals({}, seed=0, count=3, max_attempts=2)
    assert next(samples) is not None
    assert next(samples) is not None
    with pytest.raises(SamplingExhaustedError) as caught:
        next(samples)
    assert caught.value.produced == 2 and caught.value.requested == 3


def test_empty_request():
    assert list(random_deals({}, seed=0, count=0)) == []


@pytest.mark.parametrize(
    "options",
    [
        {"seed": -1},
        {"seed": 2**32},
        {"seed": True},
        {"seed": 1.5},
        {"seed": 0, "count": -1},
        {"seed": 0, "count": True},
        {"seed": 0, "max_attempts": 0},
    ],
)
def test_bad_sampling_options(options):
    with pytest.raises(ValueError):
        list(random_deals({}, **options))
