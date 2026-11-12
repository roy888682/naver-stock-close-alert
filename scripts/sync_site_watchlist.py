"""Fetch the live private Site before a GitHub report; never use stale data."""
import argparse
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_alert.cloud import validate_config

SITE_URL = "https://close-watch-list.hansharedid.chatgpt.site/api/runner/watchlist"
MAX_BYTES = 262_144


class SyncError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials or accept a sign-in page as a snapshot.
        return None


def fetch_watchlist(token, *, opener=None, sleep=time.sleep):
    if not isinstance(token, str) or len(token.strip()) < 32 or len(token) > 8192 or any(c in token for c in "\r\n\x00"):
        raise SyncError("GitHub Actions Secret SITE_SERVICE_TOKEN을 등록하세요.")
    opener = opener or build_opener(NoRedirect())
    request = Request(SITE_URL, headers={
        "OAI-Sites-Authorization": "Bearer " + token.strip(),
        "Accept": "application/json", "Cache-Control": "no-cache",
        "User-Agent": "CloseWatch-GitHubRunner/1.0",
    })
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                if response.status != 200 or response.geturl() != SITE_URL:
                    raise SyncError("사이트 목록 응답을 확인할 수 없습니다. 발송을 중단했습니다.")
                if response.headers.get_content_type() != "application/json":
                    raise SyncError("사이트가 목록 대신 로그인 화면 또는 잘못된 응답을 보냈습니다.")
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise SyncError("사이트 목록 응답이 너무 큽니다. 발송을 중단했습니다.")
            try:
                return validate_config(json.loads(raw.decode("utf-8-sig")))
            except (ValueError, UnicodeError, TypeError):
                raise SyncError("사이트 목록의 전체 형식을 검증하지 못했습니다. 발송을 중단했습니다.") from None
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                sleep(2 * (attempt + 1))
                continue
            if exc.code in (401, 403):
                challenge = exc.headers.get("cf-mitigated", "") == "challenge" if exc.headers else False
                kind = "접속 확인 화면" if challenge else "접근 인증"
                raise SyncError(f"사이트 {kind} 실패 (HTTP {exc.code}): SITE_SERVICE_TOKEN과 사이트 접근 설정을 확인하세요.") from None
            raise SyncError("사이트 목록 HTTP 요청 실패. 과거 목록으로 대신 발송하지 않았습니다.") from None
        except (URLError, OSError, socket.timeout):
            if attempt < 2:
                sleep(2 * (attempt + 1))
                continue
            raise SyncError("사이트 목록 네트워크 연결 실패. 과거 목록으로 대신 발송하지 않았습니다.") from None
    raise SyncError("사이트 목록을 읽지 못했습니다.")


def write_snapshot(path, document):
    document = validate_config(document)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".site-watchlist-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="비공개 사이트의 최신 종목 목록 읽기")
    parser.add_argument("--output", default="data/site-watchlist.json")
    args = parser.parse_args(argv)
    try:
        document = fetch_watchlist(os.environ.get("SITE_SERVICE_TOKEN", ""))
        write_snapshot(args.output, document)
        print(json.dumps({"live_site_read": True, "rules": len(document["rules"])}, ensure_ascii=False))
        return 0
    except (SyncError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, SyncError) else "사이트 목록 저장 실패. 발송을 중단했습니다."
        print(json.dumps({"error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
