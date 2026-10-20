"""Run independent cold CLI queries and retain wall-clock budget evidence."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from bridge_dealer import Deal
from bridge_dealer.cards import hand_text
from bridge_dealer.dds_native import library_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    base = Deal.from_pbn("N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3")
    fixed = {"N": {"predeal": hand_text(base.hand("N"))}}
    cases = [
        ("unconstrained", {}),
        ("fixed_one", fixed),
        ("fixed_two", {p: {"predeal": hand_text(base.hand(p))} for p in "NS"}),
    ]
    for n in (1, 2, 3):
        cases.append(
            (
                f"fixed_one_{n}_shapes",
                {**fixed, **{p: {"shape": list(base.shape(p))} for p in "ESW"[:n]}},
            )
        )
    cases += [
        (
            "fixed_one_3_unordered",
            {**fixed, **{p: {"shape_any": list(base.shape(p))} for p in "ESW"}},
        ),
        ("hcp_two", {"N": {"hcp": [12, 16]}, "S": {"hcp": [8, 12]}}),
        (
            "shape_hcp_two",
            {
                p: {"shape_any": [4, 3, 3, 3], "hcp": bounds}
                for p, bounds in (("N", [12, 16]), ("S", [8, 12]))
            },
        ),
        ("rare_37_hcp_and_shape", {"N": {"hcp": 37, "shape_any": [4, 3, 3, 3]}}),
        ("four_hcp", {"N": {"hcp": 37}, **{p: {"hcp": 1} for p in "ESW"}}),
        ("infeasible", {p: {"hcp": 11} for p in "NESW"}),
        ("tiny_exact", json.loads((root / "examples/two_unknown_cards.json").read_text())),
    ]
    record = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "budget_seconds": 10,
        "strains": ["S", "H", "D", "C", "NT"],
        "level": 3,
        "declarer": "N",
        "seed": 20261020,
        "scope": "cold CLI process, imports, normalization, sampling, DDS and JSON result",
        "cases": [],
    }
    for name, conditions in cases:
        path = output / f"{name}.json"
        path.write_text(json.dumps(conditions, indent=2) + "\n")
        started = perf_counter()
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "bridge_dealer",
                "success",
                str(path),
                "--seed",
                "20261020",
                "--strains",
                "S,H,D,C,NT",
                "--level",
                "3",
                "--budget",
                "10",
                "--min-samples",
                "100",
            ],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=10,
        )
        wall = perf_counter() - started
        result = json.loads(child.stdout)
        assert child.returncode in (0, 1, 3), child.stderr
        assert wall < 10 and result["within_budget"], (name, wall, result)
        assert len(result["results"]) == 5
        assert all(r["samples"] == result["samples"] for r in result["results"])
        for item in result["results"]:
            p = item["probability"]
            if p is not None:
                assert 0 <= item["interval"][0] <= p <= item["interval"][1] <= 1
        record["cases"].append(
            {"name": name, "wall_seconds": wall, "returncode": child.returncode, "result": result}
        )
        (output / "report.json").write_text(json.dumps(record, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "name": name,
                    "wall_seconds": round(wall, 3),
                    "samples": result["samples"],
                    "status": result["status"],
                    "strategy": result["strategy"],
                    "unresolved": result["unresolved"],
                    "probabilities": {r["strain"]: r["probability"] for r in result["results"]},
                }
            ),
            flush=True,
        )
    files = [*sorted((root / "src/bridge_dealer").glob("*.py")), Path(__file__).resolve()]
    record["source_sha256"] = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    lib = library_path()
    record["dds_library"] = str(lib)
    record["dds_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
    record["all_within_ten_seconds"] = True
    (output / "report.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
