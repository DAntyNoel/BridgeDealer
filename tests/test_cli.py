import json
import subprocess
import sys

from conftest import ROOT

from bridge_dealer import Deal


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "bridge_dealer", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def report(result):
    return json.loads(result.stderr.splitlines()[-1])


def test_jsonl_and_exhaustion_report():
    result = run("enumerate", "examples/two_unknown_cards.json", "--format", "jsonl")
    assert result.returncode == 0, result.stderr
    deals = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(deals) == 2
    assert report(result)["count"] == 2 and report(result)["complete"] is True
    for record in deals:
        deal = Deal.from_pbn(record["deal"])
        assert record["hcp"] == {s: deal.hcp(s) for s in "NESW"}


def test_four_hands_example_and_limit():
    result = run("enumerate", "examples/four_shapes.json", "--limit", 3, "--time-limit", 15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('[Deal "') == 3
    assert report(result)["complete"] is False
    assert report(result)["stop_reason"] == "limit"


def test_invalid_and_unsatisfiable(tmp_path):
    invalid = tmp_path / "bad.json"
    invalid.write_text('{"N": {"hcp": 10}, "N": {"hcp": 20}}')
    result = run("check", invalid)
    assert result.returncode == 2 and "Duplicate JSON key" in report(result)["error"]
    result = run("check", "examples/impossible.json")
    assert result.returncode == 1 and report(result)["status"] == "infeasible"
    result = run("enumerate", "examples/impossible.json")
    assert result.returncode == 0 and result.stdout == ""
    assert report(result)["complete"] is True and report(result)["count"] == 0


def test_output_file_is_never_silently_overwritten(tmp_path):
    target = tmp_path / "deals.pbn"
    result = run("enumerate", "examples/two_unknown_cards.json", "--output", target)
    assert result.returncode == 0 and result.stdout == ""
    saved = target.read_bytes()
    assert saved.count(b'[Deal "') == 2
    result = run("enumerate", "examples/two_unknown_cards.json", "--output", target)
    assert result.returncode == 2
    assert target.read_bytes() == saved


def test_closed_output_pipe_stops_solver_cleanly():
    child = subprocess.Popen(
        [sys.executable, "-m", "bridge_dealer", "enumerate", "examples/four_hands_85.json"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child.stdout.close()
    child.wait(timeout=30)
    stderr = child.stderr.read()
    child.stderr.close()
    assert child.returncode == 141
    assert "Traceback" not in stderr and "Exception ignored" not in stderr
    summary = json.loads(stderr.splitlines()[-1])
    assert summary["status"] == "output_closed" and summary["complete"] is False


def test_v2_cli_sampling_and_enumeration(tmp_path):
    result = run(
        "random",
        "examples/two_unknown_cards.json",
        "--backend",
        "v2",
        "--format",
        "jsonl",
        "--seed",
        42,
        "--count",
        23,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 23
    assert report(result)["backend"] == "v2"
    assert report(result)["total_deals"] == 2
    again = run(
        "random",
        "examples/two_unknown_cards.json",
        "--backend",
        "v2",
        "--format",
        "jsonl",
        "--seed",
        42,
        "--count",
        23,
    )
    assert result.stdout == again.stdout
    result = run("enumerate", "examples/two_unknown_cards.json", "--backend", "v2")
    assert result.returncode == 0, result.stderr
    assert report(result)["count"] == 2 and report(result)["complete"]
    target = tmp_path / "existing.pbn"
    target.write_text("retain me")
    result = run("random", "examples/two_unknown_cards.json", "--backend", "v2", "--output", target)
    assert result.returncode == 2 and target.read_text() == "retain me"


def test_v2_cli_limits_and_unsupported_conditions(tmp_path):
    result = run("enumerate", "examples/two_unknown_cards.json", "--backend", "v2", "--limit", 1)
    assert result.returncode == 0, result.stderr
    assert report(result)["count"] == 1 and not report(result)["complete"]
    path = tmp_path / "hcp.json"
    path.write_text('{"N": {"hcp": 10}}')
    result = run("random", path, "--backend", "v2")
    assert result.returncode == 2 and "HCP" in report(result)["error"]


def test_v2_closed_output_pipe():
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bridge_dealer",
            "enumerate",
            "examples/two_unknown_cards.json",
            "--backend",
            "v2",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child.stdout.close()
    child.wait(timeout=30)
    stderr = child.stderr.read()
    child.stderr.close()
    assert child.returncode == 141 and "Traceback" not in stderr
    assert json.loads(stderr.splitlines()[-1])["complete"] is False
