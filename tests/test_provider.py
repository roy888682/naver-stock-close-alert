"""Recorded NAVER response shapes; no network or SMTP needed."""

import unittest
from datetime import date, datetime
from decimal import Decimal

from stock_alert.provider import NaverProvider, ProviderError, _UnknownSymbol


class StubProvider(NaverProvider):
    def __init__(self, basic, rows):
        super().__init__()
        self.basic = basic
        self.rows = rows
        self.urls = []

    def _get_json(self, url):
        self.urls.append(url)
        return self.basic if url.endswith("/basic") else self.rows


class ProviderTests(unittest.TestCase):
    def kr(self, status="OPEN"):
        return StubProvider(
            {"stockName": "삼성전자", "nationType": "KOR", "marketStatus": status,
             "localTradedAt": "2026-10-30T15:30:00+09:00"},
            [{"localTradedAt": "2026-10-30", "closePrice": "220,500"},
             {"localTradedAt": "2026-10-29", "closePrice": "230,500"}])

    def test_kr_live_daily_row_is_excluded(self):
        q = self.kr().fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T10:00:00+09:00"))
        self.assertEqual((q.trade_date, q.close), (date(2026, 10, 29), Decimal("230500")))

    def test_kr_buffer_and_market_status_both_required(self):
        q = self.kr("CLOSE").fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T16:34:00+09:00"))
        self.assertEqual(q.trade_date, date(2026, 10, 29))
        q = self.kr("OPEN").fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T17:00:00+09:00"))
        self.assertEqual(q.trade_date, date(2026, 10, 29))
        q = self.kr("CLOSE").fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T16:35:00+09:00"))
        self.assertEqual((q.trade_date, q.currency), (date(2026, 10, 30), "KRW"))

    def test_delayed_basic_cannot_certify_same_day_row(self):
        p = self.kr("CLOSE")
        p.basic["localTradedAt"] = "2026-10-29T15:30:00+09:00"
        self.assertEqual(p.fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T17:00:00+09:00")).trade_date, date(2026, 10, 29))

    def test_us_exchange_date_and_afterhours_not_used(self):
        p = StubProvider(
            {"stockName": "애플", "nationType": "USA", "reutersCode": "AAPL.O",
             "marketStatus": "CLOSE", "localTradedAt": "2026-10-29T16:00:00-04:00",
             "currencyType": {"code": "USD"}, "overMarketPriceInfo": {"overPrice": "9999"}},
            [{"localTradedAt": "2026-10-29T16:00:00-04:00", "closePrice": "348.91"},
             {"localTradedAt": "2026-10-28T16:00:00-04:00", "closePrice": "343.83"}])
        # 06:30 in Korea is still the preceding trading date in New York.
        q = p.fetch_close("US", "AAPL", datetime.fromisoformat("2026-10-30T06:30:00+09:00"))
        self.assertEqual((q.symbol, q.trade_date, q.close), ("AAPL.O", date(2026, 10, 29), Decimal("348.91")))
        q = p.fetch_close("US", "AAPL", datetime.fromisoformat("2026-10-29T16:30:00-04:00"))
        self.assertEqual(q.trade_date, date(2026, 10, 28))

    def test_us_dst_uses_new_york_zone(self):
        p = StubProvider(
            {"stockName": "IBM", "nationType": "USA", "reutersCode": "IBM", "marketStatus": "CLOSE", "localTradedAt": "2026-11-02T16:00:00-05:00"},
            [{"localTradedAt": "2026-11-02T16:00:00-05:00", "closePrice": "258.28"},
             {"localTradedAt": "2026-10-30T16:00:00-04:00", "closePrice": "252.00"}])
        # After the November DST transition 06:30 KST is only 16:30 EST.
        self.assertEqual(p.fetch_close("US", "IBM", datetime.fromisoformat("2026-11-03T06:30:00+09:00")).trade_date, date(2026, 10, 30))
        self.assertEqual(p.fetch_close("US", "IBM", datetime.fromisoformat("2026-11-03T07:00:00+09:00")).trade_date, date(2026, 11, 2))

    def test_bare_nyse_symbol_resolution(self):
        p = StubProvider({"stockName": "IBM", "nationType": "USA", "reutersCode": "IBM", "marketStatus": "CLOSE", "localTradedAt": "2026-10-29T16:00:00-04:00"}, [{"localTradedAt": "2026-10-29T16:00:00-04:00", "closePrice": "258.28"}])
        original = p._get_json
        def response(url):
            if "/IBM.O/" in url:
                raise _UnknownSymbol("unknown")
            return original(url)
        p._get_json = response
        q = p.fetch_close("US", "IBM", datetime.fromisoformat("2026-10-30T10:00:00+09:00"))
        self.assertEqual(q.symbol, "IBM")
        self.assertIn("/IBM/price?", p.urls[-1])

    def test_class_share_candidates(self):
        self.assertEqual(NaverProvider._us_candidates("BRK.B")[0], "BRKb")
        self.assertEqual(NaverProvider._us_candidates("BRK.A")[0], "BRKa")
        self.assertEqual(NaverProvider._us_candidates("BRKb")[0], "BRKb")
        self.assertEqual(NaverProvider._us_candidates("IBM.N"), ["IBM"])

    def test_stale_invalid_and_future_data_fail_closed(self):
        p = self.kr("CLOSE")
        p.rows = [{"localTradedAt": "2026-10-01", "closePrice": "100"},
                  {"localTradedAt": "2026-11-01", "closePrice": "100"},
                  {"localTradedAt": "2026-10-30", "closePrice": "NaN"}]
        with self.assertRaises(ProviderError):
            p.fetch_close("KR", "005930", datetime.fromisoformat("2026-10-30T17:00:00+09:00"))

    def test_invalid_inputs_are_rejected(self):
        p = self.kr()
        for market, symbol in [("XX", "005930"), ("KR", "5930"), ("US", "../../secrets")]:
            with self.subTest(market=market, symbol=symbol), self.assertRaises(ProviderError):
                p.fetch_close(market, symbol, datetime.fromisoformat("2026-10-30T17:00:00+09:00"))
        with self.assertRaises(ProviderError):
            p.fetch_close("KR", "005930", datetime(2026, 10, 30))


if __name__ == "__main__":
    unittest.main()
