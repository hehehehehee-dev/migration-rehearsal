"""Serve the migration workbench on loopback."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from demo import approved, canonical, cases
from mcp_replay import connect, plan_argument
from rehearsal import CONTRACT_SHA256, fingerprint, rehearse_locally
import threading

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
CASES = {case["id"]:case for case in cases()}
DEMO_CASES = ("whole_dollars", "round_cents", "missing_backfill_scope",
              "currency_conversion", "broken_reference", "partial_backfill")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/cases":
            return self.respond({
                "cases":[{**CASES[id], "plan_json":canonical(CASES[id]["plan"]),
                           "sha256":fingerprint(canonical(CASES[id]["plan"]))} for id in DEMO_CASES],
                "runtime": self.server.runtime, "contract_pin": CONTRACT_SHA256,
            })
        if path in ("/api/bench", "/api/trace"):
            artifacts = (("mcp-benchmark-results.json" if self.server.mcp_path else "benchmark-results.json")
                         if path.endswith("bench") else "integration-results.json")
            artifact = ROOT / artifacts
            return self.respond(json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else {})
        assets = {"/":("index.html","text/html"),"/style.css":("style.css","text/css"),
                  "/app.js":("app.js","text/javascript")}
        if path in assets:
            file, kind = assets[path]
            return self.respond((UI/file).read_bytes(), content_type=kind)
        self.respond({"error":"Unknown resource."}, 404)

    def do_POST(self):
        if self.path != "/api/rehearse":
            return self.respond({"error":"Unknown resource."}, 404)
        origin = self.headers.get("Origin")
        valid_origins = {f"http://127.0.0.1:{self.server.server_port}",
                         f"http://localhost:{self.server.server_port}"}
        if origin and origin not in valid_origins:
            return self.respond({"error":"Open the workbench on the local machine."}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.respond({"error":"JSON data required."}, 415)
        try:
            length = int(self.headers.get("Content-Length","0"))
            if not 0 < length <= 2048:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            case = CASES[data["case_id"]]
            plan = canonical(case["plan"])
            if data.get("confirm_plan_sha256") != fingerprint(plan):
                return self.respond({"error":"Confirm this candidate before running."}, 403)
        except (ValueError,KeyError,TypeError):
            return self.respond({"error":"Choose a known candidate."}, 400)
        try:
            report = self.server.run_case(plan)
        except (OSError,RuntimeError) as exc:
            self.log_error("%s",exc)
            return self.respond({"error":"The rehearsal server could not finish the run."}, 503)
        self.respond(report)

    def respond(self, payload, status=200, content_type="application/json"):
        if not isinstance(payload,bytes):
            payload=json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type",content_type+"; charset=utf-8")
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

class Workbench(ThreadingHTTPServer):
    def __init__(self, port, mcp_path):
        super().__init__(("127.0.0.1",port), Handler)
        self.mcp_path = mcp_path
        self.runtime = "Local Python"
        if mcp_path:
            self.runtime = ("Synapse Custom" if mcp_path.name == "rehearse_database_migration_server.py"
                            else "Composed MCP")
        self.guard = threading.Lock()

    def run_case(self, plan):
        # The HTTP handler grants this process permission for the one confirmed digest.
        with self.guard, approved([plan]):
            if self.mcp_path is None:
                return rehearse_locally(plan)
            with connect(self.mcp_path) as (session,tools):
                if len(tools)!=1:
                    raise RuntimeError("Expected a composed MCP server")
                tool=tools[0]
                report = session.call(tool["name"],plan_argument(tool,plan))
                if "verdict" not in report:
                    raise RuntimeError("MCP tool returned no verdict")
                return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--server",type=Path,help="Run through this composed MCP server")
    args=parser.parse_args()
    server_path = args.server.resolve(strict=True) if args.server else None
    with Workbench(args.port,server_path) as server:
        print(f"Workbench: http://127.0.0.1:{server.server_port}",flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
