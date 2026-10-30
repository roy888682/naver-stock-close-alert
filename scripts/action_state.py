"""Persist the alert database on a dedicated Git branch, never an expiring cache.

The state branch is public when the repository is public. Only alerts.db is
written; the SMTP credential file and environment are deliberately excluded.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

BRANCH = "alert-state"
REF = "refs/heads/" + BRANCH


class StateError(RuntimeError):
    pass


def _git(*args, input=None, ok=(0,)):
    result = subprocess.run(["git", *args], input=input, capture_output=True)
    if result.returncode not in ok:
        # Git diagnostics can contain credential-bearing remote URLs.
        raise StateError(f"상태 저장소 Git 작업 실패 ({args[0]}). Actions 권한과 네트워크를 확인하세요.")
    return result


def _remote_head():
    result = _git("ls-remote", "--exit-code", "--heads", "origin", REF, ok=(0, 2))
    if result.returncode == 2:
        return None  # Git's explicit 'no matching ref'; transport errors are fatal.
    fields = result.stdout.decode("ascii").strip().split()
    if len(fields) != 2 or fields[1] != REF or not re.fullmatch(r"[0-9a-f]{40,64}", fields[0]):
        raise StateError("상태 브랜치 응답을 확인할 수 없습니다.")
    return fields[0]


def _validate_database(path):
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise StateError("알림 상태 데이터베이스가 손상되었습니다. 복원 후 다시 실행하세요.")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"rules", "alerts", "settings"} <= tables:
                raise StateError("알림 상태 데이터베이스의 필수 테이블이 없습니다.")
    except sqlite3.Error:
        raise StateError("알림 상태 데이터베이스를 읽을 수 없습니다. 기존 상태를 확인하세요.") from None


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(dir=path.parent, prefix=".alert-state-")
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(database, marker):
    database, marker = Path(database), Path(marker)
    marker.unlink(missing_ok=True)  # A failed restore must never authorize saving.
    head = _remote_head()
    if head is not None:
        _git("fetch", "--no-tags", "origin", REF)
        head = _git("rev-parse", "FETCH_HEAD").stdout.decode("ascii").strip()
        content = _git("show", f"{head}:alerts.db").stdout
        # Validate before replacing any local file.
        database.parent.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(dir=database.parent, prefix=".restore-")
        temporary = Path(raw)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            _validate_database(temporary)
            os.replace(temporary, database)
        finally:
            temporary.unlink(missing_ok=True)
    elif database.exists():
        raise StateError("상태 브랜치가 없는데 로컬 데이터가 있습니다. 초기 상태를 확인하세요.")
    _write_atomic(marker, json.dumps({"version": 1, "head": head}).encode("utf-8"))
    print("알림 상태 복원 완료." if head else "최초 실행: 새 알림 상태를 시작합니다.")


def save(database, marker):
    database, marker = Path(database), Path(marker)
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        head = record["head"]
        if record.get("version") != 1 or (head is not None and not re.fullmatch(r"[0-9a-f]{40,64}", head)):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise StateError("성공한 상태 복원 기록이 없어 저장하지 않습니다.") from None
    if not database.exists():
        print("실행에서 생성된 데이터베이스가 없어 상태 저장을 생략합니다.")
        return
    _validate_database(database)
    if _remote_head() != head:
        raise StateError("다른 실행이 상태 브랜치를 변경했습니다. 덮어쓰지 않았습니다. Actions 실행을 확인하세요.")
    blob = _git("hash-object", "-w", "--", str(database)).stdout.decode("ascii").strip()
    tree = _git("mktree", input=f"100644 blob {blob}\talerts.db\n".encode("ascii")).stdout.decode("ascii").strip()
    if head is not None:
        old_tree = _git("rev-parse", f"{head}^{{tree}}").stdout.decode("ascii").strip()
        if tree == old_tree:
            print("알림 상태에 변경이 없습니다.")
            return
    args = ["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit-tree", tree, "-m", "Persist completed-close alert state"]
    if head is not None:
        args.extend(["-p", head])
    commit = _git(*args).stdout.decode("ascii").strip()
    # Never force-push: a concurrent update must reject this state write.
    _git("push", "origin", f"{commit}:{REF}")
    # A check may checkpoint pending mail before SMTP and save again after
    # acceptance. Advance the lease only after the remote accepted this commit.
    _write_atomic(marker, json.dumps({"version": 1, "head": commit}).encode("utf-8"))
    print("발송 기록을 alert-state 브랜치에 저장했습니다.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitHub Actions 알림 상태 복원·저장")
    parser.add_argument("action", choices=("restore", "save"))
    parser.add_argument("--database", default="data/alerts.db")
    parser.add_argument("--marker", default="data/github-state.json")
    args = parser.parse_args(argv)
    try:
        (restore if args.action == "restore" else save)(args.database, args.marker)
        return 0
    except (StateError, OSError) as exc:
        print(f"알림 상태 처리 실패: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
