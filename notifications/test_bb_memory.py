"""Unit tests for BlueBubbles → OpenClaw memory ingest."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from notifications.bb_memory import (
    PAGE_LIMIT,
    SECTION_HEADING,
    append_entry,
    ensure_memory_hint,
    entry_token,
    format_entry,
    handles_from_incidents_text,
    is_direct_to_handle,
    iter_new_messages,
    message_datetime,
    message_guid,
    remember_messages,
    should_remember,
)

TZ = ZoneInfo("America/Chicago")


def _message(**overrides):
    base = {
        "guid": "AAA-111",
        "text": "Hey Max, you have 1 new LOD you need to submit to PROs whenever you get a chance.",
        "isFromMe": True,
        "dateCreated": 1790157609205,
        "associatedMessageGuid": None,
        "chats": [
            {
                "guid": "any;-;+16083336132",
                "chatIdentifier": "+16083336132",
            }
        ],
    }
    base.update(overrides)
    return base


class ChatFilterTests(unittest.TestCase):
    def test_max_dm_is_direct(self):
        self.assertTrue(
            is_direct_to_handle("any;-;+16083336132", "+16083336132", "+16083336132")
        )

    def test_group_is_not_direct(self):
        self.assertFalse(
            is_direct_to_handle(
                "any;+;b10a6aaddb2f4462811f32abf65e8ca4",
                "b10a6aaddb2f4462811f32abf65e8ca4",
                "+16083336132",
            )
        )

    def test_other_dm_is_not_max(self):
        self.assertFalse(
            is_direct_to_handle("any;-;+14109803992", "+14109803992", "+16083336132")
        )

    def test_remember_notification(self):
        self.assertTrue(should_remember(_message(), ["+16083336132"]))

    def test_skip_reaction(self):
        self.assertFalse(
            should_remember(
                _message(associatedMessageGuid="p:0/ABC", text="Liked a message"),
                ["+16083336132"],
            )
        )

    def test_skip_inbound(self):
        self.assertFalse(should_remember(_message(isFromMe=False), ["+16083336132"]))

    def test_skip_empty(self):
        self.assertFalse(should_remember(_message(text="  "), ["+16083336132"]))

    def test_skip_group_even_if_from_me(self):
        message = _message(
            chats=[
                {
                    "guid": "any;+;b10a6aaddb2f4462811f32abf65e8ca4",
                    "chatIdentifier": "b10a6aaddb2f4462811f32abf65e8ca4",
                }
            ]
        )
        self.assertFalse(should_remember(message, ["+16083336132"]))


class FormatTests(unittest.TestCase):
    def test_timestamp_is_chicago(self):
        when = message_datetime(_message(), TZ)
        self.assertIsNotNone(when)
        assert when is not None
        self.assertEqual(when.tzinfo, TZ)
        self.assertEqual(when.year, 2026)

    def test_entry_is_one_bullet_and_stable_token(self):
        when = datetime(2026, 9, 23, 12, 50, tzinfo=TZ)
        entry = format_entry(when, "Hey Max,\nBMI returned 29 works.", "GUID-1")
        self.assertIn(f"<!-- bb:{entry_token('GUID-1')} -->", entry)
        self.assertIn("Hey Max, BMI returned 29 works.", entry)
        self.assertEqual(entry.count("\n- "), 1)

    def test_append_is_idempotent_and_keeps_other_notes(self):
        when = datetime(2026, 9, 23, 12, 50, tzinfo=TZ)
        entry = format_entry(when, "Hey Max, backup sync failed.", "GUID-2")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "2026-09-23.md"
            day.write_text("# 2026-09-23\n\nOperator note stays.\n", encoding="utf-8")
            self.assertTrue(append_entry(root, when, entry))
            self.assertFalse(append_entry(root, when, entry))
            text = day.read_text(encoding="utf-8")
            self.assertIn("Operator note stays.", text)
            self.assertIn(SECTION_HEADING, text)
            self.assertEqual(text.count("backup sync failed"), 1)

    def test_remember_sorts_oldest_first_and_marks_other_chats_seen(self):
        older = _message(guid="OLD", dateCreated=1790100000000, text="Hey Max, older.")
        newer = _message(guid="NEW", dateCreated=1790200000000, text="Hey Max, newer.")
        other = _message(
            guid="COLE",
            text="hello cole",
            chats=[{"guid": "any;-;+14109803992", "chatIdentifier": "+14109803992"}],
        )
        seen: set[str] = set()
        with tempfile.TemporaryDirectory() as tmp:
            written, pending = remember_messages(
                [newer, other, older],
                handles=["+16083336132"],
                memory_root=Path(tmp),
                tz=TZ,
                seen=seen,
                dry_run=False,
            )
            self.assertEqual(written, 2)
            self.assertEqual(pending, 2)
            self.assertEqual(seen, {"OLD", "NEW", "COLE"})
            body = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(Path(tmp).glob("*.md"))
            )
            self.assertLess(body.index("older"), body.index("newer"))
            self.assertNotIn("hello cole", body)


class HintAndTargetsTests(unittest.TestCase):
    def test_incidents_targets_inline_and_list(self):
        text = """
notify_backend: bluebubbles
notify_targets: ['+16083336132']
message_greeting: Hey Max,
"""
        self.assertEqual(handles_from_incidents_text(text), ["+16083336132"])
        listed = """
notify_targets:
  - '+16083336132'
  - '+19995550100'
notify_channel: bluebubbles
"""
        self.assertEqual(
            handles_from_incidents_text(listed),
            ["+16083336132", "+19995550100"],
        )

    def test_hint_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MEMORY.md"
            path.write_text("# Orion Memory\n\n- Keep it short.\n", encoding="utf-8")
            self.assertTrue(ensure_memory_hint(path))
            self.assertFalse(ensure_memory_hint(path))
            body = path.read_text(encoding="utf-8")
            self.assertIn("Keep it short.", body)
            self.assertEqual(body.count("## iMessage memory"), 1)
            self.assertIn("memory_search", body)


class PagingTests(unittest.TestCase):
    @patch("notifications.bb_memory.query_messages")
    def test_incremental_stops_after_known_page(self, query):
        old_page = [_message(guid=f"OLD{i}", text=f"Hey Max, old {i}") for i in range(PAGE_LIMIT)]
        new_row = _message(guid="NEW", text="Hey Max, brand new notification")
        first = [new_row, *old_page[:-1]]
        query.side_effect = [(first, None), (old_page, None)]
        seen = {f"OLD{i}" for i in range(PAGE_LIMIT)}
        rows = iter_new_messages("http://bluebubbles.local", "secret", seen, backfill=False)
        self.assertEqual([message_guid(row) for row in rows], ["NEW"])
        self.assertEqual(query.call_count, 2)


class StateFileTests(unittest.TestCase):
    def test_roundtrip_shape(self):
        payload = {"version": 1, "seen": ["a"], "index_dirty": False}
        raw = json.dumps(payload)
        loaded = json.loads(raw)
        self.assertEqual(loaded["seen"], ["a"])


if __name__ == "__main__":
    unittest.main()
