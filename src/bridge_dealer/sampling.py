"""Seeded rejection sampling using endplay, with no hand swapping or repair."""

from collections.abc import Iterator, Mapping

from .cards import SEATS, Deal
from .conditions import Conditions, as_conditions
from .enumeration import check_conditions


class InfeasibleConditionsError(ValueError):
    """CP-SAT proved that no complete deal satisfies the conditions."""


class SamplingExhaustedError(RuntimeError):
    """The shuffle budget was exhausted; this is not proof of impossibility."""

    def __init__(self, produced: int, requested: int, max_attempts: int):
        self.produced = produced
        self.requested = requested
        self.max_attempts = max_attempts
        super().__init__(
            f"Produced {produced}/{requested} deals after {max_attempts} shuffles; "
            "the attempt limit does not prove the conditions impossible"
        )


def random_deals(
    conditions: Conditions | Mapping,
    *,
    seed: int,
    count: int = 1,
    max_attempts: int = 1_000_000,
) -> Iterator[Deal]:
    """Yield conditionally uniform samples, with replacement, using endplay.

    ``seed`` is an unsigned 32-bit integer. Reproducibility requires the same
    conditions and locked dependency versions. A bounded feasibility check runs
    before sampling; if it times out, sampling still proceeds within its budget.
    """
    conditions = as_conditions(conditions)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in 0..4294967295")
    if type(count) is not int or count < 0:
        raise ValueError("count must be a nonnegative integer")
    if type(max_attempts) is not int or max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")
    if not count:
        return
    if check_conditions(conditions) == "infeasible":
        raise InfeasibleConditionsError("No deal satisfies these conditions (proved by CP-SAT)")

    # Import lazily: enumeration and condition parsing need no native DDS/plotting
    # libraries bundled with endplay. Sampling uses its public generator unchanged.
    from endplay.dealer import generate_deals
    from endplay.dealer.generate import DealNotGeneratedError
    from endplay.evaluate import hcp
    from endplay.types import Deal as EndplayDeal
    from endplay.types import Denom

    active = [
        (name, hand)
        for name, hand in zip(("north", "east", "south", "west"), conditions.hands)
        if hand.shapes is not None or hand.hcp != (0, 37)
    ]
    suits = tuple(Denom.suits())

    def accept(deal):
        for name, condition in active:
            hand = getattr(deal, name)
            if condition.shapes is not None:
                shape = tuple(len(hand[suit]) for suit in suits)
                if shape not in condition.shapes:
                    return False
            if condition.hcp != (0, 37) and not condition.hcp[0] <= hcp(hand) <= condition.hcp[1]:
                return False
        return True

    generated = generate_deals(
        accept,
        predeal=EndplayDeal(conditions.predeal_pbn()),
        swapping=0,
        produce=count,
        seed=seed,
        max_attempts=max_attempts,
        strict=True,
    )
    produced = 0
    try:
        for raw in generated:
            deal = Deal.from_pbn(raw.to_pbn())
            # Check the shared representation, including all fixed cards, before
            # letting an adapter/version mismatch silently emit an invalid deal.
            if not conditions.matches(deal):
                raise RuntimeError(f"endplay returned a deal violating conditions for {SEATS}")
            produced += 1
            yield deal
    except DealNotGeneratedError as error:
        raise SamplingExhaustedError(produced, count, max_attempts) from error
    finally:
        generated.close()
