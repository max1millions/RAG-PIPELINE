"""Programmed incident templates skip the LLM for known Mac ops failures."""

from __future__ import annotations

import os
import unittest

os.environ["ORION_INCIDENT_LLM"] = "0"

from incidents.interpret import FIX_HOST, interpret_incident
from incidents.templates import match_incident_template


def _cron(tool: str, module: str, **overrides):
    base = {
        "fingerprint": "eeab865aeeab865a",
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


class TemplateMatchTests(unittest.TestCase):
    def test_pull_all_fills_repo_variable(self):
        rec = _cron(
            "cron_pull_all",
            "OTHER-SCRIPTS",
            message="1 pull/submodule failure(s): FAIL DATABASE-INSERT (main). See pull-all.log",
            raw_stderr_tail="1 pull/submodule failure(s): FAIL DATABASE-INSERT (main). See pull-all.log",
        )
        hit = match_incident_template(rec, "Hey Max,")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["fix_target"], FIX_HOST)
        self.assertEqual(
            hit["user_message"],
            "Hey Max, the scheduled pull-all job failed because the "
            "DATABASE-INSERT repo couldn't be pulled on the production Mac. "
            "This is an environment issue, not a repo patch.",
        )

    def test_pull_all_two_repos(self):
        rec = _cron(
            "cron_pull_all",
            "OTHER-SCRIPTS",
            message="FAIL DATABASE-INSERT (main); FAIL ISWC-SERVICE (main)",
            raw_stderr_tail="FAIL DATABASE-INSERT (main); FAIL ISWC-SERVICE (main)",
        )
        hit = match_incident_template(rec, "Hey Max,")
        assert hit is not None
        self.assertIn("the DATABASE-INSERT and ISWC-SERVICE repos", hit["user_message"])

    def test_backup_and_gateway(self):
        backup = match_incident_template(
            _cron("cron_sync_backup", "OTHER-SCRIPTS", message="rsync failed"),
            "Hey Max,",
        )
        gateway = match_incident_template(
            _cron("api_gateway", "OTHER-SCRIPTS", message="exited with code 1"),
            "Hey Max,",
        )
        assert backup is not None and gateway is not None
        self.assertIn("backup sync job failed", backup["user_message"])
        self.assertIn("API gateway", gateway["user_message"])

    def test_disk_and_mysql(self):
        disk = match_incident_template(
            _cron(
                "cron_run_cron_job",
                "OTHER-SCRIPTS",
                message="No space left on device",
                raw_stderr_tail="OSError: [Errno 28] No space left on device",
            ),
            "Hey Max,",
        )
        mysql = match_incident_template(
            _cron(
                "cron_update_popularity_score",
                "DATABASE-INSERT",
                message="Failed to connect to database.",
                raw_stderr_tail="Can't connect to MySQL server",
            ),
            "Hey Max,",
        )
        assert disk is not None and mysql is not None
        self.assertIn("out of disk space", disk["user_message"])
        self.assertIn("MySQL on the production Mac could not be reached", mysql["user_message"])

    def test_unknown_stays_unmatched(self):
        rec = _cron(
            "cron_email_reader",
            "EMAIL-READER",
            message="TypeError: boom",
            raw_stderr_tail="Traceback (most recent call last):\nTypeError: boom",
        )
        self.assertIsNone(match_incident_template(rec, "Hey Max,"))

    def test_interpret_skips_llm_for_pull_all(self):
        rec = _cron(
            "cron_pull_all",
            "OTHER-SCRIPTS",
            message="1 pull/submodule failure(s): FAIL DATABASE-INSERT (main). See pull-all.log",
            raw_stderr_tail="1 pull/submodule failure(s): FAIL DATABASE-INSERT (main). See pull-all.log",
        )
        out = interpret_incident(rec)
        self.assertEqual(out["fix_target"], FIX_HOST)
        self.assertIn("DATABASE-INSERT repo couldn't be pulled", out["user_message"])
        self.assertIn("environment issue, not a repo patch", out["user_message"])


if __name__ == "__main__":
    unittest.main()
