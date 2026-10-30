"use strict";

const $ = (id) => document.getElementById(id);
let snapshot = { rules: [], settings: {}, recent_alerts: [] };
let checking = false;
let noticeTimer;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function announce(message, type = "success", persist = false) {
  clearTimeout(noticeTimer);
  const notice = $("notice");
  notice.textContent = message;
  notice.className = `notice ${type}`;
  notice.hidden = false;
  if (!persist) noticeTimer = setTimeout(() => { notice.hidden = true; }, 10000);
}

async function api(path, options = {}) {
  const mutating = options.method && !["GET", "HEAD"].includes(options.method.toUpperCase());
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(mutating ? { "X-Stock-Alert": "1" } : {}), ...options.headers },
    credentials: "same-origin",
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`서버 응답을 읽을 수 없습니다 (${response.status}).`); }
  if (!response.ok) throw new Error(data.error || data.detail || data.message || `요청 실패 (${response.status})`);
  return data;
}

function money(value, market) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency", currency: market === "US" ? "USD" : "KRW",
    minimumFractionDigits: market === "US" ? 2 : 0, maximumFractionDigits: market === "US" ? 4 : 2,
  }).format(number);
}

function dateTime(value) {
  if (!value) return "기록 없음";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function market() { return document.querySelector('input[name="market"]:checked').value; }

function updateMarket() {
  const kr = market() === "KR";
  $("ticker").placeholder = kr ? "005930" : "AAPL";
  $("ticker-hint").textContent = kr ? "한국 종목은 6자리 코드 입력 · 예: 005930" : "미국 티커 입력 · 예: AAPL, MSFT, IBM";
  $("name").placeholder = kr ? "예: 삼성전자" : "예: Apple";
  $("target-price").placeholder = kr ? "70000" : "200.00";
  $("currency-unit").textContent = kr ? "원" : "달러";
  updateRange();
}

function updateRange() {
  const target = Number($("target-price").value);
  const lower = Number($("lower-pct").value);
  const upper = Number($("upper-pct").value);
  if (!target || target <= 0 || !Number.isFinite(target) || !Number.isFinite(lower) || !Number.isFinite(upper) || lower < 0 || lower > 100 || upper < 0) {
    $("range-preview").textContent = "목표 가격을 입력해 주세요";
    return;
  }
  $("range-preview").textContent = `${money(target * (1 - lower / 100), market())} ~ ${money(target * (1 + upper / 100), market())}`;
}

function resetEditor() {
  $("rule-form").reset();
  $("rule-id").value = "";
  $("editor-title").textContent = "새 알림 등록";
  $("save-rule").replaceChildren(document.createTextNode("알림 등록하기 "), el("span", "", "＋"));
  $("cancel-edit").hidden = true;
  updateMarket();
}

function editRule(rule) {
  $("rule-id").value = rule.id;
  document.querySelector(`input[name="market"][value="${rule.market === "US" ? "US" : "KR"}"]`).checked = true;
  $("ticker").value = rule.ticker;
  $("name").value = rule.name || "";
  $("target-price").value = rule.target_price;
  $("lower-pct").value = rule.lower_pct;
  $("upper-pct").value = rule.upper_pct;
  $("mode").value = rule.mode === "daily" ? "daily" : "entry";
  $("editor-title").textContent = "알림 수정";
  $("save-rule").textContent = "변경 내용 저장";
  $("cancel-edit").hidden = false;
  updateMarket();
  $("editor-title").scrollIntoView({ behavior: "smooth", block: "center" });
  $("ticker").focus({ preventScroll: true });
}

function rulePayload(rule) {
  return {
    market: rule.market, ticker: rule.ticker, name: rule.name || "",
    target_price: rule.target_price, upper_pct: rule.upper_pct, lower_pct: rule.lower_pct,
    mode: rule.mode, enabled: Boolean(rule.enabled),
  };
}

async function toggleRule(rule, button) {
  button.disabled = true;
  try {
    await api(`/api/rules/${encodeURIComponent(rule.id)}`, { method: "PUT", body: JSON.stringify({ ...rulePayload(rule), enabled: !rule.enabled }) });
    await refresh();
    announce(`${rule.ticker} 알림을 ${rule.enabled ? "일시정지했습니다" : "다시 시작했습니다"}.`);
  } catch (error) { announce(error.message, "error", true); }
  finally { button.disabled = false; }
}

async function deleteRule(rule, button) {
  if (!window.confirm(`${rule.name || rule.ticker} (${rule.ticker}) 알림을 삭제할까요?`)) return;
  button.disabled = true;
  try {
    await api(`/api/rules/${encodeURIComponent(rule.id)}`, { method: "DELETE" });
    if (String($("rule-id").value) === String(rule.id)) resetEditor();
    await refresh();
    announce(`${rule.ticker} 알림을 삭제했습니다.`);
  } catch (error) { announce(error.message, "error", true); }
  finally { button.disabled = false; }
}

function renderRule(rule) {
  const card = el("article", `rule-item${!rule.enabled ? " paused" : ""}${rule.in_range ? " in-range" : ""}`);
  const top = el("div", "rule-top");
  const stock = el("div", "rule-stock");
  stock.append(el("span", "market-icon", rule.market));
  const identity = el("div");
  identity.append(el("h3", "", rule.name || rule.ticker), el("p", "ticker-sub", `${rule.ticker} · ${rule.market === "US" ? "미국 · USD" : "한국 · KRW"}`));
  stock.append(identity);
  const status = !rule.enabled ? ["neutral", "일시정지"] : rule.last_error ? ["error", "확인 오류"] : rule.in_range ? ["success", "범위 안"] : rule.last_price ? ["waiting", "범위 대기"] : ["neutral", "확인 전"];
  top.append(stock, el("span", `badge ${status[0]}`, status[1]));
  const prices = el("div", "rule-prices");
  const close = el("div");
  close.append(el("small", "", "최근 확인 종가"), el("strong", "", money(rule.last_price, rule.market)));
  const target = el("div");
  target.append(el("small", "", "목표 가격"), el("strong", "target-price", money(rule.target_price, rule.market)));
  prices.append(close, target);
  const targetValue = Number(rule.target_price);
  const range = el("p", "rule-range");
  range.append(document.createTextNode("알림 범위  "), el("strong", "", `${money(targetValue * (1 - Number(rule.lower_pct) / 100), rule.market)} ~ ${money(targetValue * (1 + Number(rule.upper_pct) / 100), rule.market)}`));
  const meta = el("p", "rule-meta", `${rule.last_date ? `거래일 ${rule.last_date}` : "거래일 확인 전"} · ${rule.last_checked ? `확인 ${dateTime(rule.last_checked)}` : "아직 확인하지 않았습니다"}`);
  const bottom = el("div", "rule-bottom");
  const actions = el("div", "row-actions");
  const edit = el("button", "text-button", "수정");
  edit.type = "button"; edit.addEventListener("click", () => editRule(rule));
  const toggle = el("button", "text-button", rule.enabled ? "일시정지" : "알림 재개");
  toggle.type = "button"; toggle.addEventListener("click", () => toggleRule(rule, toggle));
  const remove = el("button", "text-button danger", "삭제");
  remove.type = "button"; remove.addEventListener("click", () => deleteRule(rule, remove));
  actions.append(edit, toggle, remove);
  bottom.append(el("span", "", rule.mode === "daily" ? "거래일마다 알림" : "범위 진입 시 1회"), actions);
  card.append(top, prices, range, meta);
  if (rule.last_error) card.append(el("p", "rule-error", rule.last_error));
  card.append(bottom);
  return card;
}

function renderHistory(records) {
  $("history-empty").hidden = records.length > 0;
  $("history-table").hidden = records.length === 0;
  $("history-body").replaceChildren();
  for (const record of records) {
    const row = el("tr");
    const rule = snapshot.rules.find((item) => item.ticker === record.ticker);
    const recordMarket = record.market || rule?.market;
    const price = record.price === undefined || record.price === null ? "—" : recordMarket ? money(record.price, recordMarket) : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 6 }).format(Number(record.price));
    row.append(el("td", "", record.ticker || "—"), el("td", "", record.trade_date || "—"), el("td", "", price));
    const status = el("td");
    const sent = ["sent", "success", "delivered"].includes(record.status);
    const uncertain = ["uncertain", "pending"].includes(record.status);
    const badge = el("span", `badge ${sent ? "success" : uncertain ? "waiting" : "error"}`, sent ? "메일 서버 접수" : uncertain ? "접수 확인 필요" : "발송 실패");
    if (uncertain) badge.title = "메일 접수 여부를 확인해야 합니다. 자동 재발송은 차단됩니다.";
    if (record.error) badge.title = `${badge.title ? badge.title + " " : ""}${record.error}`;
    status.append(badge);
    row.append(status, el("td", "", dateTime(record.created_at)));
    $("history-body").append(row);
  }
}

function renderSettings(settings) {
  const mapping = { recipient: "recipient", smtp_host: "smtp-host", smtp_port: "smtp-port", smtp_security: "smtp-security", smtp_username: "smtp-username", smtp_from: "smtp-from", interval_minutes: "interval-minutes" };
  for (const [key, id] of Object.entries(mapping)) {
    if (settings[key] !== undefined && document.activeElement !== $(id)) $(id).value = settings[key];
  }
  $("recipient-summary").textContent = settings.recipient || "ykhan@dacpole.com";
  const configured = Boolean(settings.smtp_password_configured && settings.smtp_host && settings.smtp_from);
  $("smtp-badge").className = `badge ${configured ? "success" : "neutral"}`;
  $("smtp-badge").textContent = configured ? "메일 발송 설정됨" : "메일 설정 필요";
}

async function refresh(updateSettings = true) {
  const data = await api("/api/rules");
  snapshot = { rules: data.rules || [], settings: data.settings || {}, recent_alerts: data.recent_alerts || [] };
  $("total-count").replaceChildren(document.createTextNode(snapshot.rules.length), el("span", "", "개"));
  $("active-count").replaceChildren(document.createTextNode(snapshot.rules.filter((r) => r.enabled).length), el("span", "", "개"));
  $("range-count").replaceChildren(document.createTextNode(snapshot.rules.filter((r) => r.enabled && r.in_range).length), el("span", "", "개"));
  $("list-count").textContent = snapshot.rules.length;
  $("rules-list").replaceChildren(...snapshot.rules.map(renderRule));
  $("rules-empty").hidden = snapshot.rules.length > 0;
  if (updateSettings) renderSettings(snapshot.settings);
  renderHistory(snapshot.recent_alerts);
}

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    const active = Boolean(status.scheduler_running);
    $("scheduler-dot").className = `status-dot${active ? " active" : ""}`;
    $("scheduler-label").textContent = status.checking ? "종가 확인 중" : active ? "자동 확인 실행 중" : "자동 확인 대기";
    if (status.last_check) $("last-check").textContent = `마지막 확인 · ${dateTime(status.last_check)}`;
  } catch {
    $("scheduler-dot").className = "status-dot error";
    $("scheduler-label").textContent = "서버 연결 확인 필요";
  }
}

async function check(send) {
  if (checking) return;
  checking = true;
  $("check-only").disabled = true;
  $("check-send").disabled = true;
  const button = send ? $("check-send") : $("check-only");
  const original = button.textContent;
  button.textContent = "확인 중…";
  announce(send ? "완료된 거래일의 종가를 확인하고 알림을 처리합니다." : "종가와 조건을 확인합니다. 메일은 발송하지 않습니다.", "info", true);
  try {
    const data = await api("/api/check", { method: "POST", body: JSON.stringify({ send }) });
    $("check-results-list").replaceChildren();
    const results = data.results || [];
    for (const result of results) $("check-results-list").append(el("li", "", `${result.ticker || "종목"} · ${result.message || result.status || "확인 완료"}`));
    $("check-results").hidden = results.length === 0;
    const errors = Array.isArray(data.errors) ? data.errors.length : Number(data.errors || 0);
    const sent = Array.isArray(data.sent) ? data.sent.length : Number(data.sent || 0);
    announce(`${send ? `확인 완료 · 메일 서버 접수 ${sent}건` : "확인 완료 · 메일 발송 없음"}${errors ? ` · 오류 ${errors}건 (아래 결과 확인)` : ""}`, errors ? "error" : "success", Boolean(errors));
    await refresh(false);
    await refreshStatus();
  } catch (error) { announce(error.message, "error", true); }
  finally { checking = false; $("check-only").disabled = false; $("check-send").disabled = false; button.textContent = original; }
}

$("rule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("save-rule");
  const id = $("rule-id").value;
  const existing = snapshot.rules.find((rule) => String(rule.id) === id);
  const body = {
    market: market(), ticker: $("ticker").value.trim().toUpperCase(), name: $("name").value.trim(),
    target_price: $("target-price").value, upper_pct: $("upper-pct").value, lower_pct: $("lower-pct").value,
    mode: $("mode").value, enabled: existing ? Boolean(existing.enabled) : true,
  };
  button.disabled = true;
  try {
    await api(id ? `/api/rules/${encodeURIComponent(id)}` : "/api/rules", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
    resetEditor();
    await refresh(false);
    announce(`${body.ticker} 알림을 ${id ? "수정" : "등록"}했습니다.`);
  } catch (error) { announce(error.message, "error", true); }
  finally { button.disabled = false; }
});

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("save-settings");
  button.disabled = true;
  const body = {
    recipient: $("recipient").value.trim(), smtp_host: $("smtp-host").value.trim(),
    smtp_port: Number($("smtp-port").value), smtp_security: $("smtp-security").value,
    smtp_username: $("smtp-username").value.trim(), smtp_from: $("smtp-from").value.trim(),
    interval_minutes: Number($("interval-minutes").value),
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    await refresh();
    await refreshStatus();
    announce("이메일과 자동 확인 설정을 저장했습니다.");
  } catch (error) { announce(error.message, "error", true); }
  finally { button.disabled = false; }
});

$("test-email").addEventListener("click", async () => {
  const button = $("test-email");
  button.disabled = true;
  button.textContent = "발송 중…";
  try {
    const data = await api("/api/test-email", { method: "POST", body: "{}" });
    announce(data.message || `${snapshot.settings.recipient || "수신 이메일"}로 보낼 테스트 메일을 서버가 접수했습니다. 수신함을 확인하세요.`);
  } catch (error) { announce(error.message, "error", true); }
  finally { button.disabled = false; button.textContent = "테스트 메일 보내기"; }
});

document.querySelectorAll('input[name="market"]').forEach((input) => input.addEventListener("change", updateMarket));
["target-price", "lower-pct", "upper-pct"].forEach((id) => $(id).addEventListener("input", updateRange));
$("cancel-edit").addEventListener("click", resetEditor);
$("check-only").addEventListener("click", () => check(false));
$("check-send").addEventListener("click", () => check(true));
updateMarket();
refresh().catch((error) => announce(`프로그램에 연결하지 못했습니다. ${error.message}`, "error", true));
refreshStatus();
setInterval(refreshStatus, 30000);
setInterval(() => { if (!checking && !$("rule-id").value) refresh(false).catch(() => {}); }, 60000);
