import os
from itertools import combinations
from pathlib import Path

import pytest

from bridge_dealer import Deal
from bridge_dealer.cards import hand_text

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
BASE = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


@pytest.fixture(scope="session")
def base():
    return Deal.from_pbn(BASE)


@pytest.fixture(scope="session")
def eight_unknown(base):
    # Remove two cards from each complete hand; total possibilities = 8!/(2!)^4.
    return {
        seat: {"predeal": hand_text(tuple(c for i, c in enumerate(hand) if i not in (0, 6)))}
        for seat, hand in zip("NESW", base.hands)
    }


@pytest.fixture(scope="session")
def brute_force(eight_unknown):
    # Independent combination traversal. This never calls the solver or the
    # production condition matcher and visits each allocation once by construction.
    known = []
    for seat in "NESW":
        cards = []
        for s, ranks in enumerate(eight_unknown[seat]["predeal"].split(".")):
            cards.extend(13 * s + "AKQJT98765432".index(r) for r in ranks)
        known.append(cards)
    remaining = set(range(52)) - {c for h in known for c in h}
    result = []
    for north in combinations(sorted(remaining), 2):
        after_n = remaining - set(north)
        for east in combinations(sorted(after_n), 2):
            after_e = after_n - set(east)
            for south in combinations(sorted(after_e), 2):
                west = after_e - set(south)
                result.append(
                    Deal(
                        tuple(
                            tuple(known[p]) + tuple(extra)
                            for p, extra in enumerate((north, east, south, west))
                        )
                    )
                )
    return result


def oracle_matches(raw, deal):
    # Work from suit strings and printed ranks, separately from production HCP
    # tables, card-count code and compiled shape alternatives.
    for i, seat in enumerate("NESW"):
        condition = raw.get(seat, {})
        text = deal.to_pbn()[2:].split()[i]
        suits = text.split(".")
        shape = [len(suit) for suit in suits]
        if "shape" in condition and shape != condition["shape"]:
            return False
        if "shapes" in condition and shape not in condition["shapes"]:
            return False
        if "shape_any" in condition and sorted(shape) != sorted(condition["shape_any"]):
            return False
        points = sum(text.count(rank) * n for rank, n in zip("AKQJ", (4, 3, 2, 1)))
        bounds = condition.get("hcp", [0, 37])
        lo, hi = (bounds, bounds) if isinstance(bounds, int) else bounds
        if not lo <= points <= hi:
            return False
    return True
