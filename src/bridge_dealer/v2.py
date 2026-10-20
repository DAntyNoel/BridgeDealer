"""Experimental exact sampler/enumerator for predeals and suit lengths.

Shape-constrained compilation currently requires at least one complete known
hand. Nontrivial HCP constraints on incomplete hands remain on the v1 backend.
Every compiled shape matrix is weighted by its number of concrete completions.
"""

import math
import random
from bisect import bisect_right
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from itertools import combinations
from time import perf_counter

from .cards import HCP, Deal, hand_shape
from .conditions import Conditions, as_conditions
from .enumeration import EnumerationResult
from .sampling import InfeasibleConditionsError

ALL_CARDS = (1 << 52) - 1
FACTORIAL = tuple(math.factorial(n) for n in range(53))
ALL_SHAPES = tuple(
    (s, h, d, 13 - s - h - d) for s in range(14) for h in range(14 - s) for d in range(14 - s - h)
)
Masks = tuple[int, int, int, int]


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _bits(mask):
    while mask:
        bit = mask & -mask
        yield bit
        mask ^= bit


def deal_from_masks(masks: Masks) -> Deal:
    """Convert the compact experimental representation into a validated Deal."""
    return Deal(tuple(tuple(bit.bit_length() - 1 for bit in _bits(mask)) for mask in masks))


def _partitions(pool, groups):
    """Yield labelled group masks, without permuting cards within a hand."""
    if len(groups) == 1:
        yield ((groups[0][0], sum(pool)),)
        return
    seat, size = groups[0]
    for selected in combinations(pool, size):
        mask = sum(selected)
        remaining = tuple(bit for bit in pool if not bit & mask)
        for tail in _partitions(remaining, groups[1:]):
            yield ((seat, mask),) + tail


@dataclass(frozen=True)
class _Matrix:
    weight: int
    forced: Masks
    # Each entry holds concrete free-card bits and (seat, required count) pairs.
    groups: tuple


class SamplerPlan:
    """An exact, reusable distribution; RNG state belongs to each sample call."""

    def __init__(self, conditions, *, max_matrices=100_000):
        _integer(max_matrices, "max_matrices", 1)
        self.conditions = as_conditions(conditions)
        hands = self.conditions.hands
        if any(h.exclude for h in hands):
            raise ValueError("v2 compiled sampling does not support excluded cards; use v1")
        self.fixed_masks = tuple(sum(1 << c for c in h.predeal) for h in hands)
        self.free_mask = ALL_CARDS ^ sum(self.fixed_masks)
        self.pool = tuple(_bits(self.free_mask))
        self.slots = tuple(13 - len(h.predeal) for h in hands)
        self.open_seats = tuple(p for p, slots in enumerate(self.slots) if slots)
        self.total_deals = 0
        self.matrices = ()
        self.cdf = ()
        self.strategy = "predeal_partition"
        self.compile_seconds = 0.0
        start = perf_counter()
        try:
            self._compile(max_matrices)
        finally:
            self.compile_seconds = perf_counter() - start

    def _compile(self, max_matrices):
        hands = self.conditions.hands
        fixed_lengths = tuple(hand_shape(h.predeal) for h in hands)
        for p, hand in enumerate(hands):
            points = sum(HCP[c] for c in hand.predeal)
            if not self.slots[p]:
                if not hand.matches(hand.predeal):
                    return
            elif hand.hcp != (0, 37):
                # An already-guaranteed range needs no point-state DP.
                remaining_points = sum(HCP[b.bit_length() - 1] for b in self.pool)
                if not hand.hcp[0] <= points <= points + remaining_points <= hand.hcp[1]:
                    raise ValueError(
                        "v2 experiment does not support HCP restrictions on incomplete hands; "
                        "use --backend v1"
                    )
        shaped = any(h.shapes is not None and self.slots[p] for p, h in enumerate(hands))
        if not shaped:
            self.total_deals = FACTORIAL[len(self.pool)] // math.prod(
                FACTORIAL[n] for n in self.slots
            )
            return
        self.strategy = "shape_multinomial"
        if all(self.slots):
            raise ValueError("v2 shape sampling currently requires one complete predealt hand")
        free_suits = tuple(
            tuple(bit for bit in self.pool if (bit.bit_length() - 1) // 13 == s) for s in range(4)
        )
        domains = []
        for p, hand in enumerate(hands):
            candidates = hand.shapes if hand.shapes is not None else ALL_SHAPES
            domains.append(
                tuple(
                    shape
                    for shape in candidates
                    if all(
                        fixed_lengths[p][s] <= shape[s] <= fixed_lengths[p][s] + len(free_suits[s])
                        for s in range(4)
                    )
                )
            )
        if any(not rows for rows in domains):
            return
        order = sorted(range(4), key=lambda p: (len(domains[p]), p))
        last = order[-1]
        last_domain = set(domains[last])
        rows = [None] * 4
        matrices, cumulative = [], []

        def visit(i, residual):
            if i == 3:
                if residual not in last_domain:
                    return
                rows[last] = residual
                weight = 1
                forced = list(self.fixed_masks)
                groups = []
                for s, pool in enumerate(free_suits):
                    counts = tuple(rows[p][s] - fixed_lengths[p][s] for p in range(4))
                    weight *= FACTORIAL[len(pool)] // math.prod(FACTORIAL[n] for n in counts)
                    owners = tuple((p, n) for p, n in enumerate(counts) if n)
                    if len(owners) == 1:
                        forced[owners[0][0]] |= sum(pool)
                    elif owners:
                        groups.append((pool, owners))
                if len(matrices) >= max_matrices:
                    raise ValueError(
                        "v2 matrix budget exceeded; no partial distribution was compiled"
                    )
                matrices.append(_Matrix(weight, tuple(forced), tuple(groups)))
                cumulative.append((cumulative[-1] if cumulative else 0) + weight)
                return
            p = order[i]
            for shape in domains[p]:
                following = tuple(x - y for x, y in zip(residual, shape))
                if min(following) < 0:
                    continue
                # Fixed cards in the remaining seats must still fit.
                if any(
                    following[s] < sum(fixed_lengths[q][s] for q in order[i + 1 :])
                    for s in range(4)
                ):
                    continue
                rows[p] = shape
                visit(i + 1, following)

        visit(0, (13, 13, 13, 13))
        self.matrices = tuple(matrices)
        self.cdf = tuple(cumulative)
        self.total_deals = cumulative[-1] if cumulative else 0

    def sample_masks(self, *, count: int, seed: int) -> Iterator[Masks]:
        """Stream compact complete deals uniformly, with replacement."""
        _integer(count, "count")
        _integer(seed, "seed")
        if seed >= 2**32:
            raise ValueError("seed must be in 0..4294967295")
        if not count:
            return
        if not self.total_deals:
            raise InfeasibleConditionsError("No deal satisfies these predeal/shape conditions")
        rng = random.Random(seed)
        for _ in range(count):
            if self.strategy == "predeal_partition":
                masks = list(self.fixed_masks)
                pool = list(self.pool)
                rng.shuffle(pool)
                offset = 0
                for p in self.open_seats:
                    end = offset + self.slots[p]
                    masks[p] |= sum(pool[offset:end])
                    offset = end
            else:
                matrix = self.matrices[bisect_right(self.cdf, rng.randrange(self.total_deals))]
                masks = list(matrix.forced)
                for cards, groups in matrix.groups:
                    pool = list(cards)
                    rng.shuffle(pool)
                    offset = 0
                    for p, size in groups:
                        end = offset + size
                        masks[p] |= sum(pool[offset:end])
                        offset = end
            yield tuple(masks)

    def sample_deals(self, *, count: int, seed: int) -> Iterator[Deal]:
        for masks in self.sample_masks(count=count, seed=seed):
            yield deal_from_masks(masks)

    def sample_batches(self, *, count: int, seed: int, batch_size: int = 4096):
        """Yield tuples of Deals; changing chunk size preserves the full sequence."""
        _integer(batch_size, "batch_size", 1)
        batch = []
        for deal in self.sample_deals(count=count, seed=seed):
            batch.append(deal)
            if len(batch) == batch_size:
                yield tuple(batch)
                batch.clear()
        if batch:
            yield tuple(batch)

    def enumerate_masks(self) -> Iterator[Masks]:
        """Stream each labelled complete deal exactly once, in a stable order."""
        if not self.total_deals:
            return
        if self.strategy == "predeal_partition":
            if len(self.open_seats) == 2:
                # Fixed-two-hands path: C(26, 13), with no inner permutations.
                a, b = self.open_seats
                for selected in combinations(self.pool, self.slots[a]):
                    mask = sum(selected)
                    masks = list(self.fixed_masks)
                    masks[a] |= mask
                    masks[b] |= self.free_mask ^ mask
                    yield tuple(masks)
            elif self.open_seats:
                groups = tuple((p, self.slots[p]) for p in self.open_seats)
                for partition in _partitions(self.pool, groups):
                    masks = list(self.fixed_masks)
                    for p, mask in partition:
                        masks[p] |= mask
                    yield tuple(masks)
            else:
                yield self.fixed_masks
            return

        def traverse(matrix, i, masks):
            if i == len(matrix.groups):
                yield masks
                return
            pool, groups = matrix.groups[i]
            for partition in _partitions(pool, groups):
                following = list(masks)
                for p, mask in partition:
                    following[p] |= mask
                yield from traverse(matrix, i + 1, tuple(following))

        for matrix in self.matrices:
            yield from traverse(matrix, 0, matrix.forced)


def compile_sampler(
    conditions: Conditions | Mapping, *, max_matrices: int = 100_000
) -> SamplerPlan:
    """Compile the entire supported distribution, or fail without sampling a subset."""
    return SamplerPlan(conditions, max_matrices=max_matrices)


def enumerate_deals_v2(
    conditions: Conditions | Mapping,
    on_deal: Callable[[Deal], bool | None],
    *,
    limit: int | None = None,
    time_limit: float | None = None,
) -> EnumerationResult:
    """Callback adapter with the same conservative completion semantics as v1."""
    if not callable(on_deal):
        raise ValueError("on_deal must be callable")
    if limit is not None:
        _integer(limit, "limit", 1)
    if time_limit is not None and (
        type(time_limit) not in (int, float) or not math.isfinite(time_limit) or time_limit <= 0
    ):
        raise ValueError("time_limit must be finite and positive")
    start = perf_counter()
    count = 0

    def result(status, complete, reason):
        return EnumerationResult(count, status, complete, reason, perf_counter() - start)

    try:
        plan = compile_sampler(conditions)
        for masks in plan.enumerate_masks():
            if time_limit is not None and perf_counter() - start >= time_limit:
                return result("FEASIBLE" if count else "UNKNOWN", False, "time_limit")
            keep_going = on_deal(deal_from_masks(masks))
            count += 1
            if keep_going is False:
                return result("FEASIBLE", False, "callback")
            if limit is not None and count >= limit:
                return result("FEASIBLE", False, "limit")
    except KeyboardInterrupt:
        return result("INTERRUPTED", False, "interrupt")
    return result("OPTIMAL" if count else "INFEASIBLE", True, None)
