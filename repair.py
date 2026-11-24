"""Ground a one-expression proposal in a real rehearsal report."""
from contextlib import closing
from copy import deepcopy
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
from urllib.parse import urlsplit

from demo import canonical
from rehearsal import CONTRACT_SHA256, EXPRESSIONS, FIXTURE_HASH, fingerprint

RECEIPT = Path(__file__).resolve().parent / "repair-proposal.json"
PROPOSAL_TOOL = "propose_backfill_expression"
EXPLANATIONS = {
    "rounded_cents": "Multiply the price by 100 before rounding to whole cents.",
    "rounded_cents_arithmetic": "Scale by 100, add half a cent, then truncate the positive value.",
    "truncated_dollars": "Truncate the dollar amount before multiplying by 100.",
    "rounded_dollars": "Round to whole dollars before multiplying by 100.",
    "halved_cents": "Scale the original price by 50 and round.",
}
MODEL_CHOICES = tuple(EXPLANATIONS)


class RepairUnavailable(ValueError):
    pass


def eligible(plan, report):
    """Only propose a fix for an isolated, verified conversion failure."""
    checks = report["checks"]
    return (
        report["verdict"] == "blocked"
        and report["execution"] == "sqlite_committed_on_copy"
        and report["contract_sha256"] == CONTRACT_SHA256
        and report["fixture_sha256"] == FIXTURE_HASH
        and report["plan_sha256"] == fingerprint(canonical(plan))
        and set(report["issues"]) == {"price_cents_mismatch"}
        and all(checks.get(name) is True for name in
                ("integrity", "foreign_keys", "protected_records", "application_queries", "indexes"))
        and plan["add_column"] is True
        and len(plan["backfills"]) == 1
        and plan["backfills"][0]["scope"] == "all"
        and plan["post_step"] == "none"
    )


def live_endpoint(url):
    endpoint = urlsplit(url)
    if (endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1"}
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path.rstrip("/") != "/v1"):
        raise ValueError("Use a loopback model endpoint such as http://127.0.0.1:11455/v1.")
    return url.rstrip("/")


def call_model(plan, report, endpoint, model):
    """Request a typed patch from a local model. This call runs no migration."""
    if not eligible(plan, report):
        raise RepairUnavailable("This run does not have an isolated price conversion failure.")
    old = plan["backfills"][0]["expression"]
    options = {name: EXPRESSIONS[name] for name in MODEL_CHOICES if name != old}
    evidence = report["issues"]["price_cents_mismatch"]
    prompt = {
        "completed_run": {"candidate": plan, "mismatch_count": evidence["count"],
                          "examples": evidence["examples"][:2]},
        "goal": "Convert positive decimal USD prices to integer cents. Preserve the value.",
        "available_expressions": options,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Propose a one-expression change using the evidence. "
             "Choose an available expression based on its actual operations. "
             "Your proposal is unverified until the tool rehearses it."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "tools": [{"type": "function", "function": {
            "name": PROPOSAL_TOOL, "description": "Propose one backfill expression for human review.",
            "parameters": {"type": "object", "properties": {
                "expression": {"enum": list(options)},
            }, "required": ["expression"], "additionalProperties": False},
        }}],
        "tool_choice": {"type": "function", "function": {"name": PROPOSAL_TOOL}},
        "temperature": 0, "max_tokens": 180,
    }
    local = urlsplit(live_endpoint(endpoint))
    try:
        with closing(HTTPConnection(local.hostname, local.port or 80, timeout=30)) as connection:
            connection.request("POST", local.path + "/chat/completions", json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = response.read(32769)
            if response.status != 200 or len(payload) > 32768:
                raise ValueError("Unexpected model response")
        call, = json.loads(payload)["choices"][0]["message"]["tool_calls"]
        if call["function"]["name"] != PROPOSAL_TOOL:
            raise ValueError("Wrong response tool")
        patch = json.loads(call["function"]["arguments"])
    except (OSError, HTTPException, ValueError, KeyError, TypeError, IndexError) as exc:
        raise RepairUnavailable("The local model did not return a repair proposal.") from exc
    return {
        "model": model, "input_sha256": report["plan_sha256"],
        "contract_sha256": report["contract_sha256"], "tool_call": call,
        "evidence": prompt, "patch": patch,
    }


def recorded_proposal():
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or not all(isinstance(receipt.get(key), str)
                                                    for key in ("model", "input_sha256", "contract_sha256")):
            raise ValueError("Invalid proposal receipt")
        return receipt
    except (OSError, ValueError) as exc:
        raise RepairUnavailable("The recorded agent proposal is unavailable.") from exc


def review_proposal(plan, report, receipt, mode):
    """Verify provenance and construct the one allowed draft without executing it."""
    if not eligible(plan, report):
        raise RepairUnavailable("This run does not have an isolated price conversion failure.")
    if receipt.get("input_sha256") != report["plan_sha256"] or receipt.get("contract_sha256") != CONTRACT_SHA256:
        raise RepairUnavailable("The agent proposal belongs to a different plan or contract.")
    patch = receipt.get("patch")
    old = plan["backfills"][0]["expression"]
    if not isinstance(patch, dict) or set(patch) != {"expression"}:
        raise RepairUnavailable("The agent must propose exactly one expression.")
    new = patch["expression"]
    if not isinstance(new, str) or new not in MODEL_CHOICES or new == old:
        raise RepairUnavailable("The proposed expression is outside the review scope.")
    draft = deepcopy(plan)
    draft["backfills"][0]["expression"] = new
    return {
        "mode": mode, "model": receipt["model"], "reason": EXPLANATIONS[new],
        "before_expression": EXPRESSIONS[old], "after_expression": EXPRESSIONS[new],
        "draft": draft, "draft_sha256": fingerprint(canonical(draft)),
        "original_sha256": report["plan_sha256"], "contract_sha256": CONTRACT_SHA256,
    }
