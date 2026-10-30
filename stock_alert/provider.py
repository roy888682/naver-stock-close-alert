"""Read completed regular-session daily prices from NAVER Finance.

The daily-price feed also contains a *live* row during the trading session.
Never use that row until the local closing buffer has elapsed AND NAVER's
basic quote says the regular market is closed. After-hours quote fields are
deliberately ignored. These public endpoints are not a supported official API.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as urlquote
from urllib.request import Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ProviderError(RuntimeError):
    """No trustworthy completed closing price was available."""


class _UnknownSymbol(ProviderError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    market: str
    currency: str
    close: Decimal
    trade_date: date
    source_url: str


class NaverProvider:
    """Small, bounded HTTP client; respects the operating system's proxy settings.

    The optional opener is useful for testing. The production default does not
    disable proxies or TLS verification. Korean same-day rows become eligible
    at 16:35 KST, US rows at 17:00 New York time, and only with CLOSE status.
    Prior-date rows are already completed sessions. A seven-day freshness guard
    prevents a stale or delisted symbol from generating a new alert.
    """

    def __init__(self, timeout: float = 10, retries: int = 1, opener=None):
        self.timeout = timeout
        self.retries = max(0, min(int(retries), 3))
        self._opener = opener if opener is not None else build_opener()
        self._resolved: dict[str, str] = {}

    def _get_json(self, url: str) -> Any:
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NaverStockCloseAlert/1.0)",
            "Accept": "application/json",
            "Referer": "https://m.stock.naver.com/",
        })
        for attempt in range(self.retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    # Bound unexpectedly large responses before decoding JSON.
                    raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ProviderError("네이버 응답 크기가 예상 범위를 초과했습니다.")
                    return json.loads(raw.decode("utf-8-sig"))
            except HTTPError as exc:
                if exc.code in (400, 404, 409):
                    raise _UnknownSymbol("네이버에서 해당 종목을 찾지 못했습니다.") from exc
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == self.retries:
                    raise ProviderError(f"네이버 시세 조회 실패 (HTTP {exc.code}).") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise ProviderError("네이버 연결에 실패했습니다. 인터넷 연결과 프록시 설정을 확인하세요.") from exc
            except (ValueError, UnicodeError) as exc:
                raise ProviderError("네이버 시세 응답을 읽을 수 없습니다. 데이터 형식이 바뀌었을 수 있습니다.") from exc
            time.sleep(0.5 * (attempt + 1))
        raise ProviderError("네이버 시세 조회에 실패했습니다.")

    @staticmethod
    def _us_candidates(ticker: str) -> list[str]:
        raw = ticker.strip()
        if not raw or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\- ]{0,19}", raw):
            raise ProviderError("미국 티커를 확인하세요. 예: AAPL, IBM, BRK.B, AAPL.O")
        upper = raw.upper()
        if upper.endswith(".O"):
            return [upper]
        if upper.endswith(".N"):
            # NAVER uses bare Reuters symbols for NYSE (IBM, not IBM.N).
            # Do not treat .A as an exchange suffix: BRK.A is a class share.
            return [upper[:-2]]
        class_match = re.fullmatch(r"([A-Z0-9]+)[.\- ]([A-Z])", upper)
        if class_match:
            base = class_match.group(1) + class_match.group(2).lower()
            return [base, base + ".O"]
        # Preserve an explicitly entered Reuters class suffix such as BRKb.
        if re.fullmatch(r"[A-Z0-9]+[a-z]", raw):
            return [raw, raw + ".O"]
        return [upper + ".O", upper]

    def _basic(self, market: str, ticker: str) -> tuple[str, dict[str, Any]]:
        if market == "KR":
            if not re.fullmatch(r"\d{6}", ticker):
                raise ProviderError("한국 종목코드는 005930처럼 6자리로 입력하세요.")
            data = self._get_json(f"https://m.stock.naver.com/api/stock/{ticker}/basic")
            if not isinstance(data, dict) or not data.get("stockName"):
                raise ProviderError("한국 종목의 기본 정보를 확인할 수 없습니다.")
            if data.get("nationType") not in (None, "KOR"):
                raise ProviderError("한국 종목 정보가 아닙니다.")
            return ticker, data

        candidates = self._us_candidates(ticker)
        cached = self._resolved.get(ticker)
        if cached:
            candidates = [cached] + [c for c in candidates if c != cached]
        for candidate in candidates:
            try:
                data = self._get_json(f"https://api.stock.naver.com/stock/{urlquote(candidate)}/basic")
            except _UnknownSymbol:
                continue
            if not isinstance(data, dict) or not data.get("stockName"):
                continue
            exchange = data.get("stockExchangeType") or {}
            if data.get("nationType", exchange.get("nationType")) != "USA":
                continue
            currency = data.get("currencyType") or {}
            if currency.get("code", currency.get("name", "USD")) != "USD":
                raise ProviderError("USD로 거래되는 미국 종목만 지원합니다.")
            resolved = data.get("reutersCode", candidate)
            if not isinstance(resolved, str) or not re.fullmatch(r"[A-Za-z0-9.\-]{1,25}", resolved):
                raise ProviderError("네이버가 반환한 미국 종목코드를 확인할 수 없습니다.")
            self._resolved[ticker] = resolved
            return resolved, data
        raise ProviderError("네이버에서 지원하는 미국 종목을 찾지 못했습니다. 티커 또는 네이버 종목코드를 확인하세요.")

    def fetch_close(self, market: str, ticker: str, now: datetime | None = None) -> Quote:
        market = str(market).strip().upper()
        ticker = str(ticker).strip()
        if market not in ("KR", "US"):
            raise ProviderError("시장은 KR 또는 US여야 합니다.")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ProviderError("조회 시각에는 시간대 정보가 필요합니다.")
        try:
            zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
        except ZoneInfoNotFoundError as exc:
            raise ProviderError("시간대 데이터가 없습니다. pip install -r requirements.txt를 실행하세요.") from exc
        local_now = now.astimezone(zone)
        symbol, basic = self._basic(market, ticker)
        base = "https://m.stock.naver.com/api/stock" if market == "KR" else "https://api.stock.naver.com/stock"
        rows = self._get_json(f"{base}/{urlquote(symbol)}/price?page=1&pageSize=20")
        if not isinstance(rows, list) or not rows:
            raise ProviderError("네이버의 일별 종가 목록이 비어 있거나 형식이 바뀌었습니다.")

        eligible: list[tuple[date, Decimal]] = []
        buffer_time = day_time(16, 35) if market == "KR" else day_time(17, 0)
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_date = row.get("localTradedAt")
            if not isinstance(raw_date, str):
                continue
            try:
                # Overseas rows include their actual exchange UTC offset.
                if "T" in raw_date:
                    stamp = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    session_date = stamp.astimezone(zone).date() if stamp.tzinfo else stamp.date()
                else:
                    session_date = date.fromisoformat(raw_date)
                price = Decimal(str(row.get("closePrice", "")).replace(",", ""))
            except (ValueError, InvalidOperation):
                continue
            if not price.is_finite() or price <= 0 or session_date > local_now.date():
                continue
            if session_date == local_now.date():
                if local_now.time().replace(tzinfo=None) < buffer_time or basic.get("marketStatus") != "CLOSE":
                    continue
                # Require the basic quote and daily row to refer to the same
                # session; a delayed basic response must not certify a live row.
                try:
                    basic_day = date.fromisoformat(str(basic.get("localTradedAt", ""))[:10])
                except ValueError:
                    continue
                if basic_day != session_date:
                    continue
                operating = basic.get("marketOperatingTimeInfo") or {}
                regular_close = operating.get("regularMarketClosingTime")
                if regular_close:
                    try:
                        close_at = datetime.fromisoformat(regular_close.replace("Z", "+00:00"))
                        if close_at.tzinfo is None or now < close_at:
                            continue
                    except (TypeError, ValueError):
                        continue
            eligible.append((session_date, price))

        if not eligible:
            raise ProviderError("완료된 정규장 종가가 아직 없습니다. 장 마감 이후 다시 확인하세요.")
        trade_date, close = max(eligible, key=lambda item: item[0])
        if (local_now.date() - trade_date).days > 7:
            raise ProviderError(f"최신 완료 종가가 {trade_date.isoformat()}로 오래되었습니다. 거래정지·상장폐지·데이터 지연을 확인하세요.")
        page = f"https://m.stock.naver.com/domestic/stock/{symbol}/total" if market == "KR" else f"https://m.stock.naver.com/worldstock/stock/{symbol}/total"
        return Quote(symbol=symbol, name=str(basic["stockName"]), market=market,
                     currency="KRW" if market == "KR" else "USD", close=close,
                     trade_date=trade_date, source_url=page)

    def lookup(self, market: str, ticker: str) -> dict[str, str]:
        """Resolve a name using basic information only, independent of market hours."""
        market = str(market).strip().upper()
        ticker = str(ticker).strip().upper()
        if market not in ("KR", "US"):
            raise ProviderError("시장은 KR 또는 US여야 합니다.")
        if not re.fullmatch(r"[0-9]{6}" if market == "KR" else r"[A-Z][A-Z0-9.\-]{0,19}", ticker):
            raise ProviderError("한국은 6자리 종목코드, 미국은 영문 티커를 입력하세요.")
        symbol, basic = self._basic(market, ticker)
        return dict(market=market, ticker=ticker, symbol=symbol,
                    name=str(basic["stockName"]), currency="KRW" if market == "KR" else "USD")
