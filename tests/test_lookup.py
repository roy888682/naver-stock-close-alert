"""Name lookup verifies identity without fetching prices or sending mail."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stock_alert.provider import NaverProvider, ProviderError
from stock_alert.server import make_server
from stock_alert.service import Service


class ProviderLookupTests(unittest.TestCase):
    def test_basic_identity_is_enough_even_without_price_data(self):
        provider = NaverProvider()
        provider._basic = Mock(return_value=("005930", {"stockName": "삼성전자"}))
        provider._get_json = Mock(side_effect=AssertionError("A daily-price request must not be made"))
        result = provider.lookup("kr", " 005930 ")
        self.assertEqual(result, dict(market="KR", ticker="005930", symbol="005930", name="삼성전자", currency="KRW"))
        provider._basic.assert_called_once_with("KR", "005930")
        provider._get_json.assert_not_called()

    def test_user_ticker_and_reuters_symbol_remain_distinct(self):
        provider = NaverProvider()
        provider._basic = Mock(return_value=("BRKb", {"stockName": "버크셔 해서웨이 Class B"}))
        result = provider.lookup("US", "brk.b")
        self.assertEqual((result["ticker"], result["symbol"], result["currency"]), ("BRK.B", "BRKb", "USD"))
        provider._basic.assert_called_once_with("US", "BRK.B")

    def test_invalid_inputs_never_reach_the_network(self):
        provider = NaverProvider()
        provider._basic = Mock()
        for market, ticker in [("JP", "005930"), ("KR", "5930"), ("US", "../../private"), ("US", "")]:
            with self.subTest(market=market, ticker=ticker), self.assertRaises(ProviderError):
                provider.lookup(market, ticker)
        provider._basic.assert_not_called()


class LookupProvider:
    def __init__(self):
        self.calls = []
        self.error = None
        self.name = "Example stock"

    def lookup(self, market, ticker):
        self.calls.append((market, ticker))
        if self.error:
            raise self.error
        return dict(market=market, ticker=ticker, symbol="AAPL.O" if ticker == "AAPL" else ticker,
                    name=self.name, currency="KRW" if market == "KR" else "USD")

    def fetch_close(self, *args):
        raise AssertionError("Name lookup must not fetch prices")


class LookupCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = LookupProvider()
        self.mailer = Mock(side_effect=AssertionError("Name lookup must not send mail"))
        self.service = Service(Path(self.directory.name) / "alerts.db", self.provider, self.mailer)


class ServiceLookupTests(LookupCase):
    def test_cached_result_is_normalized_and_independent_of_returned_objects(self):
        first = self.service.lookup("us", " aapl ")
        first["name"] = "changed by browser caller"
        second = self.service.lookup("US", "AAPL")
        self.assertEqual(second["name"], "Example stock")
        second["symbol"] = "changed again"
        self.assertEqual(self.service.lookup("US", "AAPL")["symbol"], "AAPL.O")
        self.assertEqual(self.provider.calls, [("US", "AAPL")])
        self.assertEqual(self.service.rules(), [])
        self.assertEqual(self.service.snapshot()["recent_alerts"], [])
        self.mailer.assert_not_called()

    def test_cache_expires_after_one_hour(self):
        clock = [100.0]
        with patch("stock_alert.service.time.monotonic", side_effect=lambda: clock[0]):
            self.service.lookup("US", "AAPL")
            self.provider.name = "Updated company name"
            clock[0] = 3699.0
            self.assertEqual(self.service.lookup("US", "AAPL")["name"], "Example stock")
            clock[0] = 3700.0
            self.assertEqual(self.service.lookup("US", "AAPL")["name"], "Updated company name")
        self.assertEqual(len(self.provider.calls), 2)

    def test_provider_errors_are_not_cached(self):
        self.provider.error = ProviderError("Temporary NAVER failure")
        with self.assertRaises(ProviderError):
            self.service.lookup("KR", "005930")
        self.provider.error = None
        self.assertEqual(self.service.lookup("KR", "005930")["ticker"], "005930")
        self.assertEqual(self.provider.calls, [("KR", "005930"), ("KR", "005930")])

    def test_invalid_input_is_rejected_before_provider_lookup(self):
        for market, ticker in [("KR", "5930"), ("US", "../../private"), ("XX", "AAPL")]:
            with self.subTest(market=market, ticker=ticker), self.assertRaises(ValueError):
                self.service.lookup(market, ticker)
        self.assertEqual(self.provider.calls, [])

    def test_cache_has_a_bounded_size_and_evicts_the_oldest_entry(self):
        clock = [100.0]
        with patch("stock_alert.service.time.monotonic", side_effect=lambda: clock[0]):
            for index in range(129):
                clock[0] += 1
                self.service.lookup("US", f"T{index:03d}")
            self.assertEqual(len(self.service.lookup_cache), 128)
            self.service.lookup("US", "T128")
            self.assertEqual(len(self.provider.calls), 129)
            self.service.lookup("US", "T000")
            self.assertEqual(len(self.provider.calls), 130)
            self.assertEqual(len(self.service.lookup_cache), 128)


class HttpLookupTests(LookupCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.service, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            # A name lookup is a read-only GET and needs no mutation header.
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_get_resolves_a_name_without_mutating_rules(self):
        status, result = self.get("/api/lookup?market=US&ticker=aapl")
        self.assertEqual(status, 200)
        self.assertEqual((result["name"], result["symbol"]), ("Example stock", "AAPL.O"))
        self.assertEqual(self.service.rules(), [])
        self.mailer.assert_not_called()

    def test_invalid_or_ambiguous_query_parameters_return_400(self):
        for query in ["market=KR", "ticker=005930", "market=KR&ticker=", "market=KR&ticker=5930",
                      "market=KR&ticker=005930&extra=1", "market=KR&market=US&ticker=005930",
                      "market=KR&ticker=005930&ticker=035420"]:
            with self.subTest(query=query):
                self.assertEqual(self.get("/api/lookup?" + query)[0], 400)
        self.assertEqual(self.provider.calls, [])

    def test_provider_failure_returns_502_then_can_be_retried(self):
        self.provider.error = ProviderError("네이버 연결 실패")
        status, result = self.get("/api/lookup?market=KR&ticker=005930")
        self.assertEqual((status, result["error"]), (502, "네이버 연결 실패"))
        self.provider.error = None
        self.assertEqual(self.get("/api/lookup?market=KR&ticker=005930")[0], 200)
        self.assertEqual(len(self.provider.calls), 2)


if __name__ == "__main__":
    unittest.main()
