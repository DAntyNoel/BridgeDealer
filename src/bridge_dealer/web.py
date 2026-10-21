"""Local HTML workbench and cancellable, single-flight DDS jobs."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from .conditions import Conditions
from .success import normalize_contracts, query_success
from .templates import DEFAULT_TEMPLATE_PATH, HandTemplates, TemplateConflict

STATIC = Path(__file__).with_name("static")
DEFAULT_CONTRACTS = [{"declarer": "N", "level": 3, "strain": s} for s in ("S", "H", "D", "C", "NT")]


def validate_request(raw):
    if not isinstance(raw, dict) or set(raw) - {
        "conditions",
        "contracts",
        "exact",
        "budget_seconds",
        "max_samples",
        "min_samples",
        "threads",
        "seed",
    }:
        raise ValueError("Invalid query fields")
    conditions = Conditions.from_dict(raw.get("conditions", {}))
    supplied = raw.get("contracts", DEFAULT_CONTRACTS)
    contracts = normalize_contracts(DEFAULT_CONTRACTS if supplied == [] else supplied)
    exact = raw.get("exact", False)
    if type(exact) is not bool:
        raise ValueError("exact must be a boolean")
    options = {
        "contracts": contracts,
        "exact": exact,
        "budget_seconds": raw.get("budget_seconds", 20),
    }
    budget = options["budget_seconds"]
    if type(budget) not in (float, int) or not 1 <= budget <= 3600:
        raise ValueError("Time budget must be 1..3600 seconds")
    for key, default, lo, hi in (
        ("max_samples", 100000, 1, 10000000),
        ("min_samples", 10000, 1, 10000000),
        ("threads", 8, 1, 32),
    ):
        value = raw.get(key, default)
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"{key} must be an integer in {lo}..{hi}")
        options[key] = value
    if "seed" in raw:
        if type(raw["seed"]) is not int or not 0 <= raw["seed"] < 2**32:
            raise ValueError("seed must be an integer in 0..4294967295")
        options["seed"] = raw["seed"]
    return conditions, options


class Jobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}
        self.worker = None

    def start(self, raw):
        conditions, options = validate_request(raw)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RuntimeError("已有解析正在运行，请等待完成或先取消。")
            while len(self.items) >= 20:
                del self.items[next(iter(self.items))]
            identity = secrets.token_hex(12)
            event = threading.Event()
            job = {
                "id": identity,
                "state": "running",
                "progress": {"samples": 0},
                "started": perf_counter(),
                "cancel": event,
                "request": {"conditions": conditions.to_dict(), **options},
            }
            self.items[identity] = job

            def progress(value):
                with self.lock:
                    job["progress"] = {k: v for k, v in value.items() if k != "result"}
                    if "result" in value:
                        job["result"] = value["result"]

            def run():
                try:
                    result = query_success(
                        conditions, **options, on_progress=progress, cancel_event=event
                    )
                    with self.lock:
                        job.update(
                            result=result,
                            state="cancelled" if result["status"] == "cancelled" else "done",
                        )
                except Exception as error:
                    with self.lock:
                        job.update(state="error", error=str(error))
                        # A previously published prefix remains useful, but it is final now.
                        if "result" in job:
                            job["result"] = {
                                **job["result"],
                                "status": "error",
                                "running": False,
                                "stop_reason": "worker_error",
                                "errors": [str(error)],
                                "results": [
                                    {**item, "probability": None, "interval": None}
                                    for item in job["result"]["results"]
                                ],
                            }

            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
            return identity

    def snapshot(self, identity):
        with self.lock:
            job = self.items[identity]
            return {k: v for k, v in job.items() if k not in ("cancel", "started")} | {
                "elapsed_seconds": perf_counter() - job["started"]
                if job["state"] == "running"
                else job.get("result", {}).get("elapsed_seconds", 0)
            }

    def cancel(self, identity):
        with self.lock:
            self.items[identity]["cancel"].set()

    def close(self):
        with self.lock:
            for job in self.items.values():
                job["cancel"].set()
        if self.worker:
            self.worker.join(timeout=3)


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, templates_path=DEFAULT_TEMPLATE_PATH):
        self.jobs = Jobs()
        self.templates = HandTemplates(templates_path)
        super().__init__(address, Handler)

    def server_close(self):
        self.jobs.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def same_origin(self):
        # Host and Origin validation also prevents DNS rebinding and cross-site submissions.
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (origin is None or origin == "http://" + host)

    def do_GET(self):
        if not self.same_origin():
            return self.respond(403, {"error": "Only local same-origin requests are allowed"})
        path = urlsplit(self.path).path
        if path == "/api/hand-templates":
            try:
                return self.respond(200, {"templates": self.server.templates.list()})
            except OSError as error:
                return self.respond(500, {"error": f"无法读取本地模板：{error}"})
        if path == "/api/jobs":
            with self.server.jobs.lock:
                active = next(
                    (j["id"] for j in self.server.jobs.items.values() if j["state"] == "running"),
                    None,
                )
            return self.respond(200, {"active_id": active})
        if path.startswith("/api/jobs/"):
            try:
                return self.respond(200, self.server.jobs.snapshot(path.split("/")[-1]))
            except KeyError:
                return self.respond(404, {"error": "解析任务不存在"})
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        if path not in files:
            return self.respond(404, {"error": "Not found"})
        name, mime = files[path]
        data = (STATIC / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.same_origin():
            return self.respond(403, {"error": "Only local same-origin requests are allowed"})
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self.server.jobs.cancel(path.split("/")[3])
                return self.respond(202, {"state": "cancelling"})
            parts = path.strip("/").split("/")
            template_route = parts[:2] == ["api", "hand-templates"] and (
                len(parts) in (2, 3) or len(parts) == 4 and parts[3] == "delete"
            )
            if path != "/api/jobs" and not template_route:
                return self.respond(404, {"error": "Not found"})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 131072:
                raise ValueError("Request must be a JSON object no larger than 128 KB")
            raw = json.loads(self.rfile.read(length))
            if template_route:
                if len(parts) == 4:
                    self.server.templates.delete(parts[2])
                    return self.respond(200, {"deleted": True})
                entry = self.server.templates.save(raw, parts[2] if len(parts) == 3 else None)
                return self.respond(201 if len(parts) == 2 else 200, {"template": entry})
            identity = self.server.jobs.start(raw)
            self.respond(202, {"id": identity})
        except TemplateConflict as error:
            self.respond(409, {"error": str(error)})
        except (ValueError, TypeError) as error:
            self.respond(400, {"error": str(error)})
        except KeyError:
            self.respond(404, {"error": "模板或解析任务不存在"})
        except RuntimeError as error:
            self.respond(409, {"error": str(error)})
        except OSError as error:
            self.respond(500, {"error": f"无法保存本地模板：{error}"})


def serve(port=8789):
    with WorkbenchServer(("127.0.0.1", port)) as server:
        print(f"桥牌研究室 · http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
