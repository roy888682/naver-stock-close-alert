"""Loopback-only browser API with same-origin mutation protection."""
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .provider import ProviderError

STATIC = Path(__file__).parent / "static"


def make_server(service, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "StockAlert"

        def log_message(self, fmt, *args):
            logging.info("HTTP %s %s", self.command, self.path.split("?")[0])

        def output(self, data, status=200, content_type="application/json; charset=utf-8"):
            payload = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def safe_host(self):
            raw_host = self.headers.get("Host", "")
            try:
                parsed = urlsplit("http://" + raw_host)
                return (parsed.hostname in ("127.0.0.1", "localhost", "::1") and
                        parsed.port == self.server.server_port and not parsed.username and not parsed.password and not parsed.path)
            except ValueError:
                return False

        def body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("본문 길이가 잘못되었습니다.") from None
            if not 0 <= length <= 65536:
                raise ValueError("요청 본문이 너무 큽니다.")
            # Consume bounded bodies before rejecting. Closing a Windows socket
            # with unread request bytes can reset it and discard the error reply.
            raw = self.rfile.read(length)
            if length > 32768:
                raise ValueError("요청 본문이 너무 큽니다.")
            if self.headers.get("X-Stock-Alert") != "1":
                raise PermissionError("요청 보안 헤더가 없습니다.")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers["Host"]:
                raise PermissionError("동일 출처 요청만 허용됩니다.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("JSON 형식으로 요청하세요.")
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON 객체를 입력하세요.")
            return data

        def dispatch(self):
            if not self.safe_host():
                return self.output({"error": "이 프로그램은 localhost 주소에서만 사용할 수 있습니다."}, 403)
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/api/lookup":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    if set(query) != {"market", "ticker"} or any(len(values) != 1 for values in query.values()):
                        raise ValueError("시장과 종목 티커를 하나씩 입력하세요.")
                    return self.output(service.lookup(query["market"][0], query["ticker"][0]))
                if path == "/api/rules":
                    return self.output(service.snapshot())
                if path == "/api/status":
                    return self.output(dict(app_id="naver-stock-close-alert", instance_token=getattr(service, "instance_token", None),
                                            checking=service.check_lock.locked(), last_check=service.last_check, scheduler_running=service.scheduler_running))
                files = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                         "/app.js": ("app.js", "application/javascript"), "/style.css": ("style.css", "text/css"),
                         "/static/app.js": ("app.js", "application/javascript"), "/static/style.css": ("style.css", "text/css")}
                if path in files:
                    filename, content_type = files[path]
                    return self.output((STATIC / filename).read_bytes(), content_type=content_type + "; charset=utf-8")
            elif self.command in ("POST", "PUT", "DELETE"):
                data = self.body()
                if self.command == "POST" and path == "/api/rules":
                    return self.output(service.save_rule(data), 201)
                match = re.fullmatch(r"/api/rules/([0-9]+)", path)
                if match and self.command == "PUT":
                    return self.output(service.save_rule(data, int(match[1])))
                if match and self.command == "DELETE":
                    service.delete_rule(int(match[1]))
                    return self.output({"ok": True})
                if self.command == "PUT" and path == "/api/settings":
                    return self.output(service.update_settings(data))
                if self.command == "DELETE" and path == "/api/settings/password":
                    return self.output(service.delete_password())
                if self.command == "POST" and path == "/api/check":
                    send = data.get("send", False)
                    if not isinstance(send, bool):
                        raise ValueError("send는 true 또는 false여야 합니다.")
                    return self.output(service.check(send=send))
                if self.command == "POST" and path == "/api/test-email":
                    return self.output(service.test_email())
            return self.output({"error": "요청한 주소를 찾을 수 없습니다."}, 404)

        def handle_request(self):
            self.connection.settimeout(30)
            try:
                self.dispatch()
            except PermissionError as exc:
                self.output({"error": str(exc)}, 403)
            except KeyError:
                self.output({"error": "종목을 찾을 수 없습니다."}, 404)
            except (ValueError, UnicodeError) as exc:
                self.output({"error": str(exc)}, 400)
            except ProviderError as exc:
                self.output({"error": str(exc)}, 502)
            except RuntimeError as exc:
                self.output({"error": str(exc)}, 409)
            except (ConnectionError, TimeoutError):
                pass
            except Exception:
                logging.exception("API error")
                self.output({"error": "서버 처리 오류가 발생했습니다."}, 500)

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request
        do_DELETE = handle_request

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
