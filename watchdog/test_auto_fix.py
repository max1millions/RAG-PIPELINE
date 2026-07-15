#!/usr/bin/env python3
"""Unit tests for watchdog auto_fix gating, request build, and disabled default path."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from watchdog.checks import auto_fix_config, should_attempt_auto_fix  # noqa: E402
from watchdog.remediate import build_remediation_request, resolve_fix_repo  # noqa: E402


class AutoFixConfigTests(unittest.TestCase):
    def test_mode_code_when_enabled_without_fix_sql(self) -> None:
        cfg = auto_fix_config(
            {"auto_fix": {"enabled": True, "mode": "code", "repo": "CWR-INTERFACE"}}
        )
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["mode"], "code")
        self.assertEqual(cfg["repo"], "CWR-INTERFACE")
        self.assertIsNone(cfg["fix_sql_file"])

    def test_mode_defaults_to_sql_when_fix_sql_file_set(self) -> None:
        cfg = auto_fix_config(
            {
                "auto_fix": {
                    "enabled": True,
                    "fix_sql_file": "SQL-SCRIPTS/SCRIPTS/fix.sql",
                }
            }
        )
        self.assertEqual(cfg["mode"], "sql")
        self.assertEqual(cfg["fix_sql_file"], "SQL-SCRIPTS/SCRIPTS/fix.sql")

    def test_explicit_mode_overrides_fix_sql_default(self) -> None:
        cfg = auto_fix_config(
            {
                "auto_fix": {
                    "enabled": True,
                    "mode": "code",
                    "fix_sql_file": "SQL-SCRIPTS/SCRIPTS/fix.sql",
                }
            }
        )
        self.assertEqual(cfg["mode"], "code")

    def test_null_fix_sql_normalized(self) -> None:
        cfg = auto_fix_config({"auto_fix": {"enabled": False, "fix_sql_file": None}})
        self.assertIsNone(cfg["fix_sql_file"])
        self.assertEqual(cfg["mode"], "code")


class ShouldAttemptAutoFixTests(unittest.TestCase):
    def test_disabled_global_flag_blocks_code_mode(self) -> None:
        check = {
            "id": "role_code_violations",
            "auto_fix": {"enabled": True, "mode": "code", "repo": "CWR-INTERFACE"},
        }
        with patch("watchdog.checks.feature_enabled", return_value=False):
            self.assertFalse(should_attempt_auto_fix(check))

    def test_enabled_global_and_code_mode(self) -> None:
        check = {
            "id": "role_code_violations",
            "auto_fix": {"enabled": True, "mode": "code", "repo": "CWR-INTERFACE"},
        }
        with patch("watchdog.checks.feature_enabled", return_value=True):
            self.assertTrue(should_attempt_auto_fix(check))

    def test_sql_mode_requires_fix_file(self) -> None:
        check = {"auto_fix": {"enabled": True, "mode": "sql", "fix_sql_file": None}}
        with patch("watchdog.checks.feature_enabled", return_value=True):
            self.assertFalse(should_attempt_auto_fix(check))

    def test_sql_mode_with_fix_file(self) -> None:
        check = {
            "auto_fix": {
                "enabled": True,
                "mode": "sql",
                "fix_sql_file": "SQL-SCRIPTS/SCRIPTS/fix.sql",
            }
        }
        with patch("watchdog.checks.feature_enabled", return_value=True):
            self.assertTrue(should_attempt_auto_fix(check))

    def test_per_check_disabled_blocks(self) -> None:
        check = {"auto_fix": {"enabled": False, "mode": "code"}}
        with patch("watchdog.checks.feature_enabled", return_value=True):
            self.assertFalse(should_attempt_auto_fix(check))


class RemediationRequestTests(unittest.TestCase):
    def test_build_includes_sample_rows_and_sql_file(self) -> None:
        record = {
            "fingerprint": "abcdef0123456789",
            "check_id": "role_code_violations",
            "message": "Found 3.0 role code violations.",
            "assertion_reason": "count 3 > max 0.0",
            "sql_file": "SQL-SCRIPTS/VALIDATION/ROLE_CODE_VALIDATION.sql",
            "sample_rows": [{"WorkID": 1, "Violation": "Missing C"}],
        }
        text = build_remediation_request(record, repos_name="CWR-INTERFACE")
        self.assertIn("watchdog ref abcdef01", text)
        self.assertIn("Repo: CWR-INTERFACE", text)
        self.assertIn("role_code_violations", text)
        self.assertIn("ROLE_CODE_VALIDATION.sql", text)
        self.assertIn("Missing C", text)
        self.assertIn("do not write to production mysql", text.lower())

    def test_resolve_repo_prefers_auto_fix_repo(self) -> None:
        record = {"repos_name": "SQL-SCRIPTS"}
        check = {"repos_hint": "SQL-SCRIPTS", "auto_fix": {"repo": "CWR-INTERFACE"}}
        self.assertEqual(resolve_fix_repo(record, check), "CWR-INTERFACE")

    def test_resolve_repo_falls_back_to_record(self) -> None:
        record = {"repos_name": "CIS-NET-AUTOMATION"}
        self.assertEqual(resolve_fix_repo(record, None), "CIS-NET-AUTOMATION")


class RunWatchdogDisabledPathTests(unittest.TestCase):
    def test_run_skips_code_fix_when_flag_off(self) -> None:
        from watchdog import run as run_mod

        check_def = {
            "id": "role_code_violations",
            "auto_fix": {"enabled": True, "mode": "code", "repo": "CWR-INTERFACE"},
            "message_template": "Found {metric_value} role code violations.",
            "severity": "error",
            "repos_hint": "SQL-SCRIPTS",
            "sql_file": "SQL-SCRIPTS/VALIDATION/ROLE_CODE_VALIDATION.sql",
        }
        fail_result = {
            "check_id": "role_code_violations",
            "ok": True,
            "passed": False,
            "metric_value": 2.0,
            "reason": "count 2 > max 0.0",
            "severity": "error",
            "sample_rows": [{"WorkID": 9}],
            "sql_file": check_def["sql_file"],
            "message_template": check_def["message_template"],
        }

        with (
            patch.object(run_mod, "require_feature"),
            patch.object(run_mod, "load_incidents_config", return_value={}),
            patch.object(
                run_mod,
                "load_watchdog_config",
                return_value={"checks": [check_def]},
            ),
            patch.object(run_mod, "run_all_checks", return_value=[fail_result]),
            patch.object(run_mod, "feature_enabled", side_effect=lambda name: name == "watchdog_notify"),
            patch.object(run_mod, "should_attempt_auto_fix", return_value=False) as attempt,
            patch.object(run_mod, "remediate_record") as remediate,
            patch.object(
                run_mod,
                "upsert_anomaly",
                return_value=({"fingerprint": "aabbccdd", "check_id": "role_code_violations"}, True, "new"),
            ),
            patch.object(run_mod, "send_anomaly_notification", return_value=(True, "ok")),
            patch.object(run_mod, "mark_anomaly_notified"),
            patch.object(run_mod, "load_active", return_value={"incidents": {}}),
            patch.object(run_mod, "save_active"),
            patch.object(run_mod, "_save_run_history"),
        ):
            result = run_mod.run_watchdog(dry_run=False)

        attempt.assert_called()
        remediate.assert_not_called()
        self.assertEqual(result["auto_fixed"], [])
        self.assertEqual(len(result["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
