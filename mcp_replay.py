"""Verify the two MCP surfaces and optionally compare the same local model."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
import time
import urllib.request

from demo import approved, benchmark, canonical, cases
from rehearsal import PHASES

ROOT = Path(__file__).resolve().parent

class Session:
    def __init__(self, server):
        env = os.environ.copy()
        env["PYTHONUTF8"] = env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [
            str(ROOT/".tooling"/"python"),
            str(ROOT/".tooling"/"python"/"win32"),
            str(ROOT/".tooling"/"python"/"win32"/"lib"),
            str(ROOT/".tooling"/"python"/"pywin32_system32"),
            str(ROOT), env.get("PYTHONPATH"),
        ]))
        self.server = subprocess.Popen([sys.executable, "-B", str(server)], cwd=ROOT,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       text=True, encoding="utf-8", env=env)
        self.responses = Queue()
        self.request_id = 0

        def read_stdout():
            for line in self.server.stdout:
                self.responses.put(json.loads(line))
            self.responses.put(None)

        Thread(target=read_stdout, daemon=True).start()

    def send(self, method, params=None, request=True):
        self.request_id += 1
        packet = {"jsonrpc":"2.0", "method":method}
        if params is not None:
            packet["params"] = params
        if request:
            packet["id"] = self.request_id
        self.server.stdin.write(json.dumps(packet)+"\n")
        self.server.stdin.flush()
        if not request:
            return None
        while True:
            response = self.responses.get(timeout=20)
            if response is None:
                raise RuntimeError("MCP server disconnected")
            if response.get("id") == self.request_id:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["result"]

    def call(self, name, arguments):
        result = self.send("tools/call", {"name":name,"arguments":arguments})
        if result.get("isError"):
            return {"error": result.get("content")}
        payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        while isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        return payload

    def close(self):
        self.server.stdin.close()
        try:
            self.server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.server.terminate()
            self.server.wait(timeout=3)
        self.server.stdout.close()

@contextmanager
def connect(server):
    session = Session(server)
    try:
        session.send("initialize",{
            "protocolVersion":"2025-03-26","capabilities":{},
            "clientInfo":{"name":"migration-rehearsal","version":"1"},
        })
        session.send("notifications/initialized", request=False)
        yield session, session.send("tools/list")["tools"]
    finally:
        session.close()

def plan_argument(tool, plan):
    required = tool["inputSchema"].get("required", [])
    if len(required) != 1:
        raise RuntimeError(f"Expected one plan argument, found {required}")
    return {required[0]: plan}


def agent_outcome(report, call_count):
    if report is None:
        unit = "call" if call_count == 1 else "calls"
        return {"verdict": "incomplete", "tool_issue_codes": [],
                "agent_response": f"Incomplete. No tool returned a verdict after {call_count} {unit}."}

    verdict = report["verdict"]
    issues = report["issues"]
    codes = sorted(issues)
    if codes:
        reasons = []
        for code in codes:
            label = code.replace("_", " ")
            count = issues[code]["count"]
            reasons.append(f"{label} ({count:,})" if count != 1 else label)
        evidence = ", ".join(reasons).capitalize()
    elif verdict == "passed":
        evidence = f"Verified {report['checks']['price_rows_checked']:,} track prices"
    else:
        evidence = "The tool returned no issue code"
    return {"verdict": verdict, "tool_issue_codes": codes,
            "agent_response": f"{verdict.capitalize()}. {evidence}."}


def drive_agent(session, tools, plan, base_url, model):
    # The operator-approved candidate is pinned by the host on both sides.
    plan_tools = {t["name"]: t for t in tools if t["inputSchema"].get("required")}
    definitions = [{"type":"function", "function":{
        "name":t["name"], "description":t.get("description",""), "parameters":{"type":"object","properties":{},"additionalProperties":False}
    }} for t in tools]
    messages = [
        {"role":"system","content":"You are a database release reviewer. Use the supplied MCP tools to rehearse the attached candidate. Call the sole tool if there is one. If there are six, start with validate_request and follow each tool result's next field to the following phase. The host supplies the exact approved candidate. End when a tool returns a verdict."},
        {"role":"user","content":f"Rehearse this attached candidate: {plan}"},
    ]
    started = time.perf_counter()
    calls, report = [], None
    names = {t["name"] for t in tools}
    next_hint = next(iter(plan_tools), None)
    for _ in range(10):
        prompt = list(messages)
        if next_hint:
            prompt.append({"role":"user","content":f"The tool protocol indicates the next phase is {next_hint}. Use that named tool now."})
        body = json.dumps({"model":model,"messages":prompt,"tools":definitions,
                           "temperature":0,"max_tokens":180,"tool_choice":"required"}).encode()
        request = urllib.request.Request(base_url.rstrip("/")+"/chat/completions",body,
                                         {"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=75) as response:
            msg = json.load(response)["choices"][0]["message"]
        messages.append({k:v for k,v in msg.items() if k in {"role","content","tool_calls"}})
        if not msg.get("tool_calls"):
            break
        for call in msg["tool_calls"]:
            function = call["function"]
            name = function["name"]
            try:
                args = json.loads(function["arguments"])
                if args != {}:
                    raise ValueError("These tools use the candidate attached by the operator.")
                if name in plan_tools:
                    args = plan_argument(plan_tools[name], plan)
                result = session.call(name,args) if name in names else {"error":"Unknown tool"}
            except (ValueError,RuntimeError) as exc:
                result = {"error":str(exc)}
            next_hint = result.get("next", next_hint)
            calls.append({"name":name,"arguments":function["arguments"],"result":result})
            if "verdict" in result:
                report = result
                break
            messages.append({"role":"tool","name":name,"tool_call_id":call["id"],
                             "content":json.dumps(result)})
        if report is not None:
            break
    outcome = agent_outcome(report, len(calls))
    unit = "call" if len(calls) == 1 else "calls"
    print(f'Live agent ({len(calls)} {unit}). {outcome["agent_response"]}', flush=True)
    return {"calls":len(calls), **outcome, "milliseconds":round(1000*(time.perf_counter()-started)),
            "trace":calls}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server", type=Path, required=True)
    p.add_argument("--confirm", action="store_true", required=True,
                   help="Authorize the frozen set on disposable fixture copies")
    p.add_argument("--reference", action="store_true", help="Identify a hand-authored MCP wrapper")
    p.add_argument("--base-url")
    p.add_argument("--agent-case", default="whole_dollars", choices=("whole_dollars","round_cents","currency_conversion","missing_backfill_scope"))
    p.add_argument("--integration-output", type=Path)
    p.add_argument("--model",default="qwen2.5-coder-7b")
    args=p.parse_args()
    selected=cases()
    plans=[canonical(c["plan"]) for c in selected]
    with approved(plans), connect(args.server.resolve()) as (composed, tools):
        if len(tools)!=1:
            raise RuntimeError(f"Expected one tool; received {len(tools)}")
        tool=tools[0]
        def run(plan):
            return composed.call(tool["name"],plan_argument(tool,plan))

        result = benchmark(selected,run)
        result["tool"] = tool["name"]
        verified = result["total"] == result["matched"] and result["source_unchanged"]
        result["synapse_verified"] = verified and not args.reference
        (ROOT/"mcp-benchmark-results.json").write_text(json.dumps(result,indent=2)+"\n")
        meta = {"synapse_verified":result["synapse_verified"]}
        runtime = "Reference MCP" if args.reference else "Synapse MCP"
        print(f'{runtime}: {result["matched"]}/{result["total"]}',flush=True)
        hero=plans[[c["id"] for c in selected].index(args.agent_case)]
        with connect(ROOT/"small_tools_server.py") as (separate, small):
            def run_separately(plan):
                report = None
                for index, phase in enumerate(PHASES):
                    report = separate.call(phase.__name__, {"plan_json":plan} if index==0 else {})
                return report
            separate_result=benchmark(selected,run_separately)
            (ROOT/"small-benchmark-results.json").write_text(json.dumps(separate_result,indent=2)+"\n")
            print(f'Separate MCP: {separate_result["matched"]}/{separate_result["total"]}',flush=True)
            meta["deterministic_comparison"] = {
                "composed_calls_per_case":1,"separate_calls_per_case":6,
                "composed_matched":result["matched"],"separate_matched":separate_result["matched"],
            }
            if args.base_url:
                meta["live_comparison"] = {
                    "model":args.model, "case_id":args.agent_case,
                    "composed":drive_agent(composed,tools,hero,args.base_url,args.model),
                    "separate":drive_agent(separate,small,hero,args.base_url,args.model),
                }
        filename = "integration-results.json" if args.base_url else "integration-scripted-results.json"
        output = args.integration_output or ROOT/filename
        output.write_text(json.dumps(meta,indent=2)+"\n")
    if not verified or separate_result["misses"] or not separate_result["source_unchanged"]:
        raise SystemExit(1)

if __name__=="__main__":
    main()
