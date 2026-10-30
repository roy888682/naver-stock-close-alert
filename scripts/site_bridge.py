"""Validate a private Site's daily request before enabling SMTP delivery.

Ordinary code/configuration pushes remain previews. A send needs a changed
request file for this exact Site and today's Korean date in the same commit.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE_ID = "appgprj_6ae4941c8a3c8191b9c7fe8ed95ee3fa"
REQUEST_FILE = "cloud-run-request.json"
SEOUL = timezone(timedelta(hours=9))
ACTIONS = {"check", "check-only", "test-email", "lookup"}


def validate_request(document, now=None):
    if not isinstance(document, dict) or set(document) != {"version", "site_id", "request_date"}:
        raise ValueError("Site 실행 요청의 필수 항목을 확인하세요.")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("지원하지 않는 Site 실행 요청 버전입니다.")
    if document["site_id"] != SITE_ID:
        raise ValueError("등록된 웹사이트의 실행 요청이 아닙니다.")
    requested = document["request_date"]
    if not isinstance(requested, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested):
        raise ValueError("Site 실행 요청 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    try:
        day = date.fromisoformat(requested)
    except ValueError:
        raise ValueError("Site 실행 요청 날짜를 확인하세요.") from None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("실행 확인 시각에는 시간대 정보가 필요합니다.")
    if day != now.astimezone(SEOUL).date():
        raise ValueError("오늘 한국 날짜의 요청만 발송할 수 있습니다. 최신 웹 조건으로 다시 요청하세요.")
    return document


def decide_action(event_name, inputs=None, changed_paths=(), request=None, now=None):
    if event_name == "push":
        if REQUEST_FILE not in changed_paths:
            return "check-only"
        validate_request(request, now)
        return "check"
    if event_name == "workflow_dispatch":
        inputs = {} if inputs is None else inputs
        if not isinstance(inputs, dict):
            raise ValueError("수동 실행 입력을 확인하세요.")
        action = inputs.get("action", "check-only")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError("알 수 없는 수동 실행 작업입니다.")
        return action
    raise ValueError("지원하지 않는 실행 이벤트입니다.")


def changed_paths(commit):
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("실행 커밋을 확인할 수 없습니다.")
    result = subprocess.run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit, "--"],
                            capture_output=True, check=False)
    if result.returncode or len(result.stdout) > 1_000_000:
        raise ValueError("실행 커밋의 변경 파일을 확인할 수 없습니다.")
    return result.stdout.decode("utf-8").splitlines()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 262_144:
        raise ValueError("실행 입력 파일이 너무 큽니다.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="웹사이트의 오전 7시 실행 요청 검증")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--request", default=REQUEST_FILE)
    parser.add_argument("--config", default="watchlist.json")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    try:
        event = read_json(args.event)
        if not isinstance(event, dict):
            raise ValueError("실행 이벤트 형식을 확인하세요.")
        paths = changed_paths(args.commit) if args.event_name == "push" else []
        request = read_json(args.request) if REQUEST_FILE in paths else None
        action = decide_action(args.event_name, event.get("inputs"), paths, request)
        if action in ("check", "check-only"):
            # Match the existing runner's full schema before touching alert state.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from stock_alert.cloud import load_config
            load_config(args.config)
        if args.output:
            with Path(args.output).open("a", encoding="utf-8") as handle:
                handle.write(f"action={action}\n")
        print(json.dumps({"action": action}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
