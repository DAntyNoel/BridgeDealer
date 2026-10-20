"""Research probe: exact 100k sampling with one/two constrained seats.

This is not a production backend. The probe requires explicit allowed shapes on
each constrained seat; the design's HCP-only specialization is not implemented.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import platform
import random
import sys
import time
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from bridge_dealer import Conditions, Deal, enumerate_deals
from bridge_dealer.cards import hand_text
from bridge_dealer.sampling import SamplingExhaustedError, random_deals

ALL_CARDS = (1 << 52) - 1
FACT = tuple(math.factorial(n) for n in range(53))
VALUES = (4, 3, 2, 1)


def cumulative(weights):
    result = []
    total = 0
    for weight in weights:
        total += weight
        result.append(total)
    return tuple(result)


def choose(rng, cdf):
    return bisect_right(cdf, rng.randrange(cdf[-1]))


@lru_cache(None)
def zero_table(groups, size):
    """Buckets of all group assignments, stored as uint16 position bitmasks."""
    codes = np.arange(groups ** size, dtype=np.uint32)
    masks = np.zeros((len(codes), groups), dtype=np.uint16)
    counts = np.zeros((len(codes), groups), dtype=np.uint8)
    for i in range(size):
        owners = codes // (groups ** i) % groups
        for p in range(groups):
            yes = (owners == p).astype(np.uint16)
            masks[:, p] |= yes << i
            counts[:, p] += yes.astype(np.uint8)
    keys = np.zeros(len(codes), dtype=np.uint32)
    for p in range(groups - 1):
        keys += counts[:, p].astype(np.uint32) * (size + 1) ** p
    order = np.argsort(keys, kind="stable")
    keys = keys[order]
    masks = masks[order]
    counts = counts[order]
    starts = np.r_[0, np.flatnonzero(keys[1:] != keys[:-1]) + 1]
    ends = np.r_[starts[1:], len(keys)]
    buckets = {}
    for start, end in zip(starts, ends):
        r = tuple(int(x) for x in counts[start])
        assert end - start == FACT[size] // math.prod(FACT[x] for x in r)
        buckets[r] = masks[start:end]
    return buckets, masks.nbytes


@dataclass(frozen=True)
class Option:
    q: tuple[int, ...]
    r: tuple[int, ...]
    weight: int
    honor_masks: tuple[int, ...]


class Matrix:
    def __init__(self, plan, rows):
        self.plan = plan
        self.rows = rows
        self.options = [
            plan.local_options(s, tuple(row[s] for row in rows))
            for s in range(4)
        ]
        self.memo = {}
        self.draws = {}
        self.total = 0
        self.shape_weight = 0
        if any(not opts for opts in self.options):
            return
        self.shape_weight = math.prod(sum(o.weight for o in opts) for opts in self.options)
        self.kernels = []
        for opts in self.options:
            kernel = {}
            for o in opts:
                kernel[o.q] = kernel.get(o.q, 0) + o.weight
            self.kernels.append(tuple(sorted(kernel.items())))
        k = len(plan.active)
        self.min_suffix = [[0] * k for _ in range(5)]
        self.max_suffix = [[0] * k for _ in range(5)]
        for s in reversed(range(4)):
            for p in range(k):
                self.min_suffix[s][p] = self.min_suffix[s+1][p] + min(o.q[p] for o in self.options[s])
                self.max_suffix[s][p] = self.max_suffix[s+1][p] + max(o.q[p] for o in self.options[s])
        self.total = self.count(0, (0,) * k)
        # Compile all nonzero reachable transitions once, before batch sampling.
        for (s, t), count in list(self.memo.items()):
            if not count or s == 4:
                continue
            entries, weights = [], []
            for o in self.options[s]:
                following = tuple(x+y for x, y in zip(t, o.q))
                w = o.weight * self.count(s+1, following)
                if w:
                    entries.append((o, following))
                    weights.append(w)
            assert sum(weights) == count
            self.draws[s, t] = (tuple(entries), cumulative(weights))
        self.local_cdfs = [cumulative(o.weight for o in opts) for opts in self.options]

    def count(self, s, t):
        key = (s, t)
        if key in self.memo:
            return self.memo[key]
        if any(t[p] + self.min_suffix[s][p] > self.plan.high[p] or
               t[p] + self.max_suffix[s][p] < self.plan.low[p]
               for p in range(len(t))):
            value = 0
        elif s == 4:
            value = 1
        else:
            value = sum(weight * self.count(s+1, tuple(x+y for x, y in zip(t, q)))
                        for q, weight in self.kernels[s])
        self.memo[key] = value
        return value


class Plan:
    def __init__(self, raw):
        self.conditions = Conditions.from_dict(raw)
        self.active = tuple(p for p, h in enumerate(self.conditions.hands)
                            if h.shapes is not None or h.hcp != (0, 37))
        if len(self.active) not in (1, 2):
            raise ValueError("Probe requires one or two constrained seats")
        if any(self.conditions.hands[p].shapes is None for p in self.active):
            raise ValueError("Probe requires explicit allowed shapes for active seats")
        self.inactive = tuple(p for p in range(4) if p not in self.active)
        self.low = tuple(self.conditions.hands[p].hcp[0] for p in self.active)
        self.high = tuple(self.conditions.hands[p].hcp[1] for p in self.active)
        self.fixed_masks = tuple(sum(1 << c for c in h.predeal) for h in self.conditions.hands)
        self.fixed_union = 0
        self.known_owner = {}
        for p, h in enumerate(self.conditions.hands):
            self.fixed_union |= self.fixed_masks[p]
            self.known_owner.update((c, p) for c in h.predeal)
        self.free_slots = tuple(13 - len(h.predeal) for h in self.conditions.hands)
        self.free_zeros = []
        self.expansions = []
        self.tables = []
        self.table_bytes = 0
        seen_sizes = set()
        for s in range(4):
            free = tuple(s*13+r for r in range(4, 13) if s*13+r not in self.known_owner)
            self.free_zeros.append(free)
            self.expansions.append(tuple(sum(1 << card for i, card in enumerate(free) if m >> i & 1)
                                         for m in range(1 << len(free))))
            table, size = zero_table(len(self.active)+1, len(free))
            self.tables.append(table)
            if len(free) not in seen_sizes:
                seen_sizes.add(len(free))
                self.table_bytes += size
        self.local_cache = {}
        rows = []
        for p in self.active:
            fixed_lengths = tuple(sum(c//13 == s for c in self.conditions.hands[p].predeal) for s in range(4))
            rows.append(tuple(row for row in self.conditions.hands[p].shapes
                              if all(x >= y for x, y in zip(row, fixed_lengths))))
        self.all_matrices = tuple(Matrix(self, pair) for pair in itertools.product(*rows))
        self.matrices = tuple(m for m in self.all_matrices if m.total)
        self.cdf = cumulative(m.total for m in self.matrices)
        self.total_active_assignments = self.cdf[-1] if self.cdf else 0
        self.pool_size = sum(self.free_slots[p] for p in self.inactive)
        self.pool_completions = FACT[self.pool_size] // math.prod(FACT[self.free_slots[p]] for p in self.inactive)
        self.total_deals = self.total_active_assignments * self.pool_completions
        self.shape_only_deals = sum(m.shape_weight for m in self.all_matrices) * self.pool_completions
        self.predeal_only_deals = FACT[sum(self.free_slots)] // math.prod(FACT[n] for n in self.free_slots)

    def local_options(self, s, lengths):
        key = (s, lengths)
        if key in self.local_cache:
            return self.local_cache[key]
        k = len(self.active)
        free_honors = tuple(r for r in range(4) if s*13+r not in self.known_owner)
        fixed_lengths = tuple(sum(c//13 == s for c in self.conditions.hands[p].predeal) for p in self.active)
        fixed_q = tuple(sum(VALUES[c%13] for c in self.conditions.hands[p].predeal
                            if c//13 == s and c%13 < 4) for p in self.active)
        z = len(self.free_zeros[s])
        result = []
        for owners in itertools.product(range(k+1), repeat=len(free_honors)):
            h, q, masks = [0]*k, list(fixed_q), [0]*k
            for rank, p in zip(free_honors, owners):
                if p < k:
                    h[p] += 1
                    q[p] += VALUES[rank]
                    masks[p] |= 1 << (s*13+rank)
            r = tuple(lengths[p] - fixed_lengths[p] - h[p] for p in range(k))
            r = (*r, z - sum(r))
            if min(r) < 0:
                continue
            weight = FACT[z] // math.prod(FACT[n] for n in r)
            result.append(Option(tuple(q), r, weight, tuple(masks)))
        self.local_cache[key] = tuple(result)
        return self.local_cache[key]

    def sample(self, count, seed):
        if not self.total_active_assignments:
            raise ValueError("No solution")
        rng = random.Random(seed)
        output = np.empty((count, 4), dtype=np.uint64)
        k = len(self.active)
        for i in range(count):
            matrix = self.matrices[choose(rng, self.cdf)]
            active_masks = [self.fixed_masks[p] for p in self.active]
            t = (0,)*k
            for s in range(4):
                entries, cdf = matrix.draws[s, t]
                o, t = entries[choose(rng, cdf)]
                bucket = self.tables[s][o.r]
                low = bucket[rng.randrange(len(bucket))]
                expansion = self.expansions[s]
                for p in range(k):
                    active_masks[p] |= o.honor_masks[p] | expansion[int(low[p])]

            hands = list(self.fixed_masks)
            occupied = self.fixed_union
            for p, mask in zip(self.active, active_masks):
                hands[p] = mask
                occupied |= mask
            remaining = ALL_CARDS ^ occupied
            pool = []
            bits = remaining
            while bits:
                lowbit = bits & -bits
                pool.append(lowbit)
                bits ^= lowbit
            for p in self.inactive[:-1]:
                selected = sum(rng.sample(pool, self.free_slots[p]))
                hands[p] |= selected
                remaining ^= selected
                pool = [bit for bit in pool if not bit & selected]
            hands[self.inactive[-1]] |= remaining
            output[i] = hands
        return output


def validate(plan, hands):
    pop = np.array([x.bit_count() for x in range(8192)], dtype=np.uint8)
    hcp = np.array([sum(VALUES[r] for r in range(4) if x >> r & 1) for x in range(16)], dtype=np.uint8)
    shapes = np.empty((len(hands), 4, 4), dtype=np.uint8)
    points = np.zeros((len(hands), 4), dtype=np.uint8)
    for s in range(4):
        suit = (hands >> (13*s)) & 8191
        shapes[:, :, s] = pop[suit]
        points += hcp[suit & 15]
    assert np.all(shapes.sum(axis=2) == 13)
    assert np.all(np.bitwise_or.reduce(hands, axis=1) == ALL_CARDS)
    for p, hand in enumerate(plan.conditions.hands):
        assert np.all((hands[:, p] & plan.fixed_masks[p]) == plan.fixed_masks[p])
        assert np.all((points[:, p] >= hand.hcp[0]) & (points[:, p] <= hand.hcp[1]))
        if hand.shapes is not None:
            matched = np.zeros(len(hands), dtype=bool)
            for shape in hand.shapes:
                matched |= np.all(shapes[:, p, :] == shape, axis=1)
            assert np.all(matched)
    return True


def shape_rejection_probe(plan, attempts=20000):
    matrices = tuple(m for m in plan.all_matrices if m.shape_weight)
    cdf = cumulative(m.shape_weight for m in matrices)
    rng = random.Random(1907)
    accepted = 0
    start = time.perf_counter()
    for _ in range(attempts):
        m = matrices[choose(rng, cdf)]
        t = [0] * len(plan.active)
        for s in range(4):
            o = m.options[s][choose(rng, m.local_cdfs[s])]
            for p in range(len(t)):
                t[p] += o.q[p]
        accepted += all(a <= q <= b for a, q, b in zip(plan.low, t, plan.high))
    return {"attempts": attempts, "accepted": accepted,
            "seconds_honor_stage_only": time.perf_counter()-start,
            "exact_acceptance_probability": plan.total_deals/plan.shape_only_deals}


def run_correctness_checks():
    # Independent counts, including fixed cards on unconstrained seats.
    checks = [
        ({"N": {"shape": [12, 1, 0, 0], "hcp": 10},
          "E": {"shape": [1, 12, 0, 0], "hcp": 10}}, 85),
        ({"N": {"shape": [5, 4, 3, 1]}},
         math.prod(math.comb(13, n) for n in (5, 4, 3, 1))),
        ({"N": {"shape": [12, 1, 0, 0], "hcp": 10},
          "E": {"shape": [1, 12, 0, 0], "hcp": 10},
          "S": {"predeal": "..AKQJT98765432."},
          "W": {"predeal": "...AKQJT98765432"}}, 85),
    ]
    for raw, expected in checks:
        plan = Plan(raw)
        assert plan.total_active_assignments == expected
        validate(plan, plan.sample(200, 8))

    cross_checks = []
    base = Deal.from_pbn("N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3")
    for n in (1, 2):
        raw = {seat: {"predeal": hand_text(tuple(c for i, c in enumerate(hand) if i not in (0, 6)))}
               for seat, hand in zip("NESW", base.hands)}
        for seat in "NESW"[:n]:
            raw[seat].update(shape=list(base.shape(seat)), hcp=base.hcp(seat))
        plan = Plan(raw)
        reference = enumerate_deals(raw, lambda _: None)
        assert reference.complete and plan.total_deals == reference.count
        validate(plan, plan.sample(1000, 177))
        cross_checks.append({"active_seats": n, "collapsed_count": plan.total_deals,
                             "cp_sat_count": reference.count, "samples_validated": 1000})
    return {"closed_form_checks": len(checks), "cp_sat_checks": cross_checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--count", type=int, default=100000)
    parser.add_argument("--baseline-attempts", type=int, default=20000)
    args = parser.parse_args()
    correctness = run_correctness_checks()

    cases = [
        ("one_active", {"N": {"shape": [5, 4, 3, 1], "hcp": [12, 16]}}),
        ("two_active", {"N": {"shape": [5, 4, 3, 1], "hcp": [12, 16]},
                        "S": {"shape_any": [4, 3, 3, 3], "hcp": [8, 12]}}),
    ]
    report = {"environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                              "machine": platform.machine(), "system": platform.system()},
              "scope": "Research prototype; generation into uint64 masks; excludes PBN/DDS; with replacement",
              "independent_count_checks": correctness["closed_form_checks"] + len(correctness["cp_sat_checks"]),
              "correctness_checks": correctness, "cases": []}
    for name, raw in cases:
        zero_table.cache_clear()
        started = time.perf_counter()
        plan = Plan(raw)
        compile_seconds = time.perf_counter() - started
        started = time.perf_counter()
        hands = plan.sample(args.count, 20260922)
        sample_seconds = time.perf_counter() - started
        started = time.perf_counter()
        validate(plan, hands)
        assert np.array_equal(hands[:min(256, args.count)], plan.sample(min(256, args.count), 20260922))
        validation_seconds = time.perf_counter() - started
        p = plan.total_deals / plan.predeal_only_deals
        result = {"name": name, "conditions": raw, "count": args.count,
                  "compile_seconds": compile_seconds, "sample_seconds": sample_seconds,
                  "validation_seconds": validation_seconds, "all_samples_valid": True,
                  "seed_prefix_reproduced": True, "output_bytes": hands.nbytes,
                  "active_shape_tuples": len(plan.all_matrices),
                  "positive_shape_tuples": len(plan.matrices),
                  "dp_states": sum(len(m.memo) for m in plan.all_matrices),
                  "compiled_draw_states": sum(len(m.draws) for m in plan.all_matrices),
                  "zero_assignment_table_bytes": plan.table_bytes,
                  "active_assignment_count": plan.total_active_assignments,
                  "full_deal_count": plan.total_deals,
                  "full_rejection_exact_acceptance_probability": p,
                  "full_rejection_expected_attempts_for_count": args.count / p,
                  "shape_rejection_probe": shape_rejection_probe(plan)}
        report["cases"].append(result)
        print(json.dumps(result), flush=True)

    raw = cases[-1][1]
    start = time.perf_counter()
    produced = 0
    exhausted = False
    try:
        for _ in random_deals(raw, seed=20260922, count=args.count, max_attempts=args.baseline_attempts):
            produced += 1
    except SamplingExhaustedError:
        exhausted = True
    report["v1_baseline_probe"] = {
        "case": "two_active", "attempt_budget": args.baseline_attempts,
        "produced": produced, "budget_exhausted": exhausted,
        "seconds_including_feasibility_and_adapters": time.perf_counter()-start,
        "not_a_full_100k_benchmark": True}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["v1_baseline_probe"]), flush=True)


if __name__ == "__main__":
    main()
