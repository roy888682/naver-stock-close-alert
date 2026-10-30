"""Exercise the real HTTP API on an ephemeral loopback port; no network mail."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urljoin, urlsplit

from stock_alert.server import make_server
from stock_alert.service import Service


class FakeProvider:
    def __init__(self):
        self.calls = []

    def fetch_close(self, market, ticker):
        self.calls.append((market, ticker))
        return SimpleNamespace(symbol=ticker, name="Test stock", market=market,
                               currency="USD" if market == "US" else "KRW",
                               close=Decimal("100"), trade_date=date(2026, 10, 29),
                               source_url="https://stock.naver.com/test")


class FakeMailer:
    def __init__(self):
        self.calls = []

    def __call__(self, settings, subject, body):
        self.calls.append((settings, subject, body))
        return f"<http-test-{len(self.calls)}@test.invalid>"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = FakeProvider()
        self.mailer = FakeMailer()
        self.service = Service(Path(self.directory.name) / "alerts.db",
                               self.provider, self.mailer)
        self.server = make_server(self.service, port=0)
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.worker = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def request(self, method, path, data=None, *, headers=None, secure=True, raw=None):
        request_headers = {}
        if method != "GET":
            request_headers["Content-Type"] = "application/json"
            if secure:
                request_headers["X-Stock-Alert"] = "1"
                request_headers["Origin"] = self.origin
        request_headers.update(headers or {})
        body = raw if raw is not None else (json.dumps(data or {}).encode() if method != "GET" else None)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            response_headers = dict(response.getheaders())
            if response_headers.get("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return response.status, payload, response_headers
        finally:
            connection.close()

    def create_rule(self, **changes):
        data = dict(market="KR", ticker="005930", name="삼성전자",
                    target_price="100", upper_pct="5", lower_pct="5",
                    mode="entry", enabled=True)
        data.update(changes)
        status, rule, _ = self.request("POST", "/api/rules", data)
        self.assertEqual(status, 201)
        return rule

    def test_page_assets_resolve_to_served_javascript_and_stylesheet(self):
        class PageAssets(HTMLParser):
            def __init__(self):
                super().__init__()
                self.scripts = []
                self.stylesheets = []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "script" and attributes.get("src"):
                    self.scripts.append(attributes["src"])
                if tag == "link" and "stylesheet" in attributes.get("rel", "").split() and attributes.get("href"):
                    self.stylesheets.append(attributes["href"])

        status, document, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        assets = PageAssets()
        assets.feed(document.decode("utf-8"))
        self.assertTrue(assets.scripts, "The dashboard must load its application script")
        self.assertTrue(assets.stylesheets, "The dashboard must load its stylesheet")
        for references, expected_types in ((assets.scripts, ("application/javascript", "text/javascript")),
                                            (assets.stylesheets, ("text/css",))):
            for reference in references:
                with self.subTest(asset=reference):
                    url = urlsplit(urljoin(self.origin + "/", reference))
                    self.assertEqual(f"{url.scheme}://{url.netloc}", self.origin)
                    path = url.path + ("?" + url.query if url.query else "")
                    asset_status, content, asset_headers = self.request("GET", path)
                    self.assertEqual(asset_status, 200)
                    self.assertIn(asset_headers["Content-Type"].split(";")[0], expected_types)
                    self.assertIsInstance(content, bytes)
                    self.assertTrue(content.strip())

    def test_rules_crud_and_partial_update(self):
        status, snapshot, _ = self.request("GET", "/api/rules")
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["rules"], [])
        rule = self.create_rule()
        self.assertEqual(rule["ticker"], "005930")
        status, updated, _ = self.request("PUT", f"/api/rules/{rule['id']}", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["target_price"], "100")
        self.assertEqual(self.request("GET", "/api/rules")[1]["rules"][0]["id"], rule["id"])
        self.assertEqual(self.request("DELETE", f"/api/rules/{rule['id']}")[0], 200)
        self.assertEqual(self.request("GET", "/api/rules")[1]["rules"], [])
        self.assertEqual(self.request("DELETE", f"/api/rules/{rule['id']}")[0], 404)
        self.assertEqual(self.request("PUT", f"/api/rules/{rule['id']}", {"enabled": True})[0], 404)

    def test_preview_does_not_send_and_real_check_is_deduplicated(self):
        self.create_rule()
        status, preview, _ = self.request("POST", "/api/check", {"send": False})
        self.assertEqual(status, 200)
        self.assertEqual((preview["sent"], preview["errors"]), (0, 0))
        self.assertEqual(preview["results"][0]["status"], "preview")
        self.assertEqual(self.mailer.calls, [])
        status, checked, _ = self.request("POST", "/api/check", {"send": True})
        self.assertEqual(status, 200)
        self.assertEqual(checked["sent"], 1)
        self.assertEqual(len(self.mailer.calls), 1)
        self.assertEqual(self.request("POST", "/api/check", {"send": True})[1]["sent"], 0)
        self.assertEqual(len(self.mailer.calls), 1)
        alert = self.request("GET", "/api/rules")[1]["recent_alerts"][0]
        self.assertEqual((alert["status"], alert["trade_date"]), ("sent", "2026-10-29"))

    def test_send_requires_an_actual_json_boolean(self):
        self.create_rule()
        for value in ("false", "true", 0, 1, None, [], {}):
            with self.subTest(value=value):
                status, payload, _ = self.request("POST", "/api/check", {"send": value})
                self.assertEqual(status, 400)
                self.assertIn("error", payload)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.mailer.calls, [])

    def test_missing_security_header_blocks_each_mutation_method(self):
        for method, path, data in (("POST", "/api/rules", {"ticker": "005930", "target_price": "100"}),
                                   ("PUT", "/api/settings", {"recipient": "elsewhere@example.com"}),
                                   ("DELETE", "/api/rules/1", {}),
                                   ("POST", "/api/test-email", {}),
                                   ("POST", "/api/check", {"send": True})):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, data, secure=False)[0], 403)
        self.assertEqual(self.service.rules(), [])
        self.assertEqual(self.mailer.calls, [])

    def test_cross_origin_mutation_is_rejected_even_with_header(self):
        for origin in ("https://example.com", "null", "http://localhost:9999", self.origin + ".evil.invalid"):
            with self.subTest(origin=origin):
                status, _, _ = self.request("POST", "/api/test-email", headers={"Origin": origin})
                self.assertEqual(status, 403)
        self.assertEqual(self.mailer.calls, [])
        # A same-origin request, including the private header, is accepted.
        self.assertEqual(self.request("POST", "/api/test-email")[0], 200)
        self.assertEqual(len(self.mailer.calls), 1)

    def test_host_header_rejects_dns_rebinding_and_wrong_ports(self):
        for host in (f"evil.example:{self.port}", f"127.0.0.1.evil.example:{self.port}",
                     "127.0.0.1:1", "localhost:bad", f"user@localhost:{self.port}",
                     f"127.0.0.1:{self.port}/bad", ""):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/api/rules", headers={"Host": host})[0], 403)
        status, _, _ = self.request("GET", "/api/status", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_only_allowlisted_static_files_are_served_and_secret_is_not_exposed(self):
        secret = "never-expose-this-private-app-password"
        with patch.dict(os.environ, {"SMTP_PASSWORD": secret}):
            for path in ("/.env", "/data/alerts.db", "/../.env", "/%2e%2e/.env", "/stock_alert/service.py"):
                with self.subTest(path=path):
                    status, payload, _ = self.request("GET", path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(secret, repr(payload))
            status, payload, headers = self.request("GET", "/api/rules")
            self.assertEqual(status, 200)
            self.assertTrue(payload["settings"]["smtp_password_configured"])
            self.assertNotIn(secret, repr(payload))
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_settings_update_and_explicit_test_mail_use_saved_addresses(self):
        status, settings, _ = self.request("PUT", "/api/settings", {"recipient": "alerts@example.com", "interval_minutes": 45})
        self.assertEqual(status, 200)
        self.assertEqual(settings["recipient"], "alerts@example.com")
        self.assertEqual(settings["interval_minutes"], 45)
        self.assertEqual(self.mailer.calls, [])
        status, payload, _ = self.request("POST", "/api/test-email")
        self.assertEqual(status, 200)
        self.assertIn("message", payload)
        self.assertEqual(self.mailer.calls[0][0]["recipient"], "alerts@example.com")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.request("PUT", "/api/settings", {"smtp_password": "dont-store"})[0], 400)

    def test_invalid_json_shape_content_type_and_oversized_body_are_rejected(self):
        for body in (b"{broken", b"[]", b"null", b'"string"'):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/rules", raw=body)[0], 400)
        self.assertEqual(self.request("POST", "/api/test-email", headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("POST", "/api/test-email", raw=b"x" * 32769)[0], 400)
        self.assertEqual(self.mailer.calls, [])

    def test_status_reflects_active_check_and_busy_check_is_conflict(self):
        status, payload, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["checking"])
        self.service.check_lock.acquire()
        try:
            self.assertTrue(self.request("GET", "/api/status")[1]["checking"])
            self.assertEqual(self.request("POST", "/api/check", {"send": False})[0], 409)
        finally:
            self.service.check_lock.release()


if __name__ == "__main__":
    unittest.main()
