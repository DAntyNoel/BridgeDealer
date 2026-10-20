"""Command-line interface. Deals go to stdout; a JSON report goes to stderr."""

import argparse
import json
import os
import platform
import secrets
import sys
from contextlib import nullcontext
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from . import __version__
from ._runtime import PROCESS_STARTED
from .cards import SEATS
from .conditions import Conditions
from .enumeration import check_conditions, enumerate_deals
from .sampling import InfeasibleConditionsError, SamplingExhaustedError, random_deals
from .success import query_success
from .v2 import compile_sampler, enumerate_deals_v2


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _parser():
    parser = argparse.ArgumentParser(description="条件桥牌发牌：随机抽样与 CP-SAT 全枚举")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("serve", help="启动本地桥牌网页工作台")
    web.add_argument("--port", type=int, default=8789)
    for name, help_text in (
        ("random", "按条件随机发牌，支持种子复现"),
        ("enumerate", "不重不漏枚举，流式输出"),
        ("check", "检查条件是否存在可行牌局"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("conditions", type=Path, help="按 N/E/S/W 配置的 JSON 文件")
        if name != "check":
            command.add_argument(
                "--backend",
                choices=("v1", "v2"),
                default="v1",
                help="v2 为已知牌/牌型实验后端；默认仍为 v1",
            )
            command.add_argument("--format", choices=("pbn", "jsonl"), default="pbn")
            command.add_argument(
                "--output", default="-", help="输出文件（不覆盖已有文件）；默认 stdout"
            )
        if name == "random":
            command.add_argument("--seed", type=int, help="0..4294967295；省略时自动生成并记录")
            command.add_argument("--count", type=int, default=10)
            command.add_argument("--max-attempts", type=int, default=1_000_000)
        elif name == "enumerate":
            command.add_argument("--limit", type=int, help="可选的输出上限；省略表示不限数量")
            command.add_argument("--time-limit", type=float, help="可选的求解时间上限（秒）")
        else:
            command.add_argument("--time-limit", type=float, default=5)
    success = commands.add_parser("success", help="在总时间预算内估计定约双明手成功率")
    success.add_argument("conditions", type=Path)
    success.add_argument("--declarer", choices=tuple(SEATS), default="N")
    success.add_argument("--strains", default="S,H,D,C,NT", help="逗号分隔，默认五种花色")
    success.add_argument("--level", type=int, default=3, help="定约阶数 1..7，默认 3")
    success.add_argument(
        "--budget", type=float, default=20, help="总墙钟预算（秒），默认 20，含冷启动"
    )
    success.add_argument("--max-samples", type=int, default=100_000)
    success.add_argument("--min-samples", type=int, default=10_000)
    success.add_argument("--threads", type=int, default=8)
    success.add_argument("--seed", type=int)
    success.add_argument("--dds-library", type=Path)
    success.add_argument(
        "--no-suit-symmetry", action="store_true", help="关闭花色对称复用，对同一副牌逐花色求解"
    )
    success.add_argument(
        "--exact", action="store_true", help="不限时、不限副数，枚举全部分布求精确真值"
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        from .web import serve

        serve(args.port)
        return 0
    report = {"mode": args.command, "version": __version__, "count": 0}
    start = PROCESS_STARTED if args.command == "success" else perf_counter()
    exit_code = 0
    try:
        raw = json.loads(
            args.conditions.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
        conditions = Conditions.from_dict(raw)
        report.update(
            conditions=conditions.to_dict(),
            conditions_sha256=conditions.fingerprint,
            python=platform.python_version(),
            ortools=version("ortools"),
        )
        if args.command == "success":

            def progress(state):
                print(
                    json.dumps(
                        {
                            "mode": "success_progress",
                            "samples": state["samples"],
                            "elapsed_seconds": state["elapsed_seconds"],
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )

            result = query_success(
                conditions,
                declarer=args.declarer,
                strains=args.strains,
                level=args.level,
                budget_seconds=args.budget,
                max_samples=args.max_samples,
                min_samples=args.min_samples,
                seed=args.seed,
                threads=args.threads,
                dds_library=args.dds_library,
                suit_symmetry=not args.no_suit_symmetry,
                exact=args.exact,
                on_progress=progress if args.exact else None,
                _started_at=start,
            )
            print(json.dumps(result, ensure_ascii=False))
            report.update(status=result["status"], count=result["samples"])
            if result["status"] == "error":
                exit_code = 2
            elif result["status"] == "infeasible":
                exit_code = 1
            elif result["status"] == "insufficient_samples":
                exit_code = 3
        elif args.command == "check":
            status = check_conditions(conditions, time_limit=args.time_limit)
            report.update(status=status)
            exit_code = {"feasible": 0, "infeasible": 1, "unknown": 3}[status]
        else:
            report["backend"] = args.backend
            output = (
                nullcontext(sys.stdout)
                if args.output == "-"
                else open(args.output, "x", encoding="utf-8")
            )
            with output as stream:

                def emit(deal):
                    board = report["count"] + 1
                    if args.format == "pbn":
                        stream.write(f'[Board "{board}"]\n[Deal "{deal.to_pbn()}"]\n\n')
                    else:
                        record = {
                            "board": board,
                            "deal": deal.to_pbn(),
                            "hcp": {seat: deal.hcp(seat) for seat in SEATS},
                            "shape": {seat: deal.shape(seat) for seat in SEATS},
                        }
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    report["count"] = board

                if args.command == "random":
                    seed = secrets.randbits(32) if args.seed is None else args.seed
                    report.update(seed=seed)
                    if args.backend == "v2":
                        plan = compile_sampler(conditions)
                        report.update(
                            strategy=plan.strategy,
                            total_deals=plan.total_deals,
                            compile_seconds=plan.compile_seconds,
                            shape_matrices=len(plan.matrices),
                        )
                        deals = plan.sample_deals(count=args.count, seed=seed)
                    else:
                        report.update(endplay=version("endplay"), numpy=version("numpy"))
                        deals = random_deals(
                            conditions, seed=seed, count=args.count, max_attempts=args.max_attempts
                        )
                    for deal in deals:
                        emit(deal)
                    report.update(status="requested_count_reached", requested=args.count)
                else:
                    enumerate_fn = enumerate_deals_v2 if args.backend == "v2" else enumerate_deals
                    result = enumerate_fn(
                        conditions, emit, limit=args.limit, time_limit=args.time_limit
                    )
                    report.update(asdict(result))
                    if result.status == "INTERRUPTED":
                        exit_code = 130
                # Flush inside the error boundary, including stdout. Otherwise a
                # closed pipe could fail only at interpreter shutdown after a
                # misleading success report had already been printed.
                stream.flush()
    except InfeasibleConditionsError as error:
        report.update(status="infeasible", error=str(error))
        exit_code = 1
    except SamplingExhaustedError as error:
        report.update(status="attempt_limit", requested=error.requested, error=str(error))
        exit_code = 3
    except BrokenPipeError:
        # e.g. pipe output into head; report interruption without printing a traceback.
        report.update(status="output_closed", complete=False)
        exit_code = 141
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    except KeyboardInterrupt:
        report.update(status="interrupted", complete=False)
        exit_code = 130
    except (ValueError, OSError) as error:
        report.update(status="error", error=str(error))
        exit_code = 2
    report["elapsed_seconds"] = perf_counter() - start
    print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
    return exit_code
