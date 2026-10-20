"""Compare saved baseline and current queries in separate cold CLI processes."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from bridge_dealer.dds_native import library_path

CASES = (
    "unconstrained",
    "fixed_one",
    "fixed_two",
    "fixed_one_3_shapes",
    "hcp_two",
    "shape_hcp_two",
    "rare_37_hcp_and_shape",
)


def source_hashes(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "bridge_dealer").glob("*.py"))
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--baseline-src", type=Path, required=True)
    parser.add_argument("--conditions-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--budget", type=float, default=20)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "query-report.json"
    if target.exists():
        raise FileExistsError(target)
    baseline = args.baseline_src.resolve()
    assert (baseline / "bridge_dealer/success.py").is_file()
    variants = {"baseline": baseline, "optimized": root / "src"}
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "budget_seconds": args.budget,
        "threads": 4,
        "seed": 20261020,
        "strains": ["S", "H", "D", "C", "NT"],
        "scope": "sequential independent cold CLI processes; five probability outputs",
        "source_sha256": {name: source_hashes(src) for name, src in variants.items()},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runs": [],
    }
    for repeat in range(args.repeats):
        for case in args.cases:
            # Alternate execution order on repeated runs to reduce ordering bias.
            names = tuple(variants) if repeat % 2 == 0 else tuple(reversed(variants))
            for name in names:
                env = {
                    **os.environ,
                    "PYTHONPATH": str(variants[name]),
                    "MPLCONFIGDIR": str(root / ".cache/matplotlib"),
                    "XDG_CACHE_HOME": str(root / ".cache"),
                }
                started = perf_counter()
                child = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "bridge_dealer",
                        "success",
                        str(args.conditions_dir.resolve() / f"{case}.json"),
                        "--strains",
                        "S,H,D,C,NT",
                        "--seed",
                        "20261020",
                        "--budget",
                        str(args.budget),
                        "--threads",
                        "4",
                        "--min-samples",
                        "10000",
                        "--dds-library",
                        str(library_path()),
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=args.budget,
                )
                elapsed = perf_counter() - started
                assert child.returncode in (0, 1, 3), (child.stdout, child.stderr)
                result = json.loads(child.stdout)
                assert elapsed < args.budget and result["within_budget"], (name, case, elapsed)
                assert len(result["results"]) == 5
                item = {
                    "variant": name,
                    "repeat": repeat,
                    "case": case,
                    "wall_seconds": elapsed,
                    "result": result,
                }
                report["runs"].append(item)
                target.write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "variant": name,
                            "repeat": repeat,
                            "case": case,
                            "wall_seconds": round(elapsed, 3),
                            "samples": result["samples"],
                            "solved_strains": result.get("solved_strains", report["strains"]),
                            "probabilities": {
                                item["strain"]: item["probability"] for item in result["results"]
                            },
                        }
                    ),
                    flush=True,
                )
    lib = Path(report["runs"][-1]["result"]["dds_library"])
    report["dds_library"] = str(lib)
    report["dds_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
    report["all_within_budget"] = True
    target.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
