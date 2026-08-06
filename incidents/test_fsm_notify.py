"""Unit tests for incident notify dedupe (one iMessage per open failure)."""

from __future__ import annotations

import unittest

from incidents.fsm import (
    mark_notified,
    should_notify,
    upsert_from_mac,
)
from incidents.ingest import _message_from_stdio


def _norm(**overrides):
    base = {
        "fingerprint": "abc123deadbeef",
        "detected_at": "2026-08-06T07:01:17+00:00",
        "source": "cron",
        "host": "mac",
        "tool": "cron_update_popularity_score",
        "module": "DATABASE-INSERT",
        "repos_name": "DATABASE-INSERT",
        "repos_rel_path": "update_popularity_score.py",
        "severity": "error",
        "kind": "nonzero_exit",
        "returncode": 1,
        "message": "Failed to connect to database.",
        "stack_trace": "",
        "raw_stderr_tail": "warnings.warn(",
        "mac_payload": {},
    }
    base.update(overrides)
    return base


class ShouldNotifyTests(unittest.TestCase):
    def test_new_incident_notifies(self):
        ok, reason = should_notify(None, renotify_every=10, renotify_hours=24)
        self.assertTrue(ok)
        self.assertEqual(reason, "new")

    def test_notified_never_spam(self):
        rec = {"state": "NOTIFIED", "seen_count": 99, "detected_at": "2026-08-06T07:01:17+00:00",
               "last_notified_at": "2026-08-06T07:05:00+00:00"}
        ok, reason = should_notify(
            rec,
            renotify_every=1,
            renotify_hours=0,
            new_detected_at="2026-08-06T07:01:17+00:00",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "already_notified")

    def test_resolved_same_mac_ts_does_not_reopen(self):
        rec = {"state": "RESOLVED", "detected_at": "2026-08-06T07:01:17+00:00"}
        ok, reason = should_notify(
            rec,
            renotify_every=10,
            renotify_hours=24,
            new_detected_at="2026-08-06T07:01:17+00:00",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "resolved_same_event")

    def test_resolved_new_mac_ts_reopens(self):
        rec = {"state": "RESOLVED", "detected_at": "2026-08-06T07:01:17+00:00"}
        ok, reason = should_notify(
            rec,
            renotify_every=10,
            renotify_hours=24,
            new_detected_at="2026-08-07T07:01:17+00:00",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "reopened")

    def test_resolved_without_new_ts_legacy_reopen(self):
        # Watchdog callers omit new_detected_at.
        rec = {"state": "RESOLVED", "detected_at": "2026-08-06T07:01:17+00:00"}
        ok, reason = should_notify(rec, renotify_every=10, renotify_hours=24)
        self.assertTrue(ok)
        self.assertEqual(reason, "reopened")


class UpsertFromMacTests(unittest.TestCase):
    def test_second_poll_after_notify_is_duplicate(self):
        active: dict = {"incidents": {}}
        rec, notify, reason = upsert_from_mac(
            active, _norm(), renotify_every=10, renotify_hours=24
        )
        self.assertTrue(notify)
        self.assertEqual(reason, "new")
        mark_notified(rec)

        rec2, notify2, reason2 = upsert_from_mac(
            active, _norm(), renotify_every=10, renotify_hours=24
        )
        self.assertFalse(notify2)
        self.assertEqual(reason2, "already_notified")
        self.assertEqual(rec2["state"], "NOTIFIED")
        self.assertEqual(rec2["seen_count"], 2)

    def test_resolved_same_event_stays_resolved(self):
        active: dict = {"incidents": {}}
        rec, _, _ = upsert_from_mac(active, _norm(), renotify_every=10, renotify_hours=24)
        mark_notified(rec)
        rec["state"] = "RESOLVED"

        rec2, notify2, reason2 = upsert_from_mac(
            active, _norm(), renotify_every=10, renotify_hours=24
        )
        self.assertFalse(notify2)
        self.assertEqual(reason2, "resolved_same_event")
        self.assertEqual(rec2["state"], "RESOLVED")


class MessageFromStdioTests(unittest.TestCase):
    def test_prefers_stdout_error_over_warnings_warn(self):
        stdout = (
            "2026-08-06 02:01:16  ERROR     Failed to connect to database.\n"
        )
        stderr = (
            "/Users/YOU/.venvs/fastmcp/lib/python3.14/site-packages/requests/"
            "__init__.py:113: RequestsDependencyWarning: urllib3 ...\n"
            "  warnings.warn(\n"
        )
        msg = _message_from_stdio(stdout, stderr)
        self.assertIn("Failed to connect", msg)
        self.assertNotIn("warnings.warn", msg.lower())


if __name__ == "__main__":
    unittest.main()
