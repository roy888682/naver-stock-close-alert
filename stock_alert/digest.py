"""One complete watchlist email, with durable per-Korean-day delivery state."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from urllib.parse import quote, urlsplit

from .mailer import MailError
from .provider import ProviderError
from .service import in_range, utcnow

SEOUL = timezone(timedelta(hours=9))
REPORT_DAYS = {1, 2, 3, 4, 5}  # Tuesday through Saturday, in Korea.


def number(value):
    formatted = format(Decimal(str(value)), ",f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def source_link(ticker, source_url=""):
    parsed = urlsplit(source_url)
    if (parsed.scheme == "https" and parsed.netloc == "m.stock.naver.com"
            and parsed.path.startswith(("/domestic/stock/", "/worldstock/stock/", "/domestic/etf/", "/worldstock/etf/"))):
        return source_url
    return "https://finance.naver.com/search/searchList.naver?query=" + quote(ticker, safe="")


def collect_rows(service):
    """Keep every registered rule; unavailable quotes never become a match."""
    rows, cache, errors = [], {}, 0
    for rule in service.rules():
        currency = "KRW" if rule["market"] == "KR" else "USD"
        target = Decimal(rule["target_price"])
        row = dict(ticker=rule["ticker"], name=rule["name"] or rule["ticker"],
                   target=target, currency=currency,
                   lower=target * (1 - Decimal(rule["lower_pct"]) / 100),
                   upper=target * (1 + Decimal(rule["upper_pct"]) / 100),
                   lower_pct=rule["lower_pct"], upper_pct=rule["upper_pct"],
                   matched=False, status="일시정지", close=None, trade_date=None,
                   source_url=source_link(rule["ticker"]), error="")
        if rule["enabled"]:
            try:
                key = (rule["market"], rule["ticker"])
                if key not in cache:
                    try:
                        cache[key] = service.provider.fetch_close(*key)
                    except Exception as exc:
                        cache[key] = exc
                result = cache[key]
                if isinstance(result, Exception):
                    raise result
                day = result.trade_date.isoformat()
                # An older response must not replace a newer trusted close.
                if rule["last_date"] and day < rule["last_date"]:
                    raise ValueError("이전에 확인한 거래일보다 오래된 시세입니다.")
                matched = in_range(rule, result.close)
                row.update(name=rule["name"] or result.name, matched=matched,
                           status="목표가 도달" if matched else "목표가 미달",
                           close=result.close, trade_date=day, currency=result.currency,
                           source_url=source_link(rule["ticker"], result.source_url))
                with service.db() as db:
                    db.execute("UPDATE rules SET last_price=?,last_date=?,last_checked=?,last_error=NULL WHERE id=?",
                               (str(result.close), day, utcnow(), rule["id"]))
            except Exception as exc:
                errors += 1
                # Only intentionally sanitized provider/validation errors are shown.
                message = str(exc)[:300] if isinstance(exc, (ProviderError, ValueError)) else "종가를 조회하지 못했습니다."
                row.update(matched=False, close=None, trade_date=None, status="조회 실패", error=message)
                with service.db() as db:
                    db.execute("UPDATE rules SET last_checked=?,last_error=? WHERE id=?", (utcnow(), message, rule["id"]))
        rows.append(row)
    # Python's stable sort keeps the existing list order within each group.
    return sorted(rows, key=lambda row: not row["matched"]), errors


def render_report(rows, report_date, test=False):
    matched = sum(row["matched"] for row in rows)
    title = f"{'[테스트] ' if test else ''}종가 알림 목록 · {report_date}"
    summary = f"전체 {len(rows)}개 · 목표가 도달 {matched}개"
    explanation = "목표가 도달은 완료된 정규장 종가가 설정한 알림가격범위 안에 있다는 뜻입니다. 범위의 양 끝값을 포함합니다."
    plain = [title, summary, "발송 일정: 매주 화요일~토요일 오전 7시 (한국 시간)", "", "번호 / 종목 / 목표가 / 알림가격범위 / 종가 (거래일) / 상태"]
    cells = []
    cell_style = "padding:12px 10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;"
    for index, row in enumerate(rows, 1):
        target = f"{number(row['target'])} {row['currency']}"
        bounds = f"{number(row['lower'])} ~ {number(row['upper'])} {row['currency']}"
        percentages = f"-{number(row['lower_pct'])}% ~ +{number(row['upper_pct'])}%"
        close = f"{number(row['close'])} {row['currency']} ({row['trade_date']})" if row["close"] is not None else "—"
        name = f"{row['name']} ({row['ticker']})"
        plain.append(f"{index} / {name} / {target} / {bounds} ({percentages}) / {close} / {row['status']}")
        plain.append(row["source_url"])
        if row["error"]:
            plain.append(row["error"])
        color = "#d32f2f" if row["matched"] else "#1f2937"
        style = f"color:{color};font-weight:{'700' if row['matched'] else '500'};"
        status = f'<strong style="{style}">{escape(row["status"])}</strong>'
        if row["error"]:
            status += f'<br><span style="font-size:12px;color:#6b7280;">{escape(row["error"])}</span>'
        link = f'<a href="{escape(row["source_url"], quote=True)}" style="{style}text-decoration:underline;">{escape(name)}</a>'
        values = [str(index), link, escape(target), escape(bounds) + '<br><span style="font-size:12px;color:#6b7280;">' + escape(percentages) + '</span>', escape(close), status]
        cells.append('<tr style="background:' + ('#fff5f5' if row["matched"] else '#ffffff') + ';">' + ''.join(f'<td style="{cell_style}">{value}</td>' for value in values) + '</tr>')
    if not rows:
        plain.append("등록된 종목이 없습니다.")
        cells.append(f'<tr><td colspan="6" style="{cell_style}">등록된 종목이 없습니다.</td></tr>')
    header = ''.join(f'<th scope="col" style="{cell_style}background:#f3f4f6;white-space:nowrap;">{label}</th>' for label in ("번호", "종목", "목표가", "알림가격범위", "종가 (거래일)", "상태"))
    html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body style="margin:0;padding:24px;background:#f5f6f8;font-family:Arial,\'Malgun Gothic\',sans-serif;color:#1f2937;">'
            '<div style="max-width:1000px;margin:auto;padding:24px;background:#ffffff;border-radius:12px;">'
            f'<h1 style="margin:0 0 10px;font-size:22px;">{escape(title)}</h1><p>{escape(summary)}</p>'
            '<p style="font-size:13px;color:#6b7280;">매주 화요일~토요일 오전 7시 · 한국 시간</p>'
            f'<div style="overflow-x:auto;"><table style="border-collapse:collapse;width:100%;font-size:14px;"><thead><tr>{header}</tr></thead><tbody>{"".join(cells)}</tbody></table></div>'
            f'<p style="font-size:12px;color:#6b7280;line-height:1.6;">{escape(explanation)}<br>종목명을 누르면 네이버증권으로 연결됩니다. 휴장일에는 최근 완료된 거래일의 종가를 표시합니다.</p></div></body></html>')
    plain.extend(["", explanation, "종목명 링크는 네이버증권으로 연결됩니다. 휴장일에는 최근 완료된 거래일의 종가를 표시합니다."])
    subject = f"{'[테스트] ' if test else ''}[종가 목록] {report_date} · 전체 {len(rows)}개 · 목표가 도달 {matched}개"
    return subject, "\n".join(plain) + "\n", html


def send_digest(service, *, test=False, now=None, send=True):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("보고서 발송 시각에는 시간대 정보가 필요합니다.")
    local = now.astimezone(SEOUL)
    day = local.date().isoformat()
    if send and not test and local.weekday() not in REPORT_DAYS:
        return dict(status="skipped", message="화요일~토요일 한국 시간에만 정기 목록을 발송합니다.", sent=0, errors=0)
    if not service.check_lock.acquire(blocking=False):
        raise RuntimeError("다른 확인 작업이 실행 중입니다. 잠시 후 다시 시도하세요.")
    try:
        with service.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS daily_reports (kind TEXT NOT NULL,report_date TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,message_id TEXT,error TEXT,PRIMARY KEY(kind,report_date))")
            kind = "test" if test else "daily"
            previous = db.execute("SELECT status FROM daily_reports WHERE kind=? AND report_date=?", (kind, day)).fetchone()
        if send and previous:
            if previous["status"] in ("pending", "uncertain"):
                raise MailError("오늘 목록 메일의 이전 발송 접수 여부가 불명확합니다. 보낸 편지함과 발송 기록을 확인하세요.")
            if previous["status"] == "sent":
                return dict(status="skipped", message="오늘 목록 메일은 이미 발송했습니다.", sent=0, errors=0)
        rows, errors = collect_rows(service)
        subject, body, html_body = render_report(rows, day, test=test)
        result = dict(report_date=day, test=test, total=len(rows), matched=sum(row["matched"] for row in rows), errors=errors, sent=0,
                      results=[{key: row[key] for key in ("ticker", "name", "status", "trade_date", "source_url")} for row in rows])
        if not send:
            return {**result, "status": "preview"}
        with service.db() as db:
            db.execute("INSERT INTO daily_reports(kind,report_date,status,created_at) VALUES (?,?,'pending',?) ON CONFLICT(kind,report_date) DO UPDATE SET status='pending',created_at=excluded.created_at,message_id=NULL,error=NULL", (kind, day, utcnow()))
        try:
            message_id = service.mailer(service._mail_settings(), subject, body, html_body=html_body)
        except Exception as exc:
            uncertain = not isinstance(exc, MailError) or exc.uncertain
            with service.db() as db:
                db.execute("UPDATE daily_reports SET status=?,error=? WHERE kind=? AND report_date=?",
                           ("uncertain" if uncertain else "failed", str(exc)[:300] if isinstance(exc, MailError) else "메일 처리 오류", kind, day))
            raise MailError(str(exc) if isinstance(exc, MailError) else "목록 메일 발송 처리 오류가 발생했습니다.", uncertain=uncertain) from None
        with service.db() as db:
            db.execute("UPDATE daily_reports SET status='sent',message_id=?,error=NULL WHERE kind=? AND report_date=?", (message_id, kind, day))
        return {**result, "status": "sent", "sent": 1, "message": "메일 서버가 전체 종목 목록을 접수했습니다."}
    finally:
        service.check_lock.release()
