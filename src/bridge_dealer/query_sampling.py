"""Uniform proposals for deadline queries, followed by whole-deal rejection."""

import math
import random
from bisect import bisect_right
from itertools import product
from time import perf_counter

from .cards import HCP, hand_shape
from .conditions import Conditions
from .v2 import compile_sampler


def matches_masks(conditions, masks):
    for condition, mask in zip(conditions.hands, masks):
        if any(mask >> c & 1 for c in condition.exclude):
            return False
        if condition.shapes is not None:
            shape = tuple((mask >> (13 * s) & 8191).bit_count() for s in range(4))
            if shape not in condition.shapes:
                return False
        if condition.hcp != (0, 37):
            points = sum(
                HCP[c] for s in range(4) for c in range(s * 13, s * 13 + 4) if mask >> c & 1
            )
            if not condition.hcp[0] <= points <= condition.hcp[1]:
                return False
    return True


class Proposals:
    def __init__(self, conditions, *, seed, compile_deadline):
        self.conditions, self.rng = conditions, random.Random(seed)
        self.raw = conditions.to_dict()
        predeals = {p: {"predeal": h["predeal"]} for p, h in self.raw.items() if "predeal" in h}
        self.base = compile_sampler(predeals)
        self.strategy = "uniform_predeal_rejection"
        self.tried, self.accepted = 0, 0
        self.matrices, self.cdf = [], []
        self.exact_deals = None
        self.population_size = None
        self.proposal_population = self.base.total_deals
        # Enumerating a tiny complete proposal space also handles HCP exactly.
        if self.base.total_deals <= 32:
            self.exact_deals = [
                m for m in self.base.enumerate_masks() if matches_masks(conditions, m)
            ]
            self.population_size = len(self.exact_deals)
            self.strategy = "small_space_enumeration"
            return
        self.active = tuple(
            p for p, h in enumerate(conditions.hands) if h.shapes is not None and self.base.slots[p]
        )
        for p, h in enumerate(conditions.hands):
            if not self.base.slots[p] and not h.matches(h.predeal):
                self.exact_deals, self.population_size = [], 0
                return
        if not self.active:
            point_seats = tuple(
                p for p, h in enumerate(conditions.hands) if h.hcp != (0, 37) and self.base.slots[p]
            )
            if point_seats:
                selected = sorted(
                    point_seats,
                    key=lambda p: (
                        conditions.hands[p].hcp[1] - conditions.hands[p].hcp[0],
                        -abs(sum(conditions.hands[p].hcp) / 2 - 10),
                        p,
                    ),
                )[:2]
                if self._compile_points(tuple(selected), compile_deadline):
                    if (
                        len(point_seats) > 2 or any(h.exclude for h in conditions.hands)
                    ) and self.exact_deals is None:
                        self.population_size = None
                    return
            self.stream = self.base.sample_masks(count=2**63 - 1, seed=seed)
            return
        self.inactive = tuple(p for p in range(4) if p not in self.active)
        alternative = None
        if any(h.hcp != (0, 37) and self.base.slots[p] for p, h in enumerate(conditions.hands)):
            point_raw = {
                p: {k: v for k, v in hand.items() if k != "shapes"} for p, hand in self.raw.items()
            }
            alternative = Proposals(
                Conditions.from_dict(point_raw),
                seed=seed,
                compile_deadline=min(compile_deadline, perf_counter() + 0.25),
            )
        fixed_lengths = tuple(hand_shape(h.predeal) for h in conditions.hands)
        pools = tuple(
            tuple(b for b in self.base.pool if (b.bit_length() - 1) // 13 == s) for s in range(4)
        )
        domains = [
            tuple(
                row
                for row in conditions.hands[p].shapes
                if all(row[s] >= fixed_lengths[p][s] for s in range(4))
            )
            for p in self.active
        ]
        if math.prod(map(len, domains)) > 200_000:
            self.stream = self.base.sample_masks(count=2**63 - 1, seed=seed)
            self._prefer_points(alternative)
            return
        timed_out = False
        for rows in product(*domains):
            if perf_counter() >= compile_deadline:
                timed_out = True
                break
            groups, weight = [], 1
            for s, pool in enumerate(pools):
                counts = tuple(row[s] - fixed_lengths[p][s] for p, row in zip(self.active, rows))
                rest = len(pool) - sum(counts)
                if rest < 0:
                    break
                weight *= math.factorial(len(pool)) // (
                    math.factorial(rest) * math.prod(math.factorial(n) for n in counts)
                )
                groups.append((pool, counts))
            else:
                self.matrices.append(tuple(groups))
                self.cdf.append((self.cdf[-1] if self.cdf else 0) + weight)
        if timed_out:
            self.matrices.clear()
            self.cdf.clear()
            self.stream = self.base.sample_masks(count=2**63 - 1, seed=seed)
            self._prefer_points(alternative)
        elif not self.cdf:
            self.exact_deals, self.population_size = [], 0
        else:
            self.strategy = "joint_shape_rejection"
            free_pool = sum(self.base.slots[p] for p in self.inactive)
            self.proposal_population = (
                self.cdf[-1]
                * math.factorial(free_pool)
                // math.prod(math.factorial(self.base.slots[p]) for p in self.inactive)
            )
            self._prefer_points(alternative)

    def _prefer_points(self, alternative):
        if (
            alternative is not None
            and alternative.cdf
            and alternative.proposal_population < self.proposal_population
        ):
            self.active, self.inactive = alternative.active, alternative.inactive
            self.matrices, self.cdf = alternative.matrices, alternative.cdf
            self.proposal_population = alternative.proposal_population
            self.strategy = "hcp_point_groups_rejection"
            self.population_size = None

    def _compile_points(self, active, deadline):
        """Count concrete assignments by point category, not by suit lengths."""
        self.active = active
        self.inactive = tuple(p for p in range(4) if p not in active)
        values = (4, 3, 2, 1, 0)
        pools = tuple(
            tuple(bit for bit in self.base.pool if HCP[bit.bit_length() - 1] == value)
            for value in values
        )
        choices = []
        for pool in pools[:4]:
            choices.append(
                tuple(
                    counts
                    for counts in product(range(len(pool) + 1), repeat=len(active))
                    if sum(counts) <= len(pool)
                )
            )
        fixed_points = tuple(sum(HCP[c] for c in self.conditions.hands[p].predeal) for p in active)
        for allocation in product(*choices):
            if perf_counter() >= deadline:
                self.matrices.clear()
                self.cdf.clear()
                return False
            points = tuple(
                fixed_points[i]
                + sum(value * counts[i] for value, counts in zip(values, allocation))
                for i in range(len(active))
            )
            if any(
                not self.conditions.hands[p].hcp[0] <= points[i] <= self.conditions.hands[p].hcp[1]
                for i, p in enumerate(active)
            ):
                continue
            zeros = tuple(
                self.base.slots[p] - sum(counts[i] for counts in allocation)
                for i, p in enumerate(active)
            )
            if min(zeros) < 0 or sum(zeros) > len(pools[4]):
                continue
            groups, weight = [], 1
            for pool, counts in zip(pools, (*allocation, zeros)):
                rest = len(pool) - sum(counts)
                weight *= math.factorial(len(pool)) // (
                    math.factorial(rest) * math.prod(math.factorial(n) for n in counts)
                )
                groups.append((pool, counts))
            self.matrices.append(tuple(groups))
            self.cdf.append((self.cdf[-1] if self.cdf else 0) + weight)
        self.strategy = "hcp_point_groups"
        if not self.cdf:
            self.exact_deals, self.population_size = [], 0
        else:
            free_pool = sum(self.base.slots[p] for p in self.inactive)
            self.population_size = (
                self.cdf[-1]
                * math.factorial(free_pool)
                // math.prod(math.factorial(self.base.slots[p]) for p in self.inactive)
            )
            self.proposal_population = self.population_size
        return True

    def next(self):
        """One proposal attempt, never an unbounded acceptance loop."""
        self.tried += 1
        if self.cdf:
            groups = self.matrices[bisect_right(self.cdf, self.rng.randrange(self.cdf[-1]))]
            masks, remainder = list(self.base.fixed_masks), []
            for cards, counts in groups:
                pool = list(cards)
                self.rng.shuffle(pool)
                offset = 0
                for p, size in zip(self.active, counts):
                    masks[p] |= sum(pool[offset : offset + size])
                    offset += size
                remainder.extend(pool[offset:])
            self.rng.shuffle(remainder)
            offset = 0
            for p in self.inactive:
                size = self.base.slots[p]
                masks[p] |= sum(remainder[offset : offset + size])
                offset += size
            masks = tuple(masks)
        else:
            masks = next(self.stream)
        if matches_masks(self.conditions, masks):
            self.accepted += 1
            return masks
        return None
