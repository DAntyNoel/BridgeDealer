import copy
from collections import Counter
from itertools import combinations
from math import comb, factorial

import pytest
from conftest import oracle_matches

from bridge_dealer import (
    Deal,
    InfeasibleConditionsError,
    compile_sampler,
    enumerate_deals,
    enumerate_deals_v2,
)
from bridge_dealer.cards import hand_text
from bridge_dealer.v2 import deal_from_masks


def fixed(raw, base, seat):
    raw[seat] = {"predeal": hand_text(base.hand(seat))}


def test_predeal_count_and_batch_stream(base):
    raw = {}
    fixed(raw, base, "N")
    plan = compile_sampler(raw)
    assert plan.total_deals == factorial(39) // factorial(13) ** 3
    expected = list(plan.sample_deals(count=41, seed=42))
    for size in (1, 7, 100):
        actual = [
            d for batch in plan.sample_batches(count=41, seed=42, batch_size=size) for d in batch
        ]
        assert actual == expected
    assert expected == list(plan.sample_deals(count=41, seed=42))
    assert expected != list(plan.sample_deals(count=41, seed=43))
    assert all(d.hand("N") == base.hand("N") for d in expected)


@pytest.mark.parametrize("known", "NESW")
@pytest.mark.parametrize("count", (1, 2, 3))
@pytest.mark.parametrize("kind", ("shape", "shape_any", "shapes"))
def test_fixed_hand_with_one_to_three_shapes(base, known, count, kind):
    raw = {}
    fixed(raw, base, known)
    others = [s for s in "NESW" if s != known]
    for seat in others[:count]:
        shape = list(base.shape(seat))
        value = [shape, shape] if kind == "shapes" else shape
        raw[seat] = {kind: value}
    plan = compile_sampler(raw)
    samples = list(plan.sample_deals(count=50, seed=84))
    assert all(d.hand(known) == base.hand(known) and oracle_matches(raw, d) for d in samples)
    assert len(samples) == 50
    if count >= 2 and kind != "shape_any":
        expected = 1
        for s in range(4):
            expected *= factorial(13 - base.shape(known)[s]) // (
                factorial(base.shape(others[0])[s])
                * factorial(base.shape(others[1])[s])
                * factorial(base.shape(others[2])[s])
            )
        assert plan.total_deals == expected


def test_three_ordered_shapes_reduce_without_changing_sequence(base):
    raw = {s: {"shape": list(base.shape(s))} for s in "ESW"}
    fixed(raw, base, "N")
    full = compile_sampler(raw)
    del raw["W"]
    reduced = compile_sampler(raw)
    assert full.total_deals == reduced.total_deals
    assert list(full.sample_deals(count=30, seed=5)) == list(reduced.sample_deals(count=30, seed=5))


@pytest.mark.parametrize("known", ("NE", "NS", "NW", "ES", "EW", "SW"))
def test_two_fixed_hands_count_and_prefix(base, known):
    raw = {s: {"predeal": hand_text(base.hand(s))} for s in known}
    plan = compile_sampler(raw)
    assert plan.total_deals == comb(26, 13)
    actual = []
    result = enumerate_deals_v2(raw, actual.append, limit=19)
    assert len(actual) == len(set(actual)) == result.count == 19
    assert not result.complete and result.stop_reason == "limit"
    assert all(d.hand(s) == base.hand(s) for d in actual for s in known)


def test_exact_sets_against_independent_oracle(base, eight_unknown, brute_force):
    for count in (0, 1, 2, 3):
        raw = copy.deepcopy(eight_unknown)
        fixed(raw, base, "W")
        for seat in "NES"[:count]:
            raw[seat]["shape_any"] = list(base.shape(seat))
        expected = {
            d for d in brute_force if d.hand("W") == base.hand("W") and oracle_matches(raw, d)
        }
        plan = compile_sampler(raw)
        actual = [deal_from_masks(masks) for masks in plan.enumerate_masks()]
        assert len(actual) == len(set(actual)) == len(expected) == plan.total_deals
        assert set(actual) == expected
        assert all(d in expected for d in plan.sample_deals(count=100, seed=9))


def test_partial_predeals_no_shapes_matches_all_2520(brute_force, eight_unknown):
    plan = compile_sampler(eight_unknown)
    actual = {deal_from_masks(masks) for masks in plan.enumerate_masks()}
    assert plan.total_deals == len(actual) == 2520
    assert actual == set(brute_force)


def test_two_open_hands_complete_against_independent_combinations(base):
    raw = {s: {"predeal": hand_text(base.hand(s))} for s in "NW"}
    for seat in "ES":
        raw[seat] = {"predeal": hand_text(base.hand(seat)[:-3])}
    pool = sorted(base.hand("E")[-3:] + base.hand("S")[-3:])
    expected = set()
    for selected in combinations(pool, 3):
        hands = list(base.hands)
        hands[1] = base.hand("E")[:-3] + selected
        hands[2] = base.hand("S")[:-3] + tuple(c for c in pool if c not in selected)
        expected.add(Deal(tuple(hands)))
    actual = []
    result = enumerate_deals_v2(raw, actual.append)
    assert result.complete and result.count == len(actual) == len(expected) == 20
    assert set(actual) == expected


def test_nonuniform_matrix_weights_and_overlapping_unions(base):
    # Six free cards spanning two suits, with a fixed fourth hand.
    raw = {s: {"predeal": hand_text(base.hand(s))} for s in "NESW"}
    removed = {"N": (0, 3), "E": (0, 4), "S": (0, 2)}
    for seat, positions in removed.items():
        raw[seat] = {
            "predeal": hand_text(
                tuple(c for i, c in enumerate(base.hand(seat)) if i not in positions)
            )
        }
    reference = []
    enumerate_deals(raw, reference.append)
    shapes = sorted({d.shape("E") for d in reference})
    raw["E"]["shapes"] = [list(s) for s in shapes] + [list(shapes[0])]
    plan = compile_sampler(raw)
    assert len({m.weight for m in plan.matrices}) > 1
    assert plan.total_deals == len(reference)
    actual = [deal_from_masks(m) for m in plan.enumerate_masks()]
    assert len(actual) == len(set(actual)) == len(reference)
    assert set(actual) == set(reference)
    # A deterministic, broad check catches equal-matrix rather than equal-deal sampling.
    frequencies = Counter(plan.sample_masks(count=30000, seed=7))
    assert len(frequencies) == len(reference)
    expected = 30000 / len(reference)
    assert all(abs(n - expected) < expected * 0.4 for n in frequencies.values())


def test_infeasible_and_fixed_conditions(base):
    raw = {s: {"predeal": hand_text(base.hand(s))} for s in "NESW"}
    plan = compile_sampler(raw)
    assert plan.total_deals == 1
    assert list(plan.sample_deals(count=3, seed=1)) == [base] * 3
    assert list(plan.enumerate_masks()) == [plan.fixed_masks]
    raw["N"]["hcp"] = base.hcp("N") + 1
    plan = compile_sampler(raw)
    assert plan.total_deals == 0
    with pytest.raises(InfeasibleConditionsError):
        list(plan.sample_masks(count=1, seed=1))
    result = enumerate_deals_v2(raw, lambda _: pytest.fail("unexpected solution"))
    assert result.complete and result.status == "INFEASIBLE"
    assert list(plan.sample_masks(count=0, seed=1)) == []


def test_incompatible_third_shape(base):
    raw = {s: {"shape": list(base.shape(s))} for s in "ESW"}
    fixed(raw, base, "N")
    raw["W"]["shape"] = [13, 0, 0, 0]
    assert compile_sampler(raw).total_deals == 0


def test_budget_and_unsupported_conditions(base):
    raw = {"E": {"shape_any": [4, 3, 3, 3]}}
    fixed(raw, base, "N")
    with pytest.raises(ValueError, match="budget"):
        compile_sampler(raw, max_matrices=1)
    with pytest.raises(ValueError, match="complete predealt"):
        compile_sampler({"E": {"shape": [4, 3, 3, 3]}})
    raw["E"] = {"hcp": 10}
    with pytest.raises(ValueError, match="HCP"):
        compile_sampler(raw)


@pytest.mark.parametrize(
    "kwargs", [{"count": -1}, {"count": True}, {"seed": -1}, {"seed": 2**32}, {"seed": True}]
)
def test_invalid_sampling_options(kwargs):
    with pytest.raises(ValueError):
        list(compile_sampler({}).sample_masks(**({"count": 1, "seed": 0} | kwargs)))


def test_enumeration_stops_and_exceptions(base):
    raw = {s: {"predeal": hand_text(base.hand(s))} for s in "NESW"}
    assert enumerate_deals_v2(raw, lambda _: False).stop_reason == "callback"
    assert enumerate_deals_v2(raw, lambda _: None, limit=1).complete is False
    result = enumerate_deals_v2(raw, lambda _: None, time_limit=1e-9)
    assert result.complete is False and result.stop_reason == "time_limit"

    def fail(_):
        raise LookupError("callback failed")

    with pytest.raises(LookupError, match="callback failed"):
        enumerate_deals_v2(raw, fail)
    for kwargs in (
        {"limit": True},
        {"limit": 0},
        {"time_limit": float("nan")},
        {"time_limit": True},
    ):
        with pytest.raises(ValueError):
            enumerate_deals_v2(raw, lambda _: None, **kwargs)
