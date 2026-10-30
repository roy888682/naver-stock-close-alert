"""Manage cloud alert rules from GitHub's web workflow form, without JSON editing.

This module prepares a validated local configuration. The calling workflow owns
the Git commit/push and reports when the remote save has actually succeeded.
"""
import argparse
import html
import json
import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from .cloud import load_config, validate_config
from .provider import NaverProvider
from .service import validate_rule

INPUTS = {"operation", "market", "ticker", "target_price", "upper_pct", "lower_pct", "mode", "rule_id"}
OPERATIONS = {"save", "pause", "resume", "delete", "list"}


def apply_inputs(document, inputs, provider):
    """Return a new configuration/result; never mutate the supplied document."""
    original = validate_config(document)
    if not isinstance(inputs, dict) or set(inputs) - INPUTS:
        raise ValueError("알 수 없는 입력 항목입니다. 종목 관리 웹 입력창을 사용하세요.")
    if any(not isinstance(value, str) or len(value) > 256 for value in inputs.values()):
        raise ValueError("입력값은 256자 이하의 문자열이어야 합니다.")
    values = {key: value.strip() for key, value in inputs.items()}
    operation = values.get("operation", "list")
    if operation not in OPERATIONS:
        raise ValueError("작업은 save, pause, resume, delete, list 중에서 선택하세요.")
    result = {"operation": operation, "changed": False}
    if operation == "list":
        return original, result

    identifier = values.get("rule_id", "")
    rules = original["rules"]
    if identifier:
        matches = [row for row in rules if row["id"] == identifier]
        if not matches:
            raise ValueError("해당 rule_id가 없습니다. list로 현재 목록의 ID를 확인하세요.")
    else:
        identity = validate_rule({"market": values.get("market", "KR"), "ticker": values.get("ticker", ""), "target_price": "1"})
        matches = [row for row in rules if (row["market"], row["ticker"]) == (identity["market"], identity["ticker"])]
        if len(matches) > 1:
            raise ValueError("같은 티커의 조건이 여러 개입니다. list에서 ID를 확인한 뒤 rule_id로 선택하세요.")

    old = matches[0] if matches else None
    if operation != "save" and old is None:
        raise ValueError("등록된 종목을 찾지 못했습니다. 시장·티커를 확인하거나 list로 목록을 확인하세요.")
    if operation == "save":
        if old is None and len(rules) >= 100:
            raise ValueError("종목은 최대 100개까지 등록할 수 있습니다. 기존 종목을 삭제한 뒤 등록하세요.")
        # Validate every editable field before contacting the market provider.
        candidate = validate_rule({
            "market": values.get("market", "KR"), "ticker": values.get("ticker", ""),
            "target_price": values.get("target_price", ""),
            "upper_pct": values.get("upper_pct") or "5", "lower_pct": values.get("lower_pct") or "5",
            "mode": values.get("mode") or "daily", "enabled": old["enabled"] if old else True,
        })
        lookup = provider.lookup(candidate["market"], candidate["ticker"])
        if not isinstance(lookup, dict) or not isinstance(lookup.get("name"), str) or not lookup["name"].strip():
            raise ValueError("종목명을 확인할 수 없습니다. 시장·티커를 확인하고 다시 실행하세요.")
        candidate.update(id=old["id"] if old else "rule-" + uuid.uuid4().hex,
                         name=lookup["name"].strip(), enabled=bool(candidate["enabled"]))
        if old:
            rules[rules.index(old)] = candidate
        else:
            rules.append(candidate)
        result["rule_id"] = candidate["id"]
    elif operation == "delete":
        rules.remove(old)
        result["rule_id"] = old["id"]
    else:
        old["enabled"] = operation == "resume"
        result["rule_id"] = old["id"]

    updated = validate_config(original)
    # original's fresh normalized rows were mutated above; compare to a separate
    # validation of the caller's untouched document to detect semantic no-ops.
    result["changed"] = updated != validate_config(document)
    return updated, result


def _cell(value):
    """Treat market/provider text as data inside the generated Markdown table."""
    value = html.escape(str(value), quote=True).replace("\r", " ").replace("\n", " ")
    for char in "\\`*_[]()":
        value = value.replace(char, "\\" + char)
    return value.replace("|", "&#124;")


def render_summary(config, result=None, error=None):
    lines = ["## 종목·가격 알림 관리", ""]
    if error:
        lines.extend(["**입력 처리 실패: 저장하지 않았습니다.**", "", _cell(error), ""])
    elif result and result.get("changed"):
        lines.extend(["입력값과 종목명 확인을 마쳤습니다. **아래 GitHub 저장 단계까지 성공해야 반영됩니다.**", ""])
    else:
        lines.extend(["현재 등록 목록입니다. 변경된 조건이 없습니다.", ""])
    lines.extend(["범위는 목표가 기준이며 양 끝값을 포함합니다. 한국은 KRW, 미국은 USD입니다.", "",
                  "| ID | 시장 | 티커 | 종목명 | 목표가 | -% | +% | 알림 가격 범위 | 방식 | 사용 |",
                  "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |"])
    for row in config["rules"]:
        target = Decimal(row["target_price"])
        lower = target * (1 - Decimal(row["lower_pct"]) / 100)
        upper = target * (1 + Decimal(row["upper_pct"]) / 100)
        values = (row["id"], row["market"], row["ticker"], row["name"], row["target_price"],
                  row["lower_pct"], row["upper_pct"], f"{lower:f} ~ {upper:f}",
                  "거래일마다" if row["mode"] == "daily" else "진입 시 1회", "사용" if row["enabled"] else "일시정지")
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    if not config["rules"]:
        lines.extend(["", "아직 등록된 종목이 없습니다. save를 선택하고 티커·목표가·비율을 입력하세요."])
    lines.extend(["", "등록·수정한 조건은 다음 한국 시간 오전 7시 자동 확인부터 사용됩니다.",
                  "일시정지·재시작·삭제는 시장과 티커만 입력하면 됩니다. 같은 티커에 조건이 여러 개면 ID도 입력하세요.", ""])
    return "\n".join(lines)


def _atomic_write(path, config):
    path = Path(path)
    descriptor, raw = tempfile.mkstemp(dir=path.parent, prefix=".watchlist-", suffix=".tmp")
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="웹 입력창의 종목·가격 조건을 검증하고 준비합니다.")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--config", default="watchlist.json")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)
    config, result, error = {"version": 1, "rules": []}, None, None
    try:
        config = load_config(args.config)
        if not args.event:
            raise ValueError("웹 입력 이벤트 파일이 없습니다. GitHub의 종목 관리 Run workflow를 사용하세요.")
        event_path = Path(args.event)
        if event_path.stat().st_size > 262_144:
            raise ValueError("입력 이벤트가 너무 큽니다.")
        event = json.loads(event_path.read_text(encoding="utf-8-sig"))
        if not isinstance(event, dict) or not isinstance(event.get("inputs", {}), dict):
            raise ValueError("웹 입력 이벤트 형식을 확인할 수 없습니다.")
        updated, result = apply_inputs(config, event.get("inputs", {}), NaverProvider())
        if result["changed"]:
            _atomic_write(args.config, updated)
        config = updated
    except (OSError, ValueError, RuntimeError) as exc:
        error = str(exc)
    if args.summary:
        try:
            with Path(args.summary).open("a", encoding="utf-8") as handle:
                handle.write(render_summary(config, result, error))
        except OSError:
            error = error or "실행 요약을 저장할 수 없습니다. Actions 로그를 확인하세요."
    print(json.dumps({"error": error} if error else result, ensure_ascii=False, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
