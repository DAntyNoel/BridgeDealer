import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bridge_dealer import Conditions, query_success
from bridge_dealer.cards import hand_text, parse_hand
from bridge_dealer.dds_native import STRAINS, DDSSolver
from bridge_dealer.query_symmetry import strain_representatives


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({}, ("S", "S", "S", "S", "NT")),
        ({"N": {"hcp": [12, 16]}}, ("S", "S", "S", "S", "NT")),
        ({"N": {"shape_any": [4, 3, 3, 3]}}, ("S", "S", "S", "S", "NT")),
        ({"N": {"shape": [5, 3, 3, 2]}}, ("S", "H", "H", "C", "NT")),
        ({"N": {"predeal": "A.A.K.K"}}, ("S", "S", "D", "D", "NT")),
        ({"N": {"shape": [5, 4, 3, 1]}}, STRAINS),
        ({"N": {"predeal": "QJ6.K652.J85.T98"}}, STRAINS),
        (
            {"N": {"shape_any": [4, 3, 3, 3]}, "E": {"predeal": "A..."}},
            ("S", "H", "H", "H", "NT"),
        ),
        (
            {"N": {"shapes": [[5, 4, 3, 1], [1, 5, 4, 3], [3, 1, 5, 4], [4, 3, 1, 5]]}},
            ("S", "S", "S", "S", "NT"),
        ),
    ],
)
def test_exact_condition_symmetries_include_cyclic_groups(raw, expected):
    actual = strain_representatives(Conditions.from_dict(raw), STRAINS)
    assert tuple(actual[s] for s in STRAINS) == expected


def test_representatives_are_selected_from_requested_strains():
    assert strain_representatives(Conditions.from_dict({}), ("H", "D", "NT")) == {
        "H": "H",
        "D": "H",
        "NT": "NT",
    }


def test_partial_predeals_preserve_exact_census_probabilities():
    # Four unknown cards, one slot each: independent 4! allocations. Every
    # predeal is invariant under S/H interchange, while individual DD outcomes
    # need not be. This verifies the population bijection, not equality per deal.
    fixed = [[] for _ in range(4)]
    for rank in range(2, 13):
        fixed[(rank - 2) % 4].extend((rank, 13 + rank))
    remainder = iter(range(26, 52))
    for hand in fixed:
        hand.extend(next(remainder) for _ in range(12 - len(hand)))
    raw = {seat: {"predeal": hand_text(tuple(hand))} for seat, hand in zip("NESW", fixed)}
    fixed_masks = [sum(1 << c for c in hand) for hand in fixed]
    masks = [
        tuple(mask | (1 << card) for mask, card in zip(fixed_masks, allocation))
        for allocation in itertools.permutations((0, 1, 13, 14))
    ]
    solver = DDSSolver(threads=4)
    outcomes = solver.success(masks, declarer=0, strains=STRAINS, level=3)
    expected = [sum(row[i] for row in outcomes) for i in range(5)]
    assert expected[0] == expected[1]
    result = query_success(raw, level=3, budget_seconds=3, seed=20261020)
    assert result["status"] == "exact" and result["samples"] == 24
    assert result["strain_representatives"]["H"] == "S"
    assert len(result["solved_strains"]) < 5
    assert [item["successes"] for item in result["results"]] == expected


def test_suit_permutation_preserves_dd_outcome_on_relabelled_deals():
    # Directly check individual outcomes of distinct, relabeled physical deals
    # rather than relying on an equality of Monte Carlo estimates.
    from random import Random

    rng = Random(20261020)
    masks, relabelled = [], []
    for _ in range(8):
        cards = list(range(52))
        rng.shuffle(cards)
        hands = [cards[i * 13 : i * 13 + 13] for i in range(4)]
        masks.append(tuple(sum(1 << c for c in hand) for hand in hands))
        relabelled.append(
            tuple(
                sum(1 << ((1 - c // 13) * 13 + c % 13 if c // 13 < 2 else c) for c in hand)
                for hand in hands
            )
        )
    solver = DDSSolver(threads=4)
    for declarer in range(4):
        for level in (1, 3, 6):
            original = solver.success(masks, declarer=declarer, strains=("S",), level=level)
            transformed = solver.success(relabelled, declarer=declarer, strains=("H",), level=level)
            assert original == transformed


def test_symmetry_query_reduces_work_without_multiplying_sample_size():
    result = query_success({}, max_samples=32, min_samples=1, budget_seconds=3, seed=20261020)
    assert result["status"] == "estimated" and result["samples"] == 32
    assert result["solved_strains"] == ["S", "NT"]
    assert result["dds_positions"] == 64
    assert not result["same_deals_across_strains"]
    assert len(result["results"]) == 5
    assert len({item["successes"] for item in result["results"][:4]}) == 1


def test_symmetry_can_be_disabled_for_identical_physical_deals():
    result = query_success(
        {}, max_samples=32, min_samples=1, budget_seconds=3, seed=20261020, suit_symmetry=False
    )
    assert result["samples"] == 32
    assert result["solved_strains"] == list(STRAINS)
    assert result["dds_positions"] == 160 and result["same_deals_across_strains"]


def test_cli_can_disable_symmetry():
    root = Path(__file__).resolve().parents[1]
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_dealer",
            "success",
            "examples/two_unknown_cards.json",
            "--no-suit-symmetry",
            "--budget",
            "3",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["same_deals_across_strains"] and len(result["solved_strains"]) == 5


def test_invariant_predeal_check_uses_ranks_not_just_suit_lengths():
    raw = {"N": {"predeal": "A.K.."}}
    assert len(parse_hand(raw["N"]["predeal"])) == 2
    actual = strain_representatives(Conditions.from_dict(raw), STRAINS)
    assert actual == {"S": "S", "H": "H", "D": "D", "C": "D", "NT": "NT"}
