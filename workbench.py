"""Serve the local migration workbench and its optional agent repair review."""
import argparse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import gzip
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from demo import approved, canonical, cases
from mcp_replay import connect, plan_argument
from rehearsal import CONTRACT_SHA256, contract_spec, fingerprint, plan_family, rehearse_locally
from repair import (
    REVIEWER_CHOICES, RepairUnavailable, call_model, eligible, live_endpoint, recorded_proposal,
    review_proposal, reviewer_proposal,
)

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
CASES = {case["id"]: case for case in cases()}
DEMO_CASES = ("whole_dollars", "round_cents", "missing_backfill_scope",
              "currency_conversion", "broken_reference", "partial_backfill", "playlist_swapped", "playlist_preserved")
STATE_LIMIT = 32


def describe_case(case_id):
    case = CASES[case_id]
    plan = canonical(case["plan"])
    family = plan_family(case["plan"])
    return {**case, "plan_json": plan, "sha256": fingerprint(plan),
            "family": family, "contract_pin": contract_spec(family)[1]}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/cases":
            return self.respond({
                "cases": [describe_case(case_id) for case_id in DEMO_CASES],
                "runtime": self.server.runtime, "contract_pin": CONTRACT_SHA256,
            })
        reports = {
            "/api/bench": "mcp-benchmark-results.json" if self.server.mcp_path else "benchmark-results.json",
            "/api/trace": "integration-results.json",
            "/api/comparison": "live-comparison-results.json",
        }
        if path in reports:
            artifact = ROOT / reports[path]
            data = json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else {}
            if path == "/api/comparison":
                payload = json.dumps(data, separators=(",", ":")).encode()
                encodings = self.headers.get("Accept-Encoding", "").split(",")
                if any(encoding.strip() == "gzip" for encoding in encodings):
                    return self.respond(gzip.compress(payload), content_encoding="gzip")
                return self.respond(payload)
            return self.respond(data)
        assets = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                  "/app.js": ("app.js", "text/javascript")}
        if path in assets:
            file, kind = assets[path]
            return self.respond((UI / file).read_bytes(), content_type=kind)
        self.respond({"error": "Unknown resource."}, 404)

    def do_POST(self):
        routes = {"/api/rehearse": self.rehearse,
                  "/api/repair/propose": self.propose, "/api/repair/run": self.verify_repair}
        if self.path not in routes:
            return self.respond({"error": "Unknown resource."}, 404)
        origin = self.headers.get("Origin")
        valid_origins = {f"http://127.0.0.1:{self.server.server_port}",
                         f"http://localhost:{self.server.server_port}"}
        if origin and origin not in valid_origins:
            return self.respond({"error": "Open the workbench on the local machine."}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.respond({"error": "JSON data required."}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, RecursionError):
            return self.respond({"error": "Invalid request."}, 400)
        try:
            routes[self.path](data)
        except RepairUnavailable as exc:
            self.respond({"error": str(exc)}, 422)
        except (OSError, RuntimeError) as exc:
            self.log_error("%s", exc)
            self.respond({"error": "The rehearsal server could not finish the run."}, 503)

    def rehearse(self, data):
        case_id = data.get("case_id")
        if not isinstance(case_id, str) or case_id not in CASES:
            return self.respond({"error": "Choose a known candidate."}, 400)
        plan = CASES[case_id]["plan"]
        encoded = canonical(plan)
        if data.get("confirm_plan_sha256") != fingerprint(encoded):
            return self.respond({"error": "Confirm this candidate before running."}, 403)
        report = self.server.run_case(encoded)
        if eligible(plan, report):
            token = self.server.store(self.server.blocked_runs, (plan, report))
            choices = {key: label for key, label in REVIEWER_CHOICES.items()
                       if key != plan["backfills"][0]["expression"]}
            return self.respond({**report, "repair_run_id": token,
                                 "repair_mode": self.server.agent_mode(report), "reviewer_choices": choices})
        self.respond(report)

    def propose(self, data):
        entry = self.server.lookup(self.server.blocked_runs, data.get("run_id"))
        if entry is None:
            return self.respond({"error": "Rehearse the faulty candidate before requesting a proposal."}, 404)
        plan, report = entry
        if "reviewer_expression" in data:
            proposal = reviewer_proposal(plan, report, data["reviewer_expression"])
        else:
            proposal = self.server.propose_repair(plan, report)
        token = self.server.store(self.server.drafts, proposal)
        self.respond({**proposal, "proposal_id": token})

    def verify_repair(self, data):
        token = data.get("proposal_id")
        if not isinstance(token, str):
            return self.respond({"error": "Review a proposal before running it."}, 404)
        with self.server.state_lock:
            draft = self.server.drafts.get(token)
            if draft is None:
                return self.respond({"error": "Review a fresh proposal before running it."}, 404)
            if data.get("confirm_plan_sha256") != draft["draft_sha256"]:
                return self.respond({"error": "Confirm the exact draft before running it."}, 403)
            self.server.drafts.pop(token)
        report = self.server.run_case(canonical(draft["draft"]))
        self.respond(report)

    def respond(self, payload, status=200, content_type="application/json", *, content_encoding=None):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)


class Workbench(ThreadingHTTPServer):
    def __init__(self, port, mcp_path, model_url=None, model=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.mcp_path = mcp_path
        self.model_url = model_url
        self.model = model
        self.recording = None
        if not model_url:
            try:
                self.recording = recorded_proposal()
            except RepairUnavailable:
                pass
        self.runtime = "Local Python"
        if mcp_path:
            self.runtime = ("Synapse Custom" if mcp_path.name == "rehearse_database_migration_server.py"
                            else "Composed MCP")
        self.guard = threading.Lock()
        self.state_lock = threading.Lock()
        self.blocked_runs = OrderedDict()
        self.drafts = OrderedDict()

    def agent_mode(self, report):
        if self.model_url:
            return "live"
        if self.recording and self.recording["input_sha256"] == report["plan_sha256"]:
            return "recorded"
        return None

    def lookup(self, cache, token):
        if not isinstance(token, str):
            return None
        with self.state_lock:
            return cache.get(token)

    def store(self, cache, value):
        token = uuid4().hex
        with self.state_lock:
            cache[token] = value
            if len(cache) > STATE_LIMIT:
                cache.popitem(last=False)
        return token

    def propose_repair(self, plan, report):
        if self.model_url:
            receipt = call_model(plan, report, self.model_url, self.model)
            return review_proposal(plan, report, receipt, "live")
        if self.recording is None:
            raise RepairUnavailable("The recorded agent proposal is unavailable.")
        return review_proposal(plan, report, self.recording, "recorded")

    def run_case(self, plan):
        # The HTTP handler grants this process permission for the one confirmed digest.
        with self.guard, approved([plan]):
            if self.mcp_path is None:
                return rehearse_locally(plan)
            with connect(self.mcp_path) as (session, tools):
                if len(tools) != 1:
                    raise RuntimeError("Expected a composed MCP server")
                tool = tools[0]
                report = session.call(tool["name"], plan_argument(tool, plan))
                if "verdict" not in report:
                    raise RuntimeError("MCP tool returned no verdict")
                return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server", type=Path, help="Run through this composed MCP server")
    parser.add_argument("--model-url", type=live_endpoint, help="Optional loopback OpenAI-compatible /v1 endpoint")
    parser.add_argument("--model", default="qwen2.5-coder-1.5b", help="Model ID for live repair proposals")
    args = parser.parse_args()
    server_path = args.server.resolve(strict=True) if args.server else None
    with Workbench(args.port, server_path, args.model_url, args.model) as server:
        print(f"Workbench: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
