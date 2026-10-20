"""Strict, backend-independent conditions for one to four bridge hands."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import permutations

from .cards import HCP, SEATS, Deal, Shape, hand_shape, hand_text, parse_hand, seat_index


def _shape(value: object) -> Shape:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("A shape must be four integers in SHDC order")
    if any(type(n) is not int or not 0 <= n <= 13 for n in value) or sum(value) != 13:
        raise ValueError("Shape lengths must be integers in 0..13 adding up to 13")
    return tuple(value)


def _hcp(value: object) -> tuple[int, int]:
    if type(value) is int:
        value = (value, value)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("hcp must be an integer or an inclusive [minimum, maximum] pair")
    lo, hi = value
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 37:
        raise ValueError("HCP bounds must be integers satisfying 0 <= minimum <= maximum <= 37")
    return lo, hi


@dataclass(frozen=True)
class HandCondition:
    shapes: tuple[Shape, ...] | None = None
    hcp: tuple[int, int] = (0, 37)
    predeal: tuple[int, ...] = ()
    exclude: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hcp", _hcp(self.hcp))
        if self.shapes is not None:
            if not self.shapes:
                raise ValueError("shapes cannot be empty")
            object.__setattr__(self, "shapes", tuple(sorted({_shape(s) for s in self.shapes})))
        cards = tuple(self.predeal)
        if (
            len(cards) > 13
            or any(type(c) is not int or not 0 <= c < 52 for c in cards)
            or len(set(cards)) != len(cards)
        ):
            raise ValueError("predeal must contain at most 13 distinct valid card IDs")
        object.__setattr__(self, "predeal", tuple(sorted(cards)))
        excluded = tuple(self.exclude)
        if any(type(c) is not int or not 0 <= c < 52 for c in excluded) or len(
            set(excluded)
        ) != len(excluded):
            raise ValueError("exclude must contain distinct valid card IDs")
        if set(cards) & set(excluded):
            raise ValueError("A seat cannot both hold and exclude the same card")
        object.__setattr__(self, "exclude", tuple(sorted(excluded)))

    @classmethod
    def from_dict(cls, raw: Mapping) -> "HandCondition":
        if not isinstance(raw, Mapping):
            raise ValueError("Each seat's condition must be an object")
        unknown = set(raw) - {"shape", "shapes", "shape_any", "hcp", "predeal", "exclude"}
        if unknown:
            raise ValueError(f"Unknown condition fields: {sorted(unknown, key=str)!r}")
        shape_keys = set(raw) & {"shape", "shapes", "shape_any"}
        if len(shape_keys) > 1:
            raise ValueError("Use only one of shape, shapes and shape_any for each seat")
        shapes = None
        if "shape" in raw:
            shapes = (_shape(raw["shape"]),)
        elif "shape_any" in raw:
            shapes = tuple(sorted(set(permutations(_shape(raw["shape_any"])))))
        elif "shapes" in raw:
            if not isinstance(raw["shapes"], (list, tuple)) or not raw["shapes"]:
                raise ValueError("shapes must be a nonempty list of shapes")
            shapes = tuple(_shape(s) for s in raw["shapes"])
        excluded = raw.get("exclude", "...")
        if not isinstance(excluded, str) or len(excluded.split(".")) != 4:
            raise ValueError("exclude must be a dot-separated SHDC card string")
        # Each suit may contain 13 excluded cards; the combined exclusion can exceed 13.
        excluded_cards = tuple(
            c + 13 * suit
            for suit, holding in enumerate(excluded.split("."))
            for c in parse_hand(holding + "...")
        )
        return cls(
            shapes,
            _hcp(raw.get("hcp", (0, 37))),
            parse_hand(raw.get("predeal", "...")),
            excluded_cards,
        )

    def matches(self, hand: tuple[int, ...]) -> bool:
        return (
            (self.shapes is None or hand_shape(hand) in self.shapes)
            and self.hcp[0] <= sum(HCP[c] for c in hand) <= self.hcp[1]
            and set(self.predeal).issubset(hand)
            and not set(self.exclude).intersection(hand)
        )


@dataclass(frozen=True)
class Conditions:
    hands: tuple[HandCondition, ...] = (HandCondition(),) * 4

    def __post_init__(self) -> None:
        if len(self.hands) != 4 or any(not isinstance(h, HandCondition) for h in self.hands):
            raise ValueError("Conditions must contain four HandCondition objects in NESW order")
        cards = [card for hand in self.hands for card in hand.predeal]
        if len(cards) != len(set(cards)):
            raise ValueError("A predealt card cannot belong to more than one seat")
        object.__setattr__(self, "hands", tuple(self.hands))

    @classmethod
    def from_dict(cls, raw: Mapping) -> "Conditions":
        if not isinstance(raw, Mapping):
            raise ValueError("Conditions must be an object keyed by N, E, S and W")
        hands = [HandCondition()] * 4
        seen = set()
        for seat, value in raw.items():
            index = seat_index(seat)
            if index in seen:
                raise ValueError(f"Repeated seat {seat!r}")
            seen.add(index)
            hands[index] = HandCondition.from_dict(value)
        return cls(tuple(hands))

    def matches(self, deal: Deal) -> bool:
        return all(c.matches(hand) for c, hand in zip(self.hands, deal.hands))

    def predeal_pbn(self) -> str:
        return "N:" + " ".join(hand_text(h.predeal) for h in self.hands)

    def to_dict(self) -> dict:
        result = {}
        for seat, hand in zip(SEATS, self.hands):
            value = {}
            if hand.shapes is not None:
                value["shapes"] = [list(s) for s in hand.shapes]
            if hand.hcp != (0, 37):
                value["hcp"] = list(hand.hcp)
            if hand.predeal:
                value["predeal"] = hand_text(hand.predeal)
            if hand.exclude:
                value["exclude"] = hand_text(hand.exclude)
            if value:
                result[seat] = value
        return result

    @property
    def fingerprint(self) -> str:
        data = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()


def as_conditions(value: Conditions | Mapping) -> Conditions:
    return value if isinstance(value, Conditions) else Conditions.from_dict(value)
