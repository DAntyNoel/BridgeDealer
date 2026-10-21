"""Disposable process: sampling, native DDS calls, atomic prefix checkpoints."""

import json
import sys
from time import perf_counter

from .conditions import Conditions
from .dds_contexts import DDSContextPool
from .enumeration import check_conditions, enumerate_deals
from .query_sampling import Proposals
from .v2 import compile_sampler


def solve(solver, batch, request):
    if request.get("contracts"):
        return solver.success_contracts(batch, request["contracts"])
    return solver.success(
        batch, declarer=request["declarer"], strains=request["strains"], level=request["level"]
    )


def checkpoint_batch_size(request, samples, size):
    """Finish a batch at the display target before continuing larger batches."""
    target = request["min_samples"]
    return min(size, target - samples) if samples < target else size


def run_exact(request, conditions, state, emit):
    """Unbounded streamed census. Completion, not sample count, establishes truth."""
    state["method"] = "enumeration"
    try:
        plan = compile_sampler(conditions)
    except ValueError:
        plan = None
    state["strategy"] = "v2_exhaustive" if plan is not None else "cpsat_exhaustive"
    if plan is not None:
        state["population_size"] = plan.total_deals
    emit()
    batch = []
    batch_size = checkpoint_batch_size(request, 0, 40)
    with DDSContextPool(request.get("dds_library"), threads=request["threads"]) as solver:
        state.update(
            dds_version=solver.version,
            dds_library=str(solver.path),
            dds_engine="context_pool_small_tt",
        )

        def flush():
            nonlocal batch_size
            if not batch:
                return
            emit()
            started = perf_counter()
            outcomes = solve(solver, batch, request)
            state["dds_seconds"] += perf_counter() - started
            for outcome in outcomes:
                for i, success in enumerate(outcome):
                    state["successes"][i] += success
            state["samples"] += len(batch)
            batch_size = checkpoint_batch_size(request, state["samples"], 40)
            batch.clear()
            emit()

        last_sampled = perf_counter()

        def accept(masks):
            nonlocal last_sampled
            state["sampling_seconds"] += perf_counter() - last_sampled
            batch.append(masks)
            state["generated"] += 1
            if len(batch) >= batch_size:
                flush()
            last_sampled = perf_counter()

        if plan is not None:
            for masks in plan.enumerate_masks():
                accept(masks)
        else:
            result = enumerate_deals(
                conditions,
                lambda deal: accept(tuple(sum(1 << c for c in hand) for hand in deal.hands)),
            )
            if not result.complete:
                raise RuntimeError(f"Enumeration did not finish: {result.stop_reason}")
        flush()
    state["population_size"] = state["samples"]
    emit("done", stop_reason="exhausted" if state["samples"] else "infeasible")


def main():
    request = json.load(sys.stdin)
    deadline = request["deadline"]
    conditions = Conditions.from_dict(request["conditions"])
    strains = request["strains"]
    counts = [0] * len(request.get("contracts") or strains)
    state = {
        "samples": 0,
        "successes": counts,
        "proposed": 0,
        "generated": 0,
        "sampling_seconds": 0.0,
        "dds_seconds": 0.0,
        "method": "monte_carlo",
        "population_size": None,
        "strategy": None,
    }

    def emit(kind="checkpoint", **extra):
        print(json.dumps({**state, "type": kind, **extra}), flush=True)

    solver = None
    try:
        if request.get("exact"):
            run_exact(request, conditions, state, emit)
            return
        started = perf_counter()
        proposals = Proposals(
            conditions,
            seed=request["seed"],
            compile_deadline=min(deadline, perf_counter() + 0.6),
        )
        state["sampling_seconds"] += perf_counter() - started
        state["strategy"] = proposals.strategy
        state["population_size"] = proposals.population_size
        exact = proposals.exact_deals
        if exact is not None:
            state["method"] = "enumeration"
            if not exact:
                emit("done", stop_reason="infeasible")
                return
        elif proposals.population_size is None and any(
            h.hcp != (0, 37) or h.exclude for h in conditions.hands
        ):
            # A timeout is unknown, never proof of impossibility.
            if (
                check_conditions(
                    conditions, time_limit=min(0.2, max(0.001, deadline - perf_counter()))
                )
                == "infeasible"
            ):
                emit("done", stop_reason="infeasible")
                return
        emit()
        if perf_counter() >= deadline:
            emit("done", stop_reason="time_budget")
            return
        solver = DDSContextPool(request.get("dds_library"), threads=request["threads"])
        state["dds_version"] = solver.version
        state["dds_library"] = str(solver.path)
        state["dds_engine"] = "context_pool_small_tt"
        emit()
        max_samples = request["max_samples"]
        batch_size, seconds_per_deal = 1, None
        max_batch = max(1, 200 // len(counts))
        while state["samples"] < max_samples:
            remaining = deadline - perf_counter()
            if remaining < 0.04:
                break
            if seconds_per_deal is not None:
                batch_size = max(1, min(max_batch, int(remaining / seconds_per_deal * 0.2)))
                if remaining < seconds_per_deal * 1.3:
                    break
            batch_size = min(batch_size, max_samples - state["samples"])
            batch_size = checkpoint_batch_size(request, state["samples"], batch_size)
            started = perf_counter()
            if exact is not None:
                batch = exact[state["samples"] : state["samples"] + batch_size]
            else:
                batch = []
                while len(batch) < batch_size and perf_counter() < deadline - 0.04:
                    candidate = proposals.next()
                    if candidate is not None:
                        batch.append(candidate)
            state["sampling_seconds"] += perf_counter() - started
            state["proposed"] = proposals.tried
            state["generated"] += len(batch)
            # Publish generated count before a non-interruptible native call.
            emit()
            if not batch or perf_counter() >= deadline:
                break
            started = perf_counter()
            outcomes = solve(solver, batch, request)
            duration = perf_counter() - started
            state["dds_seconds"] += duration
            seconds_per_deal = max(duration / len(batch), 0.0001)
            # Only publish complete batches containing every requested strain.
            # Never discard hard deals inside a batch and replace them.
            for outcome in outcomes:
                for i, success in enumerate(outcome):
                    counts[i] += success
            state["samples"] += len(batch)
            emit()
            if exact is not None and state["samples"] == len(exact):
                emit("done", stop_reason="exhausted")
                return
        emit("done", stop_reason="sample_cap" if state["samples"] >= max_samples else "time_budget")
    except Exception as error:
        emit("error", error=f"{type(error).__name__}: {error}")
    finally:
        if solver is not None:
            solver.close()


if __name__ == "__main__":
    main()
