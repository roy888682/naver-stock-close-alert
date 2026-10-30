"""Web-form edits preserve rule identity and fail before overwriting settings."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from stock_alert import watchlist_form as form


def document(*rows):
    return {"version": 1, "rules": list(rows)}


def rule(identifier="samsung", **changes):
    return {"id": identifier, "market": "KR", "ticker": "005930", "name": "삼성전자",
            "target_price": "100", "upper_pct": "5", "lower_pct": "5", "mode": "daily", "enabled": True, **changes}


def inputs(**changes):
    return {"operation": "save", "market": "KR", "ticker": "005930", "target_price": "100",
            "upper_pct": "5", "lower_pct": "5", "mode": "daily", "rule_id": "", **changes}


class Provider:
    def __init__(self, name="삼성전자"):
        self.name = name
        self.calls = []
        self.error = None

    def lookup(self, market, ticker):
        self.calls.append((market, ticker))
        if self.error:
            raise self.error
        return {"market": market, "ticker": ticker, "name": self.name}


class FormTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()

    def test_save_looks_up_name_preserves_korean_leading_zeroes_and_calculates_range(self):
        updated, result = form.apply_inputs(document(), inputs(target_price="70000", upper_pct="3", lower_pct="2"), self.provider)
        saved = updated["rules"][0]
        self.assertEqual(self.provider.calls, [("KR", "005930")])
        self.assertEqual((saved["ticker"], saved["name"], saved["enabled"]), ("005930", "삼성전자", True))
        self.assertRegex(saved["id"], r"^rule-[0-9a-f]{32}$")
        self.assertTrue(result["changed"])
        summary = form.render_summary(updated, result)
        self.assertIn("68600.00 ~ 72100.00", summary)
        self.assertIn("GitHub 저장 단계까지 성공해야", summary)

    def test_existing_ticker_updates_same_stable_id_and_keeps_pause_state(self):
        original = document(rule(enabled=False))
        updated, result = form.apply_inputs(original, inputs(target_price="110"), self.provider)
        self.assertEqual(len(updated["rules"]), 1)
        self.assertEqual(updated["rules"][0]["id"], "samsung")
        self.assertFalse(updated["rules"][0]["enabled"])
        self.assertEqual(original, document(rule(enabled=False)))
        self.assertTrue(result["changed"])

    def test_numeric_formatting_only_is_a_no_op(self):
        updated, result = form.apply_inputs(document(rule()), inputs(target_price="100.00", upper_pct="5.000000"), self.provider)
        self.assertFalse(result["changed"])
        self.assertEqual(updated, document(rule()))

    def test_blank_percentage_defaults_and_us_ticker_normalization(self):
        self.provider.name = "애플"
        updated, _ = form.apply_inputs(document(), inputs(market="US", ticker=" aapl ", target_price="200.50", upper_pct="", lower_pct="", mode=""), self.provider)
        saved = updated["rules"][0]
        self.assertEqual((saved["ticker"], saved["target_price"], saved["upper_pct"], saved["lower_pct"], saved["mode"]), ("AAPL", "200.5", "5", "5", "daily"))

    def test_pause_resume_delete_need_no_price_or_lookup(self):
        current = document(rule())
        for operation, expected in (("pause", False), ("pause", False), ("resume", True)):
            updated, result = form.apply_inputs(current, inputs(operation=operation, target_price="invalid", upper_pct="ignored"), self.provider)
            self.assertEqual(updated["rules"][0]["enabled"], expected)
            self.assertEqual(updated["rules"][0]["id"], "samsung")
            self.assertEqual(result["changed"], current["rules"][0]["enabled"] != expected)
            current = updated
        updated, _ = form.apply_inputs(current, inputs(operation="delete", target_price=""), self.provider)
        self.assertEqual(updated, document())
        self.assertEqual(self.provider.calls, [])

    def test_list_ignores_form_defaults_and_never_calls_provider(self):
        updated, result = form.apply_inputs(document(rule()), inputs(operation="list", ticker="", target_price=""), self.provider)
        self.assertEqual(updated, document(rule()))
        self.assertFalse(result["changed"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(form.apply_inputs(document(), {}, self.provider)[1]["operation"], "list")

    def test_ambiguous_ticker_needs_existing_id_and_explicit_id_allows_ticker_edit(self):
        current = document(rule("a"), rule("b", target_price="200"))
        with self.assertRaisesRegex(ValueError, "여러 개"):
            form.apply_inputs(current, inputs(), self.provider)
        self.provider.name = "애플"
        updated, _ = form.apply_inputs(current, inputs(rule_id="b", market="US", ticker="AAPL", target_price="300"), self.provider)
        self.assertEqual(updated["rules"][0], rule("a"))
        self.assertEqual((updated["rules"][1]["id"], updated["rules"][1]["ticker"]), ("b", "AAPL"))
        deleted, _ = form.apply_inputs(updated, inputs(operation="delete", rule_id="a", ticker=""), self.provider)
        self.assertEqual([row["id"] for row in deleted["rules"]], ["b"])

    def test_unknown_id_or_unregistered_pause_never_creates_rule(self):
        for values in (inputs(rule_id="missing"), inputs(operation="pause"), inputs(operation="resume"), inputs(operation="delete")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                form.apply_inputs(document(), values, self.provider)
        self.assertEqual(self.provider.calls, [])

    def test_deleted_rule_gets_fresh_identity_when_registered_again(self):
        saved, _ = form.apply_inputs(document(), inputs(), self.provider)
        deleted, _ = form.apply_inputs(saved, inputs(operation="delete"), self.provider)
        readded, _ = form.apply_inputs(deleted, inputs(), self.provider)
        self.assertNotEqual(saved["rules"][0]["id"], readded["rules"][0]["id"])

    def test_all_validation_happens_before_network_and_original_is_untouched(self):
        invalid = [inputs(target_price="0"), inputs(target_price="NaN"), inputs(ticker="5930"),
                   inputs(ticker="$(touch secret)"), inputs(upper_pct="-1"), inputs(lower_pct="101"),
                   inputs(mode="unknown"), inputs(operation="other"), inputs(rule_id=True),
                   inputs(extra="value"), inputs(target_price=100), inputs(ticker="a" * 257)]
        original = document(rule())
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                form.apply_inputs(original, values, self.provider)
        with self.assertRaises(ValueError):
            form.apply_inputs(document(rule(), rule()), inputs(), self.provider)
        self.assertEqual(original, document(rule()))
        self.assertEqual(self.provider.calls, [])

    def test_hundred_rule_limit_prevents_network_but_still_allows_edit(self):
        current = document(*[rule(str(i), ticker=f"{i:06}") for i in range(100)])
        with self.assertRaisesRegex(ValueError, "100"):
            form.apply_inputs(current, inputs(ticker="005930"), self.provider)
        self.assertEqual(self.provider.calls, [])
        updated, _ = form.apply_inputs(current, inputs(rule_id="0"), self.provider)
        self.assertEqual(len(updated["rules"]), 100)

    def test_unusable_provider_name_fails_without_changing_document(self):
        self.provider.name = ""
        original = document(rule())
        with self.assertRaises(ValueError):
            form.apply_inputs(original, inputs(target_price="110"), self.provider)
        self.assertEqual(original, document(rule()))

    def test_summary_escapes_market_text_instead_of_executing_markup(self):
        name = '<script>alert(1)</script>|![link](https://bad.invalid)'
        summary = form.render_summary(document(rule(name=name)))
        self.assertNotIn("<script>", summary)
        self.assertNotIn("![link]", summary)
        self.assertIn("&lt;script&gt;", summary)
        self.assertIn("&#124;", summary)
        self.assertEqual(summary.count("| "), form.render_summary(document(rule())).count("| "))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config_path = self.directory / "watchlist.json"
        self.event_path = self.directory / "event.json"
        self.summary_path = self.directory / "summary.md"
        self.original = json.dumps(document(rule()), indent=1)
        self.config_path.write_text(self.original, encoding="utf-8")
        self.provider = Provider()

    def cli(self, event):
        self.event_path.write_text(json.dumps(event), encoding="utf-8")
        output = io.StringIO()
        with patch.object(form, "NaverProvider", return_value=self.provider), redirect_stdout(output):
            code = form.main(["--event", str(self.event_path), "--config", str(self.config_path), "--summary", str(self.summary_path)])
        return code, json.loads(output.getvalue())

    def test_valid_edit_writes_config_and_prepared_summary(self):
        code, result = self.cli({"inputs": inputs(target_price="110")})
        self.assertEqual((code, result["changed"]), (0, True))
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["rules"][0]["target_price"], "110")
        self.assertIn("GitHub 저장 단계까지 성공해야", self.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(list(self.directory.glob(".watchlist-*.tmp")), [])

    def test_list_push_and_semantic_no_op_preserve_exact_file(self):
        for event in ({"inputs": inputs(operation="list")}, {"ref": "refs/heads/main"}, {"inputs": inputs(target_price="100.00")}):
            code, result = self.cli(event)
            self.assertEqual((code, result["changed"]), (0, False))
            self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.original)

    def test_invalid_input_and_provider_failure_leave_config_unchanged_and_summary_explains(self):
        code, result = self.cli({"inputs": inputs(lower_pct="101")})
        self.assertEqual(code, 1)
        self.assertIn("error", result)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.original)
        self.assertIn("저장하지 않았습니다", self.summary_path.read_text(encoding="utf-8"))
        self.provider.error = RuntimeError("종목 조회 실패")
        code, _ = self.cli({"inputs": inputs(target_price="110")})
        self.assertEqual(code, 1)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.original)

    def test_failed_atomic_replace_keeps_previous_file_and_removes_temporary(self):
        with patch.object(form.os, "replace", side_effect=OSError("disk failure")):
            code, _ = self.cli({"inputs": inputs(target_price="110")})
        self.assertEqual(code, 1)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.original)
        self.assertEqual(list(self.directory.glob(".watchlist-*.tmp")), [])

    def test_malformed_event_shape_fails_before_network(self):
        for event in ([], {"inputs": None}, {"inputs": []}):
            with self.subTest(event=event):
                code, _ = self.cli(event)
                self.assertEqual(code, 1)
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
