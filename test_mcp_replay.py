from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from compare_live import AMBIGUOUS, assess
from mcp_replay import agent_outcome, drive_agent, plan_argument


TOOLS = [{"name": "rehearse", "inputSchema": {"required": ["plan_json"]}}]


def completion(message):
    return BytesIO(json.dumps({"choices": [{"message": message}]}).encode())


def tool_call(name, call_id="1"):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": "{}"}}]}


class AgentOutcomeTests(TestCase):
    def test_mcp_argument_is_a_json_object(self):
        tool = {"inputSchema": {"required": ["plan"]}}
        plan = '{ "post_step": "none", "backfills": [], "add_column": true }'
        self.assertEqual(plan_argument(tool, plan),
                         {"plan": {"post_step": "none", "backfills": [], "add_column": True}})

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

    def test_shared_prompt_hides_phase_hints_but_trace_keeps_guard_failures(self):
        tools = [*TOOLS, {"name":"finish", "inputSchema":{}}]
        session = Mock()
        session.call.side_effect = [
            {"error":"Run rehearse next.", "code":"phase_out_of_order"},
            {"next":"finish", "issues_so_far":[]},
            {"verdict":"passed", "issues":{}, "checks":{"price_rows_checked":42}},
        ]
        responses = [completion(tool_call(name, str(index))) for index,name in enumerate(["finish","rehearse","finish"])]
        with patch("mcp_replay.urllib.request.urlopen", side_effect=responses) as network, redirect_stdout(StringIO()):
            result = drive_agent(session, tools, "approved plan", "http://127.0.0.1:1", "test", request_text="Review this")

        model_payloads = [json.loads(call.args[0].data) for call in network.call_args_list]
        self.assertTrue(all(payload["tool_choice"] == "auto" for payload in model_payloads))
        observations = [json.loads(message["content"]) for message in model_payloads[-1]["messages"] if message["role"]=="tool"]
        self.assertEqual(observations[-1], {"issues_so_far":[]})
        self.assertNotIn("rehearse", observations[0]["error"])
        self.assertEqual(result["trace"][0]["result"]["error"], "Run rehearse next.")
        self.assertEqual(result["verdict"], "passed")

    def test_unapproved_tool_attempt_can_recover_to_clarification_without_mcp(self):
        session = Mock()
        request, subject = AMBIGUOUS["ambiguous_price_request"]
        replies = [completion(tool_call("rehearse")), completion({"role":"assistant", "content":"Which tracks are affected?"})]
        with patch("mcp_replay.urllib.request.urlopen", side_effect=replies), redirect_stdout(StringIO()):
            trace = drive_agent(session, TOOLS, None, "http://127.0.0.1:1", "test", request_text=request)

        session.call.assert_not_called()
        result = assess(trace, None, subject)
        self.assertEqual(result["wrong_calls"], 1)
        self.assertTrue(result["matched"])
        self.assertTrue(result["recovered_after_error"])
        self.assertFalse(result["clarified_before_tool_attempt"])

    def test_clarification_requires_a_specific_request(self):
        _, subject = AMBIGUOUS["ambiguous_price_request"]
        generic = {"trace":[], "model_message":"Please provide the approved plan for the affected tracks."}
        self.assertFalse(assess(generic, None, subject)["matched"])
        specific = {**generic, "model_message":"Please provide the scope for the affected tracks."}
        self.assertTrue(assess(specific, None, subject)["matched"])
