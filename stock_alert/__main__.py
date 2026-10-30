import argparse
import errno
import http.client
import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .server import make_server
from .service import Service

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "naver-stock-close-alert"


class AlreadyRunning(RuntimeError):
    """Only raised when another process owns this data directory's lock."""


def load_env(path):
    """Minimal .env loader; no expansion or code execution, environment wins."""
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key == "SMTP_PASSWORD":
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)


@contextmanager
def instance_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "process.lock").open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise AlreadyRunning("같은 데이터 폴더를 사용하는 프로그램이 이미 실행 중입니다.") from None
            raise
    except BaseException:
        handle.close()
        raise
    try:
        yield
    finally:
        handle.close()


def _read_status(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.6)
    try:
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        raw = response.read(32769)
        if response.status != 200 or len(raw) > 32768:
            return None
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def find_running_instance(directory, fallback_port):
    """Attach to this folder's server; also recognize older releases on the default port."""
    metadata = Path(directory) / "dashboard.json"
    for attempt in range(3):
        try:
            if metadata.exists():
                if metadata.stat().st_size > 4096:
                    return None
                record = json.loads(metadata.read_text(encoding="utf-8"))
                url, token = record["url"], record["token"]
                if not isinstance(url, str):
                    return None
                parsed = urlsplit(url)
                if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
                        or parsed.username is not None or parsed.password is not None
                        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                        or not parsed.port or not isinstance(token, str) or not token):
                    return None
                status = _read_status(parsed.port)
                if status and status.get("app_id") == APP_ID and status.get("instance_token") == token:
                    return f"http://127.0.0.1:{parsed.port}"
            elif isinstance(fallback_port, int) and 1 <= fallback_port <= 65535:
                status = _read_status(fallback_port)
                # Releases before instance metadata existed have these three fields.
                if (status and "app_id" not in status and "instance_token" not in status
                        and isinstance(status.get("checking"), bool)
                        and isinstance(status.get("scheduler_running"), bool)
                        and "last_check" in status and (status["last_check"] is None or isinstance(status["last_check"], str))):
                    return f"http://127.0.0.1:{fallback_port}"
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if attempt < 2:
            time.sleep(0.1)
    return None


def _write_instance_metadata(directory, url, token):
    path = Path(directory) / "dashboard.json"
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(dict(url=url, token=token)), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_instance_metadata(directory):
    try:
        (Path(directory) / "dashboard.json").unlink(missing_ok=True)
    except OSError:
        pass


def scheduler(service, stop):
    service.scheduler_running = True
    try:
        while not stop.is_set():
            try:
                result = service.check(send=True)
                logging.info("Check: %s rules, %s accepted, %s errors", len(result["results"]), result["sent"], result["errors"])
            except RuntimeError:
                pass
            due = time.monotonic()
            while not stop.wait(1):
                if time.monotonic() - due >= service.settings()["interval_minutes"] * 60:
                    break
    finally:
        service.scheduler_running = False


def main(argv=None):
    parser = argparse.ArgumentParser(description="네이버증권 한국·미국 종가 이메일 알림")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true")
    group.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env(ROOT / ".env")
    directory = Path(os.environ.get("APP_DATA_DIR", ROOT / "data"))
    try:
        with instance_lock(directory):
            service = Service(directory / "alerts.db")
            if args.once or args.check_only:
                result = service.check(send=args.once)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1 if result["errors"] else 0
            server = make_server(service, args.host, args.port)
            service.instance_token = secrets.token_urlsafe(24)
            stop = threading.Event()
            worker = threading.Thread(target=scheduler, args=(service, stop), daemon=True)
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                _write_instance_metadata(directory, url, service.instance_token)
                worker.start()
                print(f"종가 알림: {url}\n종료: Ctrl+C", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                server.server_close()
                if worker.ident is not None:
                    worker.join(timeout=35)
                _remove_instance_metadata(directory)
        return 0
    except AlreadyRunning:
        if not args.once and not args.check_only:
            url = find_running_instance(directory, args.port)
            if url:
                print(f"이미 실행 중인 종가 알림: {url}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
        print("이미 실행 중인 종가 알림 프로그램이 있습니다. 기존 웹 화면을 사용하거나 기존 프로그램을 종료한 뒤 다시 실행하세요.")
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"실행 실패: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
