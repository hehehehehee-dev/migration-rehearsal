"""Run the same release-review requests against one MCP tool and six phases."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median

from demo import approved, canonical, cases, score
from mcp_replay import ROOT, connect, drive_agent
from rehearsal import FIXTURE

CASE_IDS = ("whole_dollars", "round_cents", "missing_backfill_scope", "currency_conversion",
            "playlist_swapped", "playlist_preserved")
AMBIGUOUS = {
    "ambiguous_price_request": (
        "Check the migration from dollars to cents for the affected tracks.",
        r"\b(?:scope|tracks?|rows?|subset|catalog|songs?)\b",
    ),
    "ambiguous_playlist_request": (
        "Check the migration to the new playlist membership table. Some of the memberships should move.",
        r"\b(?:playlists?|memberships?|mappings?|tracks?|move|move[ds]|pairs|relationships?)\b",
    ),
}
QUESTION = re.compile(r"\?|\b(?:please|can you)\s+(?:clarify|specify|tell me|confirm|provide)\b", re.I)
GENERIC_PLAN_REQUEST = re.compile(r"\b(?:provide|send|attach|share)\s+(?:the|an?|your)\s+(?:approved\s+)?(?:migration\s+)?plan\b", re.I)


def run_case(endpoint, model, server, request, case=None, subject=None):
    plan = canonical(case["plan"]) if case else None
    with approved([plan] if plan else []), connect(server) as (session, tools):
        record = drive_agent(session, tools, plan, endpoint, model, request_text=request)

    messages = record["model_turns"]
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    for turn in messages:
        usage = turn.get("usage")
        if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields)
                or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]):
            raise RuntimeError("This comparison requires a model endpoint that returns measured token usage.")
    tokens = {key:sum(turn["usage"][key] for turn in messages) for key in fields}
    return {**record, **assess(record, case, subject), "tokens":tokens}


def assess(record, case, subject):
    trace = record["trace"]
    wrong = [index for index, step in enumerate(trace, 1) if "error" in step["result"]]
    mcp_calls = sum(step["mcp_arguments"] is not None for step in trace)
    report = next((step["result"] for step in trace if "verdict" in step["result"]), None)
    if case is not None:
        matched = report is not None and score(case, report)
    else:
        text = record["model_message"] or ""
        targeted = any(QUESTION.search(sentence) and re.search(subject, sentence, re.I)
                       and not GENERIC_PLAN_REQUEST.search(sentence)
                       for sentence in re.split(r"(?<=[.!?])\s+", text))
        assert mcp_calls == 0, "An unapproved request reached MCP."
        matched = targeted
    return {
        "matched": matched, "mcp_calls": mcp_calls, "wrong_calls": len(wrong), "wrong_call_indices": wrong,
        "recovered_after_error": matched if wrong else None,
        "clarified_before_tool_attempt": (matched and not trace) if case is None else None,
    }


def summarize(records):
    with_errors = [run for run in records if run["wrong_calls"]]
    ambiguity = [run for run in records if run["clarified_before_tool_attempt"] is not None]
    plans = [run for run in records if run["clarified_before_tool_attempt"] is None]
    return {
        "runs": len(records), "matched": sum(run["matched"] for run in records),
        "attempts": sum(run["calls"] for run in records), "mcp_calls": sum(run["mcp_calls"] for run in records),
        "wrong_calls": sum(run["wrong_calls"] for run in records), "runs_with_errors": len(with_errors),
        "recovered_runs": sum(run["recovered_after_error"] for run in with_errors),
        "plan_runs": len(plans), "matched_plan_runs": sum(run["matched"] for run in plans),
        "ambiguity_runs": len(ambiguity), "clarified_without_attempt": sum(run["clarified_before_tool_attempt"] for run in ambiguity),
        "clarified_eventually": sum(run["matched"] for run in ambiguity),
        "median_ms": median(run["milliseconds"] for run in records),
        "median_plan_ms": median(run["milliseconds"] for run in plans),
        "tokens": {key:sum(run["tokens"][key] for run in records) for key in records[0]["tokens"]},
    }


def successful_pair_costs(pairs, sides):
    comparable = [pair for pair in pairs if pair["id"] in CASE_IDS and all(pair[side]["matched"] for side in sides)]
    costs = {}
    for side in sides:
        runs = [pair[side] for pair in comparable]
        costs[side] = {
            "mcp_calls": sum(run["mcp_calls"] for run in runs),
            "tokens": sum(run["tokens"]["total_tokens"] for run in runs),
            "median_ms": median(run["milliseconds"] for run in runs) if runs else None,
        }
    return {"pairs":len(comparable), **costs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="qwen2.5-coder-7b")
    parser.add_argument("--confirm", required=True, action="store_true", help="Approve the bundled plans on disposable copies")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "live-comparison-results.json")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    labeled = {case["id"]:case for case in cases()}
    servers = {"composed":ROOT / "rehearse_database_migration_server.py", "separate":ROOT / "small_tools_server.py"}
    before = FIXTURE.read_bytes()
    pairs = []
    for repetition in range(args.repetitions):
        for index, key in enumerate((*CASE_IDS, *AMBIGUOUS)):
            case = labeled.get(key)
            if case:
                request = f"Rehearse this attached approved plan and check whether we should release it: {canonical(case['plan'])}"
                subject = None
            else:
                request, subject = AMBIGUOUS[key]
            order = ["composed", "separate"]
            if (index + repetition) % 2:
                order.reverse()
            pair = {"id":key, "repetition":repetition+1, "request":request, "run_order":order,
                    "expected":case["expected"] if case else {"action":"clarify", "subject_pattern":subject}}
            for side in order:
                print(f"{repetition+1} | {key} | {side}", flush=True)
                pair[side] = run_case(args.base_url, args.model, servers[side], request, case, subject)
                run = pair[side]
                print(f"  matched={run['matched']} attempts={run['calls']} wrong={run['wrong_calls']} "
                      f"tokens={run['tokens']['total_tokens']} time={run['milliseconds']}ms", flush=True)
            pairs.append(pair)
    unchanged = before == FIXTURE.read_bytes()
    evidence = {"protocol":"shared prompt, optional tool calls, no next-step hints", "model":args.model,
                "repetitions":args.repetitions, "temperature":0, "source_unchanged":unchanged,
                "server_sha256":{name:sha256(path.read_bytes()).hexdigest() for name,path in servers.items()},
                "summary":{side:summarize([pair[side] for pair in pairs]) for side in servers},
                "successful_pair_costs":successful_pair_costs(pairs, servers), "pairs":pairs}
    args.output.write_text(json.dumps(evidence, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps(evidence["summary"], indent=2), flush=True)
    return 0 if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
