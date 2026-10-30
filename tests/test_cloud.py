"""Cloud configuration identity, durable deduplication, and secret isolation."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stock_alert import cloud
from stock_alert.mailer import MailError
from stock_alert.service import DEFAULTS


def document(*rows):
    return {"version": 1, "rules": list(rows)}


def rule(identifier="samsung", **changes):
    return {"id": identifier, "market": "KR", "ticker": "005930",
            "target_price": "100", "upper_pct": "5", "lower_pct": "5", **changes}


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = set()

    def fetch_close(self, market, ticker):
        self.calls.append((market, ticker))
        if ticker in self.fail:
            raise RuntimeError("시세 조회 실패")
        return SimpleNamespace(close=Decimal("100"), trade_date=date(2026, 10, 29),
                               name="삼성전자" if market == "KR" else "Apple",
                               currency="KRW" if market == "KR" else "USD", source_url="https://example.invalid/quote")


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.db_path = self.directory / "alerts.db"
        self.config_path = self.directory / "watchlist.json"
        self.provider = Provider()
        self.sent = []
        self.service = cloud.CloudService(self.db_path, self.provider, self.mail)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()

    def mail(self, settings, subject, body):
        self.sent.append((settings, subject, body))
        return "<fake@invalid>"

    def write_config(self, config):
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def cli(self, *args):
        output = io.StringIO()
        with patch.object(cloud, "CloudService", return_value=self.service), redirect_stdout(output):
            code = cloud.main(["--config", str(self.config_path), "--data-dir", str(self.directory), *args])
        return code, json.loads(output.getvalue())

    def test_full_validation_precedes_mutation(self):
        cloud.sync_rules(self.service, document(rule()))
        before = self.service.rules()
        with self.assertRaises(ValueError):
            cloud.sync_rules(self.service, document(rule(target_price="200"), rule("invalid", ticker="bad")))
        self.assertEqual(self.service.rules(), before)

    def test_strict_schema_rejects_ambiguous_configuration(self):
        bad_documents = [
            {"version": True, "rules": []},
            {"version": 1, "rules": [], "smtp_password": "do-not-store"},
            document(rule(), rule()),
            document(rule("contains spaces")),
            document(rule(extra=True)),
            document(rule(enabled="false")),
            document({"id": "missing"}),
            document(rule(name=None)),
            document(*[rule(str(index)) for index in range(101)]),
        ]
        for config in bad_documents:
            with self.subTest(config=config), self.assertRaises(ValueError):
                cloud.validate_config(config)

    def test_daily_default_and_number_formatting_are_canonical(self):
        validated = cloud.validate_config(document(rule(target_price="100.000", upper_pct=5)))
        normalized = validated["rules"][0]
        self.assertEqual(normalized["mode"], "daily")
        self.assertEqual(normalized["target_price"], "100")
        self.assertIs(normalized["enabled"], True)

    def test_reorder_name_and_format_edits_keep_deduplication_across_restart(self):
        config = document(rule(), rule("apple", market="US", ticker="AAPL"))
        cloud.sync_rules(self.service, config)
        self.assertEqual(self.service.check()["sent"], 2)
        original = {row["ticker"]: (row["id"], row["revision"]) for row in self.service.rules()}
        restarted = cloud.CloudService(self.db_path, self.provider, self.mail)
        cloud.sync_rules(restarted, document(rule("apple", market="US", ticker="AAPL", name="애플"),
                                              rule(name="삼성전자", target_price="100.000")))
        self.assertEqual({row["ticker"]: (row["id"], row["revision"]) for row in restarted.rules()}, original)
        self.assertEqual(restarted.check()["sent"], 0)

    def test_condition_edit_resets_revision_and_permits_new_alert(self):
        cloud.sync_rules(self.service, document(rule()))
        self.assertEqual(self.service.check()["sent"], 1)
        original = self.service.rules()[0]
        cloud.sync_rules(self.service, document(rule(target_price="101")))
        changed = self.service.rules()[0]
        self.assertEqual(changed["id"], original["id"])
        self.assertEqual(changed["revision"], original["revision"] + 1)
        self.assertEqual(self.service.check()["sent"], 1)

    def test_removed_then_readded_id_has_new_identity(self):
        cloud.sync_rules(self.service, document(rule()))
        self.service.check()
        old_id = self.service.rules()[0]["id"]
        cloud.sync_rules(self.service, document())
        self.assertEqual(self.service.rules(), [])
        cloud.sync_rules(self.service, document(rule()))
        self.assertGreater(self.service.rules()[0]["id"], old_id)
        self.assertEqual(self.service.check()["sent"], 1)

    def test_configuration_is_authoritative_including_unmanaged_rules(self):
        self.service.save_rule({"market": "US", "ticker": "AAPL", "target_price": "100"})
        cloud.sync_rules(self.service, document(rule()))
        self.assertEqual([row["ticker"] for row in self.service.rules()], ["005930"])

    def test_replacing_full_watchlist_remains_within_limit(self):
        cloud.sync_rules(self.service, document(*[rule("old-" + str(index)) for index in range(100)]))
        cloud.sync_rules(self.service, document(*[rule("new-" + str(index)) for index in range(100)]))
        self.assertEqual(len(self.service.rules()), 100)

    def test_partial_failure_keeps_successful_rule_deduplication(self):
        cloud.sync_rules(self.service, document(rule(), rule("apple", market="US", ticker="AAPL")))
        self.provider.fail.add("AAPL")
        outcome = self.service.check()
        self.assertEqual((outcome["sent"], outcome["errors"]), (1, 1))
        restarted = cloud.CloudService(self.db_path, self.provider, self.mail)
        self.assertEqual(restarted.check()["sent"], 0)
        self.assertEqual(len(self.sent), 1)

    def test_cloud_uses_fixed_routing_and_environment_only(self):
        with self.service.db() as db:
            db.execute("UPDATE settings SET value=? WHERE key='smtp_host'", (json.dumps("different.invalid"),))
            db.execute("UPDATE settings SET value=? WHERE key='recipient'", (json.dumps("different@invalid.test"),))
        self.service.password_store.path.write_text("not-a-valid-local-credential", encoding="utf-8")
        os.environ["SMTP_PASSWORD"] = " abcd efgh ijkl mnop "
        restarted = cloud.CloudService(self.db_path, self.provider, self.mail)
        self.assertEqual(restarted.settings(), DEFAULTS)
        self.assertEqual(restarted._mail_settings()["smtp_password"], "abcdefghijklmnop")
        with restarted.db() as db:
            self.assertNotIn("abcdefghijklmnop", " ".join(row["value"] for row in db.execute("SELECT value FROM settings")))

    def test_missing_secret_fails_before_any_quote_or_email(self):
        self.write_config(document(rule()))
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("SMTP_PASSWORD", result["error"])
        self.assertEqual((self.provider.calls, self.sent), ([], []))

    def test_empty_and_disabled_watchlists_need_no_secret(self):
        for config in (document(), document(rule(enabled=False))):
            self.write_config(config)
            code, result = self.cli()
            self.assertEqual((code, result["sent"], result["errors"]), (0, 0, 0))
        self.assertEqual(self.provider.calls, [])

    def test_check_only_needs_no_secret_and_does_not_consume_alert(self):
        self.write_config(document(rule()))
        code, result = self.cli("--check-only")
        self.assertEqual(code, 0)
        self.assertEqual(result["results"][0]["status"], "preview")
        self.assertIsNone(self.service.rules()[0]["evaluated_date"])
        self.assertEqual(self.sent, [])
        os.environ["SMTP_PASSWORD"] = "fake-test-secret"
        self.assertEqual(self.cli()[1]["sent"], 1)

    def test_test_email_does_not_require_config(self):
        os.environ["SMTP_PASSWORD"] = "fake-test-secret"
        code, result = self.cli("--test-email")
        self.assertEqual(code, 0)
        self.assertIn("message", result)
        self.assertEqual(len(self.sent), 1)
        settings = self.sent[0][0]
        self.assertEqual((settings["smtp_from"], settings["recipient"]), ("royhan88888@gmail.com", "ykhan@dacpole.com"))

    def test_lookup_does_not_create_database_or_read_config(self):
        output = io.StringIO()
        expected = {"market": "US", "ticker": "AAPL", "name": "애플"}
        unused = self.directory / "unused-data"
        with patch.object(cloud, "NaverProvider") as provider, redirect_stdout(output):
            provider.return_value.lookup.return_value = expected
            code = cloud.main(["--config", "missing.json", "--data-dir", str(unused), "--lookup", "US", "AAPL"])
        self.assertEqual((code, json.loads(output.getvalue())), (0, expected))
        self.assertFalse(unused.exists())

    def test_malformed_config_fails_without_creating_runner_state(self):
        self.config_path.write_text("{ broken", encoding="utf-8")
        untouched = self.directory / "untouched"
        output = io.StringIO()
        with redirect_stdout(output):
            code = cloud.main(["--config", str(self.config_path), "--data-dir", str(untouched)])
        self.assertEqual(code, 1)
        self.assertFalse(untouched.exists())

    def test_pending_state_is_committed_and_persisted_before_smtp(self):
        events = []

        def before_send():
            # An independent connection sees the committed reservation.
            with closing(sqlite3.connect(self.db_path)) as db:
                self.assertEqual(db.execute("SELECT status FROM alerts").fetchall(), [("pending",)])
            events.append("persisted")

        def transport(settings, subject, body):
            self.assertEqual(events, ["persisted"])
            events.append("smtp")
            return "<accepted@invalid>"

        service = cloud.CloudService(self.db_path, self.provider, transport, before_send=before_send)
        cloud.sync_rules(service, document(rule()))
        self.assertEqual(service.check()["sent"], 1)
        self.assertEqual(events, ["persisted", "smtp"])
        with service.db() as db:
            self.assertEqual(db.execute("SELECT status FROM alerts").fetchone()[0], "sent")

    def test_failed_pre_send_persistence_never_calls_smtp(self):
        def failed_persist():
            raise RuntimeError("remote failure may contain sensitive diagnostics")

        service = cloud.CloudService(self.db_path, self.provider, self.mail, before_send=failed_persist)
        cloud.sync_rules(service, document(rule()))
        result = service.check()
        self.assertEqual((result["sent"], result["errors"]), (0, 1))
        self.assertEqual(self.sent, [])
        self.assertIn("발송 전 상태 저장 실패", result["results"][0]["message"])
        self.assertNotIn("sensitive diagnostics", result["results"][0]["message"])
        with service.db() as db:
            self.assertEqual(db.execute("SELECT status FROM alerts").fetchone()[0], "failed")

    def test_persist_state_cli_wires_database_and_marker_before_transport(self):
        self.write_config(document(rule()))
        os.environ["SMTP_PASSWORD"] = "fake-test-secret"
        factory = cloud.CloudService
        events = []

        def make_service(path, **kwargs):
            return factory(path, self.provider, lambda *args: events.append("smtp"), **kwargs)

        def save(database, marker):
            self.assertEqual(database, self.db_path)
            self.assertEqual(marker, self.directory / "github-state.json")
            events.append("persist")

        output = io.StringIO()
        with patch.object(cloud, "CloudService", side_effect=make_service), patch.object(cloud, "persist_state", side_effect=save), redirect_stdout(output):
            code = cloud.main(["--config", str(self.config_path), "--data-dir", str(self.directory), "--persist-state"])
        self.assertEqual(code, 0)
        self.assertEqual(events, ["persist", "smtp"])
        self.assertEqual(json.loads(output.getvalue())["sent"], 1)

    def test_persist_state_rejects_non_sending_actions(self):
        for action in (["--check-only"], ["--test-email"], ["--lookup", "KR", "005930"]):
            with self.subTest(action=action), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as exc:
                cloud.main(["--persist-state", *action])
            self.assertEqual(exc.exception.code, 2)

    def test_remote_pending_reservation_blocks_retry_after_interruption(self):
        def hard_stop():
            raise KeyboardInterrupt()

        service = cloud.CloudService(self.db_path, self.provider, self.mail, before_send=hard_stop)
        cloud.sync_rules(service, document(rule()))
        with self.assertRaises(KeyboardInterrupt):
            service.check()
        restarted = cloud.CloudService(self.db_path, self.provider, self.mail)
        result = restarted.check()
        self.assertEqual((result["sent"], result["errors"]), (0, 1))
        self.assertEqual(self.sent, [])
        self.assertIn("불명확", result["results"][0]["message"])


if __name__ == "__main__":
    unittest.main()
