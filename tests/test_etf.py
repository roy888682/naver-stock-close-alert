"""ETF routing and validation regressions; all NAVER HTTP responses are fake."""
import io
import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from urllib.error import HTTPError
from urllib.parse import quote

from stock_alert.cloud import validate_config
from stock_alert.provider import NaverProvider, ProviderError
from stock_alert.service import validate_rule


def basic_url(symbol, market="US"):
    base = "https://m.stock.naver.com/api/stock" if market == "KR" else "https://api.stock.naver.com/stock"
    return f"{base}/{quote(symbol)}/basic"


def price_url(symbol, market="US"):
    return basic_url(symbol, market).removesuffix("basic") + "price?page=1&pageSize=20"


def us_basic(symbol, **changes):
    data = dict(stockName=f"Test ETF {symbol}", reutersCode=symbol, nationType="USA",
                currencyType={"code": "USD"}, stockEndType="etf", isEtf=True,
                marketStatus="CLOSE", localTradedAt="2026-11-02T16:00:00-05:00",
                overMarketPriceInfo={"overPrice": "9999"})
    data.update(changes)
    return data


def kr_basic(symbol="0016X0", **changes):
    data = dict(stockName="SOL 중단기회사채(A-이상)액티브", reutersCode=symbol,
                itemCode=symbol, nationType="KOR", stockEndType="etf",
                marketStatus="CLOSE", localTradedAt="2026-11-02T15:30:00+09:00")
    data.update(changes)
    return data


class FakeOpener:
    """Route real urllib Requests to recorded response shapes or HTTP statuses."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"Unexpected NAVER request: {url}")
        response = self.responses[url]
        if isinstance(response, int):
            raise HTTPError(url, response, "mock upstream response", {}, None)
        return io.BytesIO(json.dumps(response, ensure_ascii=False).encode("utf-8"))


def provider(responses):
    opener = FakeOpener(responses)
    return NaverProvider(opener=opener, retries=0), opener


class EtfTests(unittest.TestCase):
    def test_spy_resolves_from_nasdaq_conflict_to_bare_symbol(self):
        p, http = provider({basic_url("SPY.O"): 409, basic_url("SPY"): us_basic("SPY")})
        result = p.lookup("US", "SPY")
        self.assertEqual((result["ticker"], result["symbol"], result["currency"]), ("SPY", "SPY", "USD"))
        self.assertEqual(http.calls, [basic_url("SPY.O"), basic_url("SPY")])

    def test_qqq_keeps_nasdaq_resolution_without_extra_etf_probes(self):
        p, http = provider({basic_url("QQQ.O"): us_basic("QQQ.O")})
        self.assertEqual(p.lookup("US", "QQQ")["symbol"], "QQQ.O")
        self.assertEqual(http.calls, [basic_url("QQQ.O")])

    def test_soxl_falls_back_to_naver_k_exchange_suffix(self):
        p, http = provider({basic_url("SOXL.O"): 409, basic_url("SOXL"): 409,
                            basic_url("SOXL.K"): us_basic("SOXL.K")})
        self.assertEqual(p.lookup("US", "SOXL")["symbol"], "SOXL.K")
        self.assertEqual(http.calls, [basic_url("SOXL.O"), basic_url("SOXL"), basic_url("SOXL.K")])

    def test_explicit_k_suffix_is_not_lowercase_share_class(self):
        p, http = provider({basic_url("SOXL.K"): us_basic("SOXL.K")})
        self.assertEqual(p.lookup("US", "soxl.k")["symbol"], "SOXL.K")
        self.assertEqual(http.calls, [basic_url("SOXL.K")])
        self.assertEqual(NaverProvider._us_candidates("SOXL.K"), ["SOXL.K"])

    def test_resolved_k_symbol_is_reused_for_following_lookups(self):
        p, http = provider({basic_url("SOXL.O"): 409, basic_url("SOXL"): 409,
                            basic_url("SOXL.K"): us_basic("SOXL.K")})
        p.lookup("US", "SOXL")
        p.lookup("US", "SOXL")
        self.assertEqual(http.calls[-1], basic_url("SOXL.K"))
        self.assertEqual(len(http.calls), 4)

    def test_class_share_and_existing_exchange_suffix_behavior_is_unchanged(self):
        for ticker, symbol in (("BRK.B", "BRKb"), ("BRK.A", "BRKa")):
            with self.subTest(ticker=ticker):
                p, http = provider({basic_url(symbol): us_basic(symbol, stockEndType="stock", isEtf=False)})
                self.assertEqual(p.lookup("US", ticker)["symbol"], symbol)
                self.assertEqual(http.calls, [basic_url(symbol)])
        self.assertEqual(NaverProvider._us_candidates("IBM.N"), ["IBM"])
        self.assertEqual(NaverProvider._us_candidates("AAPL.O"), ["AAPL.O"])

    def test_service_accepts_korean_alphanumeric_etf_codes_as_uppercase_strings(self):
        for ticker in ("0016X0", "0072R0", "0043B0", "0016x0", "005930"):
            with self.subTest(ticker=ticker):
                result = validate_rule(dict(market="KR", ticker=ticker, target_price="10000"))
                self.assertEqual(result["ticker"], ticker.upper())
                self.assertEqual(len(result["ticker"]), 6)

    def test_cloud_watchlist_accepts_site_alphanumeric_codes_without_changing_ids(self):
        document = {"version": 1, "rules": [dict(id="site-etf-0016X0", market="KR", ticker="0016X0",
                                                   target_price="50000.00", upper_pct="5", lower_pct="5",
                                                   mode="daily", enabled=True)]}
        result = validate_config(document)["rules"][0]
        self.assertEqual((result["id"], result["ticker"], result["target_price"]),
                         ("site-etf-0016X0", "0016X0", "50000"))

    def test_korean_etf_lookup_preserves_leading_zeroes_and_normalizes_letters(self):
        p, http = provider({basic_url("0016X0", "KR"): kr_basic()})
        found = p.lookup("KR", "0016x0")
        self.assertEqual((found["ticker"], found["symbol"], found["currency"]), ("0016X0", "0016X0", "KRW"))
        self.assertEqual(http.calls, [basic_url("0016X0", "KR")])

    def test_korean_etf_completed_close_uses_the_same_alphanumeric_daily_path(self):
        p, http = provider({basic_url("0016X0", "KR"): kr_basic(),
                            price_url("0016X0", "KR"): [{"localTradedAt": "2026-11-02", "closePrice": "51,885"}]})
        found = p.fetch_close("KR", "0016x0", datetime.fromisoformat("2026-11-02T18:00:00+09:00"))
        self.assertEqual((found.symbol, found.trade_date, found.close), ("0016X0", date(2026, 11, 2), Decimal("51885")))
        self.assertEqual(http.calls[-1], price_url("0016X0", "KR"))

    def test_invalid_tickers_still_fail_before_any_upstream_request(self):
        for market, ticker in (("KR", "0016X"), ("KR", "0016X00"), ("KR", "00/6X0"),
                               ("KR", "00한6X0"), ("US", "SOXL.K?x=1"), ("US", "../SOXL"),
                               ("US", "SOXL;id"), ("US", "https://example.com")):
            with self.subTest(market=market, ticker=ticker):
                p, http = provider({})
                with self.assertRaises(ProviderError):
                    p.lookup(market, ticker)
                with self.assertRaises(ValueError):
                    validate_rule(dict(market=market, ticker=ticker, target_price="100"))
                self.assertEqual(http.calls, [])

    def test_etf_country_and_currency_guards_are_not_bypassed_by_k_suffix(self):
        for changes in ({"nationType": "JPN"}, {"currencyType": {"code": "EUR"}}):
            with self.subTest(changes=changes):
                p, _ = provider({basic_url("SOXL.K"): us_basic("SOXL.K", **changes)})
                with self.assertRaises(ProviderError):
                    p.lookup("US", "SOXL.K")

    def test_k_etf_closing_buffer_and_us_dst_remain_enforced(self):
        rows = [{"localTradedAt": "2026-11-02T16:00:00-05:00", "closePrice": "25.35"},
                {"localTradedAt": "2026-10-30T16:00:00-04:00", "closePrice": "24.50"}]
        p, http = provider({basic_url("SOXL.K"): us_basic("SOXL.K"), price_url("SOXL.K"): rows})
        before = p.fetch_close("US", "SOXL.K", datetime.fromisoformat("2026-11-03T06:30:00+09:00"))
        after = p.fetch_close("US", "SOXL.K", datetime.fromisoformat("2026-11-03T07:00:00+09:00"))
        self.assertEqual((before.trade_date, before.close), (date(2026, 10, 30), Decimal("24.50")))
        self.assertEqual((after.trade_date, after.close), (date(2026, 11, 2), Decimal("25.35")))
        self.assertTrue(all("/SOXL.K/" in url for url in http.calls))

    def test_unknown_symbols_and_error_envelopes_use_only_three_candidates(self):
        p, http = provider({basic_url("UNKNOWN.O"): 409, basic_url("UNKNOWN"): 409, basic_url("UNKNOWN.K"): 409})
        with self.assertRaises(ProviderError):
            p.lookup("US", "UNKNOWN")
        self.assertEqual(len(http.calls), 3)
        # A 200 error envelope is not a valid instrument and must not stop fallback.
        p, http = provider({basic_url("SOXL.O"): {"code": "StockConflict", "message": "Not Exist Master"},
                            basic_url("SOXL"): 409, basic_url("SOXL.K"): us_basic("SOXL.K")})
        self.assertEqual(p.lookup("US", "SOXL")["symbol"], "SOXL.K")
        self.assertEqual(len(http.calls), 3)


if __name__ == "__main__":
    unittest.main()
