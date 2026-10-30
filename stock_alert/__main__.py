import argparse
import json
import logging
import os
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from .server import make_server
from .service import Service

ROOT = Path(__file__).resolve().parent.parent


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
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("같은 데이터 폴더를 사용하는 프로그램이 이미 실행 중입니다.") from None
    try:
        yield
    finally:
        handle.close()


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


def main():
    parser = argparse.ArgumentParser(description="네이버증권 한국·미국 종가 이메일 알림")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true")
    group.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
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
            stop = threading.Event()
            worker = threading.Thread(target=scheduler, args=(service, stop), daemon=True)
            worker.start()
            url = f"http://127.0.0.1:{args.port}"
            print(f"종가 알림: {url}\n종료: Ctrl+C", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
                server.server_close()
                worker.join(timeout=35)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"실행 실패: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
