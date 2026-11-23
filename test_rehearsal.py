import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from demo import approved, benchmark, canonical, cases
import rehearsal as engine

class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]:case for case in cases()}
        self.fault = canonical(self.cases["whole_dollars"]["plan"])
        self.clean = canonical(self.cases["round_cents"]["plan"])

    def test_unapproved_or_modified_plan_never_reads_fixture(self):
        with approved([self.clean]), patch.object(engine, "FIXTURE") as source:
            denied = engine.rehearse_locally(self.clean + " ")
        self.assertEqual(set(denied["issues"]), {"confirmation_required"})
        self.assertEqual(denied["execution"], "not_run")
        source.read_bytes.assert_not_called()

    def test_truncated_dollar_plan_commits_then_fails_contract(self):
        with approved([self.fault]):
            report = engine.rehearse_locally(self.fault)
        self.assertEqual(report["execution"], "sqlite_committed_on_copy")
        self.assertTrue(report["checks"]["integrity"])
        self.assertTrue(report["checks"]["foreign_keys"])
        mismatch = report["issues"]["price_cents_mismatch"]
        self.assertEqual(mismatch["count"], 3503)
        self.assertEqual(mismatch["examples"][0],
                         {"track_id":1,"expected_cents":99,"actual_cents":0})

    def test_wrong_fixture_fails_before_execution(self):
        with approved([self.clean]), patch.object(engine, "FIXTURE") as source:
            source.read_bytes.return_value = b"different fixture"
            report = engine.rehearse_locally(self.clean)
        self.assertEqual(report["verdict"], "unclear")
        self.assertEqual(set(report["issues"]), {"source_hash_mismatch"})
        self.assertEqual(report["execution"], "not_run")

    def test_unclear_on_interrupted_execution(self):
        with approved([self.clean]), patch.object(engine.time, "monotonic", side_effect=[0]+[2]*100):
            report = engine.rehearse_locally(self.clean)
        self.assertEqual(report["verdict"], "unclear")
        self.assertEqual(set(report["issues"]), {"execution_timeout"})
        self.assertEqual(report["execution"], "failed")

    def test_wrong_contract_pin_rejects_before_reading_fixture(self):
        with approved([self.clean]), patch.object(engine, "CONTRACT_PATH") as policy, patch.object(engine, "FIXTURE") as source:
            policy.read_text.return_value = "{}"
            report = engine.rehearse_locally(self.clean)
        self.assertEqual(report["verdict"], "unclear")
        self.assertEqual(set(report["issues"]), {"contract_hash_mismatch"})
        self.assertEqual(report["execution"], "not_run")
        self.assertIsNone(report["contract_sha256"])
        self.assertEqual(report["pinned_contract_sha256"], engine.CONTRACT_SHA256)
        source.read_bytes.assert_not_called()

    def test_contract_change_during_run_invalidates_verdict(self):
        original = engine.CONTRACT_PATH.read_text(encoding="utf-8")
        changed = json.loads(original)
        changed["target"]["units_per_dollar"] = 10
        for changed_after in (3, 5):
            with self.subTest(changed_after=changed_after), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"contract.json"
                path.write_text(original, encoding="utf-8")
                with approved([self.clean]), patch.object(engine, "CONTRACT_PATH", path):
                    context = self.clean
                    for index, phase in enumerate(engine.PHASES):
                        context = phase(context)
                        if index+1 == changed_after:
                            path.write_text(json.dumps(changed), encoding="utf-8")
                self.assertEqual(context["verdict"], "unclear")
                self.assertEqual(set(context["issues"]), {"contract_hash_mismatch"})
                self.assertIsNone(context["contract_sha256"])
                expected_execution = "not_run" if changed_after == 3 else "sqlite_committed_on_copy"
                self.assertEqual(context["execution"], expected_execution)

    def test_original_fixture_and_labeled_cases(self):
        selected = list(self.cases.values())
        with approved([canonical(case["plan"]) for case in selected]):
            result = benchmark(selected,engine.rehearse_locally)
        self.assertEqual(result["total"],26)
        self.assertEqual(result["misses"],[])
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(hashlib.sha256(engine.FIXTURE.read_bytes()).hexdigest(),engine.FIXTURE_HASH)

if __name__ == "__main__":
    unittest.main()
