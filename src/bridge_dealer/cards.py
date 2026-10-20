"""Immutable full deals; seats NESW and suits SHDC are always labelled."""

from dataclasses import dataclass

SEATS = "NESW"
SUITS = "SHDC"
RANKS = "AKQJT98765432"
HCP = tuple(max(4 - card % 13, 0) for card in range(52))
Shape = tuple[int, int, int, int]


def seat_index(seat: str) -> int:
    if not isinstance(seat, str) or len(seat) != 1 or seat.upper() not in SEATS:
        raise ValueError(f"Invalid seat {seat!r}; expected N, E, S or W")
    return SEATS.index(seat.upper())


def parse_hand(text: str) -> tuple[int, ...]:
    """Read a possibly partial PBN hand, e.g. 'AKQ.JT9..432'."""
    if not isinstance(text, str):
        raise ValueError("A predealt hand must be a PBN hand string")
    holdings = text.upper().split(".")
    if len(holdings) != 4:
        raise ValueError("A hand must have four dot-separated suits in SHDC order")
    cards = []
    for suit, holding in enumerate(holdings):
        if holding == "-":
            holding = ""
        for rank in holding:
            if rank not in RANKS:
                raise ValueError(f"Invalid rank {rank!r}; use AKQJT98765432")
            cards.append(suit * 13 + RANKS.index(rank))
    if len(set(cards)) != len(cards):
        raise ValueError("The same card occurs twice in a hand")
    if len(cards) > 13:
        raise ValueError("A hand cannot contain more than 13 cards")
    return tuple(sorted(cards))


def hand_text(cards: tuple[int, ...]) -> str:
    return ".".join(
        "".join(RANKS[card % 13] for card in sorted(cards) if card // 13 == suit)
        for suit in range(4)
    )


def hand_shape(cards: tuple[int, ...]) -> Shape:
    lengths = [0, 0, 0, 0]
    for card in cards:
        lengths[card // 13] += 1
    return tuple(lengths)


@dataclass(frozen=True)
class Deal:
    """A complete, validated 52-card deal in NESW order."""

    hands: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        if len(self.hands) != 4 or any(len(hand) != 13 for hand in self.hands):
            raise ValueError("A complete deal must contain four 13-card hands")
        flat = [card for hand in self.hands for card in hand]
        if any(type(card) is not int for card in flat) or set(flat) != set(range(52)):
            raise ValueError("A deal must contain each of the 52 cards exactly once")
        object.__setattr__(self, "hands", tuple(tuple(sorted(h)) for h in self.hands))

    def hand(self, seat: str) -> tuple[int, ...]:
        return self.hands[seat_index(seat)]

    def shape(self, seat: str) -> Shape:
        return hand_shape(self.hand(seat))

    def hcp(self, seat: str) -> int:
        return sum(HCP[card] for card in self.hand(seat))

    def to_pbn(self) -> str:
        return "N:" + " ".join(hand_text(hand) for hand in self.hands)

    @classmethod
    def from_pbn(cls, text: str) -> "Deal":
        if not isinstance(text, str):
            raise ValueError("PBN deal must be a string")
        start, sep, rest = text.strip().partition(":")
        if not sep:
            raise ValueError("PBN deal must start with a seat and colon, e.g. N:")
        offset = seat_index(start)
        parts = rest.split()
        if len(parts) != 4:
            raise ValueError("PBN deal must contain exactly four hands")
        hands = [()] * 4
        for i, part in enumerate(parts):
            hands[(offset + i) % 4] = parse_hand(part)
        return cls(tuple(hands))
