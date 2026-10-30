"""Business behavior checks: real SQLite, fake market data, no outgoing email."""
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_alert.mailer import MailError
from stock_alert.service import Service, in_range, validate_rule
from stock_alert.credentials import CredentialError


class FakeProvider:
    def __init__(self):
        self.price = Decimal("100")
        self.day = date(2026, 10, 26)
        self.calls = []

    def fetch_close(self, market, ticker):
        self.calls.append((market, ticker))
        return SimpleNamespace(
            symbol=ticker, name="Test stock", market=market,
            currency="USD" if market == "US" else "KRW",
            close=self.price, trade_date=self.day,
            source_url="https://stock.naver.com/test",
        )

    def quote(self, price, day):
        self.price = Decimal(str(price))
        self.day = date.fromisoformat(day)


class FakeMailer:
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, settings, subject, body):
        self.calls.append((settings, subject, body))
        if self.error:
            raise self.error
        return f"<message-{len(self.calls)}@test.invalid>"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "alerts.sqlite3"
        self.provider = FakeProvider()
        self.mailer = FakeMailer()
        self.service = Service(self.db_path, self.provider, self.mailer)

    def add_rule(self, **changes):
        payload = dict(market="KR", ticker="005930", name="Example",
                       target_price="100", upper_pct="5", lower_pct="5",
                       mode="entry", enabled=True)
        payload.update(changes)
        return self.service.save_rule(payload)

    def test_bounds_are_inclusive_and_use_decimal_precision(self):
        rule = self.add_rule(target_price="100.01", lower_pct="5", upper_pct="3")
        lower = Decimal("95.0095")
        upper = Decimal("103.0103")
        self.assertTrue(in_range(rule, lower))
        self.assertTrue(in_range(rule, upper))
        self.assertFalse(in_range(rule, lower - Decimal("0.000001")))
        self.assertFalse(in_range(rule, upper + Decimal("0.000001")))

    def test_asymmetric_range_is_respected(self):
        rule = self.add_rule(lower_pct="2", upper_pct="8")
        self.assertFalse(in_range(rule, Decimal("97.99")))
        self.assertTrue(in_range(rule, Decimal("98")))
        self.assertTrue(in_range(rule, Decimal("108")))
        self.assertFalse(in_range(rule, Decimal("108.01")))

    def test_entry_sends_once_then_rearms_after_a_completed_outside_day(self):
        self.add_rule()
        self.provider.quote("95", "2026-10-26")
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(self.service.check()["sent"], 0)
        self.provider.quote("101", "2026-10-27")
        self.assertEqual(self.service.check()["sent"], 0)
        self.provider.quote("106", "2026-10-28")
        self.assertEqual(self.service.check()["results"][0]["status"], "outside")
        self.provider.quote("105", "2026-10-29")
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(len(self.mailer.calls), 2)

    def test_first_observation_outside_sends_nothing(self):
        self.add_rule()
        self.provider.price = Decimal("94.999999")
        outcome = self.service.check()
        self.assertEqual(outcome["sent"], 0)
        self.assertEqual(outcome["errors"], 0)
        self.assertEqual(self.mailer.calls, [])
        self.assertFalse(self.service.rules()[0]["in_range"])

    def test_daily_mode_sends_for_each_new_trade_date_only(self):
        self.add_rule(mode="daily")
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(self.service.check()["sent"], 0)
        self.provider.quote("102", "2026-10-27")
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(self.service.check()["sent"], 0)
        self.assertEqual(len(self.mailer.calls), 2)

    def test_preview_does_not_consume_initial_entry_alert(self):
        self.add_rule()
        outcome = self.service.check(send=False)
        self.assertEqual(outcome["results"][0]["status"], "preview")
        self.assertEqual(self.mailer.calls, [])
        self.assertEqual(self.service.snapshot()["recent_alerts"], [])
        stored = self.service.rules()[0]
        self.assertIsNone(stored["evaluated_date"])
        self.assertIsNone(stored["was_in_range"])
        self.assertEqual(stored["last_price"], "100")
        self.assertEqual(self.service.check(send=True)["sent"], 1)

    def test_preview_outside_does_not_rearm_an_existing_entry(self):
        self.add_rule()
        self.assertEqual(self.service.check()["sent"], 1)
        self.provider.quote("110", "2026-10-27")
        self.service.check(send=False)
        self.provider.quote("100", "2026-10-28")
        self.assertEqual(self.service.check()["sent"], 0)
        self.assertEqual(len(self.mailer.calls), 1)

    def test_definite_mail_failure_is_retried_and_then_deduplicated(self):
        self.add_rule()
        self.mailer.error = MailError("Authentication rejected")
        outcome = self.service.check()
        self.assertEqual((outcome["sent"], outcome["errors"]), (0, 1))
        self.assertEqual(self.service.snapshot()["recent_alerts"][0]["status"], "failed")
        self.assertIsNone(self.service.rules()[0]["evaluated_date"])
        self.mailer.error = None
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(self.service.check()["sent"], 0)
        self.assertEqual(len(self.mailer.calls), 2)
        self.assertEqual(self.service.snapshot()["recent_alerts"][0]["status"], "sent")

    def test_uncertain_delivery_blocks_automatic_resend(self):
        self.add_rule()
        self.mailer.error = MailError("Connection lost after DATA", uncertain=True)
        self.assertEqual(self.service.check()["errors"], 1)
        self.assertEqual(self.service.snapshot()["recent_alerts"][0]["status"], "uncertain")
        self.mailer.error = None
        self.assertEqual(self.service.check()["errors"], 1)
        self.assertEqual(len(self.mailer.calls), 1)

    def test_pending_delivery_survives_restart_and_blocks_resend(self):
        rule = self.add_rule()
        with self.service.db() as db:
            db.execute("INSERT INTO alerts (rule_id,revision,ticker,trade_date,price,status,created_at) VALUES (?,?,?,?,?,?,?)",
                       (rule["id"], rule["revision"], rule["ticker"], self.provider.day.isoformat(), "100", "pending", "2026-10-26T00:00:00Z"))
        restarted = Service(self.db_path, self.provider, self.mailer)
        self.assertEqual(restarted.check()["errors"], 1)
        self.assertEqual(self.mailer.calls, [])

    def test_unexpected_mailer_failure_is_treated_as_uncertain(self):
        self.add_rule()
        self.mailer.error = RuntimeError("Unhandled SMTP issue")
        self.assertEqual(self.service.check()["errors"], 1)
        self.assertEqual(self.service.snapshot()["recent_alerts"][0]["status"], "uncertain")
        self.mailer.error = None
        self.service.check()
        self.assertEqual(len(self.mailer.calls), 1)

    def test_successful_delivery_deduplication_survives_restart(self):
        self.add_rule(mode="daily")
        self.assertEqual(self.service.check()["sent"], 1)
        restarted = Service(self.db_path, self.provider, self.mailer)
        self.assertEqual(restarted.check()["sent"], 0)
        self.assertEqual(len(self.mailer.calls), 1)

    def test_old_quotes_do_not_rearm_entry_state(self):
        self.add_rule()
        self.provider.quote("100", "2026-10-27")
        self.service.check()
        self.provider.quote("110", "2026-10-26")
        self.assertEqual(self.service.check()["results"][0]["status"], "skipped")
        self.assertEqual(self.service.rules()[0]["was_in_range"], 1)
        self.provider.quote("100", "2026-10-28")
        self.assertEqual(self.service.check()["sent"], 0)

    def test_one_provider_fetch_per_symbol_even_with_multiple_rules(self):
        self.add_rule()
        self.add_rule(target_price="90")
        self.service.check(send=False)
        self.assertEqual(self.provider.calls, [("KR", "005930")])

    def test_paused_rules_do_not_fetch_or_send(self):
        self.add_rule(enabled=False)
        outcome = self.service.check()
        self.assertEqual(outcome, dict(results=[], sent=0, errors=0))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.mailer.calls, [])

    def test_deleted_rule_identifier_is_never_reused_by_new_rule(self):
        first = self.add_rule()
        last = self.add_rule(ticker="000660")
        self.service.check()
        self.service.delete_rule(last["id"])
        replacement = self.add_rule(ticker="035420")
        self.assertGreater(replacement["id"], last["id"])
        self.assertNotEqual(replacement["id"], first["id"])
        self.assertEqual(self.service.check()["sent"], 1)

    def test_partial_update_preserves_enabled_setting(self):
        rule = self.add_rule(enabled=False)
        updated = self.service.save_rule({"name": "Renamed"}, rule["id"])
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["revision"], rule["revision"])
        resumed = self.service.save_rule({"enabled": True}, rule["id"])
        self.assertTrue(resumed["enabled"])
        renamed = self.service.save_rule({"name": "Still enabled"}, rule["id"])
        self.assertTrue(renamed["enabled"])

    def test_rule_validation_rejects_nonfinite_and_invalid_values(self):
        valid = dict(market="KR", ticker="005930", target_price="100", enabled=True)
        for field, value in (("target_price", "NaN"), ("target_price", "Infinity"),
                             ("target_price", "0"), ("target_price", "0.0000001"),
                             ("lower_pct", "100.01"), ("upper_pct", "NaN"),
                             ("mode", "hourly"), ("ticker", "5930"),
                             ("enabled", "false"), ("name", "bad\nname")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    validate_rule({**valid, field: value})

    def test_settings_validation_rejects_invalid_values_atomically(self):
        initial = self.service.settings()
        for change in ({"recipient": "a@example.com\r\nBcc:b@example.com"},
                       {"recipient": "not-email"}, {"smtp_security": "none"},
                       {"smtp_host": "https://smtp.gmail.com"}, {"smtp_port": "NaN"},
                       {"smtp_port": 0}, {"smtp_port": 65536},
                       {"interval_minutes": 4}, {"interval_minutes": 1441},
                       {"interval_minutes": "5.5"}, {"unknown_setting": "invalid"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.service.update_settings(change)
                self.assertEqual(self.service.settings(), initial)

    def test_public_settings_expose_only_password_presence(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": "private-app-password"}):
            exposed = self.service.settings(public=True)
            self.assertTrue(exposed["smtp_password_configured"])
            self.assertNotIn("private-app-password", repr(self.service.snapshot()))
            self.assertNotIn("smtp_password", exposed)
        with patch.dict(os.environ, {"SMTP_PASSWORD": ""}):
            self.assertFalse(self.service.settings(public=True)["smtp_password_configured"])

    def test_web_password_is_persistent_private_and_used_without_restart(self):
        password = "test-only-web-app-pass"
        result = self.service.update_settings({"smtp_password": password})
        self.assertTrue(result["smtp_password_configured"])
        self.assertNotIn(password, repr(result))
        self.assertNotIn("smtp_password", self.service.settings())
        self.assertNotIn(password, repr(self.service.snapshot()))
        with self.service.db() as db:
            self.assertNotIn(password, repr([tuple(row) for row in db.execute("SELECT * FROM settings")]))
        self.service.test_email()
        self.assertEqual(self.mailer.calls[-1][0]["smtp_password"], password)
        self.assertNotIn(password, repr(self.mailer.calls[-1][1:]))
        restarted = Service(self.db_path, self.provider, self.mailer)
        self.assertTrue(restarted.settings(public=True)["smtp_password_configured"])
        restarted.test_email()
        self.assertEqual(self.mailer.calls[-1][0]["smtp_password"], password)

    def test_blank_web_password_preserves_saved_value_and_gmail_spaces_are_removed(self):
        self.service.update_settings({"smtp_password": "abcd efgh ijkl mnop"})
        self.service.update_settings({"interval_minutes": 45, "smtp_password": ""})
        self.service.test_email()
        self.assertEqual(self.mailer.calls[-1][0]["smtp_password"], "abcdefghijklmnop")
        self.assertEqual(self.service.settings()["interval_minutes"], 45)

    def test_web_password_wins_over_environment_and_delete_disables_legacy_password(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": "legacy-test-only"}):
            self.service.update_settings({"smtp_password": "web-test-only"})
            self.service.test_email()
            self.assertEqual(self.mailer.calls[-1][0]["smtp_password"], "web-test-only")
            result = self.service.delete_password()
            self.assertFalse(result["smtp_password_configured"])
            restarted = Service(self.db_path, self.provider, self.mailer)
            self.assertEqual(restarted._mail_settings()["smtp_password"], "")
            self.assertFalse(restarted.settings(public=True)["smtp_password_configured"])

    def test_changed_account_or_host_cannot_reuse_previous_password(self):
        with patch.dict(os.environ, {"SMTP_PASSWORD": "legacy-test-only"}):
            self.service.update_settings({"smtp_password": "original-test-only"})
            result = self.service.update_settings({"smtp_username": "another@example.com"})
            self.assertFalse(result["smtp_password_configured"])
            self.assertEqual(self.service._mail_settings()["smtp_password"], "")
            self.service.update_settings({"smtp_password": "replacement-test-only"})
            result = self.service.update_settings({"smtp_host": "smtp.example.com"})
            self.assertFalse(result["smtp_password_configured"])
            result = self.service.update_settings({"smtp_password": "other-server-test-only"})
            self.assertTrue(result["smtp_password_configured"])
            self.assertEqual(self.service._mail_settings()["smtp_password"], "other-server-test-only")

    def test_password_storage_failure_rolls_back_settings_and_keeps_previous_secret(self):
        self.service.update_settings({"smtp_password": "original-test-only"})
        initial = self.service.settings()
        with patch.object(self.service.password_store, "set", side_effect=CredentialError("보관 오류")):
            with self.assertRaises(CredentialError):
                self.service.update_settings({"smtp_password": "replacement-test-only", "interval_minutes": 45})
        self.assertEqual(self.service.settings(), initial)
        self.assertEqual(self.service._mail_settings()["smtp_password"], "original-test-only")

    def test_bad_password_inputs_are_rejected_without_changing_settings(self):
        initial = self.service.settings()
        for value in (None, [], True, "x\ny", "x\ry", "x\x00y", "x" * 257):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.service.update_settings({"smtp_password": value, "interval_minutes": 45})
                self.assertEqual(self.service.settings(), initial)
                self.assertFalse(self.service.password_store.path.exists())

    def test_unreadable_credential_can_be_replaced_and_retry_is_safe(self):
        self.add_rule()
        self.service.password_store.path.write_text("invalid credential", encoding="utf-8")
        public = self.service.settings(public=True)
        self.assertFalse(public["smtp_password_configured"])
        self.assertNotIn("invalid credential", repr(public))
        self.assertEqual(self.service.check()["errors"], 1)
        self.assertEqual(self.service.snapshot()["recent_alerts"][0]["status"], "failed")
        self.assertEqual(self.mailer.calls, [])
        self.service.update_settings({"smtp_password": "replacement-test-only"})
        self.assertEqual(self.service.check()["sent"], 1)

    def test_scheduled_check_uses_saved_web_password(self):
        self.add_rule()
        self.service.update_settings({"smtp_password": "scheduled-test-only"})
        self.assertEqual(self.service.check()["sent"], 1)
        self.assertEqual(self.mailer.calls[-1][0]["smtp_password"], "scheduled-test-only")


if __name__ == "__main__":
    unittest.main()
