/** Persistent, at-most-once-biased email monitoring for Google Apps Script. */
var StockMonitor = (function () {
  'use strict';

  var PREFIX = 'STOCK_RULE_';
  var CURSOR = 'STOCK_MONITOR_CURSOR';
  var MAX_RULES = 100;
  var RUN_MILLISECONDS = 4 * 60 * 1000;

  function failure(message, status) {
    var error = new Error(message);
    error.monitorStatus = status || 'failed';
    return error;
  }

  function messageOf(error) {
    var value = error && error.message ? error.message : '종가 확인 중 오류가 발생했습니다.';
    return String(value).replace(/[\r\n]+/g, ' ').slice(0, 300);
  }

  function validDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    var parsed = new Date(value + 'T00:00:00Z');
    return !isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function finiteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  function validateRule(rule) {
    if (rule.validationError) throw failure(rule.validationError);
    if (rule.market !== 'KR' && rule.market !== 'US') throw failure('시장은 KR 또는 US여야 합니다.');
    if (typeof rule.ticker !== 'string' || !(rule.market === 'KR' ? /^\d{6}$/ : /^[A-Z][A-Z0-9.\-]{0,19}$/).test(rule.ticker)) {
      throw failure('종목 티커를 확인하세요.');
    }
    if (!finiteNumber(rule.targetPrice) || rule.targetPrice <= 0 || rule.targetPrice > 1e12 ||
        !finiteNumber(rule.upperPct) || rule.upperPct < 0 || rule.upperPct > 1000 ||
        !finiteNumber(rule.lowerPct) || rule.lowerPct < 0 || rule.lowerPct > 100) {
      throw failure('목표가와 위·아래 허용 비율을 확인하세요.');
    }
    if (rule.mode !== 'entry' && rule.mode !== 'daily') throw failure('알림 방식은 entry 또는 daily여야 합니다.');
    if (rule.revision === undefined || rule.revision === null || String(rule.revision).length === 0) {
      throw failure('규칙 버전 정보가 없습니다. 설정을 다시 저장하세요.');
    }
  }

  function validateQuote(rule, quote) {
    if (!quote || quote.market !== rule.market || !finiteNumber(quote.close) || quote.close <= 0 ||
        quote.currency !== (rule.market === 'KR' ? 'KRW' : 'USD') || !validDate(quote.tradeDate) ||
        quote.tradeDate > new Date().toISOString().slice(0, 10) || typeof quote.sourceUrl !== 'string' ||
        !/^https:\/\//.test(quote.sourceUrl) || /[\r\n]/.test(quote.sourceUrl)) {
      throw failure('확정 종가·거래일 정보를 검증하지 못했습니다. 다음 확인에서 다시 조회합니다.');
    }
  }

  function matches(rule, price) {
    var lower = rule.targetPrice * (1 - rule.lowerPct / 100);
    var upper = rule.targetPrice * (1 + rule.upperPct / 100);
    // The input/quote precision is six decimals; only absorb binary arithmetic noise.
    var tolerance = Math.max(1, Math.abs(lower), Math.abs(upper), Math.abs(price)) * 2.220446049250313e-16 * 4;
    return lower - tolerance <= price && price <= upper + tolerance;
  }

  function initialState(rule) {
    return { revision: String(rule.revision), evaluatedDate: null, wasInRange: null,
      lastSentDate: null, pendingDate: null, uncertain: false };
  }

  function readState(properties, rule) {
    var raw = properties.getProperty(PREFIX + rule.id);
    if (raw === null || raw === '') return initialState(rule);
    var state;
    try {
      state = JSON.parse(raw);
      if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error();
      if ((typeof state.revision !== 'string' && typeof state.revision !== 'number') || String(state.revision).length === 0) throw new Error();
      if (String(state.revision) !== String(rule.revision)) return initialState(rule);
      if (![null, true, false].includes(state.wasInRange) || typeof state.uncertain !== 'boolean') throw new Error();
      ['evaluatedDate', 'lastSentDate', 'pendingDate'].forEach(function (field) {
        if (state[field] !== null && !validDate(state[field])) throw new Error();
      });
    } catch (error) {
      throw failure('저장된 알림 상태를 읽지 못했습니다. 설정을 확인한 후 다시 저장하세요.');
    }
    return state;
  }

  function writeState(properties, rule, state) {
    var key = PREFIX + rule.id;
    var value = JSON.stringify(state);
    if (properties.getProperty(key) !== value) properties.setProperty(key, value);
  }

  function logError(rule, quote, error) {
    var message = messageOf(error);
    try { StockApp.updateQuote(rule, quote || null, message); } catch (ignored) { /* preserve the original error */ }
    try {
      StockApp.appendLog({ ticker: rule.ticker || ('행 ' + rule.row), tradeDate: quote ? quote.tradeDate : '',
        price: quote ? quote.close : '', status: error.monitorStatus || 'failed', message: message });
    } catch (ignored) { /* The return value still reports this rule's failure. */ }
  }

  function emailBody(rule, quote) {
    var name = quote.name || rule.name || rule.ticker;
    var difference = ((quote.close / rule.targetPrice - 1) * 100).toFixed(2);
    return name + ' (' + rule.ticker + ') 종가가 목표 범위에 들어왔습니다.\n\n' +
      '거래일: ' + quote.tradeDate + '\n' +
      '정규장 종가: ' + quote.close + ' ' + quote.currency + '\n' +
      '목표가: ' + rule.targetPrice + ' ' + quote.currency + '\n' +
      '허용 비율: -' + rule.lowerPct + '% ~ +' + rule.upperPct + '%\n' +
      '목표가 대비: ' + (Number(difference) >= 0 ? '+' : '') + difference + '%\n' +
      '출처: ' + quote.sourceUrl + '\n\n' +
      'Close Watch · 네이버증권 종가 알림';
  }

  function currentRule(rule, quote) {
    if (typeof StockApp.isCurrent !== 'function' || StockApp.isCurrent(rule)) return true;
    StockApp.appendLog({ ticker: rule.ticker, tradeDate: quote.tradeDate, price: quote.close,
      status: 'skipped', message: '조회 중 종목 설정이 변경되거나 비활성화되어 이전 설정의 처리를 생략했습니다.' });
    return false;
  }

  function check(options) {
    if (options === undefined) options = {};
    if (!options || typeof options !== 'object' || Array.isArray(options) ||
        (Object.prototype.hasOwnProperty.call(options, 'send') && typeof options.send !== 'boolean')) {
      throw failure('send 옵션은 true 또는 false여야 합니다.');
    }
    var send = options.send !== false;
    StockApp.assertOwner();
    var lock = LockService.getScriptLock();
    var totals = { checked: 0, sent: 0, errors: 0 };
    if (!lock.tryLock(1000)) return totals;
    try {
      var deadline = Date.now() + RUN_MILLISECONDS;
      var rules = StockApp.loadRules();
      if (!Array.isArray(rules) || rules.length > MAX_RULES) throw failure('관심 종목은 최대 100개까지 등록하세요.');
      var ids = Object.create(null);
      rules.forEach(function (rule) {
        if (!rule || !/^[A-Za-z0-9_-]{1,100}$/.test(String(rule.id || '')) || ids[String(rule.id)]) {
          throw failure('종목 규칙 ID가 없거나 중복됩니다. 설정 시트를 확인하세요.');
        }
        ids[String(rule.id)] = true;
      });
      var properties = PropertiesService.getScriptProperties();
      var recipient = send ? String(StockApp.recipient()).trim() : '';
      if (send && !/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(recipient)) {
        throw failure('수신 이메일 주소 하나를 정확하게 입력하세요.');
      }
      var cache = Object.create(null);
      var cursor = send ? properties.getProperty(CURSOR) : null;
      var startIndex = rules.findIndex(function (candidate) { return String(candidate.id) === cursor; });
      if (startIndex < 0) startIndex = 0;
      var truncated = false;
      for (var offset = 0; offset < rules.length; offset += 1) {
        var index = (startIndex + offset) % rules.length;
        var rule = rules[index];
        if (!rule.enabled) continue;
        if (Date.now() >= deadline) {
          if (send) properties.setProperty(CURSOR, String(rule.id));
          truncated = true;
          totals.errors += 1;
          logError(rule, null, failure('실행 시간 제한에 가까워 확인을 중지했습니다. 남은 종목은 다음 자동 확인에서 처리합니다.'));
          break;
        }
        totals.checked += 1;
        var quote = null;
        var mailAccepted = false;
        try {
          validateRule(rule);
          var key = rule.market + ':' + rule.ticker;
          if (!Object.prototype.hasOwnProperty.call(cache, key)) {
            try { cache[key] = { quote: StockNaver.fetchClose(rule.market, rule.ticker) }; }
            catch (error) { cache[key] = { error: error }; }
          }
          if (cache[key].error) throw cache[key].error;
          quote = cache[key].quote;
          validateQuote(rule, quote);
          if (!currentRule(rule, quote)) continue;
          StockApp.updateQuote(rule, quote, null);
          if (!send) continue;
          var state = readState(properties, rule);
          if (state.pendingDate || state.uncertain) {
            throw failure('이전 메일의 접수 여부가 불명확하여 자동 재발송을 중지했습니다. 수신함과 알림 기록을 확인하세요.', 'uncertain');
          }
          if (state.evaluatedDate && quote.tradeDate < state.evaluatedDate) continue;
          var matched = matches(rule, quote.close);
          var shouldSend = matched && state.lastSentDate !== quote.tradeDate &&
            (rule.mode === 'daily' || state.wasInRange !== true);
          if (!shouldSend) {
            state.evaluatedDate = quote.tradeDate;
            state.wasInRange = matched;
            writeState(properties, rule, state);
            continue;
          }
          var quota = MailApp.getRemainingDailyQuota();
          if (!finiteNumber(quota) || quota < 1) {
            throw failure('오늘의 Google 메일 발송 한도가 부족합니다. 한도가 회복되면 다음 확인에서 다시 시도합니다.');
          }
          if (!currentRule(rule, quote)) continue;
          // A process interruption after this write must never silently resend the same email.
          state.pendingDate = quote.tradeDate;
          writeState(properties, rule, state);
          try {
            MailApp.sendEmail({ to: recipient,
              subject: '[종가 알림] ' + rule.ticker + ' · ' + quote.tradeDate + ' · 목표 범위 진입',
              body: emailBody(rule, quote), name: 'Close Watch' });
          } catch (error) {
            state.uncertain = true;
            try { writeState(properties, rule, state); } catch (ignored) { /* the durable pending marker remains */ }
            throw failure('메일 접수 여부를 확인할 수 없어 자동 재발송을 중지했습니다. 수신함과 알림 기록을 확인하세요.', 'uncertain');
          }
          mailAccepted = true;
          totals.sent += 1;
          state.lastSentDate = quote.tradeDate;
          state.evaluatedDate = quote.tradeDate;
          state.wasInRange = true;
          state.pendingDate = null;
          state.uncertain = false;
          writeState(properties, rule, state);
          StockApp.appendLog({ ticker: rule.ticker, tradeDate: quote.tradeDate, price: quote.close,
            status: 'sent', message: 'Google 메일 서비스가 알림을 접수했습니다. 수신함을 확인하세요.' });
        } catch (error) {
          totals.errors += 1;
          if (mailAccepted) error = failure('메일 서비스는 알림을 접수했으나 처리 기록 저장에 실패했습니다. 이전 발송 기록을 확인하세요.', 'uncertain');
          logError(rule, quote, error);
        }
      }
      if (send && !truncated && cursor !== null) properties.deleteProperty(CURSOR);
      return totals;
    } finally {
      lock.releaseLock();
    }
  }

  return { check: check };
})();
