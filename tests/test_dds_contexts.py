import json
import random

from bridge_dealer.dds_contexts import DDSContextPool
from bridge_dealer.dds_native import STRAINS, DDSSolver


def test_context_pool_matches_native_across_deals_declarers_and_levels():
    rng = random.Random(20261020)
    masks = []
    for _ in range(12):
        cards = list(range(52))
        rng.shuffle(cards)
        masks.append(tuple(sum(1 << c for c in cards[i * 13 : i * 13 + 13]) for i in range(4)))
    native = DDSSolver(threads=4)
    with DDSContextPool(threads=4) as pool:
        for declarer in range(4):
            for level in (1, 3, 7):
                expected = native.success(masks, declarer=declarer, strains=STRAINS, level=level)
                actual = pool.success(masks, declarer=declarer, strains=STRAINS, level=level)
                assert actual == expected, json.dumps((declarer, level))
        assert len(pool.contexts) <= 4
    assert not pool.contexts
