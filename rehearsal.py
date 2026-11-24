"""Bounded SQLite migration rehearsals on a pinned, public fixture."""
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import os
import sqlite3
import time

from playlist import PLAYLIST_FAMILY, SELECTS as PLAYLIST_MAPPINGS, execute_membership_migration, verify_memberships

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chinook.sqlite"
FIXTURE_HASH = "bdf635be69850bd3be09c9a2dbeef7ddfb80036bd3ef3381383cd03b61e4a61a"
APPROVAL_ENV = "REHEARSAL_APPROVED_PLANS"
CONTRACT_PATH = FIXTURE.with_name("contract.json")
CONTRACT_SHA256 = "16c95b1c9f27fe02f42376ce13069668dfdfd8f36be1d5a52c3557b720f9c07f"
PRICE_FAMILY = "price_cents"
PLAYLIST_CONTRACT_PATH = FIXTURE.with_name("playlist-contract.json")
PLAYLIST_CONTRACT_SHA256 = "62041bc427da2b860e34ca7738a0687ecc8d360f0fdfac813673bb733b43e304"
EXPRESSIONS = {
    "rounded_cents": "CAST(ROUND(UnitPrice * 100) AS INTEGER)",
    "rounded_cents_arithmetic": "CAST(UnitPrice * 100 + 0.5 AS INTEGER)",
    "truncated_dollars": "CAST(UnitPrice AS INTEGER) * 100",
    "rounded_dollars": "CAST(ROUND(UnitPrice) AS INTEGER) * 100",
    "real_cents": "UnitPrice * 100.0",
    "halved_cents": "CAST(ROUND(UnitPrice * 50) AS INTEGER)",
    "zero": "0",
    "negated_cents": "-CAST(ROUND(UnitPrice * 100) AS INTEGER)",
    "null": "NULL",
}
SCOPES = {
    "all": "1=1",
    "first_100": "TrackId <= 100",
    "even": "TrackId % 2 = 0",
    "odd": "TrackId % 2 = 1",
    "higher_price": "UnitPrice > 1",
    "standard_price": "UnitPrice <= 1",
}
POST_STEPS = {
    "none": (),
    "edit_legacy_price": ("UPDATE Track SET UnitPrice=0.50 WHERE TrackId=1",),
    "edit_invoice_total": ("UPDATE Invoice SET Total=0 WHERE InvoiceId=1",),
    "delete_playlists": ("DELETE FROM PlaylistTrack WHERE TrackId=1",),
    "missing_column_error": ("UPDATE Track SET MissingColumn=0",),
    "rename_legacy_price": ("ALTER TABLE Track RENAME COLUMN UnitPrice TO OldUnitPrice",),
    "break_reference": ("UPDATE Track SET AlbumId=-1 WHERE TrackId=1",),
    "drop_album_index": ("DROP INDEX IF EXISTS IFK_TrackAlbumId",),
    "edit_duration": ("UPDATE Track SET Milliseconds=1 WHERE TrackId=1",),
}

def canonical(plan):
    return json.dumps(plan, sort_keys=True, separators=(",", ":"))


def fingerprint(plan_json):
    """Fingerprint the plan's exact UTF-8 bytes."""
    return sha256(plan_json.encode("utf-8")).hexdigest()

def plan_family(plan):
    return PLAYLIST_FAMILY if isinstance(plan, dict) and plan.get("family") == PLAYLIST_FAMILY else PRICE_FAMILY


def contract_spec(family):
    if family == PLAYLIST_FAMILY:
        return PLAYLIST_CONTRACT_PATH, PLAYLIST_CONTRACT_SHA256
    return CONTRACT_PATH, CONTRACT_SHA256


def check_contract(context):
    """Check the trusted pin and keep the verified contract snapshot for this run."""
    path, pin = contract_spec(context["family"])
    try:
        contract = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        digest = fingerprint(canonical(contract))
    except (OSError, ValueError, RecursionError):
        context["contract_sha256"] = None
        return block(context, "contract_unavailable", verdict="unclear")
    if digest != pin:
        context["contract_sha256"] = None
        return block(context, "contract_hash_mismatch",
                     examples=[{"expected_sha256": pin, "observed_sha256": digest}],
                     verdict="unclear")
    context.setdefault("contract_snapshot", contract)
    context["contract_sha256"] = digest
    return context


def validate_request(plan_json: str) -> dict:
    """Create a context from a JSON string containing the proposed migration plan."""
    plan = None
    if len(plan_json) <= 4096:
        try:
            plan = json.loads(plan_json, object_pairs_hook=unique_keys)
        except (ValueError, RecursionError):
            pass
    context = {"digest": fingerprint(plan_json), "issues": {}, "checks": {}, "family": plan_family(plan),
               "source_checked": False, "execution": "not_run", "verdict": None}
    if context["digest"] not in os.environ.get(APPROVAL_ENV, "").split(","):
        return block(context, "confirmation_required")
    check_contract(context)
    if context["issues"]:
        return context
    if context["family"] == PLAYLIST_FAMILY:
        return validate_membership_plan(context, plan)
    if not isinstance(plan, dict) or set(plan) != {"add_column", "backfills", "post_step"}:
        return block(context, "invalid_plan")
    operations = plan["backfills"]
    if (type(plan["add_column"]) is not bool or not isinstance(plan["post_step"], str)
            or not isinstance(operations, list) or len(operations) > 4):
        return block(context, "invalid_plan")
    if plan["post_step"] not in POST_STEPS:
        return block(context, "unsupported_operation", verdict="unsupported")
    for step in operations:
        if not isinstance(step, dict) or set(step) != {"expression", "scope"}:
            return block(context, "invalid_plan")
        if not isinstance(step["expression"], str):
            return block(context, "invalid_plan")
        if step["expression"] not in EXPRESSIONS:
            return block(context, "unsupported_operation", verdict="unsupported")
        if step["scope"] is None:
            return block(context, "scope_unspecified", verdict="unclear")
        if not isinstance(step["scope"], str):
            return block(context, "invalid_plan")
        if step["scope"] not in SCOPES:
            return block(context, "unsupported_scope", verdict="unsupported")
    context["plan"] = plan
    return context


def validate_membership_plan(context, plan):
    if set(plan) != {"family", "mapping"}:
        return block(context, "invalid_plan")
    mapping = plan["mapping"]
    if mapping is None:
        return block(context, "membership_mapping_unspecified", verdict="unclear")
    if not isinstance(mapping, str):
        return block(context, "invalid_plan")
    if mapping not in PLAYLIST_MAPPINGS:
        return block(context, "unsupported_operation", verdict="unsupported")
    context["plan"] = plan
    return context


def unique_keys(pairs):
    result = dict(pairs)
    if len(pairs) != len(result):
        raise ValueError("Duplicate plan key")
    return result

def block(context, code, count=1, examples=(), *, verdict="blocked"):
    context["issues"][code] = {"count": count, "examples": list(examples)[:5]}
    if context["verdict"] is None or verdict == "unclear":
        context["verdict"] = verdict
    return context

def create_test_snapshot(context: dict) -> dict:
    """Copy the pinned, read-only Chinook fixture to a disposable in-memory database."""
    if context["issues"]:
        return context
    try:
        # Deserialize into memory from the exact bytes whose hash was checked.
        data = FIXTURE.read_bytes()
        if sha256(data).hexdigest() != FIXTURE_HASH:
            return block(context, "source_hash_mismatch", verdict="unclear")
        with closing(sqlite3.connect(":memory:")) as copy:
            copy.deserialize(data)
            if copy.execute(context["contract_snapshot"]["database_checks"]["integrity"]).fetchall() != [("ok",)]:
                return block(context, "invalid_source", verdict="unclear")
        context["snapshot"] = data
        context["source_checked"] = True
    except (OSError, sqlite3.Error):
        block(context, "source_unavailable", verdict="unclear")
    return context

def read_contract_state(db, contract):
    return {table: db.execute(query).fetchall() for table, query in contract["protected_records"].items()}


def read_app_queries(db, contract):
    return [db.execute(query).fetchall() for query in contract["application_queries"]]


def read_indexes(db, contract):
    return db.execute(contract["index_query"]).fetchall()


def capture_baseline(context: dict) -> dict:
    """Capture protected records, application query results, and indexes before mutation."""
    if context["issues"]:
        return context
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(context["snapshot"])
            contract = context["contract_snapshot"]
            context["baseline"] = read_contract_state(db, contract)
            context["app_baseline"] = read_app_queries(db, contract)
            context["indexes"] = read_indexes(db, contract)
    except (KeyError, sqlite3.Error):
        block(context, "baseline_unavailable", verdict="unclear")
    return context

def execute_migration(context: dict) -> dict:
    """Run the bounded migration transaction in memory with a deadline."""
    if context["issues"]:
        return context
    if "baseline" not in context:
        return block(context, "missing_baseline", verdict="unclear")
    check_contract(context)
    if context["issues"]:
        return context
    plan = context["plan"]
    deadline = time.monotonic() + 1.0
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(context["snapshot"])
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            with db:
                db.execute("BEGIN")
                if context["family"] == PLAYLIST_FAMILY:
                    execute_membership_migration(db, plan["mapping"])
                else:
                    execute_price_migration(db, plan)
            context["result_snapshot"] = db.serialize()
            context["execution"] = "sqlite_committed_on_copy"
    except sqlite3.Error as exc:
        context["execution"] = "failed"
        reason = "execution_timeout" if exc.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT else "execution_error"
        block(context, reason, examples=[{"detail": str(exc)}],
              verdict="unclear" if reason == "execution_timeout" else "blocked")
    return context

def execute_price_migration(db, plan):
    if plan["add_column"]:
        db.execute("ALTER TABLE Track ADD COLUMN UnitPriceCents INTEGER")
    for step in plan["backfills"]:
        expression = EXPRESSIONS[step["expression"]]
        scope = SCOPES[step["scope"]]
        db.execute(f"UPDATE Track SET UnitPriceCents={expression} WHERE {scope}")
    for statement in POST_STEPS[plan["post_step"]]:
        db.execute(statement)


def verify_prices(db, context):
    contract = context["contract_snapshot"]
    source, target = contract["source"], contract["target"]
    table, key, column = source["table"], source["key"], target["column"]
    columns = {row[1]: row[2] for row in db.execute(f"PRAGMA table_info({table})")}
    if columns.get(column) != target["type"]:
        block(context, "target_column_missing")
        return
    expected = {row[0]: int(Decimal(str(row[-1])) * target["units_per_dollar"])
                for row in context["baseline"][table]}
    observed = dict(db.execute(f"SELECT {key}, {column} FROM {table} ORDER BY {key}"))
    mismatches = []
    for track_id, cents in expected.items():
        actual = observed.get(track_id)
        if type(actual) is not int or actual != cents:
            mismatches.append({"track_id": track_id, "expected_cents": cents, "actual_cents": actual})
    context["checks"].update(price_rows_checked=len(expected), price_cents_exact=not mismatches)
    if mismatches:
        block(context, "price_cents_mismatch", len(mismatches), mismatches)


def verify_postconditions(context: dict) -> dict:
    """Check SQLite and application state against the fixed preservation contract."""
    if context["issues"]:
        return context
    if "result_snapshot" not in context:
        return block(context, "missing_execution", verdict="unclear")
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(context["result_snapshot"])
            contract = context["contract_snapshot"]
            checks = context["checks"]
            integrity = db.execute(contract["database_checks"]["integrity"]).fetchall()
            checks["integrity"] = integrity == [("ok",)]
            if not checks["integrity"]:
                block(context, "database_integrity", len(integrity), integrity)
            orphans = db.execute(contract["database_checks"]["foreign_keys"]).fetchall()
            checks["foreign_keys"] = not orphans
            if orphans:
                block(context, "foreign_key_violation", len(orphans), orphans)

            try:
                after = read_contract_state(db, contract)
                checks["track_count"] = len(after["Track"])
                differences = [table for table in contract["protected_records"] if after[table] != context["baseline"][table]]
                if differences:
                    block(context, "protected_records_changed", len(differences), differences)
                checks["protected_records"] = not differences
            except sqlite3.Error as exc:
                checks["protected_records"] = False
                block(context, "protected_schema_changed", examples=[{"detail": str(exc)}])

            try:
                checks["application_queries"] = read_app_queries(db, contract) == context["app_baseline"]
            except sqlite3.Error:
                checks["application_queries"] = False
            if not checks["application_queries"]:
                block(context, "application_query_changed")

            checks["indexes"] = read_indexes(db, contract) == context["indexes"]
            if not checks["indexes"]:
                block(context, "required_index_changed")

            if context["family"] == PLAYLIST_FAMILY:
                membership_checks, issues = verify_memberships(db, contract, context["baseline"])
                checks.update(membership_checks)
                for code, (count, examples) in issues.items():
                    block(context, code, count, examples)
            else:
                verify_prices(db, context)
    except sqlite3.Error as exc:
        block(context, "verification_error", examples=[{"detail": str(exc)}], verdict="unclear")
    return context

def build_evidence_report(context: dict) -> dict:
    """Return a JSON-safe verdict with check coverage and concrete counterexamples."""
    if context.get("contract_sha256"):
        check_contract(context)
    if not context["issues"]:
        required = ("membership_pairs_exact", "membership_schema") if context["family"] == PLAYLIST_FAMILY else ("price_cents_exact",)
        if any(context["checks"].get(key) is not True for key in required):
            block(context, "verification_not_completed", verdict="unclear")
        elif not context["source_checked"]:
            block(context, "source_not_checked", verdict="unclear")
    return {
        "verdict": context["verdict"] or "passed",
        "family": context["family"],
        "plan_sha256": context["digest"],
        "fixture_sha256": FIXTURE_HASH if context["source_checked"] else None,
        "contract": context.get("contract_snapshot", {}).get("id"),
        "contract_sha256": context.get("contract_sha256"),
        "pinned_contract_sha256": contract_spec(context["family"])[1],
        "execution": context["execution"],
        "checks": context["checks"],
        "issues": context["issues"],
    }

PHASES = (validate_request, create_test_snapshot, capture_baseline,
          execute_migration, verify_postconditions, build_evidence_report)

def rehearse_locally(plan_json):
    """Reference composition used by the CLI and benchmark oracle plumbing."""
    context = plan_json
    for phase in PHASES:
        context = phase(context)
    return context
