"""Run the repository watchlist once, preserving alert state in SQLite.

The GitHub workflow restores/saves only alerts.db. SMTP credentials always come
from the runner's environment and are never written into this database.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from .__main__ import instance_lock
from .mailer import MailError
from .provider import NaverProvider
from .service import DEFAULTS, Service, validate_rule

RULE_FIELDS = {"id", "market", "ticker", "name", "target_price", "upper_pct",
               "lower_pct", "mode", "enabled"}
REQUIRED_FIELDS = {"id", "market", "ticker", "target_price"}
CONDITION_FIELDS = ("market", "ticker", "target_price", "upper_pct", "lower_pct", "mode")


def validate_config(document):
    """Validate the complete document before any database mutation."""
    if not isinstance(document, dict) or set(document) != {"version", "rules"}:
        raise ValueError("watchlist.json에는 version과 rules만 입력하세요.")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("watchlist.json version은 1이어야 합니다.")
    if not isinstance(document["rules"], list) or len(document["rules"]) > 100:
        raise ValueError("rules는 최대 100개의 종목으로 이루어진 목록이어야 합니다.")
    validated, identifiers = [], set()
    for index, row in enumerate(document["rules"], 1):
        if (not isinstance(row, dict) or set(row) - RULE_FIELDS
                or not REQUIRED_FIELDS <= set(row)):
            raise ValueError(f"rules {index}번: 필수 항목 또는 알 수 없는 항목을 확인하세요.")
        identifier = row["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            raise ValueError(f"rules {index}번: id는 영문·숫자·밑줄·하이픈으로 1~64자 입력하세요.")
        if identifier in identifiers:
            raise ValueError(f"rules {index}번: id가 중복되었습니다.")
        identifiers.add(identifier)
        if "name" in row and not isinstance(row["name"], str):
            raise ValueError(f"rules {index}번: name은 문자열이어야 합니다.")
        try:
            rule = validate_rule({**row, "mode": row.get("mode", "daily")})
        except ValueError as exc:
            raise ValueError(f"rules {index}번: {exc}") from None
        # Cosmetic number formatting must not reset alert deduplication.
        for field in ("target_price", "upper_pct", "lower_pct"):
            rule[field] = format(Decimal(rule[field]).normalize(), "f")
        rule["enabled"] = bool(rule["enabled"])
        validated.append({"id": identifier, **rule})
    return {"version": 1, "rules": validated}


def load_config(path):
    path = Path(path)
    if path.stat().st_size > 262_144:
        raise ValueError("watchlist.json 파일이 너무 큽니다.")
    try:
        return validate_config(json.loads(path.read_text(encoding="utf-8-sig")))
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("watchlist.json을 읽을 수 없습니다. JSON 문법과 UTF-8 인코딩을 확인하세요.") from None


def sync_rules(service, document):
    """Make watchlist authoritative, retaining identity across reorders/renames.

    All edits, including the stable-id mapping, commit together. Removing an id
    and later adding it again intentionally creates a new alert condition.
    """
    config = validate_config(document)
    with service.check_lock, service.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS cloud_rules (config_id TEXT PRIMARY KEY, rule_id INTEGER NOT NULL UNIQUE)")
        mapping = {r["config_id"]: r["rule_id"] for r in db.execute("SELECT * FROM cloud_rules")}
        wanted = {row["id"] for row in config["rules"]}
        retained_ids = {rule_id for identifier, rule_id in mapping.items() if identifier in wanted}
        # Delete first so replacing a full 100-rule list does not exceed its cap.
        for old in db.execute("SELECT id FROM rules").fetchall():
            if old["id"] not in retained_ids:
                db.execute("DELETE FROM rules WHERE id=?", (old["id"],))
        for identifier in set(mapping) - wanted:
            db.execute("DELETE FROM cloud_rules WHERE config_id=?", (identifier,))
        for row in config["rules"]:
            identifier = row["id"]
            rule = {key: value for key, value in row.items() if key != "id"}
            rule["enabled"] = int(rule["enabled"])
            old_id = mapping.get(identifier)
            old = db.execute("SELECT * FROM rules WHERE id=?", (old_id,)).fetchone() if old_id else None
            if old:
                changed = any(old[key] != rule[key] for key in CONDITION_FIELDS)
                db.execute("UPDATE rules SET " + ",".join(key + "=?" for key in rule) + " WHERE id=?",
                           (*rule.values(), old_id))
                if changed:
                    db.execute("UPDATE rules SET revision=revision+1,evaluated_date=NULL,was_in_range=NULL,last_price=NULL,last_date=NULL,last_error=NULL WHERE id=?", (old_id,))
            else:
                cursor = db.execute("INSERT INTO rules (" + ",".join(rule) + ") VALUES (" + ",".join("?" for _ in rule) + ")", tuple(rule.values()))
                db.execute("INSERT OR REPLACE INTO cloud_rules VALUES (?,?)", (identifier, cursor.lastrowid))
    return service.rules()


class CloudService(Service):
    """Fixed mail routing with an environment-only password source."""
    def __init__(self, db_path, provider=None, mailer=None, before_send=None):
        super().__init__(db_path, provider=provider, mailer=mailer)
        with self.db() as db:
            db.executemany("UPDATE settings SET value=? WHERE key=?",
                           [(json.dumps(value), key) for key, value in DEFAULTS.items()])
        if before_send is not None:
            transport = self.mailer

            def persisted_mailer(settings, subject, body):
                # Service.check commits 'pending' before invoking its mailer.
                # Persist that reservation remotely before SMTP can accept it.
                try:
                    before_send()
                except Exception:
                    raise MailError("발송 전 상태 저장 실패: 메일을 발송하지 않았습니다. Actions 상태 저장 로그를 확인하세요.") from None
                return transport(settings, subject, body)

            self.mailer = persisted_mailer

    def _password(self, settings):
        password = os.environ.get("SMTP_PASSWORD", "").strip().replace(" ", "")
        if len(password) > 256 or any(char in password for char in "\r\n\x00"):
            raise ValueError("SMTP_PASSWORD Secret에 올바른 Gmail 앱 비밀번호를 등록하세요.")
        return password


def persist_state(database, marker):
    """Lazy import keeps local/lookup usage independent of the Actions helper."""
    from scripts.action_state import save
    # Keep the final machine-readable result alone on stdout.
    with redirect_stdout(sys.stderr):
        save(database, marker)


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitHub 오전 7시 종가 알림 단발 실행")
    parser.add_argument("--config", default="watchlist.json")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--persist-state", action="store_true", help="메일 발송 직전에 pending 상태를 GitHub 상태 브랜치에 저장")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check-only", action="store_true", help="메일·중복 상태 변경 없이 종가 확인")
    action.add_argument("--test-email", action="store_true")
    action.add_argument("--lookup", nargs=2, metavar=("MARKET", "TICKER"))
    args = parser.parse_args(argv)
    if args.persist_state and (args.check_only or args.test_email or args.lookup):
        parser.error("--persist-state는 기본 확인·발송 실행에서만 사용할 수 있습니다.")
    try:
        if args.lookup:
            result = NaverProvider().lookup(*args.lookup)
        else:
            config = None if args.test_email else load_config(args.config)
            directory = Path(args.data_dir)
            with instance_lock(directory):
                before_send = (lambda: persist_state(directory / "alerts.db", directory / "github-state.json")) if args.persist_state else None
                service = CloudService(directory / "alerts.db", before_send=before_send)
                if config is not None:
                    sync_rules(service, config)
                if args.test_email or (not args.check_only and any(row["enabled"] for row in config["rules"])):
                    if not service._password(service.settings()):
                        raise ValueError("GitHub 저장소의 Actions Secret에 SMTP_PASSWORD를 등록하세요. 발신 Gmail 계정의 앱 비밀번호가 필요합니다.")
                result = service.test_email() if args.test_email else service.check(send=not args.check_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("errors") else 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        # None of these errors contain the password or the configuration body.
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
