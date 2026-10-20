"""Reproduce the fixed-hand v2 experiment, including all C(26,13) deals.

Run from the project root with .venv/bin/python research/v2_target_experiment.py
OUTPUT_DIR. Each invocation requires a new output directory.
"""

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from bridge_dealer import Deal, compile_sampler
from bridge_dealer.cards import hand_text
from bridge_dealer.v2 import deal_from_masks

BASE = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"
ROOT = Path(__file__).resolve().parents[1]
ALL_CARDS = (1 << 52) - 1


def validate(raw, masks):
    assert len(masks) == 4 and all(m.bit_count() == 13 for m in masks)
    assert masks[0] | masks[1] | masks[2] | masks[3] == ALL_CARDS
    # Original JSON and string parsing, independent of the compiler's matrices.
    ranks = "AKQJT98765432"
    for p, seat in enumerate("NESW"):
        hand = masks[p]
        condition = raw.get(seat, {})
        for s, text in enumerate(condition.get("predeal", "...").split(".")):
            for rank in text.replace("-", ""):
                assert hand & (1 << (s * 13 + ranks.index(rank)))
        shape = [(hand >> (13 * s) & 8191).bit_count() for s in range(4)]
        if "shape" in condition:
            assert shape == condition["shape"]
        if "shape_any" in condition:
            assert sorted(shape) == sorted(condition["shape_any"])
        if "shapes" in condition:
            assert shape in condition["shapes"]


def full_enumeration(raw):
    plan = compile_sampler(raw)
    assert plan.total_deals == math.comb(26, 13)
    # Map each actual free card to a bit in a 26-bit ownership code. Tracking all
    # codes uses 8 MiB rather than a Python set of 10.4 million complete deals.
    free = [c for c in range(52) if plan.free_mask >> c & 1]
    positions = {card: 1 << i for i, card in enumerate(free)}
    tables = []
    for suit in range(4):
        table = [0] * 8192
        for value in range(1, 8192):
            bit = value & -value
            table[value] = table[value ^ bit] | positions.get(suit * 13 + bit.bit_length() - 1, 0)
        tables.append(table)
    seen = bytearray(1 << 23)
    north, south = plan.fixed_masks[0], plan.fixed_masks[2]
    free_mask = plan.free_mask
    count = 0
    start = perf_counter()
    for n, e, s, w in plan.enumerate_masks():
        assert n == north and s == south
        assert e.bit_count() == w.bit_count() == 13
        assert not e & (north | south) and w == free_mask ^ e
        code = (
            tables[0][e & 8191]
            | tables[1][e >> 13 & 8191]
            | tables[2][e >> 26 & 8191]
            | tables[3][e >> 39 & 8191]
        )
        byte, bit = code >> 3, 1 << (code & 7)
        assert not seen[byte] & bit, "duplicate allocation"
        seen[byte] |= bit
        count += 1
    seconds = perf_counter() - start
    assert count == plan.total_deals == sum(byte.bit_count() for byte in seen)
    return {
        "count": count,
        "expected_count": math.comb(26, 13),
        "complete": True,
        "all_allocations_valid": True,
        "duplicates": 0,
        "enumeration_and_validation_seconds": seconds,
        "seen_bitmap_bytes": len(seen),
        "seen_bitmap_sha256": hashlib.sha256(seen).hexdigest(),
        "scope": "compact complete deals; exhaustive validation; excludes Deal/PBN conversion",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    output = args.output_dir.resolve()
    base = Deal.from_pbn(BASE)
    fixed = {"N": {"predeal": hand_text(base.hand("N"))}}
    cases = [("fixed_one", fixed)]
    for k in (1, 2, 3):
        raw = {**fixed, **{p: {"shape": list(base.shape(p))} for p in "ESW"[:k]}}
        cases.append((f"fixed_one_{k}_ordered_shapes", raw))
    cases.append(
        (
            "fixed_one_3_unordered_shapes",
            {**fixed, **{p: {"shape_any": list(base.shape(p))} for p in "ESW"}},
        )
    )
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "seed": 20261020,
        "cases": [],
    }
    for name, raw in cases:
        condition_path = output / f"{name}.json"
        condition_path.write_text(json.dumps(raw, indent=2) + "\n")
        plan = compile_sampler(raw)
        started = perf_counter()
        masks = list(plan.sample_masks(count=args.count, seed=report["seed"]))
        sample_seconds = perf_counter() - started
        started = perf_counter()
        for hand in masks:
            validate(raw, hand)
        validation_seconds = perf_counter() - started
        assert masks[:64] == list(plan.sample_masks(count=min(64, args.count), seed=report["seed"]))
        short = min(71, args.count)
        expected = list(plan.sample_deals(count=short, seed=report["seed"]))
        for size in (1, 7, 4096):
            actual = [
                d
                for chunk in plan.sample_batches(count=short, seed=report["seed"], batch_size=size)
                for d in chunk
            ]
            assert actual == expected
        with (output / f"{name}.prefix.pbn").open("x") as stream:
            for i, hand in enumerate(masks[:10], 1):
                stream.write(f'[Board "{i}"]\n[Deal "{deal_from_masks(hand).to_pbn()}"]\n\n')
        result = {
            "name": name,
            "conditions": raw,
            "count": len(masks),
            "strategy": plan.strategy,
            "compile_seconds": plan.compile_seconds,
            "sample_seconds": sample_seconds,
            "validation_seconds": validation_seconds,
            "all_samples_valid": True,
            "seed_prefix_reproduced": True,
            "chunk_sizes_reproduced": [1, 7, 4096],
            "shape_matrices": len(plan.matrices),
            "total_deals": plan.total_deals,
        }
        report["cases"].append(result)
        print(json.dumps(result), flush=True)
    fixed_two = {p: {"predeal": hand_text(base.hand(p))} for p in "NS"}
    (output / "fixed_two.json").write_text(json.dumps(fixed_two, indent=2) + "\n")
    report["fixed_two_full_enumeration"] = full_enumeration(fixed_two)
    print(json.dumps(report["fixed_two_full_enumeration"]), flush=True)
    # Exercise the actual command, serialize a complete 100k run, then parse all
    # exported deals back and independently recheck the supplied conditions.
    target = output / "sample100k.pbn"
    started = perf_counter()
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bridge_dealer",
            "random",
            str(output / "fixed_one_3_unordered_shapes.json"),
            "--backend",
            "v2",
            "--count",
            str(args.count),
            "--seed",
            str(report["seed"]),
            "--output",
            str(target),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    export_seconds = perf_counter() - started
    cli_report = json.loads(child.stderr.splitlines()[-1])
    parsed = 0
    for line in target.read_text().splitlines():
        if line.startswith('[Deal "'):
            deal = Deal.from_pbn(line[7:-2])
            validate(cases[-1][1], tuple(sum(1 << c for c in h) for h in deal.hands))
            parsed += 1
    assert parsed == cli_report["count"] == args.count
    report["pbn_export"] = {
        "count": parsed,
        "all_roundtrips_valid": True,
        "cli_and_export_seconds": export_seconds,
        "bytes": target.stat().st_size,
        "cli_report": cli_report,
    }
    report["source_sha256"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            ROOT / "src/bridge_dealer/v2.py",
            ROOT / "src/bridge_dealer/cli.py",
            Path(__file__).resolve(),
        ]
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["pbn_export"]), flush=True)


if __name__ == "__main__":
    main()
