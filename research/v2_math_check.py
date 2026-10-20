"""Research-only exact checks of the proposed factorization; no v1 backend changes."""
import copy
import json
import sys
from collections import defaultdict
from functools import lru_cache
from itertools import product
from math import comb, factorial, prod
from pathlib import Path

from bridge_dealer import Conditions, Deal, enumerate_deals
from bridge_dealer.cards import hand_text

SHAPES = tuple((a, b, c, 13-a-b-c)
               for a in range(14) for b in range(14-a) for c in range(14-a-b))
FACT = [factorial(i) for i in range(53)]


@lru_cache(None)
def suit_kernel(lengths, fixed_honors, fixed_zero_counts):
    """K_s(l,q): sum of zero-point completion counts over explicit AKQJ owners."""
    free_zero = 9 - sum(fixed_zero_counts)
    out = defaultdict(int)
    choices = [range(4) if p == -1 else (p,) for p in fixed_honors]
    for owners in product(*choices):
        h, q = [0]*4, [0]*4
        for rank, p in enumerate(owners):
            h[p] += 1
            q[p] += 4-rank
        remaining = [lengths[p]-h[p]-fixed_zero_counts[p] for p in range(4)]
        if min(remaining) < 0:
            continue
        assert sum(remaining) == free_zero
        out[tuple(q)] += FACT[free_zero] // prod(FACT[r] for r in remaining)
    return tuple(out.items())


def profiles(conditions):
    result = []
    for s in range(4):
        honors, zeros = [-1]*4, [0]*4
        for p, hand in enumerate(conditions.hands):
            for c in hand.predeal:
                if c//13 != s:
                    continue
                rank = c%13
                if rank < 4:
                    honors[rank] = p
                else:
                    zeros[p] += 1
        result.append((tuple(honors), tuple(zeros)))
    return result


def matrices(conditions):
    allowed = []
    for hand in conditions.hands:
        minimum = tuple(sum(c//13 == s for c in hand.predeal) for s in range(4))
        allowed.append(tuple(row for row in (hand.shapes or SHAPES)
                             if all(x >= f for x, f in zip(row, minimum))))
    last = set(allowed[3])

    def walk(rows, residual):
        if len(rows) == 3:
            if residual in last:
                yield (*rows, residual)
            return
        for row in allowed[len(rows)]:
            if all(x <= r for x, r in zip(row, residual)):
                yield from walk((*rows, row), tuple(r-x for r, x in zip(residual, row)))
    yield from walk((), (13,)*4)


def fixed_matrix_count(matrix, conditions, suit_profiles):
    states = {(0, 0, 0, 0): 1}
    upper = tuple(h.hcp[1] for h in conditions.hands)
    lower = tuple(h.hcp[0] for h in conditions.hands)
    for s in range(4):
        kernel = suit_kernel(tuple(matrix[p][s] for p in range(4)), *suit_profiles[s])
        following = defaultdict(int)
        for previous, count in states.items():
            for contribution, weight in kernel:
                points = tuple(a+b for a, b in zip(previous, contribution))
                if all(a <= b for a, b in zip(points, upper)):
                    following[points] += count*weight
        states = following
    return sum(count for points, count in states.items()
               if all(a >= b for a, b in zip(points, lower)))


def layered_count(raw):
    conditions = Conditions.from_dict(raw)
    suit_profiles = profiles(conditions)
    count, num_matrices = 0, 0
    for matrix in matrices(conditions):
        num_matrices += 1
        count += fixed_matrix_count(matrix, conditions, suit_profiles)
    return count, num_matrices


def local_checks():
    # The independent closed form assigns all as-yet-unfixed ranks together.
    cases = [((-1,)*4, (0,)*4), ((0,1,-1,-1), (1,0,0,1)),
             ((0,1,2,3), (0,)*4), ((0,0,1,1), (1,2,3,3))]
    checked = 0
    for honors, zeros in cases:
        fixed = tuple(zeros[p]+honors.count(p) for p in range(4))
        total = 0
        for lengths in SHAPES:
            expected = (FACT[13-sum(fixed)] // prod(FACT[l-f] for l, f in zip(lengths, fixed))
                        if all(l >= f for l, f in zip(lengths, fixed)) else 0)
            actual = sum(weight for _, weight in suit_kernel(lengths, honors, zeros))
            assert actual == expected, (lengths, honors, zeros, actual, expected)
            checked += 1
            total += actual
        assert total == 4**(13-sum(fixed))
    return checked


@lru_cache(None)
def unrestricted_by_rows(rows_left, residual, weighted):
    # Sorting residual suits is valid only in this fully unrestricted, symmetric check.
    if rows_left == 1:
        return 1
    total = 0
    for row in SHAPES:
        if all(x <= r for x, r in zip(row, residual)):
            weight = prod(comb(r, x) for r, x in zip(residual, row)) if weighted else 1
            total += weight*unrestricted_by_rows(
                rows_left-1, tuple(sorted(r-x for r, x in zip(residual, row))), weighted)
    return total


def cross_checks():
    base = Deal.from_pbn('N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3')
    known = {seat: {'predeal': hand_text(tuple(c for i,c in enumerate(hand) if i not in (0,6)))}
             for seat, hand in zip('NESW', base.hands)}
    cases = [('eight_unknown', known)]
    for kind in ('shape', 'hcp', 'both'):
        for n in range(1,5):
            raw = copy.deepcopy(known)
            for seat in 'NESW'[:n]:
                if kind in ('shape', 'both'):
                    raw[seat]['shape'] = list(base.shape(seat))
                if kind in ('hcp', 'both'):
                    raw[seat]['hcp'] = base.hcp(seat)
            cases.append((f'{kind}_{n}_seats', raw))
    raw = copy.deepcopy(known)
    raw['N']['shape_any'] = [4,3,3,3]
    cases.append(('shape_any', raw))
    root = Path.cwd()
    for name in ('four_hands_85', 'two_unknown_cards', 'impossible'):
        if name == 'impossible':
            # Fixed shapes keep this mathematical check's outer search bounded.
            raw = {seat: {'shape': [13 if p==s else 0 for s in range(4)], 'hcp': 11}
                   for p, seat in enumerate('NESW')}
        else:
            raw = json.loads((root/f'examples/{name}.json').read_text())
        cases.append((name, raw))
    results = []
    for name, raw in cases:
        count, shapes = layered_count(raw)
        reference = enumerate_deals(raw, lambda _: None)
        assert reference.complete and count == reference.count, (name, count, reference)
        results.append({'case': name, 'shape_matrices': shapes, 'layered_count': count,
                        'cp_sat_count': reference.count})
    return results


result = {'one_suit_identity_checks': local_checks(),
          'individual_ordered_shapes': len(SHAPES),
          'unrestricted_shape_matrices': unrestricted_by_rows(4, (13,)*4, False),
          'unrestricted_deals': unrestricted_by_rows(4, (13,)*4, True),
          'cross_checks': cross_checks()}
assert result['unrestricted_deals'] == FACT[52]//FACT[13]**4
balanced = ((4,3,3,3), (3,4,3,3), (3,3,4,3), (3,3,3,4))
blank = Conditions()
balanced_count = fixed_matrix_count(balanced, blank, profiles(blank))
expected_balanced = (FACT[13]//(FACT[4]*FACT[3]**3))**4
assert balanced_count == expected_balanced
result['balanced_matrix_no_hcp'] = balanced_count
result['honor_assignment_patterns_per_suit_max'] = 4**4
result['zero_point_completion_weight_max_per_suit'] = FACT[9]//(FACT[3]*FACT[2]**3)
Path(sys.argv[1]).write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
