"""Small binary DDS 3.1 C-API adapter; loaded only in the query worker."""

import ctypes as ct
import os
from pathlib import Path

STRAINS = ("S", "H", "D", "C", "NT")
MAX_BOARDS = 200


class _Deal(ct.Structure):
    _fields_ = [
        ("trump", ct.c_int),
        ("first", ct.c_int),
        ("current_suit", ct.c_int * 3),
        ("current_rank", ct.c_int * 3),
        ("cards", (ct.c_uint * 4) * 4),
    ]


class _Boards(ct.Structure):
    _fields_ = [
        ("count", ct.c_int),
        ("deals", _Deal * MAX_BOARDS),
        ("target", ct.c_int * MAX_BOARDS),
        ("solutions", ct.c_int * MAX_BOARDS),
        ("mode", ct.c_int * MAX_BOARDS),
    ]


class _Future(ct.Structure):
    _fields_ = [
        ("nodes", ct.c_int),
        ("count", ct.c_int),
        ("suit", ct.c_int * 13),
        ("rank", ct.c_int * 13),
        ("equals", ct.c_int * 13),
        ("score", ct.c_int * 13),
    ]


class _Solved(ct.Structure):
    _fields_ = [("count", ct.c_int), ("results", _Future * MAX_BOARDS)]


# Our card bit 0 is A, DDS rank bit 14 is A. Suits remain SHDC.
RANK_MASKS = tuple(sum(1 << (14 - r) for r in range(13) if value >> r & 1) for value in range(8192))


def library_path(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("BRIDGE_DDS_LIBRARY"):
        return Path(os.environ["BRIDGE_DDS_LIBRARY"]).expanduser().resolve()
    root = Path(__file__).resolve().parents[2] / "lib"
    for name in ("libdds.dylib", "libdds.so", "dds.dll"):
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(
        "DDS library not found; run uv run python scripts/build_dds.py in the checkout, "
        "or supply --dds-library / BRIDGE_DDS_LIBRARY"
    )


class DDSSolver:
    def __init__(self, path=None, *, threads=4):
        self.path = library_path(path)
        self.threads = threads
        self.lib = ct.CDLL(str(self.path))
        # GetDDSInfo writes the documented DDSInfo structure, below 2 KiB.
        info = ct.create_string_buffer(4096)
        self.lib.GetDDSInfo.argtypes = [ct.c_void_p]
        self.lib.GetDDSInfo.restype = None
        self.lib.GetDDSInfo(info)
        version = tuple((ct.c_int * 3).from_buffer(info))
        if version != (3, 1, 0):
            raise RuntimeError(f"Expected DDS 3.1.0 ABI, received {version}")
        self.version = ".".join(map(str, version))
        self.lib.SolveAllBoardsBinN.argtypes = [ct.POINTER(_Boards), ct.POINTER(_Solved), ct.c_int]
        self.lib.SolveAllBoardsBinN.restype = ct.c_int
        self.lib.ErrorMessage.argtypes = [ct.c_int, ct.c_char_p]
        self.lib.ErrorMessage.restype = None

    def success(self, masks, *, declarer, strains, level):
        """Return one boolean per deal/strain; first player is the opening defender."""
        count = len(masks) * len(strains)
        if not 1 <= count <= MAX_BOARDS:
            raise ValueError("DDS batches must contain 1..200 deal/strain positions")
        boards, solved = _Boards(), _Solved()
        boards.count = count
        target = 8 - level  # Defense needs 14 - (6 + level) tricks to defeat.
        index = 0
        for hand in masks:
            cards = tuple(tuple(RANK_MASKS[m >> (13 * s) & 8191] for s in range(4)) for m in hand)
            for strain in strains:
                deal = boards.deals[index]
                deal.trump, deal.first = STRAINS.index(strain), (declarer + 1) % 4
                for p in range(4):
                    for s in range(4):
                        deal.cards[p][s] = cards[p][s]
                boards.target[index], boards.solutions[index], boards.mode[index] = target, 1, 1
                index += 1
        code = self.lib.SolveAllBoardsBinN(ct.byref(boards), ct.byref(solved), self.threads)
        if code != 1:
            message = ct.create_string_buffer(256)
            self.lib.ErrorMessage(code, message)
            raise RuntimeError(f"DDS {code}: {message.value.decode(errors='replace')}")
        if solved.count != count:
            raise RuntimeError("DDS returned an incomplete batch")
        outcomes = []
        for i in range(count):
            result = solved.results[i]
            if not 0 <= result.count <= 13 or result.score[0] not in (-1, 0, target):
                raise RuntimeError(f"Unexpected DDS threshold score: {result.score[0]}")
            # DDS uses both -1 and 0 for failure to reach a positive target.
            outcomes.append(result.score[0] < target)
        width = len(strains)
        return [tuple(outcomes[i : i + width]) for i in range(0, count, width)]
