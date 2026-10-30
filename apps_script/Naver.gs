/** NAVER daily-close reader for Apps Script V8. No credentials are required.
 * Daily feeds contain a LIVE first row while the market is open. Same-day
 * rows require both a closing-time buffer and matching CLOSE basic metadata.
 * NAVER's public endpoints are undocumented and may change without notice.
 */
var StockNaver = (function () {
  'use strict';
  var resolved = Object.create(null);

  function failure(message, unknownSymbol) {
    var error = new Error(message);
    error.name = 'StockNaverError';
    error.unknownSymbol = Boolean(unknownSymbol);
    return error;
  }

  function normalize(market, ticker) {
    if (ticker === undefined || ticker === null) throw failure('종목 티커를 입력하세요.');
    market = String(market).trim().toUpperCase();
    ticker = String(ticker).trim().toUpperCase();
    if (market !== 'KR' && market !== 'US') throw failure('시장은 KR 또는 US여야 합니다.');
    var valid = market === 'KR' ? /^[0-9]{6}$/ : /^[A-Z][A-Z0-9.\-]{0,19}$/;
    if (!valid.test(ticker)) throw failure('한국은 6자리 종목코드, 미국은 영문 티커를 입력하세요. 예: 005930, AAPL, BRK.B');
    return {market: market, ticker: ticker};
  }

  function getJson(url) {
    for (var attempt = 0; attempt < 2; attempt++) {
      var response;
      try {
        response = UrlFetchApp.fetch(url, {
          method: 'get', muteHttpExceptions: true, followRedirects: false,
          validateHttpsCertificates: true,
          headers: {'Accept': 'application/json', 'Referer': 'https://m.stock.naver.com/'}
        });
      } catch (error) {
        throw failure('네이버 연결에 실패했습니다. 잠시 후 다시 확인하세요.');
      }
      var status = response.getResponseCode();
      if (status === 400 || status === 404 || status === 409) {
        throw failure('네이버에서 해당 종목을 찾지 못했습니다.', true);
      }
      if (status >= 200 && status < 300) {
        var text = response.getContentText('UTF-8');
        if (text.length > 2000000) throw failure('네이버 응답 크기가 예상 범위를 초과했습니다.');
        try { return JSON.parse(text.replace(/^\uFEFF/, '')); }
        catch (error) { throw failure('네이버 시세 응답 형식이 바뀌었거나 올바르지 않습니다.'); }
      }
      if (attempt === 0 && (status === 429 || (status >= 500 && status <= 599))) {
        Utilities.sleep(500);
        continue;
      }
      throw failure('네이버 시세 조회 실패 (HTTP ' + status + ').');
    }
    throw failure('네이버 시세 조회에 실패했습니다.');
  }

  function candidates(ticker) {
    if (/\.O$/.test(ticker)) return [ticker];
    if (/\.N$/.test(ticker)) return [ticker.slice(0, -2)];
    // BRK.A / BRK.B are share classes, rather than exchange suffixes.
    var share = /^([A-Z0-9]+)[.\-]([A-Z])$/.exec(ticker);
    if (share) {
      var code = share[1] + share[2].toLowerCase();
      return [code, code + '.O'];
    }
    return [ticker + '.O', ticker];
  }

  function basic(identity) {
    var market = identity.market, ticker = identity.ticker;
    if (market === 'KR') {
      var domestic = getJson('https://m.stock.naver.com/api/stock/' + ticker + '/basic');
      if (!domestic || Array.isArray(domestic) || !domestic.stockName ||
          (domestic.nationType && domestic.nationType !== 'KOR')) {
        throw failure('한국 종목의 기본 정보를 확인할 수 없습니다.');
      }
      return {symbol: ticker, data: domestic};
    }
    var codes = candidates(ticker);
    if (resolved[ticker]) codes = [resolved[ticker]].concat(codes.filter(function (code) { return code !== resolved[ticker]; }));
    for (var index = 0; index < codes.length; index++) {
      var data;
      try { data = getJson('https://api.stock.naver.com/stock/' + encodeURIComponent(codes[index]) + '/basic'); }
      catch (error) { if (error.unknownSymbol) continue; throw error; }
      if (!data || Array.isArray(data) || !data.stockName) continue;
      var exchange = data.stockExchangeType || {};
      if ((data.nationType || exchange.nationType) !== 'USA') continue;
      var currency = data.currencyType || {};
      if ((currency.code || currency.name || 'USD') !== 'USD') throw failure('USD로 거래되는 미국 종목만 지원합니다.');
      var symbol = data.reutersCode || codes[index];
      if (typeof symbol !== 'string' || !/^[A-Za-z0-9.\-]{1,25}$/.test(symbol)) {
        throw failure('네이버가 반환한 미국 종목코드를 확인할 수 없습니다.');
      }
      resolved[ticker] = symbol;
      return {symbol: symbol, data: data};
    }
    throw failure('네이버에서 지원하는 미국 종목을 찾지 못했습니다. 티커를 확인하세요.');
  }

  function isoDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
    var parsed = new Date(value + 'T00:00:00Z');
    if (!isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) return '';
    return value;
  }

  function exchangeDate(value, zone) {
    if (typeof value !== 'string') return '';
    if (value.indexOf('T') < 0) return isoDate(value);
    // Require an explicit UTC offset: the server's/script's timezone must not
    // silently determine a US exchange session date.
    if (!isoDate(value.slice(0, 10)) || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return '';
    var stamp = new Date(value);
    return isFinite(stamp.getTime()) ? Utilities.formatDate(stamp, zone, 'yyyy-MM-dd') : '';
  }

  function selectCompleted(market, data, rows, now) {
    if (!Array.isArray(rows) || rows.length === 0) throw failure('네이버의 일별 종가 목록이 비어 있거나 형식이 바뀌었습니다.');
    if (Object.prototype.toString.call(now) !== '[object Date]' || !isFinite(now.getTime())) throw failure('올바른 조회 시각이 필요합니다.');
    var zone = market === 'KR' ? 'Asia/Seoul' : 'America/New_York';
    var today = Utilities.formatDate(now, zone, 'yyyy-MM-dd');
    var localTime = Utilities.formatDate(now, zone, 'HHmm');
    var buffer = market === 'KR' ? '1635' : '1700';
    var latest = null;
    rows.forEach(function (row) {
      if (!row || typeof row !== 'object') return;
      var day = exchangeDate(row.localTradedAt, zone);
      var raw = row.closePrice;
      if (typeof raw !== 'string' && typeof raw !== 'number') return;
      var price = Number(String(raw).replace(/,/g, ''));
      if (!day || day > today || !isFinite(price) || price <= 0) return;
      if (day === today) {
        if (localTime < buffer || data.marketStatus !== 'CLOSE') return;
        if (exchangeDate(data.localTradedAt, zone) !== day) return;
        var operating = data.marketOperatingTimeInfo || {};
        if (operating.regularMarketClosingTime) {
          var closeString = operating.regularMarketClosingTime;
          if (typeof closeString !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(closeString)) return;
          var closeAt = new Date(closeString);
          if (!isFinite(closeAt.getTime()) || now.getTime() < closeAt.getTime()) return;
        }
      }
      if (!latest || day > latest.tradeDate) latest = {close: price, tradeDate: day};
    });
    if (!latest) throw failure('완료된 정규장 종가가 아직 없습니다. 장 마감 이후 다시 확인하세요.');
    var age = (Date.parse(today + 'T00:00:00Z') - Date.parse(latest.tradeDate + 'T00:00:00Z')) / 86400000;
    if (age > 7) throw failure('최신 완료 종가가 ' + latest.tradeDate + '로 오래되었습니다. 거래정지·상장폐지·데이터 지연을 확인하세요.');
    return latest;
  }

  function lookup(market, ticker) {
    var identity = normalize(market, ticker), result = basic(identity);
    return {market: identity.market, ticker: identity.ticker, symbol: result.symbol,
      name: String(result.data.stockName), currency: identity.market === 'KR' ? 'KRW' : 'USD'};
  }

  function fetchClose(market, ticker, now) {
    var identity = normalize(market, ticker);
    now = now === undefined || now === null ? new Date() : now;
    if (Object.prototype.toString.call(now) !== '[object Date]' || !isFinite(now.getTime())) throw failure('올바른 조회 시각이 필요합니다.');
    var result = basic(identity);
    var base = identity.market === 'KR' ? 'https://m.stock.naver.com/api/stock/' : 'https://api.stock.naver.com/stock/';
    var rows = getJson(base + encodeURIComponent(result.symbol) + '/price?page=1&pageSize=20');
    var completed = selectCompleted(identity.market, result.data, rows, now);
    var page = identity.market === 'KR' ? 'domestic/stock/' : 'worldstock/stock/';
    return {market: identity.market, ticker: identity.ticker, symbol: result.symbol,
      name: String(result.data.stockName), currency: identity.market === 'KR' ? 'KRW' : 'USD',
      close: completed.close, tradeDate: completed.tradeDate,
      sourceUrl: 'https://m.stock.naver.com/' + page + encodeURIComponent(result.symbol) + '/total'};
  }

  return {lookup: lookup, fetchClose: fetchClose};
})();
