"""Full-list formatting, schedule, SMTP MIME and durable report delivery."""
import io
import json
import os
import smtplib
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stock_alert import cloud
from stock_alert.digest import render_report, source_link
from stock_alert.mailer import MailError, send_mail
from stock_alert.provider import ProviderError

TUESDAY = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)
WEDNESDAY = datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)
MONDAY = datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)


def rule(identifier, **changes):
    return {"id": identifier, "market": "US", "ticker": identifier, "target_price": "100", "upper_pct": "5", "lower_pct": "5", **changes}


class Provider:
    def __init__(self):
        self.prices = {"AAPL": "100", "MSFT": "200", "QQQ": "95"}
        self.calls = []

    def fetch_close(self, market, ticker):
        self.calls.append((market, ticker))
        if ticker == "FAIL":
            raise ProviderError("완료된 정규장 종가가 아직 없습니다.")
        return SimpleNamespace(close=Decimal(self.prices.get(ticker, "100")),
                               name=ticker, currency="USD", trade_date=date(2026, 11, 2),
                               source_url=f"https://m.stock.naver.com/worldstock/stock/{ticker}.O/total")


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "alerts.db"
        self.provider = Provider()
        self.messages = []
        self.service = cloud.CloudService(self.path, self.provider, self.mail)
        environment = patch.dict(os.environ, {"SMTP_PASSWORD": "fake-test-secret"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def mail(self, settings, subject, body, *, html_body=None):
        self.messages.append((settings, subject, body, html_body))
        return "<accepted@invalid>"

    def sync(self, *rows):
        cloud.sync_rules(self.service, {"version": 1, "rules": list(rows)})

    def state(self):
        with self.service.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM daily_reports ORDER BY kind")]

    def test_all_registered_rules_are_listed_matched_first_with_red_name_and_status(self):
        self.sync(rule("AAPL", name="애플"), rule("MSFT"), rule("QQQ"), rule("PAUSED", enabled=False), rule("FAIL"))
        result = self.service.digest(now=TUESDAY)
        self.assertEqual((result["sent"], result["total"], result["matched"], result["errors"]), (1, 5, 2, 1))
        self.assertEqual([row["ticker"] for row in result["results"]][:2], ["QQQ", "AAPL"])
        self.assertEqual({row["status"] for row in result["results"]}, {"목표가 도달", "목표가 미달", "일시정지", "조회 실패"})
        _, subject, plain, html = self.messages[0]
        self.assertIn("전체 5개", subject)
        self.assertIn("95 ~ 105 USD", html)
        self.assertIn("2026-11-02", plain)
        self.assertIn('href="https://m.stock.naver.com/worldstock/stock/AAPL.O/total" style="color:#d32f2f;font-weight:700;', html)
        self.assertIn('<strong style="color:#d32f2f;font-weight:700;">목표가 도달</strong>', html)
        self.assertNotIn(("US", "PAUSED"), self.provider.calls)
        self.assertEqual(self.state()[0]["status"], "sent")

    def test_empty_and_zero_match_watchlists_still_send_one_report(self):
        for rows in ((), (rule("MSFT"),)):
            with self.subTest(rows=rows):
                self.sync(*rows)
                # A different date gives each independent scenario its own report.
                day = TUESDAY if not rows else WEDNESDAY
                result = self.service.digest(now=day)
                self.assertEqual((result["sent"], result["matched"]), (1, 0))
        self.assertIn("등록된 종목이 없습니다.", self.messages[0][3])

    def test_every_quote_failure_still_sends_a_report_with_failure_rows(self):
        self.sync(rule("FAIL"))
        result = self.service.digest(now=TUESDAY)
        self.assertEqual((result["sent"], result["errors"]), (1, 1))
        self.assertIn("조회 실패", self.messages[0][3])
        self.assertNotIn("목표가 미달</strong>", self.messages[0][3])

    def test_duplicate_ticker_is_fetched_once_and_each_rule_is_retained(self):
        self.sync(rule("one", ticker="AAPL"), rule("two", ticker="AAPL", target_price="150"))
        result = self.service.digest(now=TUESDAY)
        self.assertEqual((result["total"], result["matched"]), (2, 1))
        self.assertEqual(self.provider.calls, [("US", "AAPL")])

    def test_schedule_uses_korean_day_tuesday_through_saturday(self):
        self.sync(rule("AAPL"))
        self.assertEqual(self.service.digest(now=MONDAY)["status"], "skipped")
        sunday = datetime(2026, 11, 7, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.digest(now=sunday)["status"], "skipped")
        saturday = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(self.service.digest(now=saturday)["sent"], 1)
        self.assertEqual(self.service.digest(now=TUESDAY)["sent"], 1)
        with self.assertRaises(ValueError):
            self.service.digest(now=datetime(2026, 11, 3))

    def test_test_report_bypasses_weekday_and_does_not_consume_regular_report(self):
        self.sync(rule("AAPL"))
        self.assertEqual(self.service.digest(test=True, now=MONDAY)["sent"], 1)
        self.assertTrue(self.messages[0][1].startswith("[테스트]"))
        self.assertEqual(self.service.digest(test=True, now=TUESDAY)["sent"], 1)
        self.assertEqual(self.service.digest(now=TUESDAY)["sent"], 1)
        self.assertEqual(self.service.digest(test=True, now=TUESDAY)["sent"], 0)

    def test_same_korean_date_is_deduplicated_across_restart_but_next_date_sends(self):
        self.sync(rule("AAPL"))
        self.assertEqual(self.service.digest(now=TUESDAY)["sent"], 1)
        restarted = cloud.CloudService(self.path, self.provider, self.mail)
        self.assertEqual(restarted.digest(now=TUESDAY)["sent"], 0)
        # The same close on a following report date is still listed and mailed.
        self.assertEqual(restarted.digest(now=WEDNESDAY)["sent"], 1)
        self.assertEqual(len(self.messages), 2)

    def test_pending_reservation_is_committed_and_persisted_before_smtp(self):
        events = []
        def persist():
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute("SELECT status FROM daily_reports").fetchall(), [("pending",)])
            events.append("persist")
        def smtp(*args, **kwargs):
            self.assertEqual(events, ["persist"])
            self.assertIn("<table", kwargs["html_body"])
            events.append("smtp")
            return "<accepted@invalid>"
        service = cloud.CloudService(self.path, self.provider, smtp, before_send=persist)
        self.assertEqual(service.digest(now=TUESDAY)["sent"], 1)
        self.assertEqual(events, ["persist", "smtp"])

    def test_failed_pre_send_persistence_blocks_smtp_and_allows_known_safe_retry(self):
        def failed_persist():
            raise RuntimeError("private server diagnostics")
        service = cloud.CloudService(self.path, self.provider, self.mail, before_send=failed_persist)
        with self.assertRaises(MailError) as caught:
            service.digest(now=TUESDAY)
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn("private server", str(caught.exception))
        self.assertEqual((self.messages, self.state()[0]["status"]), ([], "failed"))
        self.assertEqual(self.service.digest(now=TUESDAY)["sent"], 1)

    def test_uncertain_smtp_blocks_retry(self):
        def uncertain(*args, **kwargs):
            raise MailError("접수 여부 불명확", uncertain=True)
        service = cloud.CloudService(self.path, self.provider, uncertain)
        with self.assertRaises(MailError):
            service.digest(now=TUESDAY)
        self.assertEqual(self.state()[0]["status"], "uncertain")
        with self.assertRaisesRegex(MailError, "불명확"):
            self.service.digest(now=TUESDAY)
        self.assertEqual(self.messages, [])

    def test_crash_after_persisted_pending_never_automatically_resends(self):
        def stop():
            raise KeyboardInterrupt()
        service = cloud.CloudService(self.path, self.provider, self.mail, before_send=stop)
        with self.assertRaises(KeyboardInterrupt):
            service.digest(now=TUESDAY)
        self.assertEqual(self.state()[0]["status"], "pending")
        with self.assertRaisesRegex(MailError, "불명확"):
            self.service.digest(now=TUESDAY)

    def test_preview_neither_sends_nor_consumes_report(self):
        self.sync(rule("AAPL"))
        self.assertEqual(self.service.digest(now=TUESDAY, send=False)["status"], "preview")
        self.assertEqual(self.state(), [])
        self.assertEqual(self.messages, [])
        self.assertEqual(self.service.digest(now=TUESDAY)["sent"], 1)

    def test_older_quote_does_not_replace_newer_cached_close_or_mark_a_match(self):
        self.sync(rule("AAPL"))
        with self.service.db() as db:
            db.execute("UPDATE rules SET last_price='110',last_date='2026-11-03'")
        result = self.service.digest(now=TUESDAY)
        self.assertEqual((result["matched"], result["errors"]), (0, 1))
        self.assertEqual(self.service.rules()[0]["last_price"], "110")

    def test_html_escapes_untrusted_names_and_rejects_foreign_link_hosts(self):
        self.sync(rule("AAPL", name='<img src=x onerror="alert(1)">'))
        self.service.digest(now=TUESDAY)
        html = self.messages[0][3]
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        for url in ("javascript:alert(1)", "https://m.stock.naver.com.evil.invalid/stock/AAPL", "https://evil.invalid/AAPL"):
            self.assertTrue(source_link("AAPL", url).startswith("https://finance.naver.com/search/"))
        for path in ("domestic/etf/069500", "worldstock/etf/SOXL.K"):
            url = "https://m.stock.naver.com/" + path + "/total"
            self.assertEqual(source_link("SOXL", url), url)

    def test_test_digest_cli_reads_config_and_wires_state_persistence(self):
        config = Path(self.directory.name) / "watchlist.json"
        config.write_text(json.dumps({"version": 1, "rules": []}), encoding="utf-8")
        output = io.StringIO()
        factory = cloud.CloudService
        events = []
        def create(path, **kwargs):
            return factory(path, self.provider, self.mail, **kwargs)
        def persist(database, marker):
            self.assertEqual(database, self.path)
            self.assertEqual(marker.name, "github-state.json")
            events.append("persist")
        with patch.object(cloud, "CloudService", side_effect=create), patch.object(cloud, "persist_state", side_effect=persist), redirect_stdout(output):
            code = cloud.main(["--config", str(config), "--data-dir", self.directory.name, "--test-digest", "--persist-state"])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result["sent"], result["test"]), (0, 1, True))
        self.assertEqual(events, ["persist"])
        self.assertEqual(self.messages[0][0]["recipient"], "ykhan@dacpole.com")


class DigestMimeTests(unittest.TestCase):
    def test_html_email_contains_plain_and_html_alternatives(self):
        smtp = Mock(spec=smtplib.SMTP)
        smtp.send_message.return_value = {}
        settings = dict(smtp_host="smtp.gmail.com", smtp_port=465, smtp_security="ssl",
                        smtp_username="sender@example.com", smtp_from="sender@example.com", recipient="recipient@example.com")
        with patch.dict(os.environ, {"SMTP_PASSWORD": "test-only-secret"}), patch("stock_alert.mailer.smtplib.SMTP_SSL", return_value=smtp):
            send_mail(settings, "목록", "일반 텍스트 목록", html_body="<table><tr><td>목표가 도달</td></tr></table>")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertIn("일반 텍스트 목록", message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("<table>", message.get_body(preferencelist=("html",)).get_content())
        self.assertNotIn("test-only-secret", message.as_string())


if __name__ == "__main__":
    unittest.main()
