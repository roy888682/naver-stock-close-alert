"""SQLite settings, rule evaluation and persistent email deduplication."""
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .mailer import MailError, send_mail
from .provider import NaverProvider

DEFAULTS = dict(recipient="ykhan@dacpole.com", smtp_host="smtp.gmail.com", smtp_port=465,
                smtp_security="ssl", smtp_username="royhan88888@gmail.com", smtp_from="royhan88888@gmail.com",
                interval_minutes=30)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def number(value, label, low, high, exclusive=False):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: 숫자를 입력하세요.") from None
    if not d.is_finite() or d < low or d > high or (exclusive and d == low):
        raise ValueError(f"{label}: 허용 범위는 {low}{' 초과' if exclusive else ' 이상'} ~ {high}입니다.")
    if d.as_tuple().exponent < -6:
        raise ValueError(f"{label}: 소수점 6자리까지 입력하세요.")
    return format(d, "f")


def validate_rule(data):
    market = str(data.get("market", "KR")).upper()
    ticker = str(data.get("ticker", "")).strip().upper()
    if market not in ("KR", "US"):
        raise ValueError("시장은 KR 또는 US여야 합니다.")
    if not re.fullmatch(r"[0-9]{6}" if market == "KR" else r"[A-Z][A-Z0-9.\-]{0,19}", ticker):
        raise ValueError("한국은 6자리 코드, 미국은 올바른 티커를 입력하세요.")
    mode = data.get("mode", "entry")
    if mode not in ("entry", "daily"):
        raise ValueError("알림 방식은 entry 또는 daily여야 합니다.")
    if not isinstance(data.get("enabled", True), bool):
        raise ValueError("enabled는 true 또는 false여야 합니다.")
    name = str(data.get("name", "")).strip()
    if len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("종목명은 줄바꿈 없이 80자까지 입력하세요.")
    return dict(market=market, ticker=ticker, name=name,
                target_price=number(data.get("target_price"), "목표가", 0, 10**12, True),
                upper_pct=number(data.get("upper_pct", 5), "상단 비율", 0, 1000),
                lower_pct=number(data.get("lower_pct", 5), "하단 비율", 0, 100),
                mode=mode, enabled=int(data.get("enabled", True)))


def in_range(rule, price):
    target = Decimal(rule["target_price"])
    lower = target * (1 - Decimal(rule["lower_pct"]) / 100)
    upper = target * (1 + Decimal(rule["upper_pct"]) / 100)
    return lower <= price <= upper


class Service:
    def __init__(self, db_path, provider=None, mailer=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.provider = provider or NaverProvider()
        self.mailer = mailer or send_mail
        self.check_lock = threading.Lock()
        self.last_check = None
        self.scheduler_running = False
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rules(
              id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT,ticker TEXT,name TEXT,target_price TEXT,
              upper_pct TEXT,lower_pct TEXT,mode TEXT,enabled INTEGER,revision INTEGER DEFAULT 1,
              last_price TEXT,last_date TEXT,last_checked TEXT,last_error TEXT,
              evaluated_date TEXT,was_in_range INTEGER);
            CREATE TABLE IF NOT EXISTS alerts(
              id INTEGER PRIMARY KEY,rule_id INTEGER,revision INTEGER,ticker TEXT,trade_date TEXT,
              price TEXT,status TEXT,created_at TEXT,error TEXT,message_id TEXT,
              UNIQUE(rule_id,revision,trade_date));
            """)
            for key, value in DEFAULTS.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=35)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def settings(self, public=False):
        with self.db() as db:
            result = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}
        if public:
            result["smtp_password_configured"] = bool(os.environ.get("SMTP_PASSWORD", "").strip())
        return result

    def update_settings(self, data):
        if not isinstance(data, dict) or set(data) - DEFAULTS.keys():
            raise ValueError("알 수 없는 설정 항목입니다.")
        result = self.settings()
        result.update(data)
        for key in ("recipient", "smtp_from", "smtp_username", "smtp_host"):
            result[key] = str(result[key]).strip()
            if len(result[key]) > 254 or any(ord(c) < 32 for c in result[key]):
                raise ValueError("메일 설정에 잘못된 문자가 있습니다.")
        for key in ("recipient", "smtp_from"):
            if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", result[key]):
                raise ValueError("발신·수신 주소는 이메일 주소 하나씩 입력하세요.")
        if not re.fullmatch(r"[A-Za-z0-9.\-]+", result["smtp_host"]):
            raise ValueError("SMTP 서버 이름을 확인하세요.")
        if result["smtp_security"] not in ("ssl", "starttls"):
            raise ValueError("SSL 또는 STARTTLS를 선택하세요.")
        for key, low, high in (("smtp_port", 1, 65535), ("interval_minutes", 5, 1440)):
            try:
                d = Decimal(str(result[key]))
                if not d.is_finite() or d != int(d) or not low <= d <= high:
                    raise ValueError()
                result[key] = int(d)
            except (ValueError, InvalidOperation, OverflowError):
                raise ValueError(f"{key}: {low} ~ {high} 사이 정수를 입력하세요.") from None
        with self.db() as db:
            db.executemany("UPDATE settings SET value=? WHERE key=?", [(json.dumps(v), k) for k, v in result.items()])
        return self.settings(public=True)

    def rules(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM rules ORDER BY id DESC")]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
            row["in_range"] = in_range(row, Decimal(row["last_price"])) if row["last_price"] else None
        return rows

    def snapshot(self):
        with self.db() as db:
            alerts = [dict(r) for r in db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")]
        return dict(rules=self.rules(), settings=self.settings(public=True), recent_alerts=alerts)

    def save_rule(self, data, rule_id=None):
        with self.check_lock, self.db() as db:
            old = db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone() if rule_id else None
            if rule_id and not old:
                raise KeyError("종목을 찾을 수 없습니다.")
            merged = dict(old) if old else {}
            if old:
                merged["enabled"] = bool(merged["enabled"])
            merged.update(data)
            new = validate_rule(merged)
            if old:
                changed = any(old[k] != new[k] for k in ("market", "ticker", "target_price", "upper_pct", "lower_pct", "mode"))
                db.execute("UPDATE rules SET " + ",".join(k + "=?" for k in new) + " WHERE id=?", (*new.values(), rule_id))
                if changed:
                    db.execute("UPDATE rules SET revision=revision+1,evaluated_date=NULL,was_in_range=NULL,last_price=NULL,last_date=NULL,last_error=NULL WHERE id=?", (rule_id,))
            else:
                if db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] >= 100:
                    raise ValueError("종목은 최대 100개까지 등록할 수 있습니다.")
                cursor = db.execute("INSERT INTO rules (" + ",".join(new) + ") VALUES (" + ",".join("?" for _ in new) + ")", tuple(new.values()))
                rule_id = cursor.lastrowid
        return next(r for r in self.rules() if r["id"] == rule_id)

    def delete_rule(self, rule_id):
        with self.check_lock, self.db() as db:
            if not db.execute("DELETE FROM rules WHERE id=?", (rule_id,)).rowcount:
                raise KeyError("종목을 찾을 수 없습니다.")

    def test_email(self):
        self.mailer(self.settings(), "[종가 알림] 이메일 연결 테스트",
                    "네이버증권 종가 알림 프로그램의 테스트 메일입니다.\n이 메일이 도착하면 수신 경로를 확인할 수 있습니다.\n" + utcnow())
        return {"message": "메일 서버가 테스트메일을 접수했습니다. 수신함·스팸함을 확인하세요."}

    def check(self, send=True):
        if not self.check_lock.acquire(blocking=False):
            raise RuntimeError("다른 확인 작업이 실행 중입니다. 잠시 후 다시 시도하세요.")
        results, cache, sent, errors = [], {}, 0, 0
        try:
            for rule in self.rules():
                if not rule["enabled"]:
                    continue
                result = dict(ticker=rule["ticker"], status="ok", message="")
                try:
                    key = (rule["market"], rule["ticker"])
                    if key not in cache:
                        cache[key] = self.provider.fetch_close(*key)
                    quote = cache[key]
                    day = quote.trade_date.isoformat()
                    price = str(quote.close)
                    matched = in_range(rule, quote.close)
                    with self.db() as db:
                        db.execute("UPDATE rules SET last_price=?,last_date=?,last_checked=?,last_error=NULL WHERE id=?",
                                   (price, day, utcnow(), rule["id"]))
                    if not send:
                        result.update(status="preview", message=f"{day} 종가 {price} {quote.currency} · " + ("범위 안 (발송 안 함)" if matched else "범위 밖"))
                    elif rule["evaluated_date"] and day < rule["evaluated_date"]:
                        result.update(status="skipped", message="이미 평가한 거래일보다 오래된 시세입니다.")
                    else:
                        should_send = matched and (rule["mode"] == "daily" or rule["was_in_range"] != 1)
                        with self.db() as db:
                            existing = db.execute("SELECT * FROM alerts WHERE rule_id=? AND revision=? AND trade_date=?", (rule["id"], rule["revision"], day)).fetchone()
                        if existing and existing["status"] in ("pending", "uncertain"):
                            raise MailError("이 거래일의 이전 발송 접수 여부가 불명확합니다. 보낸 편지함과 발송 기록을 확인하세요.")
                        if existing and existing["status"] == "sent":
                            should_send = False
                        if should_send:
                            with self.db() as db:
                                db.execute("INSERT INTO alerts (rule_id,revision,ticker,trade_date,price,status,created_at) VALUES (?,?,?,?,?,'pending',?) ON CONFLICT(rule_id,revision,trade_date) DO UPDATE SET status='pending',error=NULL,created_at=excluded.created_at",
                                           (rule["id"], rule["revision"], rule["ticker"], day, price, utcnow()))
                            try:
                                name = rule["name"] or quote.name
                                body = (f"{name} ({rule['ticker']}) 종가가 목표 범위에 들어왔습니다.\n\n거래일: {day}\n"
                                        f"정규장 종가: {price} {quote.currency}\n목표가: {rule['target_price']} {quote.currency}\n"
                                        f"허용 비율: -{rule['lower_pct']}% ~ +{rule['upper_pct']}%\n"
                                        f"목표가 대비: {(quote.close / Decimal(rule['target_price']) - 1) * 100:+.2f}%\n"
                                        f"출처: {quote.source_url}\n\n발송 시각(UTC): {utcnow()}\n")
                                message_id = self.mailer(self.settings(), f"[종가 알림] {rule['ticker']} · {day} · 목표 범위 진입", body)
                            except Exception as exc:
                                uncertain = not isinstance(exc, MailError) or exc.uncertain
                                with self.db() as db:
                                    db.execute("UPDATE alerts SET status=?,error=? WHERE rule_id=? AND revision=? AND trade_date=?",
                                               ("uncertain" if uncertain else "failed", str(exc)[:300] if isinstance(exc, MailError) else "메일 처리 오류", rule["id"], rule["revision"], day))
                                raise
                            with self.db() as db:
                                db.execute("UPDATE alerts SET status='sent',message_id=?,error=NULL WHERE rule_id=? AND revision=? AND trade_date=?", (message_id, rule["id"], rule["revision"], day))
                            sent += 1
                            result.update(status="sent", message=f"{day} 종가 {price} · 메일 서버 접수 완료")
                        else:
                            result.update(status="skipped" if matched else "outside", message=f"{day} 종가 {price} · " + ("기존 알림 조건으로 중복 발송 생략" if matched else "범위 밖"))
                        with self.db() as db:
                            db.execute("UPDATE rules SET evaluated_date=?,was_in_range=? WHERE id=?", (day, int(matched), rule["id"]))
                except Exception as exc:
                    errors += 1
                    # Provider and MailError expose deliberately sanitized messages.
                    message = str(exc)[:300] if isinstance(exc, (ValueError, RuntimeError)) else "조회·발송 처리 중 오류가 발생했습니다."
                    result.update(status="error", message=message)
                    with self.db() as db:
                        db.execute("UPDATE rules SET last_checked=?,last_error=? WHERE id=?", (utcnow(), message, rule["id"]))
                results.append(result)
            self.last_check = utcnow()
            return dict(results=results, sent=sent, errors=errors)
        finally:
            self.check_lock.release()
