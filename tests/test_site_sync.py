"""No stale fallback or credential leaks while reading the private Site."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from scripts import site_bridge as bridge
from scripts import sync_site_watchlist as sync

TOKEN = "test-only-" + "x" * 34
RULE = dict(id="rule-1", market="US", ticker="SOXL", name="ETF", target_price="20",
            upper_pct="5", lower_pct="5", mode="daily", enabled=True)


def response(document=None, *, raw=None, content_type="application/json", url=sync.SITE_URL):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status = 200
    result.headers = Message()
    result.headers["Content-Type"] = content_type
    result.geturl.return_value = url
    result.read.return_value = raw if raw is not None else json.dumps(document).encode()
    return result


class ScheduleTests(unittest.TestCase):
    def test_seoul_7am_uses_next_calendar_day_and_needs_no_dated_request(self):
        now = datetime(2026, 11, 11, 22, tzinfo=timezone.utc)
        for cron in bridge.SCHEDULES:
            self.assertEqual(bridge.decide_action("schedule", now=now, scheduled_cron=cron), "check")

    def test_weekend_and_early_jobs_never_send(self):
        for now in (datetime(2026, 11, 11, 21, 59, tzinfo=timezone.utc),
                    datetime(2026, 11, 14, 22, tzinfo=timezone.utc),
                    datetime(2026, 11, 15, 22, tzinfo=timezone.utc)):
            self.assertEqual(bridge.decide_action("schedule", now=now, scheduled_cron="0 7 * * 2-6"), "skip")

    def test_tuesday_and_saturday_are_both_report_days(self):
        for day in (9, 13):
            now = datetime(2026, 11, day, 22, tzinfo=timezone.utc)
            self.assertEqual(bridge.decide_action("schedule", now=now, scheduled_cron="20 7 * * 2-6"), "check")

    def test_unknown_schedule_and_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            bridge.decide_action("schedule", scheduled_cron="* * * * *")
        with self.assertRaises(ValueError):
            bridge.decide_action("schedule", now=datetime(2026, 11, 12), scheduled_cron="0 7 * * 2-6")

    def test_select_only_does_not_read_a_historical_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps({"inputs": {"action": "check"}}), encoding="utf-8")
            output = Path(temporary) / "output"
            with redirect_stdout(io.StringIO()):
                code = bridge.main(["--event", str(event), "--event-name", "workflow_dispatch",
                                    "--config", str(Path(temporary) / "missing.json"),
                                    "--output", str(output), "--select-only"])
            self.assertEqual(code, 0)
            self.assertEqual(output.read_text(), "action=check\n")


class SiteSyncTests(unittest.TestCase):
    def test_live_complete_snapshot_and_credential_only_in_fixed_host_header(self):
        opener = Mock()
        opener.open.return_value = response({"version": 1, "rules": [RULE]})
        actual = sync.fetch_watchlist(TOKEN, opener=opener)
        self.assertEqual(actual["rules"][0]["ticker"], "SOXL")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, sync.SITE_URL)
        self.assertEqual(request.get_header("Oai-sites-authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)

    def test_legitimate_empty_list_reflects_deleted_rules(self):
        opener = Mock()
        opener.open.return_value = response({"version": 1, "rules": []})
        self.assertEqual(sync.fetch_watchlist(TOKEN, opener=opener), {"version": 1, "rules": []})

    def test_no_redirect_to_any_login_or_foreign_host(self):
        self.assertIsNone(sync.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid/"))
        opener = Mock()
        opener.open.return_value = response({"version": 1, "rules": []}, url="https://evil.invalid/")
        with self.assertRaises(sync.SyncError):
            sync.fetch_watchlist(TOKEN, opener=opener)

    def test_html_invalid_partial_and_oversize_snapshots_rejected(self):
        invalid = [response(raw=b"<html>Sign in</html>", content_type="text/html"),
                   response(raw=b"{not-json"), response({"version": 1, "rules": [{}]}),
                   response({"version": 1, "rules": [RULE], "has_more": True}),
                   response({"version": 1, "rules": [dict(RULE, id=str(i)) for i in range(101)]}),
                   response(raw=b" " * (sync.MAX_BYTES + 1))]
        for value in invalid:
            opener = Mock()
            opener.open.return_value = value
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                sync.fetch_watchlist(TOKEN, opener=opener)

    def test_auth_failure_does_not_retry_or_expose_response_or_token(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(sync.SITE_URL, 403, TOKEN, {}, None)
        with self.assertRaises(sync.SyncError) as caught:
            sync.fetch_watchlist(TOKEN, opener=opener)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_transient_network_error_retried_but_never_forever(self):
        opener = Mock()
        opener.open.side_effect = URLError(TOKEN)
        sleep = Mock()
        with self.assertRaises(sync.SyncError) as caught:
            sync.fetch_watchlist(TOKEN, opener=opener, sleep=sleep)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_invalid_secret_rejected_before_network(self):
        for token in ("", None, "<redacted>", "***", "token\r\nheader: injected", "x" * 8193):
            opener = Mock()
            with self.assertRaises(sync.SyncError):
                sync.fetch_watchlist(token, opener=opener)
            opener.open.assert_not_called()

    def test_failed_fetch_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "list.json"
            target.write_text("old content", encoding="utf-8")
            out = io.StringIO()
            with patch.object(sync, "fetch_watchlist", side_effect=sync.SyncError("읽기 실패")), redirect_stdout(out):
                code = sync.main(["--output", str(target)])
            self.assertEqual(code, 1)
            self.assertEqual(target.read_text(), "old content")
            self.assertNotIn(TOKEN, out.getvalue())

    def test_write_only_after_full_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "list.json"
            sync.write_snapshot(path, {"version": 1, "rules": [RULE]})
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                sync.write_snapshot(path, {"version": 1, "rules": [{}]})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(path.parent.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
