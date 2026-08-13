"""Heuristic classification and fallback copy for incident interpretation."""

from __future__ import annotations

import os
import unittest

os.environ["ORION_INCIDENT_LLM"] = "0"

from incidents.interpret import (
    FIX_CODE,
    FIX_CWR_MODULE,
    FIX_HOST,
    FIX_MAC_HOST,
    classify_heuristic,
    fallback_user_message,
    interpret_incident,
    is_cwr_automation,
    mac_notify_only,
    operator_greeting,
    with_fixing_clause,
)
from incidents.notify import format_fix_message, format_message


def _cwr(**overrides):
    base = {
        "fingerprint": "deadbeefcafebabe",
        "tool": "cron_cwr_generate",
        "module": "CWR-INTERFACE",
        "repos_name": "CWR-INTERFACE",
        "kind": "nonzero_exit",
        "returncode": 1,
        "message": "1 generator(s) failed: MusicMark",
        "stack_trace": "",
        "raw_stderr_tail": "",
        "mac_payload": {"argv": ["python", "execute_all.py"]},
    }
    base.update(overrides)
    return base


def _cron(tool: str, module: str, **overrides):
    base = {
        "fingerprint": "feedfacefeedface",
        "host": "mac",
        "tool": tool,
        "module": module,
        "repos_name": module,
        "kind": "nonzero_exit",
        "returncode": 1,
        "message": "",
        "stack_trace": "",
        "raw_stderr_tail": "",
        "mac_payload": {},
    }
    base.update(overrides)
    return base


class CwrAutomationTests(unittest.TestCase):
    def test_module(self):
        self.assertTrue(is_cwr_automation(_cwr()))
        self.assertFalse(
            is_cwr_automation({"tool": "cron_pull_all", "module": "OTHER-SCRIPTS"})
        )

    def test_mcp_tool_name(self):
        self.assertTrue(is_cwr_automation({"tool": "cwr_dispatch", "module": ""}))


class ClassifyHeuristicTests(unittest.TestCase):
    def test_sftp_auth_is_host(self):
        rec = _cwr(
            tool="cron_cwr_dispatch",
            message="Authentication failed",
            raw_stderr_tail="Authentication failed: [Errno ...]",
        )
        self.assertEqual(classify_heuristic(rec), FIX_HOST)
        self.assertEqual(FIX_MAC_HOST, FIX_HOST)

    def test_connection_refused_is_host(self):
        rec = _cwr(
            tool="cron_cwr_retrieve",
            message="Connection refused",
            raw_stderr_tail="Network/IO error: Connection refused",
        )
        self.assertEqual(classify_heuristic(rec), FIX_HOST)

    def test_timeout_kind_is_host(self):
        rec = _cwr(kind="timeout", message="timed out")
        self.assertEqual(classify_heuristic(rec), FIX_HOST)

    def test_traceback_is_code(self):
        rec = _cwr(
            message="KeyError: 'foo'",
            stack_trace="Traceback (most recent call last):\n  File \"execute_all.py\"",
            raw_stderr_tail="Traceback (most recent call last):\nKeyError: 'foo'",
        )
        self.assertEqual(classify_heuristic(rec), FIX_CODE)
        self.assertEqual(FIX_CWR_MODULE, FIX_CODE)

    def test_generator_failed_is_code(self):
        self.assertEqual(classify_heuristic(_cwr()), FIX_CODE)

    def test_unknown_defaults_host(self):
        rec = _cwr(message="something odd happened", raw_stderr_tail="")
        self.assertEqual(classify_heuristic(rec), FIX_HOST)

    def test_mysql_connect_is_host(self):
        rec = _cron(
            "cron_update_popularity_score",
            "DATABASE-INSERT",
            message="Failed to connect to database.",
            raw_stderr_tail="Can't connect to MySQL server",
        )
        self.assertEqual(classify_heuristic(rec), FIX_HOST)

    def test_non_cwr_traceback_is_code(self):
        rec = _cron(
            "cron_email_reader",
            "EMAIL-READER",
            message="TypeError: 'NoneType' object is not subscriptable",
            raw_stderr_tail="Traceback (most recent call last):\nTypeError: 'NoneType'",
        )
        self.assertEqual(classify_heuristic(rec), FIX_CODE)

    def test_pull_all_non_fast_forward_is_host(self):
        rec = _cron(
            "cron_pull_all",
            "OTHER-SCRIPTS",
            message="! [rejected] non-fast-forward",
            raw_stderr_tail="failed to push some refs (non-fast-forward)",
        )
        self.assertEqual(classify_heuristic(rec), FIX_HOST)


class FallbackMessageTests(unittest.TestCase):
    def test_code_does_not_promise_fix(self):
        msg = fallback_user_message(_cwr(), FIX_CODE, greeting="Hey,")
        self.assertTrue(msg.startswith("Hey,"))
        self.assertNotIn("I'm fixing it", msg)
        self.assertIn("code bug", msg)

    def test_host_mentions_production_mac(self):
        rec = _cwr(tool="cron_cwr_dispatch", message="Authentication failed")
        msg = fallback_user_message(rec, FIX_HOST, greeting="Hey,")
        self.assertTrue(msg.startswith("Hey,"))
        self.assertIn("production Mac", msg)
        self.assertNotIn("I'm fixing it", msg)

    def test_fixing_clause_appended_once(self):
        msg = with_fixing_clause("Hey, generation crashed.")
        self.assertEqual(msg, "Hey, generation crashed. I'm fixing it.")
        self.assertEqual(with_fixing_clause(msg), msg)


class FormatMessageTests(unittest.TestCase):
    def test_prefers_llm_user_message(self):
        rec = _cwr(user_message="Hey, MusicMark generation crashed.")
        text = format_message(rec)
        self.assertIn("Hey, MusicMark generation crashed", text)
        self.assertIn("ref deadbeef", text)

    def test_fix_success_uses_greeting(self):
        rec = _cwr(fix_target=FIX_CODE)
        text = format_fix_message(rec, success=True, detail="Patched execute_all.")
        self.assertTrue(text.startswith(operator_greeting()))
        self.assertIn("finished", text.lower())

    def test_interpret_without_llm_uses_heuristic(self):
        rec = _cwr(
            message="Authentication failed",
            raw_stderr_tail="Authentication failed",
            tool="cron_cwr_dispatch",
        )
        out = interpret_incident(rec)
        self.assertEqual(out["fix_target"], FIX_HOST)
        self.assertTrue(out["user_message"].startswith(operator_greeting().split(",")[0]))

    def test_interpret_non_cwr_mysql(self):
        rec = _cron(
            "cron_update_popularity_score",
            "DATABASE-INSERT",
            message="Failed to connect to database.",
            raw_stderr_tail="Can't connect to MySQL server",
        )
        out = interpret_incident(rec)
        self.assertEqual(out["fix_target"], FIX_HOST)
        self.assertIn("production Mac", out["user_message"])


class MacNotifyOnlyTests(unittest.TestCase):
    def test_generic_mac_cron_blocked(self):
        rec = {
            "host": "mac",
            "tool": "cron_pull_all",
            "module": "OTHER-SCRIPTS",
            "repos_name": "OTHER-SCRIPTS",
            "fix_target": FIX_CODE,
        }
        self.assertTrue(mac_notify_only(rec))

    def test_cwr_code_mac_cron_allowed(self):
        rec = _cwr(fix_target=FIX_CODE, host="mac")
        self.assertFalse(mac_notify_only(rec))

    def test_cwr_host_still_blocked(self):
        rec = _cwr(fix_target=FIX_HOST, host="mac", tool="cron_cwr_dispatch")
        self.assertTrue(mac_notify_only(rec))

    def test_code_alias_cwr_module_still_allowed(self):
        rec = _cwr(fix_target="cwr_module", host="mac")
        self.assertFalse(mac_notify_only(rec))


if __name__ == "__main__":
    unittest.main()
