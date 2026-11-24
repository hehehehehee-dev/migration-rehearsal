from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from mcp_replay import agent_outcome, drive_agent


TOOLS = [{"name": "rehearse", "inputSchema": {"required": ["plan_json"]}}]


def completion(message):
    return BytesIO(json.dumps({"choices": [{"message": message}]}).encode())


class AgentOutcomeTests(TestCase):
    def test_agent_uses_tool_report_and_stops_at_its_verdict(self):
        session = Mock()
        session.call.return_value = {
            "verdict": "blocked", "issues": {"price_cents_mismatch": {"count": 3503}},
        }
        message = {
            "role": "assistant", "content": "Passed. All prices are correct.",
            "tool_calls": [{"id": str(index), "function": {"name": "rehearse", "arguments": "{}"}}
                           for index in range(2)],
        }
        with patch("mcp_replay.urllib.request.urlopen", return_value=completion(message)) as request:
            with redirect_stdout(StringIO()) as output:
                result = drive_agent(session, TOOLS, "reviewed plan", "http://127.0.0.1:1", "test")

        request.assert_called_once()
        session.call.assert_called_once_with("rehearse", {"plan_json": "reviewed plan"})
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["verdict"], "blocked")
        self.assertEqual(result["tool_issue_codes"], ["price_cents_mismatch"])
        self.assertEqual(result["agent_response"], "Blocked. Price cents mismatch (3,503).")
        self.assertIn(result["agent_response"], output.getvalue())

    def test_agent_without_a_report_is_incomplete(self):
        session = Mock()
        message = {"role": "assistant", "content": "Passed."}
        with patch("mcp_replay.urllib.request.urlopen", return_value=completion(message)):
            with redirect_stdout(StringIO()):
                result = drive_agent(session, TOOLS, "reviewed plan", "http://127.0.0.1:1", "test")

        session.call.assert_not_called()
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["tool_issue_codes"], [])
        self.assertEqual(result["agent_response"], "Incomplete. No tool returned a verdict after 0 calls.")

    def test_passed_summary_uses_verified_price_count(self):
        report = {"verdict": "passed", "issues": {}, "checks": {"price_rows_checked": 42}}
        self.assertEqual(agent_outcome(report, 1)["agent_response"], "Passed. Verified 42 track prices.")
