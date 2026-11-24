"""Regression tests for proposal boundaries and real price-repair verification."""
from copy import deepcopy
import unittest

from demo import approved, canonical, cases
from repair import RepairUnavailable, eligible, live_endpoint, recorded_proposal, review_proposal, reviewer_proposal
from rehearsal import rehearse_locally


class RepairReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = next(case["plan"] for case in cases() if case["id"] == "whole_dollars")
        with approved([canonical(cls.plan)]):
            cls.report = rehearse_locally(canonical(cls.plan))

    def test_recorded_choice_changes_only_the_expression_and_clears_the_same_checks(self):
        receipt = recorded_proposal()
        self.assertTrue(eligible(self.plan, self.report))
        draft = review_proposal(self.plan, self.report, receipt, "recorded")
        expected = deepcopy(self.plan)
        expected["backfills"][0]["expression"] = receipt["patch"]["expression"]
        self.assertEqual(draft["draft"], expected)
        self.assertEqual(self.plan["backfills"][0]["expression"], "truncated_dollars")
        corrected = canonical(draft["draft"])
        self.assertEqual(rehearse_locally(corrected)["execution"], "not_run")
        with approved([corrected]):
            after = rehearse_locally(corrected)
        self.assertEqual(after["verdict"], "passed")
        self.assertEqual(after["checks"]["price_rows_checked"], 3503)
        self.assertEqual(after["contract_sha256"], self.report["contract_sha256"])
        self.assertEqual(after["fixture_sha256"], self.report["fixture_sha256"])

    def test_proposal_cannot_extend_the_patch_or_reuse_other_evidence(self):
        receipt = recorded_proposal()
        for modification in (
            {"input_sha256": "different"},
            {"contract_sha256": "different"},
            {"patch": {"expression": "unknown"}},
            {"patch": {"expression": "rounded_cents", "scope": "first_100"}},
        ):
            with self.subTest(modification=modification), self.assertRaises(RepairUnavailable):
                review_proposal(self.plan, self.report, {**receipt, **modification}, "recorded")
        report = {**self.report, "checks": {**self.report["checks"], "foreign_keys": False}}
        self.assertFalse(eligible(self.plan, report))

    def test_supported_but_wrong_model_choice_stays_blocked(self):
        receipt = {**recorded_proposal(), "patch": {"expression": "rounded_dollars"}}
        proposal = review_proposal(self.plan, self.report, receipt, "live")
        candidate = canonical(proposal["draft"])
        with approved([candidate]):
            report = rehearse_locally(candidate)
        self.assertEqual(report["verdict"], "blocked")
        self.assertIn("price_cents_mismatch", report["issues"])
        self.assertEqual(report["contract_sha256"], proposal["contract_sha256"])

    def test_reviewer_proposals_use_the_same_approval_boundary(self):
        wrong = reviewer_proposal(self.plan, self.report, "rounded_dollars")
        correct = reviewer_proposal(self.plan, self.report, "rounded_cents")
        self.assertEqual(wrong["mode"], "reviewer")
        self.assertIsNone(wrong["model"])
        self.assertNotEqual(wrong["draft_sha256"], correct["draft_sha256"])
        with approved([canonical(wrong["draft"])]):
            denied = rehearse_locally(canonical(correct["draft"]))
        self.assertEqual(denied["execution"], "not_run")
        with self.assertRaises(RepairUnavailable):
            reviewer_proposal(self.plan, self.report, "halved_cents")

    def test_model_endpoint_stays_on_loopback(self):
        self.assertEqual(live_endpoint("http://127.0.0.1:11455/v1/"), "http://127.0.0.1:11455/v1")
        with self.assertRaises(ValueError):
            live_endpoint("https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
