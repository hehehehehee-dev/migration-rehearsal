"""Run a migration rehearsal or replay the labeled benchmark."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

from rehearsal import APPROVAL_ENV, CONTRACT_SHA256, FIXTURE, canonical, fingerprint, rehearse_locally

ROOT = Path(__file__).resolve().parent

def cases():
    return [json.loads(line) for line in (ROOT / "fixtures" / "cases.jsonl").read_text().splitlines()]

@contextmanager
def approved(plans):
    previous = os.environ.get(APPROVAL_ENV)
    os.environ[APPROVAL_ENV] = ",".join(fingerprint(plan) for plan in plans)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(APPROVAL_ENV, None)
        else:
            os.environ[APPROVAL_ENV] = previous

def score(case, report):
    expected = case["expected"]
    return (report.get("verdict") == expected["verdict"]
            and set(report.get("issues", {})) == set(expected["issues"])
            and report.get("contract_sha256") == CONTRACT_SHA256
            and ("execution" not in expected or report.get("execution") == expected["execution"]))

def benchmark(selected, run):
    results = []
    before = FIXTURE.read_bytes()
    for case in selected:
        started = time.perf_counter()
        report = run(canonical(case["plan"]))
        results.append({"id": case["id"], "split": case["split"], "expected": case["expected"],
                        "matched": score(case, report), "report": report,
                        "milliseconds": round(1000 * (time.perf_counter() - started), 2)})
    return {"total": len(results), "matched": sum(r["matched"] for r in results),
            "source_unchanged": before == FIXTURE.read_bytes(),
            "misses": [r["id"] for r in results if not r["matched"]], "results": results}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="Case ID or 'benchmark'")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--confirm", action="store_true", help="Authorize this local fixture rehearsal")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.case == "benchmark":
        chosen = [c for c in cases() if args.split == "all" or c["split"] == args.split]
    else:
        chosen = [c for c in cases() if args.case == c["id"]]
    if not chosen:
        parser.error("Unknown case")
    plans = [canonical(c["plan"]) for c in chosen]
    if not args.confirm:
        if len(plans) == 1:
            print(json.dumps(chosen[0]["plan"], indent=2))
        try:
            confirmed = input(f"Run {len(plans)} plan(s) on disposable Chinook copies? Type RUN: ") == "RUN"
        except EOFError:
            confirmed = False
        if not confirmed:
            parser.exit(2, "Rehearsal canceled.\n")
    with approved(plans):
        result = benchmark(chosen, rehearse_locally) if args.case == "benchmark" else rehearse_locally(plans[0])
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    if args.case == "benchmark":
        print(f'{result["matched"]}/{result["total"]} matched | source unchanged: {result["source_unchanged"]}')
        return int(bool(result["misses"]) or not result["source_unchanged"])
    return int(result["verdict"] != "passed")

if __name__ == "__main__":
    raise SystemExit(main())
