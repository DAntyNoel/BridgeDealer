import copy
import http.client
import json
import logging
import threading
from pathlib import Path
from time import perf_counter, sleep

import pytest

from bridge_dealer.cli import _parser, main
from bridge_dealer.dds_native import DDSSolver
from bridge_dealer.web import QUERY_FAILURE, WorkbenchServer, normalize_public_origin

PUBLIC_ORIGIN = "https://bridge.kanami.top"
PUBLIC_HOST = "bridge.kanami.top"


@pytest.fixture
def make_server(tmp_path):
    servers = []

    def make(public_origin=None):
        server = WorkbenchServer(
            ("127.0.0.1", 0),
            templates_path=tmp_path / f"templates-{len(servers)}.json",
            public_origin=public_origin,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    yield make
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(
    server, *, method="GET", host=PUBLIC_HOST, origin=None, headers=(), path=None, body=None
):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        path = path or ("/api/jobs" if method == "GET" else "/api/jobs/missing/cancel")
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            connection.putheader("Host", host)
        if origin is not None:
            connection.putheader("Origin", origin)
        for key, value in headers:
            connection.putheader(key, value)
        body = json.dumps({} if body is None else body).encode() if method == "POST" else None
        if body:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, json.load(response)
    finally:
        connection.close()


def finished_job(server, identity):
    deadline = perf_counter() + 15
    while perf_counter() < deadline:
        code, data = request(server, path="/api/jobs/" + identity, origin=PUBLIC_ORIGIN)
        assert code == 200
        if data["state"] != "running":
            return data
        sleep(0.02)
    pytest.fail("job did not finish")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (PUBLIC_ORIGIN, PUBLIC_ORIGIN),
        (PUBLIC_ORIGIN + "/", PUBLIC_ORIGIN),
        ("https://BRIDGE.KANAMI.TOP:443/", PUBLIC_ORIGIN),
        (PUBLIC_ORIGIN + ":8443", PUBLIC_ORIGIN + ":8443"),
    ],
)
def test_public_origin_normalization(value, expected):
    assert normalize_public_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://bridge.kanami.top",
        "//bridge.kanami.top",
        "https://",
        "https://user@bridge.kanami.top",
        "https://user:secret@bridge.kanami.top",
        PUBLIC_ORIGIN + "/api/jobs",
        PUBLIC_ORIGIN + "//",
        PUBLIC_ORIGIN + "?",
        PUBLIC_ORIGIN + "?x=1",
        PUBLIC_ORIGIN + "#",
        PUBLIC_ORIGIN + "#part",
        PUBLIC_ORIGIN + ":",
        PUBLIC_ORIGIN + ":invalid",
        PUBLIC_ORIGIN + ":65536",
        PUBLIC_ORIGIN + ":0",
        PUBLIC_ORIGIN + ":0443",
        PUBLIC_ORIGIN + " https://evil.example",
        "https://*.example.com",
        "https://one.example,https://two.example",
        "https://one.example,two.example",
        "https://[v1.example]",
        "\n" + PUBLIC_ORIGIN,
        "https://bridge.kanami.top\t",
        "https://bridge.kanami.top\\@evil.example",
        "https://bridge%20kanami.top",
    ],
)
def test_invalid_public_origin_is_rejected_before_listening(value):
    with pytest.raises(ValueError, match="one HTTPS origin"):
        normalize_public_origin(value)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_configured_origin_and_existing_local_access_work(make_server, method):
    server = make_server(PUBLIC_ORIGIN + "/")
    expected = 200 if method == "GET" else 404
    for host, origin in [
        (PUBLIC_HOST, PUBLIC_ORIGIN),
        (f"127.0.0.1:{server.server_port}", f"http://127.0.0.1:{server.server_port}"),
        (f"localhost:{server.server_port}", f"http://localhost:{server.server_port}"),
    ]:
        assert request(server, method=method, host=host, origin=origin)[0] == expected
        assert request(server, method=method, host=host)[0] == expected


def test_default_remains_local_only_even_with_proxy_headers(make_server):
    server = make_server()
    proxy_headers = (
        ("X-Forwarded-Host", PUBLIC_HOST),
        ("X-Forwarded-Proto", "https"),
        ("Forwarded", f"host={PUBLIC_HOST};proto=https"),
        ("CF-Connecting-IP", "127.0.0.1"),
        ("CF-Visitor", '{"scheme":"https"}'),
    )
    for method in ("GET", "POST"):
        assert request(server, method=method)[0] == 403
        assert request(server, method=method, origin=PUBLIC_ORIGIN)[0] == 403
        assert request(server, method=method, headers=proxy_headers)[0] == 403
        local_host = f"127.0.0.1:{server.server_port}"
        expected = 200 if method == "GET" else 404
        assert request(server, method=method, host=local_host)[0] == expected


def test_untrusted_host_origin_and_spoofed_headers_are_rejected(make_server):
    server = make_server(PUBLIC_ORIGIN)
    local_host = f"127.0.0.1:{server.server_port}"
    bad_pairs = [
        ("evil.example", PUBLIC_ORIGIN),
        (PUBLIC_HOST + ".evil.example", PUBLIC_ORIGIN),
        ("evil." + PUBLIC_HOST, PUBLIC_ORIGIN),
        (PUBLIC_HOST + ":443", PUBLIC_ORIGIN),
        (PUBLIC_HOST, "https://evil.example"),
        (PUBLIC_HOST, PUBLIC_ORIGIN + ".evil.example"),
        (PUBLIC_HOST, PUBLIC_ORIGIN + "/"),
        (PUBLIC_HOST, "http://" + PUBLIC_HOST),
        (PUBLIC_HOST, "null"),
        (PUBLIC_HOST, ""),
        (PUBLIC_HOST, "http://" + local_host),
        (local_host, PUBLIC_ORIGIN),
    ]
    proxy_headers = (
        ("X-Forwarded-Host", PUBLIC_HOST),
        ("X-Forwarded-Proto", "https"),
        ("Forwarded", f"for=127.0.0.1;host={PUBLIC_HOST};proto=https"),
        ("CF-Connecting-IP", "127.0.0.1"),
        ("CF-Visitor", '{"scheme":"https"}'),
    )
    for method in ("GET", "POST"):
        for host, origin in bad_pairs:
            assert request(server, method=method, host=host, origin=origin)[0] == 403
        assert request(
            server, method=method, host="evil.example", headers=proxy_headers
        )[0] == 403
        assert request(
            server, method=method, origin="https://evil.example", headers=proxy_headers
        )[0] == 403


def test_ambiguous_or_missing_host_and_duplicate_origins_are_rejected(make_server):
    server = make_server(PUBLIC_ORIGIN)
    for method in ("GET", "POST"):
        assert request(server, method=method, host=None)[0] == 403
        assert request(server, method=method, headers=(("Host", PUBLIC_HOST),))[0] == 403
        assert request(
            server,
            method=method,
            origin=PUBLIC_ORIGIN,
            headers=(("Origin", PUBLIC_ORIGIN),),
        )[0] == 403


def test_http_jobs_omit_nested_dds_paths_without_mutating_research_results(make_server):
    server = make_server(PUBLIC_ORIGIN)
    job = {
        "id": "example",
        "state": "done",
        "started": perf_counter(),
        "request": {"conditions": {}, "dds_library": "/private/request/libdds.so"},
        "progress": {"samples": 85, "nested": [{"dds_library": "/private/progress/libdds.so"}]},
        "result": {
            "status": "exact",
            "samples": 85,
            "dds_library": "/private/result/libdds.so",
            "results": [{"probability": 1, "details": {"dds_library": "/private/libdds.so"}}],
        },
    }
    original = copy.deepcopy(job)
    job["cancel"] = threading.Event()
    server.jobs.items["example"] = job
    code, data = request(server, path="/api/jobs/example", origin=PUBLIC_ORIGIN)
    assert code == 200
    assert "dds_library" not in json.dumps(data)
    assert "/private/" not in json.dumps(data)
    assert data["result"]["status"] == "exact" and data["result"]["samples"] == 85
    assert data["result"]["results"][0]["probability"] == 1
    assert {key: value for key, value in job.items() if key != "cancel"} == original


def test_http_real_dds_load_failure_redacts_result_and_progress_errors(
    make_server, monkeypatch, tmp_path, caplog
):
    missing = tmp_path / "private" / "missing-dds.so"
    monkeypatch.setenv("BRIDGE_DDS_LIBRARY", str(missing))
    server = make_server(PUBLIC_ORIGIN)
    conditions = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/two_unknown_cards.json").read_text()
    )
    with caplog.at_level(logging.ERROR, logger="bridge_dealer.web"):
        code, data = request(
            server,
            method="POST",
            path="/api/jobs",
            origin=PUBLIC_ORIGIN,
            body={"conditions": conditions, "exact": True},
        )
        assert code == 202
        job = finished_job(server, data["id"])
    assert job["result"]["status"] == "error"
    assert job["result"]["errors"] == [QUERY_FAILURE]
    assert job["progress"]["error"] == QUERY_FAILURE
    assert str(missing) not in json.dumps(job)
    original = server.jobs.items[data["id"]]
    assert any(str(missing) in error for error in original["result"]["errors"])
    assert str(missing) in original["progress"]["error"]
    assert str(missing) in caplog.text


def test_http_uncaught_dds_load_exception_is_generic_and_logged(
    make_server, monkeypatch, tmp_path, caplog
):
    missing = tmp_path / "private" / "missing-dds.so"

    def fail_to_load(*args, **kwargs):
        return DDSSolver(missing)

    monkeypatch.setattr("bridge_dealer.web.query_success", fail_to_load)
    server = make_server(PUBLIC_ORIGIN)
    with caplog.at_level(logging.ERROR, logger="bridge_dealer.web"):
        code, data = request(server, method="POST", path="/api/jobs", origin=PUBLIC_ORIGIN)
        assert code == 202
        job = finished_job(server, data["id"])
    assert job["state"] == "error" and job["error"] == QUERY_FAILURE
    assert str(missing) not in json.dumps(job)
    assert str(missing) in caplog.text and "Traceback" in caplog.text


def test_http_template_io_failures_are_generic_and_validation_remains_specific(
    make_server, tmp_path, caplog
):
    server = make_server(PUBLIC_ORIGIN)
    directory = tmp_path / "private" / "template-directory"
    directory.mkdir(parents=True)
    server.templates.path = directory
    with caplog.at_level(logging.ERROR, logger="bridge_dealer.web"):
        for method in ("GET", "POST"):
            code, data = request(
                server,
                method=method,
                path="/api/hand-templates",
                origin=PUBLIC_ORIGIN,
                body={"name": "示例模板", "condition": {}},
            )
            assert code == 500
            assert str(directory) not in json.dumps(data)
            assert "请查看本机服务日志" in data["error"]
    assert str(directory) in caplog.text and directory.is_dir()
    code, data = request(
        server, method="POST", path="/api/jobs", origin=PUBLIC_ORIGIN, body={"threads": True}
    )
    assert code == 400 and "threads must be an integer" in data["error"]


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost"])
def test_server_cannot_bind_beyond_explicit_ipv4_loopback(host, tmp_path):
    with pytest.raises(ValueError, match="listen on 127.0.0.1"):
        WorkbenchServer((host, 0), templates_path=tmp_path / "templates.json")


def test_cli_validates_and_passes_public_origin(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "bridge_dealer.web.serve", lambda port, **options: calls.append((port, options))
    )
    assert main(["serve", "--port", "12707", "--public-origin", PUBLIC_ORIGIN + "/"]) == 0
    assert calls == [(12707, {"public_origin": PUBLIC_ORIGIN})]
    assert _parser().parse_args(["serve"]).public_origin is None
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(["serve", "--public-origin", "http://evil.example"])
    assert error.value.code == 2
