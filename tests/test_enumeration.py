import copy
import json
from math import factorial
from pathlib import Path

import pytest
from conftest import oracle_matches

from bridge_dealer import check_conditions, enumerate_deals

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("kind", ["shape", "hcp"])
@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_shape_only_or_hcp_only_for_one_to_four_hands(base, eight_unknown, brute_force, kind, n):
    raw = copy.deepcopy(eight_unknown)
    for seat in "NESW"[:n]:
        raw[seat][kind] = list(base.shape(seat)) if kind == "shape" else base.hcp(seat)
    expected = {deal for deal in brute_force if oracle_matches(raw, deal)}
    actual = []
    result = enumerate_deals(raw, actual.append)
    assert expected and result.complete
    assert len(actual) == len(set(actual)) == len(expected)
    assert set(actual) == expected


@pytest.mark.parametrize("constrained_seats", range(5))
def test_complete_sets_against_independent_bruteforce(
    base, eight_unknown, brute_force, constrained_seats
):
    raw = copy.deepcopy(eight_unknown)
    for seat in "NESW"[:constrained_seats]:
        raw[seat]["shape"] = list(base.shape(seat))
        raw[seat]["hcp"] = [max(0, base.hcp(seat) - 1), base.hcp(seat) + 1]
    expected = {deal.to_pbn() for deal in brute_force if oracle_matches(raw, deal)}
    actual = []
    result = enumerate_deals(raw, lambda deal: actual.append(deal.to_pbn()))
    assert expected  # The original complete deal is a witness.
    assert set(actual) == expected
    assert len(actual) == len(expected) == result.count
    assert result.complete and result.stop_reason is None
    if constrained_seats == 0:
        assert result.count == factorial(8) // factorial(2) ** 4 == 2520


@pytest.mark.parametrize(
    "shape_condition",
    [
        {"shape_any": [4, 3, 3, 3]},
        {"shapes": [[3, 4, 3, 3], [3, 4, 3, 3], [4, 3, 3, 3]]},
    ],
)
def test_shape_unions_without_projected_duplicates(eight_unknown, brute_force, shape_condition):
    raw = copy.deepcopy(eight_unknown)
    raw["N"].update(shape_condition)
    expected = {d.to_pbn() for d in brute_force if oracle_matches(raw, d)}
    actual = []
    result = enumerate_deals(raw, lambda d: actual.append(d.to_pbn()))
    assert result.complete
    assert len(actual) == len(set(actual))
    assert set(actual) == expected


def test_four_shapes_and_hcp_without_any_predeal():
    raw = json.loads((EXAMPLES / "four_hands_85.json").read_text())
    actual = []
    result = enumerate_deals(raw, lambda d: actual.append(d))
    # N and E exchange exactly one spade and one heart of equal point value.
    # Nine zero-point ranks pair in 9*9 ways, plus AA, KK, QQ, JJ.
    assert result.complete and result.count == 9 * 9 + 4 == 85
    assert len(set(actual)) == 85
    for deal in actual:
        assert oracle_matches(raw, deal)


@pytest.mark.parametrize(
    "raw",
    [
        {seat: {"hcp": 11} for seat in "NESW"},
        {"N": {"shape": [13, 0, 0, 0]}, "E": {"shape": [13, 0, 0, 0]}},
        {"N": {"shape": [13, 0, 0, 0], "hcp": 11}},
        {"N": {"shape": [0, 4, 4, 5], "predeal": "A..."}},
    ],
)
def test_proven_infeasible(raw):
    actual = []
    result = enumerate_deals(raw, actual.append)
    assert actual == []
    assert result.complete and result.status == "INFEASIBLE"
    assert check_conditions(raw) == "infeasible"


def test_limits_callbacks_and_stable_prefix(eight_unknown):
    first, again = [], []
    result = enumerate_deals(eight_unknown, first.append, limit=7)
    enumerate_deals(eight_unknown, again.append, limit=10)
    assert first == again[:7]
    assert result.count == 7 and not result.complete and result.stop_reason == "limit"
    result = enumerate_deals(eight_unknown, lambda _: False)
    assert result.count == 1 and not result.complete and result.stop_reason == "callback"


def test_limit_equal_to_actual_count_is_conservative():
    raw = json.loads((EXAMPLES / "two_unknown_cards.json").read_text())
    result = enumerate_deals(raw, lambda _: None, limit=2)
    assert result.count == 2 and not result.complete


def test_callback_exception_is_not_swallowed():
    def fail(_):
        raise LookupError("output failed")

    with pytest.raises(LookupError, match="output failed"):
        enumerate_deals({}, fail, limit=1)


def test_timeout_is_not_infeasibility():
    result = enumerate_deals({}, lambda _: None, time_limit=1e-9)
    assert not result.complete and result.status == "UNKNOWN"
    assert result.stop_reason == "time_limit"
    assert check_conditions({}, time_limit=1e-9) == "unknown"


@pytest.mark.parametrize(
    "options",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": True},
        {"limit": 1.5},
        {"time_limit": 0},
        {"time_limit": -1},
        {"time_limit": float("nan")},
        {"time_limit": float("inf")},
        {"time_limit": True},
    ],
)
def test_bad_limits(options):
    with pytest.raises(ValueError):
        enumerate_deals({}, lambda _: None, **options)
