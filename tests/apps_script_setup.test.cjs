'use strict';

// Real Code.gs runs inside a fresh VM. Spreadsheet and trigger mocks keep all
// writes in memory; these tests cannot contact Google or send email.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Code.gs'), 'utf8');
const OWNER = 'royhan88888@gmail.com';
const RECIPIENT = 'ykhan@dacpole.com';

function makeHarness(options = {}) {
  const events = { mail: [], fetch: [], deleted: [], created: [], lockAttempts: [], released: 0 };
  const properties = new Map();
  let uuid = 0;

  class MemorySheet {
    constructor(name, values = []) {
      this.name = name;
      this.values = values.map(row => row.slice());
      this.forcedLastRow = null;
    }
    getName() { return this.name; }
    getParent() { return book; }
    getMaxRows() { return 1001; }
    getLastRow() {
      if (this.forcedLastRow !== null) return this.forcedLastRow;
      let length = this.values.length;
      while (length && !this.values[length - 1].some(value => value !== '' && value != null)) length--;
      return length;
    }
    getRange(row, column, rowCount = 1, columnCount = 1) {
      const sheet = this;
      const range = {
        getValues() {
          return Array.from({ length: rowCount }, (_, r) => Array.from({ length: columnCount }, (_, c) =>
            sheet.values[row - 1 + r]?.[column - 1 + c] ?? ''));
        },
        setValues(values) {
          values.forEach((cells, r) => cells.forEach((value, c) => {
            if (!sheet.values[row - 1 + r]) sheet.values[row - 1 + r] = [];
            sheet.values[row - 1 + r][column - 1 + c] = value;
          }));
          return range;
        },
        setValue(value) { return range.setValues([[value]]); },
        clearContent() {
          return range.setValues(Array.from({ length: rowCount }, () => Array(columnCount).fill('')));
        },
        getSheet() { return sheet; },
        getRow() { return row; },
        getColumn() { return column; },
        getLastRow() { return row + rowCount - 1; },
        getLastColumn() { return column + columnCount - 1; }
      };
      for (const method of ['setBackground', 'setFontColor', 'setFontWeight', 'setFontFamily',
        'setVerticalAlignment', 'setDataValidation', 'setNumberFormat', 'setNotes', 'setWrap']) {
        range[method] = () => range;
      }
      return range;
    }
    appendRow(values) { this.values.push(values.slice()); return this; }
    deleteRows(start, count) { this.values.splice(start - 1, count); return this; }
    setFrozenRows() { return this; }
    setRowHeight() { return this; }
    hideColumns() { return this; }
    setColumnWidth() { return this; }
    setColumnWidths() { return this; }
  }

  const sheets = new Map();
  const book = {
    getId: () => 'test-book',
    getUrl: () => 'https://docs.google.com/spreadsheets/d/test-book/edit',
    setSpreadsheetTimeZone(zone) { this.zone = zone; return this; },
    getSheetByName(name) { return sheets.get(name) || null; },
    insertSheet(name) {
      const sheet = new MemorySheet(name);
      sheets.set(name, sheet);
      return sheet;
    }
  };

  function trigger(handler, extra = {}) {
    return { handler, ...extra, getHandlerFunction() { return handler; } };
  }
  let triggers = (options.triggers || []).map(handler => trigger(handler, { old: true }));
  let creationCount = 0;
  const scriptApp = {
    getProjectTriggers: () => triggers.slice(),
    deleteTrigger(item) {
      events.deleted.push(item);
      triggers = triggers.filter(existing => existing !== item);
    },
    newTrigger(handler) {
      const descriptor = { handler };
      const builder = {
        timeBased() { descriptor.type = 'clock'; return builder; },
        everyMinutes(value) { descriptor.minutes = value; return builder; },
        forSpreadsheet(value) { descriptor.book = value.getId(); return builder; },
        onEdit() { descriptor.type = 'edit'; return builder; },
        create() {
          creationCount++;
          if (options.failCreation === creationCount) throw new Error('simulated trigger creation failure');
          const result = trigger(handler, descriptor);
          triggers.push(result);
          events.created.push(result);
          return result;
        }
      };
      return builder;
    }
  };

  const context = vm.createContext({
    console: { log() {}, error() {} },
    Session: { getEffectiveUser: () => ({ getEmail: () => options.owner ?? OWNER }) },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: key => properties.get(key) ?? null,
        setProperty(key, value) { properties.set(key, String(value)); return this; }
      })
    },
    SpreadsheetApp: {
      getActiveSpreadsheet: () => options.standalone ? null : book,
      create: () => book,
      openById(id) { assert.equal(id, book.getId()); return book; },
      newDataValidation() {
        const builder = {};
        for (const method of ['requireCheckbox', 'requireValueInList', 'setAllowInvalid']) builder[method] = () => builder;
        builder.build = () => ({});
        return builder;
      }
    },
    ScriptApp: scriptApp,
    LockService: { getScriptLock: () => ({
      tryLock(milliseconds) { events.lockAttempts.push(milliseconds); return options.lockAvailable !== false; },
      releaseLock() { events.released++; }
    }) },
    StockNaver: { fetchClose(market, ticker) {
      events.fetch.push([market, ticker]);
      if (options.fetchError) throw new Error(options.fetchError);
      return { name: 'Test stock', close: 100, tradeDate: '2026-10-30' };
    } },
    MailApp: {
      getRemainingDailyQuota: () => options.quota ?? 100,
      sendEmail(message) { events.mail.push(message); }
    },
    Utilities: {
      getUuid: () => `fresh-id-${++uuid}`,
      formatDate: () => '2026-10-30 17:30:00'
    }
  });
  vm.runInContext(source, context, { filename: 'Code.gs' });
  return { context, app: context.StockApp, events, properties, book, sheets,
    triggers: () => triggers.slice(),
    addSheet(name, values) {
      const result = new MemorySheet(name, values);
      sheets.set(name, result);
      return result;
    }
  };
}

function values(changes = {}) {
  const row = ['stable-id', true, 'KR', '005930', '', 100, '', '', 'entry', '', '', '', '', ''];
  for (const [column, value] of Object.entries(changes)) row[Number(column)] = value;
  return row;
}

function withRuleSheets(harness, rows) {
  harness.properties.set('SPREADSHEET_ID', harness.book.getId());
  const sheet = harness.addSheet('관심종목', [Array(14).fill('header'), ...rows]);
  harness.addSheet('설정', [['설정 항목', '값'], ['수신 이메일', RECIPIENT]]);
  return sheet;
}

test('a different or unavailable effective owner blocks setup and test mail before side effects', () => {
  for (const owner of ['hansharedid@gmail.com', '', 'other@example.com']) {
    const h = makeHarness({ owner });
    assert.throws(() => h.context.setup(), /royhan88888@gmail\.com/);
    assert.throws(() => h.context.sendTestEmail(), /royhan88888@gmail\.com/);
    assert.equal(h.sheets.size, 0);
    assert.equal(h.events.created.length, 0);
    assert.equal(h.events.mail.length, 0);
    assert.equal(h.events.fetch.length, 0);
  }
});

test('the designated effective owner is accepted case-insensitively', () => {
  const h = makeHarness({ owner: 'RoyHan88888@Gmail.com' });
  assert.doesNotThrow(() => h.app.assertOwner());
  assert.equal(h.context.setup(), h.book.getUrl());
});

test('parseRule preserves Korean leading zeroes, recovers numeric codes, and defaults blank percentages to five', () => {
  const h = makeHarness();
  for (const ticker of ['005930', 5930]) {
    const rule = h.app.parseRule(values({ 2: ' kr ', 3: ticker }), 2, RECIPIENT);
    assert.equal(rule.ticker, '005930');
    assert.equal(rule.market, 'KR');
    assert.equal(rule.upperPct, 5);
    assert.equal(rule.lowerPct, 5);
    assert.equal(rule.mode, 'entry');
    assert.equal(rule.validationError, '');
  }
  const us = h.app.parseRule(values({ 2: 'US', 3: ' aapl ', 6: 0, 7: 100 }), 4, RECIPIENT);
  assert.equal(us.ticker, 'AAPL');
  assert.equal(us.upperPct, 0);
  assert.equal(us.lowerPct, 100);
  assert.equal(us.validationError, '');
});

test('parseRule rejects nonpositive/nonfinite targets, invalid percentages, markets, tickers, and modes', () => {
  const h = makeHarness();
  const invalid = [
    { 5: 0 }, { 5: -1 }, { 5: '' }, { 5: NaN }, { 5: Infinity }, { 5: 'not-a-price' },
    { 6: -0.01 }, { 6: 100.01 }, { 6: Infinity }, { 7: 'NaN' }, { 7: 101 },
    { 2: 'UK' }, { 3: '0059300' }, { 2: 'US', 3: 'AAPL/EVIL' }, { 8: 'hourly' }
  ];
  for (const change of invalid) {
    const result = h.app.parseRule(values(change), 2, RECIPIENT);
    assert.ok(result.validationError, `Expected invalid rule for ${JSON.stringify(change)}`);
  }
});

test('rule revision ignores display names, row position, IDs, and pause state but changes with alert conditions', () => {
  const h = makeHarness();
  const original = h.app.parseRule(values(), 2, RECIPIENT).revision;
  const renamed = h.app.parseRule(values({ 0: 'other-id', 1: false, 4: 'New display name', 5: '100.00' }), 9, RECIPIENT);
  assert.equal(renamed.revision, original);
  for (const change of [{ 3: '000660' }, { 5: 101 }, { 6: 6 }, { 7: 4 }, { 8: 'daily' }]) {
    assert.notEqual(h.app.parseRule(values(change), 2, RECIPIENT).revision, original);
  }
  assert.notEqual(h.app.parseRule(values(), 2, 'new@example.com').revision, original);
});

test('setup creates empty input sheets, a 30-minute clock and an edit trigger, and replaces its own schedules idempotently', () => {
  const h = makeHarness({ triggers: ['unrelatedFunction'] });
  const unrelated = h.triggers()[0];
  assert.equal(h.context.setup(), h.book.getUrl());
  assert.equal(h.book.zone, 'Asia/Seoul');
  assert.deepEqual([...h.sheets.keys()].sort(), ['관심종목', '설정', '알림기록'].sort());
  assert.equal(h.sheets.get('관심종목').getLastRow(), 1, 'No sample rules may be silently enabled');
  assert.equal(h.app.recipient(), RECIPIENT);
  const firstClock = h.triggers().find(item => item.handler === 'stockCheck');
  const firstEdit = h.triggers().find(item => item.handler === 'stockEdited');
  assert.equal(firstClock.type, 'clock');
  assert.equal(firstClock.minutes, 30);
  assert.equal(firstEdit.type, 'edit');
  assert.equal(firstEdit.book, h.book.getId());
  h.context.setup();
  assert.equal(h.triggers().filter(item => item.handler === 'stockCheck').length, 1);
  assert.equal(h.triggers().filter(item => item.handler === 'stockEdited').length, 1);
  assert.ok(h.triggers().includes(unrelated));
  assert.ok(h.events.deleted.includes(firstClock));
  assert.ok(h.events.deleted.includes(firstEdit));
  assert.deepEqual(h.events.fetch, [['KR', '005930'], ['KR', '005930']]);
  assert.equal(h.events.mail.length, 0, 'Setup must not send email');
  assert.equal(h.events.released, 2);
});

test('trigger creation failure rolls back new triggers while preserving the existing schedule', () => {
  for (const failure of [1, 2]) {
    const h = makeHarness({ triggers: ['stockCheck', 'stockEdited', 'unrelatedFunction'], failCreation: failure });
    withRuleSheets(h, []);
    const old = h.triggers();
    assert.throws(() => h.app.install(h.book), /simulated trigger creation failure/);
    assert.deepEqual(h.triggers(), old);
    for (const existing of old) assert.ok(!h.events.deleted.includes(existing));
    for (const created of h.events.created) assert.ok(h.events.deleted.includes(created));
  }
});

test('failed Naver readiness check leaves old triggers intact and releases the setup lock', () => {
  const h = makeHarness({ triggers: ['stockCheck', 'stockEdited'], fetchError: 'market data unavailable' });
  const old = h.triggers();
  assert.throws(() => h.context.setup(), /market data unavailable/);
  assert.deepEqual(h.triggers(), old);
  assert.equal(h.events.created.length, 0);
  assert.equal(h.events.released, 1);
});

test('busy setup performs no spreadsheet, market-data, or trigger writes', () => {
  const h = makeHarness({ lockAvailable: false });
  assert.throws(() => h.context.setup(), /다른 확인 작업/);
  assert.equal(h.sheets.size, 0);
  assert.equal(h.events.fetch.length, 0);
  assert.equal(h.events.created.length, 0);
  assert.equal(h.events.released, 0, 'An unacquired lock must not be released');
});

test('loadRules persists fresh IDs, repairs cloned IDs, skips empty rows, and keeps IDs stable on reload', () => {
  const h = makeHarness();
  const sheet = withRuleSheets(h, [
    values(),
    values({ 0: '', 2: 'US', 3: 'AAPL' }),
    values({ 0: 'stable-id', 1: false, 2: 'US', 3: 'IBM' }),
    Array(14).fill('')
  ]);
  const first = h.app.loadRules();
  assert.equal(first.length, 3);
  assert.equal(first[0].id, 'stable-id');
  assert.equal(first[1].id, 'fresh-id-1');
  assert.equal(first[2].id, 'fresh-id-2');
  assert.equal(first[2].enabled, false);
  assert.equal(new Set(first.map(rule => rule.id)).size, 3);
  assert.equal(sheet.values[2][0], first[1].id);
  assert.equal(sheet.values[3][0], first[2].id);
  const second = h.app.loadRules();
  assert.deepEqual(Array.from(second, rule => rule.id), Array.from(first, rule => rule.id));
});

test('loadRules refuses oversized sheets instead of unbounded spreadsheet reads', () => {
  const h = makeHarness();
  const sheet = withRuleSheets(h, [values()]);
  sheet.forcedLastRow = 1002;
  assert.throws(() => h.app.loadRules(), /1,000행/);
  assert.equal(h.events.fetch.length, 0);
});

test('the owner can explicitly send a test mail, and quota exhaustion prevents sending', () => {
  const h = makeHarness();
  h.context.setup();
  h.context.sendTestEmail();
  assert.equal(h.events.mail.length, 1);
  assert.equal(h.events.mail[0].to, RECIPIENT);
  assert.match(h.events.mail[0].subject, /Google 연결 테스트/);
  assert.equal(h.sheets.get('알림기록').getLastRow(), 2);
  const empty = makeHarness({ quota: 0 });
  empty.context.setup();
  assert.throws(() => empty.context.sendTestEmail(), /발송 한도/);
  assert.equal(empty.events.mail.length, 0);
});
