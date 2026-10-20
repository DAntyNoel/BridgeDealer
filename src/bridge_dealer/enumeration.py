"""Single-worker CP-SAT enumeration with bounded-memory output callbacks."""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import perf_counter

from ortools.sat.python import cp_model

from .cards import HCP, Deal
from .conditions import Conditions, as_conditions


@dataclass(frozen=True)
class EnumerationResult:
    count: int
    status: str
    complete: bool
    stop_reason: str | None
    wall_time_seconds: float


def _positive_int(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _build_model(conditions: Conditions):
    model = cp_model.CpModel()
    owner = [[model.new_bool_var(f"card_{c}_seat_{p}") for p in range(4)] for c in range(52)]
    for row in owner:
        model.add_exactly_one(row)
    for p, condition in enumerate(conditions.hands):
        model.add(sum(owner[c][p] for c in range(52)) == 13)
        points = sum(HCP[c] * owner[c][p] for c in range(52) if HCP[c])
        model.add_linear_constraint(points, *condition.hcp)
        for c in condition.predeal:
            model.add(owner[c][p] == 1)
        for c in condition.exclude:
            model.add(owner[c][p] == 0)
        if condition.shapes is not None:
            if len(condition.shapes) == 1:
                for s, length in enumerate(condition.shapes[0]):
                    model.add(sum(owner[c][p] for c in range(s * 13, (s + 1) * 13)) == length)
            else:
                lengths = [model.new_int_var(0, 13, f"length_{p}_{s}") for s in range(4)]
                for s in range(4):
                    model.add(lengths[s] == sum(owner[c][p] for c in range(s * 13, (s + 1) * 13)))
                # Lengths are functions of the ownership variables: no free selectors
                # that could report the same deal via multiple overlapping shapes.
                model.add_allowed_assignments(lengths, condition.shapes)
    # Examine honours first. Fixed search and a single worker make the traversal
    # reproducible for a pinned solver version, without collapsing seat/suit symmetry.
    ordered = [owner[s * 13 + rank][p] for rank in range(13) for s in range(4) for p in range(4)]
    model.add_decision_strategy(ordered, cp_model.CHOOSE_FIRST, cp_model.SELECT_MAX_VALUE)
    return model, owner


def enumerate_deals(
    conditions: Conditions | Mapping,
    on_deal: Callable[[Deal], bool | None],
    *,
    limit: int | None = None,
    time_limit: float | None = None,
) -> EnumerationResult:
    """Stream every legal deal to ``on_deal``; return a completeness report.

    No list of deals or deduplication set is retained. Return False from the
    callback to stop. A limit, callback stop or timeout never certifies exhaustion.
    No rank/unrank or uniform random sampling is performed by this function.
    """
    conditions = as_conditions(conditions)
    if not callable(on_deal):
        raise ValueError("on_deal must be callable")
    if limit is not None:
        _positive_int(limit, "limit")
    if time_limit is not None and (
        isinstance(time_limit, bool)
        or not isinstance(time_limit, (int, float))
        or not math.isfinite(time_limit)
        or time_limit <= 0
    ):
        raise ValueError("time_limit must be finite and positive")
    model, owner = _build_model(conditions)
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1
    solver.parameters.keep_all_feasible_solutions_in_presolve = True
    solver.parameters.symmetry_level = 0
    solver.parameters.random_seed = 0
    solver.parameters.search_branching = cp_model.FIXED_SEARCH
    if time_limit is not None:
        solver.parameters.max_time_in_seconds = time_limit

    class Collector(cp_model.CpSolverSolutionCallback):
        count = 0
        stop_reason = None
        error = None

        def on_solution_callback(self):
            deal = Deal(
                tuple(tuple(c for c in range(52) if self.value(owner[c][p])) for p in range(4))
            )
            self.count += 1
            try:
                keep_going = on_deal(deal)
            except BaseException as error:
                self.error = error
                self.stop_reason = "callback_error"
                self.stop_search()
                return
            if keep_going is False:
                self.stop_reason = "callback"
                self.stop_search()
            elif limit is not None and self.count >= limit:
                self.stop_reason = "limit"
                self.stop_search()

    collector = Collector()
    start = perf_counter()
    try:
        status = solver.solve(model, collector)
    except KeyboardInterrupt:
        return EnumerationResult(
            collector.count, "INTERRUPTED", False, "interrupt", perf_counter() - start
        )
    if collector.error is not None:
        raise collector.error
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(f"CP-SAT rejected the model: {solver.solution_info()}")
    complete = status in (cp_model.OPTIMAL, cp_model.INFEASIBLE) and collector.stop_reason is None
    reason = collector.stop_reason
    if not complete and reason is None:
        reason = "time_limit" if time_limit is not None else "search_stopped"
    return EnumerationResult(
        collector.count, solver.status_name(status), complete, reason, perf_counter() - start
    )


def check_conditions(conditions: Conditions | Mapping, *, time_limit: float = 5) -> str:
    """Return feasible, infeasible, or unknown; timeouts do not mean impossible."""
    result = enumerate_deals(conditions, lambda _: None, limit=1, time_limit=time_limit)
    if result.count:
        return "feasible"
    return "infeasible" if result.complete else "unknown"
