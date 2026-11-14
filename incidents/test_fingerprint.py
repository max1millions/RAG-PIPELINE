"""Fingerprints ignore volatile portal URLs and tokens."""

from __future__ import annotations

import unittest

from incidents.fingerprint import compute_fingerprint, normalize_stderr


def _row(stderr: str) -> dict:
    return {
        "kind": "nonzero_exit",
        "tool": "cisnet_mlc_automation",
        "returncode": 1,
        "stderr": stderr,
    }


class FingerprintNormalizeTests(unittest.TestCase):
    def test_hubspot_retries_share_one_fingerprint(self):
        first = (
            'Fatal portal error: Locator.click: Timeout 10000ms exceeded.\n'
            'iframe title="Chat Widget" id="hubspot-conversations-iframe" '
            "src=https://app.hubspot.com/conversations-visitor/8718396/threads/"
            "utk/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?uuid=11111111-2222-3333-4444-555555555555"
        )
        second = first.replace("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        second = second.replace(
            "11111111-2222-3333-4444-555555555555",
            "99999999-8888-7777-6666-555555555555",
        )
        self.assertEqual(compute_fingerprint(_row(first)), compute_fingerprint(_row(second)))
        self.assertNotIn("hubspot.com", normalize_stderr(first))

    def test_playwright_call_log_counts_do_not_split_the_fingerprint(self):
        fatal = "Fatal portal error: Locator.wait_for: Timeout 15000ms exceeded."
        first = fatal + "\n  - 2 × waiting for element to be visible"
        second = fatal + "\n  - 14 × waiting for element to be visible\n" + (
            "https://app.hubspot.com/conversations-visitor/1/utk/abc?uuid="
            "11111111-2222-3333-4444-555555555555"
        )
        self.assertEqual(compute_fingerprint(_row(first)), compute_fingerprint(_row(second)))

    def test_different_errors_stay_distinct(self):
        click = "Fatal portal error: Locator.click: Timeout 10000ms exceeded."
        wait = "Fatal portal error: Locator.wait_for: Timeout 15000ms exceeded."
        self.assertNotEqual(compute_fingerprint(_row(click)), compute_fingerprint(_row(wait)))


if __name__ == "__main__":
    unittest.main()
