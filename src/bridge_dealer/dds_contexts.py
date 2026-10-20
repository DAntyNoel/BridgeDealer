"""One small-table DDS context per Python worker; ctypes releases the GIL."""

import ctypes as ct
from concurrent.futures import ThreadPoolExecutor
from threading import local

from .dds_native import RANK_MASKS, STRAINS, DDSSolver, _Deal, _Future


class DDSContextPool:
    def __init__(self, path=None, *, threads=4):
        native = DDSSolver(path, threads=threads)
        self.path, self.version, self.lib = native.path, native.version, native.lib
        self.contexts, self.local = [], local()
        self.lib.dds_c_create_solvercontext.argtypes = [ct.c_int, ct.c_int, ct.c_int]
        self.lib.dds_c_create_solvercontext.restype = ct.c_void_p
        self.lib.dds_c_destroy_solvercontext.argtypes = [ct.c_void_p]
        self.lib.dds_c_destroy_solvercontext.restype = None
        self.lib.dds_c_solve_board.argtypes = [
            ct.c_void_p,
            ct.POINTER(_Deal),
            ct.c_int,
            ct.c_int,
            ct.c_int,
            ct.POINTER(_Future),
        ]
        self.lib.dds_c_solve_board.restype = ct.c_int
        self.pool = ThreadPoolExecutor(max_workers=threads, initializer=self._initialize)

    def _initialize(self):
        context = self.lib.dds_c_create_solvercontext(0, 10, 20)
        if not context:
            raise RuntimeError("DDS context allocation failed")
        self.contexts.append(context)
        self.local.context = context

    def _solve(self, job):
        hand, declarer, strains, level = job
        deal = _Deal()
        deal.first = (declarer + 1) % 4
        for p in range(4):
            for suit in range(4):
                deal.cards[p][suit] = RANK_MASKS[hand[p] >> (13 * suit) & 8191]
        target, values = 8 - level, []
        for strain in strains:
            deal.trump = STRAINS.index(strain)
            result = _Future()
            code = self.lib.dds_c_solve_board(
                self.local.context, ct.byref(deal), target, 1, 1, ct.byref(result)
            )
            if code != 1:
                message = ct.create_string_buffer(256)
                self.lib.ErrorMessage(code, message)
                raise RuntimeError(f"DDS {code}: {message.value.decode(errors='replace')}")
            if not 0 <= result.count <= 13 or result.score[0] not in (-1, 0, target):
                raise RuntimeError(f"Unexpected DDS threshold score: {result.score[0]}")
            values.append(result.score[0] < target)
        return tuple(values)

    def success(self, masks, *, declarer, strains, level):
        # map yields in submission order. A slow deal never gets replaced by a
        # fast later deal, and every outcome contains all requested strains.
        jobs = ((hand, declarer, strains, level) for hand in masks)
        return list(self.pool.map(self._solve, jobs))

    def _solve_contracts(self, job):
        hand, groups, count = job
        values = [False] * count
        for (declarer, level), entries in groups.items():
            outcomes = self._solve((hand, declarer, tuple(s for _, s in entries), level))
            for (index, _), outcome in zip(entries, outcomes):
                values[index] = outcome
        return tuple(values)

    def success_contracts(self, masks, contracts):
        """Every physical sample is solved for every requested threshold."""
        groups = {}
        for index, (declarer, level, strain) in enumerate(contracts):
            groups.setdefault((declarer, level), []).append((index, strain))
        return list(
            self.pool.map(self._solve_contracts, ((h, groups, len(contracts)) for h in masks))
        )

    def close(self):
        self.pool.shutdown(wait=True)
        for context in self.contexts:
            self.lib.dds_c_destroy_solvercontext(context)
        self.contexts.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
