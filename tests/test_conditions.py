import pytest

from bridge_dealer import Conditions, Deal, HandCondition
from bridge_dealer.cards import HCP, parse_hand


def test_standard_hcp():
    assert sum(HCP) == 40
    assert [sum(HCP[s * 13 : (s + 1) * 13]) for s in range(4)] == [10] * 4
    assert sum(HCP[c] for c in parse_hand("AKQJ.AKQ.AKQ.AKQ")) == 37
    assert HCP[4] == 0  # Ten is not an HCP honour.


@pytest.mark.parametrize("start", "NESW")
def test_pbn_rotation_and_roundtrip(base, start):
    i = "NESW".index(start)
    hands = base.to_pbn()[2:].split()
    rotated = start + ":" + " ".join(hands[i:] + hands[:i])
    assert Deal.from_pbn(rotated) == base
    assert base.to_pbn() == Deal.from_pbn(base.to_pbn()).to_pbn()


def test_normalize_and_freeze():
    raw = {"n": {"shape_any": [4, 3, 3, 3], "hcp": 12}}
    conditions = Conditions.from_dict(raw)
    assert len(conditions.hands[0].shapes) == 4
    raw["n"]["hcp"] = 20
    assert conditions.hands[0].hcp == (12, 12)
    assert conditions == Conditions.from_dict(conditions.to_dict())
    assert conditions.fingerprint == Conditions.from_dict(conditions.to_dict()).fingerprint
    duplicate = HandCondition(shapes=((4, 3, 3, 3), (4, 3, 3, 3)))
    assert duplicate.shapes == ((4, 3, 3, 3),)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"Q": {}},
        {"N": {}, "n": {}},
        {"N": None},
        {"N": {"points": 10}},
        {"N": {"shape": [4, 3, 3, 2]}},
        {"N": {"shape": [True, 4, 4, 4]}},
        {"N": {"shape_any": [4, 3, 3]}},
        {"N": {"shape": [4, 3, 3, 3], "shape_any": [4, 3, 3, 3]}},
        {"N": {"shapes": []}},
        {"N": {"hcp": True}},
        {"N": {"hcp": 38}},
        {"N": {"hcp": [15, 12]}},
        {"N": {"hcp": [-1, 10]}},
        {"N": {"hcp": [10.5, 11]}},
        {"N": {"predeal": "AA..."}},
        {"N": {"predeal": "A..."}, "S": {"predeal": "A..."}},
        {"N": {"predeal": "A.."}},
        {"N": {"predeal": "10..."}},
        {"N": {"predeal": "AKQJT98765432.A.."}},
    ],
)
def test_reject_bad_conditions(raw):
    with pytest.raises(ValueError):
        Conditions.from_dict(raw)


def test_bad_deals(base):
    with pytest.raises(ValueError):
        Deal((base.hands[0],) * 4)
    with pytest.raises(ValueError):
        Deal.from_pbn("N:A... ... ... ...")
    with pytest.raises(ValueError):
        Deal.from_pbn("garbage")
