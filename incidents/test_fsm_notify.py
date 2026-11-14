"""Unit tests for incident notify dedupe (one iMessage per open failure)."""

from __future__ import annotations

import unittest

from incidents.fsm import (
    mark_notified,
    should_notify,
    upsert_from_mac,
)
from incidents.ingest import _message_from_stdio
from incidents.notify import delivery_failure


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

    def test_notified_new_mac_ts_does_not_reopen(self):
        rec = {
            "state": "NOTIFIED",
            "seen_count": 2,
            "detected_at": "2026-08-06T07:01:17+00:00",
            "last_notified_at": "2026-08-06T07:05:00+00:00",
        }
        ok, reason = should_notify(
            rec,
            renotify_every=1,
            renotify_hours=0,
            new_detected_at="2026-08-06T08:00:00+00:00",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "already_notified")

    def test_fixing_does_not_renotify(self):
        rec = {
            "state": "FIXING",
            "seen_count": 4,
            "detected_at": "2026-08-06T07:01:17+00:00",
            "last_notified_at": "2026-08-06T07:05:00+00:00",
        }
        ok, reason = should_notify(
            rec,
            renotify_every=1,
            renotify_hours=0,
            new_detected_at="2026-08-06T08:00:00+00:00",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "already_notified")

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

    def test_escalated_send_does_not_text_again(self):
        rec = {
            "state": "ESCALATED",
            "detected_at": "2026-08-06T07:01:17+00:00",
            "escalation_reason": "state-migrations warning",
        }
        ok, reason = should_notify(
            rec,
            renotify_every=1,
            renotify_hours=0,
            new_detected_at="2026-08-06T08:00:00+00:00",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "escalated_once")

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

    def test_new_mac_ts_after_notify_stays_quiet(self):
        active: dict = {"incidents": {}}
        rec, _, _ = upsert_from_mac(active, _norm(), renotify_every=10, renotify_hours=24)
        mark_notified(rec)
        rec2, notify2, reason2 = upsert_from_mac(
            active,
            _norm(detected_at="2026-08-07T07:01:17+00:00"),
            renotify_every=1,
            renotify_hours=0,
        )
        self.assertFalse(notify2)
        self.assertEqual(reason2, "already_notified")
        self.assertEqual(rec2["state"], "NOTIFIED")

    def test_hourly_cron_retries_notify_once(self):
        """Repeating cron jsonl rows (new ts, same fingerprint) send one iMessage."""
        active: dict = {"incidents": {}}
        rec, notify, reason = upsert_from_mac(
            active, _norm(), renotify_every=1, renotify_hours=0
        )
        self.assertTrue(notify)
        self.assertEqual(reason, "new")
        mark_notified(rec)

        for hour in (
            "2026-09-22T18:10:31+00:00",
            "2026-09-22T19:10:30+00:00",
            "2026-09-22T20:10:31+00:00",
        ):
            rec, notify, reason = upsert_from_mac(
                active,
                _norm(detected_at=hour),
                renotify_every=1,
                renotify_hours=0,
            )
            self.assertFalse(notify, msg=hour)
            self.assertEqual(reason, "already_notified")
            self.assertEqual(rec["state"], "NOTIFIED")

        self.assertEqual(rec["seen_count"], 4)
        self.assertEqual(rec["detected_at"], "2026-08-06T07:01:17+00:00")

    def test_same_tool_second_fingerprint_does_not_text(self):
        active: dict = {"incidents": {}}
        rec, notify, _ = upsert_from_mac(
            active, _norm(), renotify_every=10, renotify_hours=24
        )
        self.assertTrue(notify)
        mark_notified(rec)
        _, notify2, reason2 = upsert_from_mac(
            active,
            _norm(
                fingerprint="ffffffffffffffff",
                detected_at="2026-08-06T09:00:00+00:00",
                message="a later attempt of the same job",
            ),
            renotify_every=10,
            renotify_hours=24,
        )
        self.assertFalse(notify2)
        self.assertEqual(reason2, "open_tool_incident")

    def test_different_tool_still_texts(self):
        active: dict = {"incidents": {}}
        rec, _, _ = upsert_from_mac(active, _norm(), renotify_every=10, renotify_hours=24)
        mark_notified(rec)
        _, notify2, reason2 = upsert_from_mac(
            active,
            _norm(fingerprint="eeeeeeeeeeeeeeee", tool="cron_email_reader"),
            renotify_every=10,
            renotify_hours=24,
        )
        self.assertTrue(notify2)
        self.assertEqual(reason2, "new")


class DeliveryFailureTests(unittest.TestCase):
    def test_migration_warning_after_delivery_is_success(self):
        warning = "[state-migrations] Legacy state migration warnings:\n"
        self.assertIsNone(delivery_failure(1, "", warning))

    def test_real_send_failure_is_reported(self):
        self.assertIsNotNone(delivery_failure(1, "", "unauthorized"))

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
            "/home/you/.venvs/fastmcp/lib/python3.14/site-packages/requests/"
            "__init__.py:113: RequestsDependencyWarning: urllib3 ...\n"
            "  warnings.warn(\n"
        )
        msg = _message_from_stdio(stdout, stderr)
        self.assertIn("Failed to connect", msg)
        self.assertNotIn("warnings.warn", msg.lower())


if __name__ == "__main__":
    unittest.main()
