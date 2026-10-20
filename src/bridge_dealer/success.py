"""Deadline-bounded conditional double-dummy contract success queries."""

import json
import math
import os
import secrets
import selectors
import subprocess
import sys
import tempfile
from time import perf_counter

from .cards import seat_index
from .conditions import as_conditions
from .query_symmetry import strain_representatives

STRAINS = ("S", "H", "D", "C", "NT")


def normalize_contracts(contracts):
    if not isinstance(contracts, (list, tuple)) or not 1 <= len(contracts) <= 140:
        raise ValueError("Choose between 1 and 140 contracts")
    result = []
    for contract in contracts:
        if not isinstance(contract, dict) or set(contract) != {"declarer", "level", "strain"}:
            raise ValueError("Each contract requires declarer, level and strain")
        d = "NESW"[seat_index(contract["declarer"])]
        level, strain = contract["level"], contract["strain"]
        if type(level) is not int or not 1 <= level <= 7 or strain not in STRAINS:
            raise ValueError("Contract level must be 1..7 and strain S,H,D,C,NT")
        value = {"declarer": d, "level": level, "strain": strain}
        if value not in result:
            result.append(value)
    return result


def confidence_sequence(successes, samples, alpha):
    """Jeffreys beta-binomial mixture interval, simultaneous over all prefixes.

    E_n(p)=B(s+1/2,n-s+1/2)/B(1/2,1/2)/[p^s(1-p)^(n-s)].
    Ville's inequality makes {p:E_n(p)<1/alpha} anytime-valid. The caller
    allocates alpha across requested strains for simultaneous family coverage.
    """
    if not samples:
        return [0.0, 1.0]
    log_mix = (
        math.lgamma(successes + 0.5)
        + math.lgamma(samples - successes + 0.5)
        - math.lgamma(samples + 1)
        - 2 * math.lgamma(0.5)
    )
    threshold = math.log(1 / alpha)
    estimate = successes / samples

    def exceeds(p):
        return (
            log_mix
            - (successes * math.log(p) if successes else 0)
            - ((samples - successes) * math.log1p(-p) if samples > successes else 0)
            > threshold
        )

    lower, upper = 0.0, 1.0
    if successes:
        lo, hi = 0.0, estimate
        for _ in range(55):
            mid = (lo + hi) / 2
            if exceeds(mid):
                lo = mid
            else:
                hi = mid
        lower = hi
    if successes < samples:
        lo, hi = estimate, 1.0
        for _ in range(55):
            mid = (lo + hi) / 2
            if exceeds(mid):
                hi = mid
            else:
                lo = mid
        upper = lo
    return [lower, upper]


def _worker_command():
    return [sys.executable, "-m", "bridge_dealer.query_worker"]


def query_success(
    conditions,
    *,
    declarer="N",
    strains=STRAINS,
    level=3,
    budget_seconds=20.0,
    max_samples=100_000,
    min_samples=10_000,
    seed=None,
    threads=8,
    dds_library=None,
    confidence=0.95,
    suit_symmetry=True,
    exact=False,
    on_progress=None,
    contracts=None,
    cancel_event=None,
    _started_at=None,
):
    """Return an exact or Monte Carlo DD success result within a wall-clock budget.

    Native calls run in a disposable subprocess. Parent reserves time for killing
    a straggling solve, draining checkpoints and formatting the result. OS-level
    scheduling and caller-side display/file I/O are outside real-time control.
    """
    start = perf_counter() if _started_at is None else _started_at
    if type(exact) is not bool:
        raise ValueError("exact must be a boolean")
    if on_progress is not None and not callable(on_progress):
        raise ValueError("on_progress must be callable")
    if exact:
        budget_seconds = None
    elif (
        type(budget_seconds) not in (int, float)
        or not math.isfinite(budget_seconds)
        or budget_seconds <= 0
    ):
        raise ValueError("budget_seconds must be finite and positive")
    if type(confidence) not in (int, float) or not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if type(suit_symmetry) is not bool:
        raise ValueError("suit_symmetry must be a boolean")
    for value, name in (
        (max_samples, "max_samples"),
        (min_samples, "min_samples"),
        (threads, "threads"),
    ):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if threads > 32:
        raise ValueError("threads must not exceed 32")
    if type(level) is not int or not 1 <= level <= 7:
        raise ValueError("level must be an integer in 1..7")
    declarer_index = seat_index(declarer)
    if isinstance(strains, str):
        strains = strains.split(",")
    strains = tuple(dict.fromkeys(str(s).upper().strip() for s in strains))
    if not strains or any(s not in STRAINS for s in strains):
        raise ValueError("strains must contain S,H,D,C,NT")
    seed = secrets.randbits(32) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an integer in 0..4294967295")
    conditions = as_conditions(conditions)
    representatives = (
        strain_representatives(conditions, strains)
        if suit_symmetry
        else {strain: strain for strain in strains}
    )
    solved_strains = tuple(dict.fromkeys(representatives.values()))
    requested = (
        normalize_contracts(contracts)
        if contracts is not None
        else [{"declarer": declarer.upper(), "level": level, "strain": s} for s in strains]
    )
    contract_reps = (
        strain_representatives(conditions, STRAINS) if suit_symmetry else {s: s for s in STRAINS}
    )
    solved_contracts, indices = [], []
    for contract in requested:
        key = (
            seat_index(contract["declarer"]),
            contract["level"],
            contract_reps[contract["strain"]],
        )
        if key not in solved_contracts:
            solved_contracts.append(key)
        indices.append(solved_contracts.index(key))
    if contracts is None:
        # Preserve the original representative ordering and worker protocol.
        indices = [solved_strains.index(representatives[s]) for s in strains]
    solve_count = len(solved_contracts) if contracts is not None else len(solved_strains)
    reserve = None if exact else min(1.0, budget_seconds * 0.15)
    kill_at = None if exact else start + budget_seconds - reserve
    request = {
        "conditions": conditions.to_dict(),
        "declarer": declarer_index,
        "strains": solved_strains,
        "level": level,
        "seed": seed,
        "threads": threads,
        "max_samples": max_samples,
        "dds_library": str(dds_library) if dds_library else None,
        "deadline": None if exact else kill_at - min(0.12, budget_seconds * 0.03),
        "exact": exact,
    }
    if contracts is not None:
        request["contracts"] = solved_contracts
    state = {
        "samples": 0,
        "successes": [0] * solve_count,
        "generated": 0,
        "proposed": 0,
        "method": "monte_carlo",
        "population_size": None,
        "sampling_seconds": 0.0,
        "dds_seconds": 0.0,
    }
    stopped, errors, terminated = "time_budget", [], False
    if exact or perf_counter() < kill_at:
        with tempfile.TemporaryFile(mode="w+b") as source:
            source.write(json.dumps(request).encode())
            source.seek(0)
            process = subprocess.Popen(
                _worker_command(),
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            selector = selectors.DefaultSelector()
            buffers = {"stdout": b"", "stderr": b""}
            try:
                for name in buffers:
                    stream = getattr(process, name)
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                done = False
                last_progress = -math.inf
                while selector.get_map() and not done:
                    if cancel_event is not None and cancel_event.is_set():
                        stopped, terminated = "cancelled", True
                        break
                    remaining = None if exact else kill_at - perf_counter()
                    if remaining is not None and remaining <= 0:
                        terminated = True
                        break
                    timeout = min(0.2, remaining) if remaining is not None else 0.2
                    for key, _ in selector.select(timeout):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        name = key.data
                        if name == "stderr":
                            buffers[name] = (buffers[name] + chunk)[-8192:]
                            continue
                        buffers[name] += chunk
                        while b"\n" in buffers[name]:
                            line, buffers[name] = buffers[name].split(b"\n", 1)
                            message = json.loads(line)
                            state = message
                            now = perf_counter()
                            if on_progress is not None and (
                                now - last_progress >= 2 or message["type"] in ("done", "error")
                            ):
                                on_progress({**message, "elapsed_seconds": now - start})
                                last_progress = now
                            if message["type"] == "error":
                                errors.append(message["error"])
                                stopped, done = "worker_error", True
                            elif message["type"] == "done":
                                stopped, done = message["stop_reason"], True
                if not done and not terminated:
                    stopped = "worker_error"
                    errors.append("DDS worker exited without a final result")
            finally:
                selector.close()
                if process.poll() is None:
                    process.kill()
                try:
                    cleanup_time = (
                        0.2
                        if exact
                        else max(0.01, min(0.2, start + budget_seconds - perf_counter()))
                    )
                    process.wait(timeout=cleanup_time)
                except subprocess.TimeoutExpired:
                    errors.append("Worker cleanup exceeded its reserve")
                process.stdout.close()
                process.stderr.close()
            if errors and buffers["stderr"]:
                errors.append(buffers["stderr"].decode(errors="replace"))
    samples = state["samples"]
    is_exact = state["method"] == "enumeration" and stopped == "exhausted"
    if errors:
        status = "error"
    elif stopped == "cancelled":
        status = "cancelled"
    elif stopped == "infeasible":
        status = "infeasible"
    elif is_exact:
        status = "exact"
    elif samples < min_samples:
        status = "insufficient_samples"
    else:
        status = "estimated"
    results = []
    for contract, index in zip(requested, indices):
        successes = state["successes"][index]
        if status in ("infeasible", "error"):
            probability, interval = None, None
        elif is_exact:
            probability = successes / samples
            interval = [probability, probability]
        elif state["method"] == "enumeration":
            # A deterministic census prefix is not an iid random sample.
            population = state["population_size"]
            probability = None
            interval = (
                [successes / population, (successes + population - samples) / population]
                if population
                else [0.0, 1.0]
            )
        else:
            probability = successes / samples if samples else None
            interval = confidence_sequence(successes, samples, (1 - confidence) / solve_count)
        results.append(
            {
                **contract,
                "required_tricks": contract["level"] + 6,
                "successes": successes,
                "samples": samples,
                "probability": probability,
                "interval": interval,
            }
        )
    result = {
        "status": status,
        "stop_reason": stopped,
        "budget_seconds": budget_seconds,
        "seed": seed,
        "max_samples": None if exact else max_samples,
        "query_mode": "exact" if exact else "estimate",
        "min_samples": min_samples,
        "method": state["method"],
        "population_size": state["population_size"],
        "strategy": state.get("strategy"),
        "samples": samples,
        "suit_symmetry": suit_symmetry,
        "solved_strains": list(dict.fromkeys(c[2] for c in solved_contracts))
        if contracts is not None
        else list(solved_strains),
        "strain_representatives": contract_reps if contracts is not None else representatives,
        "same_deals_across_strains": all(
            c["strain"] == solved_contracts[i][2] for c, i in zip(requested, indices)
        )
        if contracts is not None
        else len(solved_strains) == len(strains),
        "dds_positions": samples * solve_count,
        "requested_contracts": requested,
        "solved_contracts": solved_contracts if contracts is not None else None,
        "generated": state["generated"],
        "unresolved": state["generated"] - samples,
        "proposed": state["proposed"],
        "worker_terminated": terminated,
        "sampling_seconds": state["sampling_seconds"],
        "dds_seconds": state["dds_seconds"],
        "dds_version": state.get("dds_version"),
        "dds_library": state.get("dds_library"),
        "dds_engine": state.get("dds_engine"),
        "inference": "exact"
        if is_exact
        else "census_bounds"
        if state["method"] == "enumeration"
        else "time_uniform_beta_binomial",
        "confidence": confidence,
        "simultaneous_strain_coverage": True,
        "interpretation": "Conditional double-dummy success under best play by both sides",
        "conditions_sha256": conditions.fingerprint,
        "results": results,
    }
    if errors:
        result["errors"] = errors
    result["elapsed_seconds"] = perf_counter() - start
    result["within_budget"] = None if exact else result["elapsed_seconds"] <= budget_seconds
    return result
