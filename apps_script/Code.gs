/** Google Sheets setup and the public functions shown in the Apps Script editor. */
var StockApp = (function () {
  'use strict';
  var OWNER = 'royhan88888@gmail.com';
  var DEFAULT_RECIPIENT = 'ykhan@dacpole.com';
  var HEADERS = ['관리 ID', '감시', '시장', '종목 티커', '종목명', '목표가', '+ %', '- %', '알림 방식', '최근 종가', '종가 거래일', '상태', '마지막 확인 (한국)', '오류 / 안내'];
  var RULES = '관심종목', SETTINGS = '설정', LOG = '알림기록';
  function props() { return PropertiesService.getScriptProperties(); }
  function assertOwner() {
    var actual = String(Session.getEffectiveUser().getEmail() || '').toLowerCase();
    if (actual !== OWNER) throw new Error(OWNER + ' 계정으로 로그인하여 실행하세요. 다른 계정에서는 메일 발송과 자동 실행을 시작하지 않습니다.');
  }
  function book() {
    var id = props().getProperty('SPREADSHEET_ID');
    if (!id) throw new Error('먼저 setup 함수를 한 번 실행하세요.');
    return SpreadsheetApp.openById(id);
  }
  function sheet(name) {
    var result = book().getSheetByName(name);
    if (!result) throw new Error(name + ' 시트가 없습니다. setup을 다시 실행하세요.');
    return result;
  }
  function safeText(value) {
    var s = String(value == null ? '' : value);
    return /^[=+\-@]/.test(s) ? "'" + s : s;
  }
  function setting(key, value) {
    var s = sheet(SETTINGS), n = s.getLastRow();
    var rows = n > 1 ? s.getRange(2, 1, n - 1, 2).getValues() : [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i][0] === key) {
        if (arguments.length > 1) s.getRange(i + 2, 2).setValue(safeText(value));
        return arguments.length > 1 ? value : rows[i][1];
      }
    }
    if (arguments.length > 1) { s.appendRow([key, safeText(value)]); return value; }
    return '';
  }
  function recipient() {
    var address = String(setting('수신 이메일') || DEFAULT_RECIPIENT).trim();
    if (!/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(address)) throw new Error('설정 시트의 수신 이메일에 이메일 주소 하나를 입력하세요.');
    return address;
  }
  function parseRule(values, row, to) {
    function number(value) { return typeof value === 'number' || typeof value === 'string' ? Number(value) : NaN; }
    var market = String(values[2]).trim().toUpperCase();
    var ticker = String(values[3]).trim().toUpperCase();
    if (market === 'KR' && /^\d{1,6}$/.test(ticker)) ticker = ('000000' + ticker).slice(-6);
    var r = {id: String(values[0]), enabled: values[1] === true || String(values[1]).toUpperCase() === 'TRUE',
      market: market, ticker: ticker, name: String(values[4] || '').trim(), targetPrice: number(values[5]),
      upperPct: values[6] === '' ? 5 : number(values[6]), lowerPct: values[7] === '' ? 5 : number(values[7]),
      mode: String(values[8] || 'entry').trim(), row: row};
    var error = '';
    if (market !== 'KR' && market !== 'US') error = '시장은 KR 또는 US를 선택하세요.';
    else if (market === 'KR' ? !/^\d{6}$/.test(ticker) : !/^[A-Z][A-Z0-9.\-]{0,19}$/.test(ticker)) error = '올바른 종목 티커를 입력하세요.';
    else if (!Number.isFinite(r.targetPrice) || r.targetPrice <= 0 || r.targetPrice > 1e12) error = '목표가는 0 초과 1조 이하 숫자여야 합니다.';
    else if (![r.upperPct, r.lowerPct].every(function (v) { return Number.isFinite(v) && v >= 0 && v <= 100; })) error = '+ %, - %는 0~100 사이 숫자로 입력하세요.';
    else if (r.mode !== 'entry' && r.mode !== 'daily') error = '알림 방식은 entry 또는 daily를 선택하세요.';
    r.validationError = error;
    r.revision = JSON.stringify([market, ticker, r.targetPrice, r.upperPct, r.lowerPct, r.mode, to]);
    return r;
  }
  function loadRules() {
    var s = sheet(RULES), n = s.getLastRow(), seen = {}, to = recipient();
    if (n <= 1) return [];
    // Bound the read even if someone accidentally formats an entire sheet.
    if (n > 1001) throw new Error('관심종목은 1,000행 이내로 정리하세요. 동시에 감시할 수 있는 종목은 최대 100개입니다.');
    var rows = s.getRange(2, 1, n - 1, HEADERS.length).getValues(), result = [];
    rows.forEach(function (values, index) {
      if (!String(values[3] || '').trim() && values[1] !== true) return;
      if (!values[0] || seen[values[0]]) {
        values[0] = Utilities.getUuid();
        s.getRange(index + 2, 1).setValue(values[0]);
      }
      seen[values[0]] = true;
      result.push(parseRule(values, index + 2, to));
    });
    return result;
  }
  function findRow(rule) {
    var s = sheet(RULES), n = s.getLastRow();
    if (n < 2) return null;
    var ids = s.getRange(2, 1, n - 1, 1).getValues();
    for (var i = 0; i < ids.length; i++) if (String(ids[i][0]) === rule.id) {
      // Never write an old quote onto a ticker changed while the HTTP request ran.
      var now = parseRule(s.getRange(i + 2, 1, 1, 14).getValues()[0], i + 2, recipient());
      return now.revision === rule.revision && now.enabled === rule.enabled ? i + 2 : null;
    }
    return null;
  }
  function updateQuote(rule, quote, error) {
    var row = findRow(rule);
    if (!row) return;
    var s = sheet(RULES), when = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss');
    if (quote) {
      if (quote.name) s.getRange(row, 5).setValue(safeText(quote.name));
      var inside = quote.close >= rule.targetPrice * (1 - rule.lowerPct / 100) && quote.close <= rule.targetPrice * (1 + rule.upperPct / 100);
      s.getRange(row, 10, 1, 5).setValues([[quote.close, quote.tradeDate, inside ? '범위 안' : '범위 밖', when, safeText(error || '')]]);
    } else s.getRange(row, 12, 1, 3).setValues([['오류', when, safeText(error || '조회 실패')]]);
  }
  function appendLog(entry) {
    var s = sheet(LOG);
    s.appendRow([Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss'), safeText(entry.ticker),
      safeText(entry.tradeDate), entry.price == null ? '' : entry.price, safeText(entry.status), safeText(entry.message), safeText(entry.messageId)]);
    // Keep operational history bounded; alert deduplication lives separately in Script Properties.
    if (s.getLastRow() > 2001) s.deleteRows(2, s.getLastRow() - 2001);
  }
  function header(s, labels) {
    s.getRange(1, 1, 1, labels.length).setValues([labels]).setBackground('#15334d').setFontColor('#ffffff').setFontWeight('bold');
    s.setFrozenRows(1);
    s.getRange(1, 1, s.getMaxRows(), labels.length).setFontFamily('Arial').setVerticalAlignment('middle');
    s.setRowHeight(1, 34);
  }
  function ensureBook() {
    var active = SpreadsheetApp.getActiveSpreadsheet();
    var id = props().getProperty('SPREADSHEET_ID');
    var ss = id ? SpreadsheetApp.openById(id) : active || SpreadsheetApp.create('네이버 종가 알림 · 30분 자동 감시');
    props().setProperty('SPREADSHEET_ID', ss.getId());
    ss.setSpreadsheetTimeZone('Asia/Seoul');
    var r = ss.getSheetByName(RULES) || ss.insertSheet(RULES);
    var config = ss.getSheetByName(SETTINGS) || ss.insertSheet(SETTINGS);
    var log = ss.getSheetByName(LOG) || ss.insertSheet(LOG);
    header(r, HEADERS); header(config, ['설정 항목', '값', '설명']);
    header(log, ['확인 시각 (한국)', '티커', '종가 거래일', '종가', '결과', '안내', '처리 ID']);
    if (config.getLastRow() === 1) config.getRange(2, 1, 8, 3).setValues([
      ['수신 이메일', DEFAULT_RECIPIENT, '조건에 맞는 종가 알림을 받을 주소 한 개'],
      ['발신 계정', OWNER, '이 계정으로 Google 권한을 승인해야 합니다. 앱 비밀번호는 필요 없습니다.'],
      ['확인 주기', '30분', 'Google 서버가 실행합니다. 실제 시각은 지연될 수 있습니다.'],
      ['자동 감시', '설정 전', 'setup 실행이 완료되면 활성 상태가 됩니다.'],
      ['입력 방법', '관심종목 시트 2행부터', 'KR/US, 티커, 목표가, +%, -%, entry/daily를 입력하고 감시를 체크하세요.'],
      ['종가 기준', '확정된 정규장 종가', '장중 현재가는 사용하지 않습니다. 휴장일에는 마지막 거래일 종가를 확인합니다.'],
      ['알림 방식', 'entry / daily', 'entry: 범위 밖→안 진입 / daily: 범위 안이면 거래일마다 1회'],
      ['확인 결과', '아직 확인하지 않음', '중복 방지 상태는 Google Script Properties에 저장됩니다.']]);
    var count = Math.min(r.getMaxRows() - 1, 1000);
    r.getRange(2, 2, count, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().build());
    r.getRange(2, 3, count, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(['KR', 'US'], true).setAllowInvalid(false).build());
    r.getRange(2, 4, count, 1).setNumberFormat('@');
    r.getRange(2, 9, count, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(['entry', 'daily'], true).setAllowInvalid(false).build());
    r.getRange(2, 6, count, 3).setNumberFormat('#,##0.########');
    r.getRange(2, 10, count, 1).setNumberFormat('#,##0.########');
    r.getRange(1, 2, 1, 8).setNotes([['모든 조건을 입력한 뒤 체크하면 감시합니다.', '한국 KR / 미국 US', '한국 005930 / 미국 AAPL, IBM, BRK.B 등. 입력하면 종목명을 조회합니다.', '티커 변경 시 네이버 종목명으로 갱신됩니다.', '한국 KRW / 미국 USD', '예: 5는 목표가보다 5% 위까지 포함', '예: 5는 목표가보다 5% 아래까지 포함', 'entry: 진입 시 / daily: 거래일마다']]);
    r.hideColumns(1); r.setColumnWidth(2, 60); r.setColumnWidths(3, 2, 110); r.setColumnWidth(5, 180);
    r.setColumnWidths(6, 6, 115); r.setColumnWidth(12, 100); r.setColumnWidth(13, 180); r.setColumnWidth(14, 380);
    config.setColumnWidth(1, 140); config.setColumnWidth(2, 300); config.setColumnWidth(3, 660);
    config.getRange(1, 1, config.getMaxRows(), 3).setWrap(true);
    log.setColumnWidth(1, 180); log.setColumnWidths(2, 4, 110); log.setColumnWidth(6, 480); log.setColumnWidth(7, 280);
    return ss;
  }
  function install(ss) {
    // Create both replacements first; a failed creation leaves the prior schedule intact.
    var old = ScriptApp.getProjectTriggers().filter(function (t) { return ['stockCheck', 'stockEdited'].indexOf(t.getHandlerFunction()) >= 0; });
    var created = [];
    try {
      created.push(ScriptApp.newTrigger('stockCheck').timeBased().everyMinutes(30).create());
      created.push(ScriptApp.newTrigger('stockEdited').forSpreadsheet(ss).onEdit().create());
    } catch (e) { created.forEach(function (t) { ScriptApp.deleteTrigger(t); }); throw e; }
    old.forEach(function (t) { ScriptApp.deleteTrigger(t); });
    setting('자동 감시', '활성 · 30분마다 · ' + OWNER);
  }
  function setup() {
    assertOwner();
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) throw new Error('다른 확인 작업이 실행 중입니다. 잠시 후 다시 시도하세요.');
    try {
      var ss = ensureBook();
      recipient();
      // Prove Naver is reachable from Google before enabling an automatic schedule.
      StockNaver.fetchClose('KR', '005930');
      install(ss);
      console.log('설정 완료: ' + ss.getUrl());
      return ss.getUrl();
    } finally { lock.releaseLock(); }
  }
  function recordResult(result) {
    setting('확인 결과', Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss') +
      ' · 확인 ' + result.checked + ' · 발송 ' + result.sent + ' · 오류 ' + result.errors);
    return result;
  }
  function edited(e) {
    assertOwner();
    if (!e || !e.range || e.range.getSheet().getName() !== RULES || e.range.getSheet().getParent().getId() !== book().getId()) return;
    if (e.range.getRow() < 2 || e.range.getColumn() > 4 || e.range.getLastColumn() < 3) return;
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(1000)) return; // The next scheduled check also resolves missing names.
    try {
      var s = e.range.getSheet(), end = Math.min(e.range.getLastRow(), e.range.getRow() + 19), to = recipient();
      for (var row = e.range.getRow(); row <= end; row++) {
        var values = s.getRange(row, 1, 1, 14).getValues()[0];
        if (!String(values[3] || '').trim()) { s.getRange(row, 5).clearContent(); continue; }
        if (!values[0]) { values[0] = Utilities.getUuid(); s.getRange(row, 1).setValue(values[0]); }
        var rule = parseRule(values, row, to);
        s.getRange(row, 5).clearContent();
        s.getRange(row, 10, 1, 5).clearContent();
        try {
          var stock = StockNaver.lookup(rule.market, rule.ticker);
          s.getRange(row, 4).setValue(safeText(rule.ticker));
          s.getRange(row, 5).setValue(safeText(stock.name));
        } catch (error) { s.getRange(row, 14).setValue(safeText(error.message)); }
      }
    } finally { lock.releaseLock(); }
  }
  return {assertOwner: assertOwner, loadRules: loadRules, recipient: recipient, updateQuote: updateQuote,
    appendLog: appendLog, setup: setup, edited: edited, recordResult: recordResult,
    setting: setting, parseRule: parseRule, install: install,
    isCurrent: function (rule) { return findRow(rule) !== null; }};
})();

function setup() { return StockApp.setup(); }
function stockCheck() {
  try { return StockApp.recordResult(StockMonitor.check({send: true})); }
  catch (error) { console.error(error.message); throw error; }
}
function stockCheckOnly() { return StockApp.recordResult(StockMonitor.check({send: false})); }
function stockEdited(e) { return StockApp.edited(e); }
function sendTestEmail() {
  StockApp.assertOwner();
  if (MailApp.getRemainingDailyQuota() < 1) throw new Error('오늘의 이메일 발송 한도를 모두 사용했습니다.');
  MailApp.sendEmail({to: StockApp.recipient(), subject: '[네이버 종가 알림] Google 연결 테스트',
    body: 'Google Apps Script 메일 연결 테스트입니다.\n발신 계정: royhan88888@gmail.com\n자동 확인 주기: 30분\n종목과 목표가를 입력하고 감시를 체크하면 확정된 정규장 종가를 확인합니다.', name: '네이버 종가 알림'});
  StockApp.appendLog({status: 'test', message: '테스트메일을 Google 메일 서비스가 접수했습니다.'});
}
function stopMonitoring() {
  StockApp.assertOwner();
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) throw new Error('확인 작업이 실행 중입니다. 잠시 후 다시 시도하세요.');
  try {
    ScriptApp.getProjectTriggers().filter(function (t) { return t.getHandlerFunction() === 'stockCheck'; })
      .forEach(function (t) { ScriptApp.deleteTrigger(t); });
    StockApp.setting('자동 감시', '중지');
  } finally { lock.releaseLock(); }
}
function onOpen() {
  // Menus are available when installed through Sheets > Extensions > Apps Script.
  try {
    SpreadsheetApp.getUi().createMenu('주가 알림').addItem('30분 자동 감시 시작 / 재설정', 'setup')
      .addItem('지금 확인하고 알림', 'stockCheck').addItem('종가만 확인 (메일 없음)', 'stockCheckOnly')
      .addItem('테스트메일 보내기', 'sendTestEmail').addSeparator().addItem('자동 감시 중지', 'stopMonitoring').addToUi();
  } catch (e) { console.log('독립 프로젝트에서는 Apps Script 편집기에서 함수를 실행하세요.'); }
}
