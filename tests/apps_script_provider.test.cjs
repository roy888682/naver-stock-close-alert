'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Naver.gs'), 'utf8');

function formatDate(date, zone, format) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(date).map(p => [p.type, p.value]));
  if (format === 'yyyy-MM-dd') return `${parts.year}-${parts.month}-${parts.day}`;
  if (format === 'HHmm') return parts.hour + parts.minute;
  throw new Error('Unsupported format ' + format);
}

function load(route) {
  const calls = [], sleeps = [];
  const context = vm.createContext({Date, Utilities: {formatDate, sleep(ms) { sleeps.push(ms); }},
    UrlFetchApp: {fetch(url, options) {
      calls.push({url, options});
      const answer = route(url, calls.length);
      if (answer instanceof Error) throw answer;
      const status = answer && answer.status || 200;
      const body = answer && Object.hasOwn(answer, 'body') ? answer.body : answer;
      return {getResponseCode() { return status; }, getContentText() { return typeof body === 'string' ? body : JSON.stringify(body); }};
    }}});
  vm.runInContext(source, context);
  return {api: context.StockNaver, calls, sleeps};
}

function kr(status = 'OPEN', day = '2026-10-30T15:30:00+09:00') {
  return {stockName: '삼성전자', nationType: 'KOR', marketStatus: status, localTradedAt: day};
}
function us(code = 'AAPL.O', day = '2026-10-29T16:00:00-04:00') {
  return {stockName: 'Apple', nationType: 'USA', reutersCode: code, currencyType: {code: 'USD'},
    marketStatus: 'CLOSE', localTradedAt: day, overMarketPriceInfo: {overPrice: '999999'}};
}
const krRows = [{localTradedAt: '2026-10-30', closePrice: '220,500'}, {localTradedAt: '2026-10-29', closePrice: '230,500'}];
const usRows = [{localTradedAt: '2026-10-29T16:00:00-04:00', closePrice: '348.91'}, {localTradedAt: '2026-10-28T16:00:00-04:00', closePrice: '343.83'}];
function prices(basic, rows) { return load(url => url.endsWith('/basic') ? basic : rows); }
function now(text) { return new Date(text); }

test('KR intraday first row is excluded; historical comma-separated close is used', () => {
  const {api} = prices(kr(), krRows);
  const q = api.fetchClose('KR', '005930', now('2026-10-30T10:00:00+09:00'));
  assert.equal(q.close, 230500); assert.equal(q.tradeDate, '2026-10-29'); assert.equal(q.currency, 'KRW');
});

test('KR same-day close needs both the buffer and explicit CLOSE status', () => {
  let client = prices(kr('CLOSE'), krRows);
  assert.equal(client.api.fetchClose('KR', '005930', now('2026-10-30T16:34:00+09:00')).tradeDate, '2026-10-29');
  assert.equal(client.api.fetchClose('KR', '005930', now('2026-10-30T16:35:00+09:00')).tradeDate, '2026-10-30');
  client = prices(kr('OPEN'), krRows);
  assert.equal(client.api.fetchClose('KR', '005930', now('2026-10-30T17:00:00+09:00')).tradeDate, '2026-10-29');
});

test('a delayed basic quote cannot certify a live same-day row', () => {
  const {api} = prices(kr('CLOSE', '2026-10-29T15:30:00+09:00'), krRows);
  assert.equal(api.fetchClose('KR', '005930', now('2026-10-30T17:00:00+09:00')).tradeDate, '2026-10-29');
});

test('US regular close uses New York exchange date, never the after-hours quote', () => {
  const {api} = prices(us(), usRows);
  const q = api.fetchClose('US', 'aapl', now('2026-10-30T06:30:00+09:00'));
  assert.equal(q.ticker, 'AAPL'); assert.equal(q.symbol, 'AAPL.O'); assert.equal(q.tradeDate, '2026-10-29');
  assert.equal(q.close, 348.91); assert.equal(q.currency, 'USD');
  assert.equal(api.fetchClose('US', 'AAPL', now('2026-10-29T16:30:00-04:00')).tradeDate, '2026-10-28');
});

test('DST transition changes the Korean wall-clock time of a US close', () => {
  const {api} = prices(us('IBM', '2026-11-02T16:00:00-05:00'), [
    {localTradedAt: '2026-11-02T16:00:00-05:00', closePrice: '258.28'},
    {localTradedAt: '2026-10-30T16:00:00-04:00', closePrice: '252.00'}
  ]);
  assert.equal(api.fetchClose('US', 'IBM', now('2026-11-03T06:30:00+09:00')).tradeDate, '2026-10-30');
  assert.equal(api.fetchClose('US', 'IBM', now('2026-11-03T07:00:00+09:00')).tradeDate, '2026-11-02');
});

test('explicit regular closing metadata prevents acceptance before a special late close', () => {
  const basic = kr('CLOSE');
  basic.marketOperatingTimeInfo = {regularMarketClosingTime: '2026-10-30T17:30:00+09:00'};
  const {api} = prices(basic, krRows);
  assert.equal(api.fetchClose('KR', '005930', now('2026-10-30T17:00:00+09:00')).tradeDate, '2026-10-29');
});

test('NYSE bare ticker resolves after NASDAQ 409 and caches its Reuters code', () => {
  const client = load(url => url.includes('/IBM.O/') ? {status: 409, body: {}} : url.endsWith('/basic') ? us('IBM') : usRows);
  assert.equal(client.api.lookup('US', 'IBM').symbol, 'IBM');
  assert.equal(client.api.lookup('US', 'IBM').symbol, 'IBM');
  assert.equal(client.calls.filter(c => c.url.includes('/IBM.O/')).length, 1);
  assert.ok(client.calls.every(c => c.options.validateHttpsCertificates === true));
});

test('class-share tickers use Reuters lowercase class suffix and lookup does not fetch prices', () => {
  const client = load(url => {
    assert.ok(url.endsWith('/basic'));
    return us(url.includes('/BRKa/') ? 'BRKa' : 'BRKb');
  });
  assert.equal(client.api.lookup('US', 'brk.b').symbol, 'BRKb');
  assert.equal(client.api.lookup('US', 'BRK.A').symbol, 'BRKa');
});

test('malformed input and invalid now fail before any network request', () => {
  const client = load(() => { throw new Error('must not fetch'); });
  for (const [market, ticker] of [['XX', '005930'], ['KR', '5930'], ['US', '../private'], ['US', ''], ['US', null], ['US', undefined]]) {
    assert.throws(() => client.api.lookup(market, ticker), /StockNaverError/);
  }
  assert.throws(() => client.api.fetchClose('KR', '005930', new Date('invalid')), /조회 시각/);
  assert.equal(client.calls.length, 0);
});

test('stale prices, future sessions, invalid dates and non-finite prices fail closed', () => {
  const client = prices(kr('CLOSE'), [
    {localTradedAt: '2026-10-01', closePrice: '100'},
    {localTradedAt: '2026-11-01', closePrice: '100'},
    {localTradedAt: '2026-02-30', closePrice: '100'},
    {localTradedAt: '2026-10-30', closePrice: 'NaN'}
  ]);
  assert.throws(() => client.api.fetchClose('KR', '005930', now('2026-10-30T17:00:00+09:00')), /오래되었습니다/);
  const empty = prices(kr(), [{localTradedAt: '2026-10-30', closePrice: '100'}]);
  assert.throws(() => empty.api.fetchClose('KR', '005930', now('2026-10-30T10:00:00+09:00')), /완료된 정규장/);
});

test('US timestamp conversion respects its offset and rejects offset-free timestamps', () => {
  const client = prices(us(), [
    {localTradedAt: '2026-10-30T02:00:00+09:00', closePrice: '300'},
    {localTradedAt: '2026-10-30T16:00:00', closePrice: '9999'}
  ]);
  assert.equal(client.api.fetchClose('US', 'AAPL', now('2026-10-30T06:30:00+09:00')).tradeDate, '2026-10-29');
});

test('429 and server errors retry once; unknown symbols and network failures do not', () => {
  for (const status of [429, 500, 503]) {
    const client = load((url, call) => call === 1 ? {status, body: {}} : kr());
    assert.equal(client.api.lookup('KR', '005930').name, '삼성전자');
    assert.deepEqual(client.sleeps, [500]); assert.equal(client.calls.length, 2);
  }
  const failing = load(() => ({status: 503, body: {}}));
  assert.throws(() => failing.api.lookup('KR', '005930'), /HTTP 503/);
  assert.equal(failing.calls.length, 2);
  const unknown = load(() => ({status: 404, body: {}}));
  assert.throws(() => unknown.api.lookup('KR', '005930'), /찾지 못했습니다/);
  assert.equal(unknown.calls.length, 1);
  const disconnected = load(() => new Error('private transport detail'));
  assert.throws(() => disconnected.api.lookup('KR', '005930'), error => /네이버 연결/.test(error.message) && !error.message.includes('private'));
  assert.equal(disconnected.calls.length, 1);
});

test('malformed, oversized and unsupported-country responses produce useful errors', () => {
  assert.throws(() => load(() => ({body: '<html>broken</html>'})).api.lookup('KR', '005930'), /응답 형식/);
  assert.throws(() => load(() => ({body: 'x'.repeat(2000001)})).api.lookup('KR', '005930'), /응답 크기/);
  const nonUS = us(); nonUS.nationType = 'CAN';
  assert.throws(() => load(() => nonUS).api.lookup('US', 'AAPL'), /지원하는 미국 종목/);
  const nonUSD = us(); nonUSD.currencyType.code = 'CAD';
  assert.throws(() => load(() => nonUSD).api.lookup('US', 'AAPL'), /USD로 거래/);
});
