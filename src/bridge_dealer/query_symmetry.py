"""Exact suit-permutation symmetries of the entire conditional deal space."""

from itertools import permutations

SUITS = ("S", "H", "D", "C")


def strain_representatives(conditions, strains):
    """Choose requested representatives with provably equal population rates.

    A permutation is accepted only if it leaves every fixed rank and every
    allowed shape set invariant for every seat. HCP is suit independent. The
    accepted permutations form a group; strains in the same orbit have equal
    conditional success probabilities under the bijective relabeling of deals.
    This does not assert that their outcomes agree on one physical deal.
    """
    holdings = [
        tuple(tuple(c % 13 for c in hand.predeal if c // 13 == s) for s in range(4))
        for hand in conditions.hands
    ]
    holdings += [
        tuple(tuple(c % 13 for c in hand.exclude if c // 13 == s) for s in range(4))
        for hand in conditions.hands
    ]
    shapes = [None if hand.shapes is None else set(hand.shapes) for hand in conditions.hands]
    parent = list(range(4))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for perm in permutations(range(4)):
        # perm maps new suit indices to old suit indices. A target strain t
        # on the permuted deal has the old strain perm[t]'s DD outcome.
        if any(tuple(hand[s] for s in perm) != hand for hand in holdings):
            continue
        if any(
            allowed is not None
            and any(tuple(row[s] for s in perm) not in allowed for row in allowed)
            for allowed in shapes
        ):
            continue
        for i, j in enumerate(perm):
            parent[root(i)] = root(j)
    first, result = {}, {}
    for strain in strains:
        orbit = "NT" if strain == "NT" else root(SUITS.index(strain))
        result[strain] = first.setdefault(orbit, strain)
    return result
