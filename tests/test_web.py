import copy
import json
import threading
from pathlib import Path
from time import perf_counter, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from bridge_dealer import Conditions, query_success
from bridge_dealer.cards import hand_text, parse_hand
from bridge_dealer.dds_native import DDSSolver
from bridge_dealer.enumeration import enumerate_deals
from bridge_dealer.query_sampling import Proposals, matches_masks
from bridge_dealer.query_symmetry import strain_representatives
from bridge_dealer.v2 import compile_sampler
from bridge_dealer.web import WorkbenchServer, validate_request

ROOT = Path(__file__).resolve().parents[1]


def test_exclusions_support_more_than_thirteen_cards_and_roundtrip():
    raw = {"N": {"exclude": "AKQJT98765432.AKQJT98765432.."}}
    c = Conditions.from_dict(raw)
    assert len(c.hands[0].exclude) == 26
    assert c.to_dict() == raw
    assert c.fingerprint != Conditions().fingerprint
    with pytest.raises(ValueError, match="both hold and exclude"):
        Conditions.from_dict({"N": {"predeal": "A...", "exclude": "A..."}})
    with pytest.raises(ValueError):
        Conditions.from_dict({"N": {"exclude": "AA..."}})


def test_exclusions_match_independent_exhaustive_oracle(eight_unknown, brute_force):
    raw = copy.deepcopy(eight_unknown)
    # Choose a residual card, rather than relying on enumeration traversal order.
    fixed = set(c for hand in raw.values() for c in parse_hand(hand["predeal"]))
    excluded = min(set(range(52)) - fixed)
    raw["N"]["exclude"] = hand_text((excluded,))
    conditions = Conditions.from_dict(raw)
    expected = {deal.to_pbn() for deal in brute_force if excluded not in deal.hand("N")}
    actual = set()
    result = enumerate_deals(conditions, lambda deal: actual.add(deal.to_pbn()))
    assert result.complete and actual == expected
    proposal = Proposals(conditions, seed=17, compile_deadline=perf_counter() + 1)
    accepted = [m for _ in range(1000) if (m := proposal.next()) is not None]
    assert accepted and all(
        not m[0] >> excluded & 1 and matches_masks(conditions, m) for m in accepted
    )
    with pytest.raises(ValueError, match="excluded"):
        compile_sampler(raw)


def test_excluded_cards_break_suit_symmetry_and_hcp_population_count():
    conditions = Conditions.from_dict({"N": {"exclude": "A...", "hcp": 12}})
    reps = strain_representatives(conditions, ("S", "H", "D", "C", "NT"))
    assert reps == {"S": "S", "H": "H", "D": "H", "C": "H", "NT": "NT"}
    p = Proposals(conditions, seed=1, compile_deadline=perf_counter() + 2)
    assert p.population_size is None
    accepted = [m for _ in range(200) if (m := p.next()) is not None]
    assert accepted and all(not m[0] & 1 for m in accepted)


def test_multicontract_same_census_matches_independent_native_solver():
    raw = json.loads((ROOT / "examples/two_unknown_cards.json").read_text())
    # Removing one allocation must affect both exact and budgeted queries.
    raw["S"]["exclude"] = "...2"
    base_raw = copy.deepcopy(raw)
    del base_raw["S"]["exclude"]
    masks = [m for m in compile_sampler(base_raw).enumerate_masks() if not m[2] >> 51 & 1]
    assert len(masks) == 1
    contracts = [
        {"declarer": d, "level": level, "strain": s}
        for d, level, s in [("N", 3, "S"), ("E", 2, "NT"), ("W", 7, "D"), ("N", 4, "S")]
    ]
    solver = DDSSolver(threads=2)
    expected = [
        sum(
            v[0]
            for v in solver.success(
                masks,
                declarer="NESW".index(c["declarer"]),
                level=c["level"],
                strains=(c["strain"],),
            )
        )
        for c in contracts
    ]
    for exact in (False, True):
        result = query_success(raw, contracts=contracts, exact=exact, budget_seconds=3)
        assert result["status"] == "exact" and result["samples"] == 1
        assert [v["successes"] for v in result["results"]] == expected
        assert result["dds_positions"] == 4
        assert result["requested_contracts"] == contracts


def test_cancellation_interrupts_unlimited_query_without_false_truth():
    event = threading.Event()
    timer = threading.Timer(0.6, event.set)
    timer.start()
    start = perf_counter()
    try:
        result = query_success({}, exact=True, cancel_event=event)
    finally:
        timer.cancel()
    assert perf_counter() - start < 2
    assert result["status"] == "cancelled" and result["worker_terminated"]
    assert all(r["probability"] is None for r in result["results"])


@pytest.fixture
def server(tmp_path):
    http = WorkbenchServer(("127.0.0.1", 0), templates_path=tmp_path / "templates.json")
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}"
    http.shutdown()
    http.server_close()
    thread.join(timeout=2)


def request(base, path, body=None, origin=None):
    req = Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Origin": origin} if origin else {},
    )
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as e:
        return e.code, json.load(e)


def wait_job(server, identity):
    deadline = perf_counter() + 8
    while perf_counter() < deadline:
        status, data = request(server, "/api/jobs/" + identity)
        assert status == 200
        if data["state"] != "running":
            return data
        sleep(0.05)
    pytest.fail("job did not finish")


def test_http_real_dds_census_and_validation(server):
    with urlopen(server, timeout=5) as response:
        assert "桥牌研究室" in response.read().decode()
    raw = json.loads((ROOT / "examples/four_hands_85.json").read_text())
    code, data = request(server, "/api/jobs", {"conditions": raw, "exact": True})
    assert code == 202
    job = wait_job(server, data["id"])
    assert job["state"] == "done" and job["result"]["status"] == "exact"
    assert job["result"]["samples"] == 85
    assert [r["probability"] for r in job["result"]["results"]] == [1, 0, 1, 0, 0]
    code, data = request(
        server, "/api/jobs", {"conditions": {"N": {"predeal": "A...", "exclude": "A..."}}}
    )
    assert code == 400
    assert request(server, "/api/jobs", {}, "https://example.com")[0] == 403
    assert (
        request(server, "/api/jobs", {"contracts": [{"declarer": "X", "level": 2, "strain": "S"}]})[
            0
        ]
        == 400
    )


def test_http_cancel_busy_and_infeasible(server):
    code, data = request(server, "/api/jobs", {"exact": True})
    assert code == 202
    assert request(server, "/api/jobs")[1]["active_id"] == data["id"]
    assert request(server, "/api/jobs", {})[0] == 409
    assert request(server, "/api/jobs/" + data["id"] + "/cancel", {})[0] == 202
    assert wait_job(server, data["id"])["result"]["status"] == "cancelled"
    code, data = request(
        server, "/api/jobs", {"conditions": {p: {"hcp": 11} for p in "NESW"}, "budget_seconds": 3}
    )
    assert code == 202
    assert wait_job(server, data["id"])["result"]["status"] == "infeasible"


def test_http_local_templates_create_update_reopen_and_delete(server, tmp_path):
    from bridge_dealer.templates import HandTemplates

    assert request(server, "/api/hand-templates")[1] == {"templates": []}
    raw = {"name": "均型 15–17", "condition": {"hcp": [15, 17], "shape_any": [4, 3, 3, 3]}}
    code, data = request(server, "/api/hand-templates", raw)
    assert code == 201
    entry = data["template"]
    assert len(entry["condition"]["shapes"]) == 4
    assert HandTemplates(tmp_path / "templates.json").list() == [entry]
    assert request(server, "/api/hand-templates", raw)[0] == 409
    raw["name"] = "改名后的均型"
    assert request(server, "/api/hand-templates/" + entry["id"], raw)[0] == 200
    assert request(server, "/api/hand-templates")[1]["templates"][0]["name"] == raw["name"]
    assert (
        request(server, "/api/hand-templates", {"name": "坏牌型", "condition": {"shape": [1, 2]}})[
            0
        ]
        == 400
    )
    assert request(server, "/api/hand-templates", raw, "https://example.com")[0] == 403
    assert request(server, "/api/hand-templates", origin="https://example.com")[0] == 403
    assert request(server, "/api/hand-templates/missing", raw)[0] == 404
    assert request(server, "/api/hand-templates/" + entry["id"] + "/delete", {})[0] == 200
    assert HandTemplates(tmp_path / "templates.json").list() == []
    assert request(server, "/api/hand-templates/" + entry["id"] + "/delete", {})[0] == 404


def test_http_template_storage_failure_is_reported_without_data_loss(server, tmp_path):
    path = tmp_path / "templates.json"
    path.write_text("{broken")
    assert request(server, "/api/hand-templates")[0] == 500
    assert request(server, "/api/hand-templates", {"name": "新模板", "condition": {}})[0] == 500
    assert path.read_text() == "{broken"


@pytest.mark.parametrize(
    "raw",
    [
        {"exact": 1},
        {"threads": True},
        {"max_samples": 0},
        {"budget_seconds": float("nan")},
        {"contracts": [1]},
        {"typo": True},
    ],
)
def test_bad_web_requests(raw):
    with pytest.raises(ValueError):
        validate_request(raw)
