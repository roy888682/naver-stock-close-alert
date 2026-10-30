'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Monitor.gs'), 'utf8');

function rule(changes = {}) {
  return { id: 'rule-1', market: 'KR', ticker: '005930', name: 'Old manual name', targetPrice: 100,
    upperPct: 5, lowerPct: 5, mode: 'entry', enabled: true, row: 2, revision: 'rev-1', ...changes };
}

function quote(changes = {}) {
  return { market: 'KR', ticker: '005930', symbol: '005930', name: 'Current quote name', currency: 'KRW',
    close: 100, tradeDate: '2026-10-26', sourceUrl: 'https://stock.naver.com/test', ...changes };
}

function harness(options = {}) {
  const env = { rules: options.rules || [rule()], properties: options.properties || new Map(),
    quote: quote(), now: Date.parse('2026-10-30T10:00:00Z'), quota: 100, lockAvailable: true,
    sendError: null, ownerError: null, fetcher: null, failSet: null, current: () => true,
    mails: [], updates: [], logs: [], fetches: [], writes: [], ownerCalls: 0,
    lockCalls: [], releases: 0, quotaCalls: 0 };
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [env.now])); }
    static now() { return env.now; }
  }
  const properties = {
    getProperty: key => env.properties.has(key) ? env.properties.get(key) : null,
    setProperty(key, value) {
      if (env.failSet && env.failSet(key, value)) throw new Error('Mock property write failed');
      env.properties.set(key, value);
      env.writes.push({ key, value });
    },
    deleteProperty: key => env.properties.delete(key),
  };
  const context = {
    Date: ClockDate,
    StockApp: {
      assertOwner() { env.ownerCalls++; if (env.ownerError) throw env.ownerError; },
      loadRules: () => env.rules,
      recipient: () => 'receiver@example.invalid',
      updateQuote: (r, q, error) => env.updates.push({ rule: r, quote: q, error }),
      appendLog: entry => env.logs.push(entry),
      isCurrent: r => env.current(r),
    },
    StockNaver: {
      fetchClose(market, ticker) {
        env.fetches.push({ market, ticker });
        return env.fetcher ? env.fetcher(market, ticker) : { ...env.quote, market, ticker,
          currency: market === 'US' ? 'USD' : 'KRW' };
      },
    },
    LockService: { getScriptLock: () => ({
      tryLock(timeout) { env.lockCalls.push(timeout); return env.lockAvailable; },
      releaseLock() { env.releases++; },
    }) },
    PropertiesService: { getScriptProperties: () => properties },
    MailApp: {
      getRemainingDailyQuota() { env.quotaCalls++; return env.quota; },
      sendEmail(payload) {
        env.mails.push(payload);
        if (env.onSend) env.onSend(payload);
        if (env.sendError) throw env.sendError;
        env.quota--;
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'Monitor.gs' });
  env.check = options => context.StockMonitor.check(options);
  env.state = id => JSON.parse(env.properties.get('STOCK_RULE_' + (id || 'rule-1')));
  env.context = context;
  return env;
}

test('default check records pending before accepting mail and uses the fresh quote name', () => {
  const h = harness();
  h.onSend = () => {
    assert.equal(h.state().pendingDate, h.quote.tradeDate);
    assert.equal(h.state().lastSentDate, null);
  };
  const result = h.check();
  assert.equal(result.sent, 1);
  assert.equal(result.errors, 0);
  assert.equal(h.state().pendingDate, null);
  assert.equal(h.state().lastSentDate, '2026-10-26');
  assert.match(h.mails[0].body, /^Current quote name/);
  assert.equal(h.mails[0].to, 'receiver@example.invalid');
  assert.equal(h.logs[0].status, 'sent');
  assert.deepEqual(h.lockCalls, [1000]);
  assert.equal(h.releases, 1);
});

test('empty and disabled watchlists make no market or mail requests', () => {
  for (const rules of [[], [rule({ enabled: false })]]) {
    const h = harness({ rules });
    assert.equal(h.check().checked, 0);
    assert.equal(h.fetches.length, 0);
    assert.equal(h.mails.length, 0);
    assert.equal(h.quotaCalls, 0);
  }
});

test('inclusive decimal boundaries allow rounding noise but reject one-millionth outside', () => {
  for (const [price, expected] of [[95.0095, 1], [103.0103, 1], [95.009499, 0], [103.010301, 0]]) {
    const h = harness({ rules: [rule({ targetPrice: 100.01, lowerPct: 5, upperPct: 3 })] });
    h.quote.close = price;
    assert.equal(h.check().sent, expected, String(price));
  }
});

test('entry mode suppresses continued matches and rearms after a completed outside day', () => {
  const h = harness();
  assert.equal(h.check().sent, 1);
  assert.equal(h.check().sent, 0);
  h.quote = quote({ tradeDate: '2026-10-27', close: 102 });
  assert.equal(h.check().sent, 0);
  h.quote = quote({ tradeDate: '2026-10-28', close: 106 });
  assert.equal(h.check().sent, 0);
  h.quote = quote({ tradeDate: '2026-10-29', close: 105 });
  assert.equal(h.check().sent, 1);
  assert.equal(h.mails.length, 2);
});

test('daily mode sends once per trade date and persists deduplication across execution restart', () => {
  const h = harness({ rules: [rule({ mode: 'daily' })] });
  assert.equal(h.check().sent, 1);
  assert.equal(h.check().sent, 0);
  const resumed = harness({ rules: h.rules, properties: h.properties });
  assert.equal(resumed.check().sent, 0);
  resumed.quote.tradeDate = '2026-10-27';
  assert.equal(resumed.check().sent, 1);
});

test('check-only never consumes an entry or rearms one through a preview outside price', () => {
  const h = harness();
  assert.equal(h.check({ send: false }).sent, 0);
  assert.equal(h.properties.size, 0);
  assert.equal(h.mails.length, 0);
  assert.equal(h.check().sent, 1);
  const original = h.properties.get('STOCK_RULE_rule-1');
  h.quote = quote({ tradeDate: '2026-10-27', close: 110 });
  h.check({ send: false });
  assert.equal(h.properties.get('STOCK_RULE_rule-1'), original);
  h.quote = quote({ tradeDate: '2026-10-28', close: 100 });
  assert.equal(h.check().sent, 0);
});

test('zero quota fails before pending and permits a later retry', () => {
  const h = harness();
  h.quota = 0;
  assert.equal(h.check().errors, 1);
  assert.equal(h.properties.size, 0);
  assert.equal(h.mails.length, 0);
  h.quota = 1;
  assert.equal(h.check().sent, 1);
});

test('unknown mail failure persists uncertain and blocks future automatic mail even on a later date', () => {
  const h = harness();
  h.sendError = new Error('Raw SMTP-like mock failure must not be shown');
  assert.equal(h.check().errors, 1);
  assert.equal(h.state().uncertain, true);
  assert.equal(h.state().pendingDate, '2026-10-26');
  assert.equal(h.logs[0].status, 'uncertain');
  assert.doesNotMatch(h.logs[0].message, /Raw SMTP/);
  const resumed = harness({ properties: h.properties });
  resumed.quote.tradeDate = '2026-10-27';
  assert.equal(resumed.check().errors, 1);
  assert.equal(resumed.mails.length, 0);
  assert.equal(resumed.quotaCalls, 0);
});

test('a durable pending marker left by a terminated run prevents resending', () => {
  const h = harness();
  h.properties.set('STOCK_RULE_rule-1', JSON.stringify({ revision: 'rev-1', evaluatedDate: null,
    wasInRange: null, lastSentDate: null, pendingDate: '2026-10-26', uncertain: false }));
  assert.equal(h.check().errors, 1);
  assert.equal(h.mails.length, 0);
});

test('older data cannot rearm an entry alert', () => {
  const h = harness();
  h.quote.tradeDate = '2026-10-28';
  h.check();
  h.quote = quote({ tradeDate: '2026-10-27', close: 110 });
  assert.equal(h.check().sent, 0);
  assert.equal(h.state().wasInRange, true);
  assert.equal(h.state().evaluatedDate, '2026-10-28');
  h.quote = quote({ tradeDate: '2026-10-29', close: 100 });
  assert.equal(h.check().sent, 0);
});

test('a deliberate configuration revision starts a new alert state', () => {
  const h = harness();
  h.check();
  h.rules[0].revision = 'rev-2';
  assert.equal(h.check().sent, 1);
  assert.equal(h.state().revision, 'rev-2');
});

test('invalid rows report errors independently and do not prevent valid rules', () => {
  const h = harness({ rules: [rule({ validationError: '목표가를 입력하세요.', targetPrice: NaN }),
    rule({ id: 'rule-2', ticker: '000660', row: 3 })] });
  const result = h.check();
  assert.equal(result.checked, 2);
  assert.equal(result.errors, 1);
  assert.equal(result.sent, 1);
  assert.equal(h.fetches.length, 1);
  assert.equal(h.updates[0].quote, null);
});

test('nonfinite prices, bad dates, future dates and wrong currencies fail closed', () => {
  for (const change of [{ close: NaN }, { close: 0 }, { tradeDate: '2026-02-30' },
    { tradeDate: '2099-01-01' }, { currency: 'EUR' }, { sourceUrl: 'http://insecure.invalid/' }]) {
    const h = harness();
    h.fetcher = () => quote(change);
    assert.equal(h.check().errors, 1, JSON.stringify(change));
    assert.equal(h.mails.length, 0);
    assert.equal(h.properties.size, 0);
  }
});

test('market quotes and provider failures are each cached per symbol in a run', () => {
  const rules = [rule(), rule({ id: 'rule-2', targetPrice: 90 })];
  const h = harness({ rules });
  h.check({ send: false });
  assert.equal(h.fetches.length, 1);
  const failing = harness({ rules });
  failing.fetcher = () => { throw new Error('시세 조회 실패'); };
  assert.equal(failing.check().errors, 2);
  assert.equal(failing.fetches.length, 1);
});

test('overlapping invocation returns without checking and does not release an unowned lock', () => {
  const h = harness();
  h.lockAvailable = false;
  assert.equal(h.check().checked, 0);
  assert.equal(h.fetches.length, 0);
  assert.equal(h.releases, 0);
});

test('rule limit and duplicate IDs fail before mail and always release the acquired lock', () => {
  for (const rules of [[rule(), rule()], Array.from({ length: 101 }, (_, i) => rule({ id: 'rule-' + i }))]) {
    const h = harness({ rules });
    assert.throws(() => h.check());
    assert.equal(h.mails.length, 0);
    assert.equal(h.releases, 1);
  }
});

test('four-minute cursor resumes with the next unprocessed rule instead of starving later rules', () => {
  const h = harness({ rules: [rule(), rule({ id: 'rule-2', ticker: '000660' })] });
  h.fetcher = (market, ticker) => { h.now += 240001; return quote({ market, ticker }); };
  const first = h.check();
  assert.equal(first.checked, 1);
  assert.equal(h.properties.get('STOCK_MONITOR_CURSOR'), 'rule-2');
  h.fetcher = (market, ticker) => quote({ market, ticker });
  const second = h.check();
  assert.equal(second.sent, 1);
  assert.equal(h.fetches[1].ticker, '000660');
  assert.equal(h.properties.has('STOCK_MONITOR_CURSOR'), false);
});

test('failed pending-state persistence prevents mail and allows retry', () => {
  const h = harness();
  h.failSet = () => true;
  assert.equal(h.check().errors, 1);
  assert.equal(h.mails.length, 0);
  assert.equal(h.properties.size, 0);
  h.failSet = null;
  assert.equal(h.check().sent, 1);
});

test('post-send state failure retains the durable pending marker and blocks a second mail', () => {
  const h = harness();
  h.failSet = (key, value) => key.startsWith('STOCK_RULE_') && JSON.parse(value).lastSentDate !== null;
  const outcome = h.check();
  assert.equal(outcome.sent, 1);
  assert.equal(outcome.errors, 1);
  assert.equal(h.state().pendingDate, '2026-10-26');
  h.failSet = null;
  assert.equal(h.check().errors, 1);
  assert.equal(h.mails.length, 1);
});

test('corrupt persisted state is not silently treated as a new unsent rule', () => {
  for (const value of ['not JSON', '{}', '[]', 'null']) {
    const h = harness();
    h.properties.set('STOCK_RULE_rule-1', value);
    assert.equal(h.check().errors, 1);
    assert.equal(h.mails.length, 0);
  }
});

test('invalid send options and unauthorized execution never acquire a lock or send mail', () => {
  const h = harness();
  assert.throws(() => h.check({ send: 'false' }));
  assert.equal(h.lockCalls.length, 0);
  h.ownerError = new Error('허용된 Google 계정이 아닙니다.');
  assert.throws(() => h.check());
  assert.equal(h.lockCalls.length, 0);
  assert.equal(h.mails.length, 0);
});

test('edits or disabled rules during market fetch prevent stale updates and mail', () => {
  const h = harness();
  h.current = () => false;
  assert.equal(h.check().sent, 0);
  assert.equal(h.updates.length, 0);
  assert.equal(h.properties.size, 0);
  assert.equal(h.logs[0].status, 'skipped');
});

test('a second configuration check immediately before pending blocks a later sheet edit', () => {
  const h = harness();
  let reads = 0;
  h.current = () => ++reads === 1;
  assert.equal(h.check().sent, 0);
  assert.equal(h.updates.length, 1);
  assert.equal(h.properties.size, 0);
  assert.equal(h.mails.length, 0);
});

test('check-only does not move the scheduling cursor', () => {
  const h = harness({ rules: [rule(), rule({ id: 'rule-2', ticker: '000660' })] });
  h.properties.set('STOCK_MONITOR_CURSOR', 'rule-2');
  h.fetcher = (market, ticker) => { h.now += 240001; return quote({ market, ticker }); };
  h.check({ send: false });
  assert.equal(h.properties.get('STOCK_MONITOR_CURSOR'), 'rule-2');
  assert.equal(h.writes.length, 0);
});
