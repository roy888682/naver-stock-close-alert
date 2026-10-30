"""Launch/relaunch behavior without starting a market check or sending mail."""
import errno
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import stock_alert.__main__ as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)

    def status_server(self, payload=None, code=200):
        state = dict(payload=payload if payload is not None else {
            "app_id": "naver-stock-close-alert", "instance_token": "test-instance-token", "scheduler_running": True,
            "checking": False, "last_check": None,
        }, code=code, requests=[])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append(self.path)
                body = json.dumps(state["payload"]).encode("utf-8")
                self.send_response(state["code"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

        self.addCleanup(close)
        state.update(port=server.server_port, url=f"http://127.0.0.1:{server.server_port}")
        return state

    def metadata(self, url, token="test-instance-token"):
        (self.data / "dashboard.json").write_text(json.dumps(dict(url=url, token=token)), encoding="utf-8")

    def test_lock_conflict_has_a_specific_exception_and_is_released(self):
        with launcher.instance_lock(self.data):
            with self.assertRaises(launcher.AlreadyRunning):
                with launcher.instance_lock(self.data):
                    self.fail("A second process must not enter the locked section")
        with launcher.instance_lock(self.data):
            self.assertTrue((self.data / "process.lock").exists())

    def test_open_failure_is_not_misreported_as_an_existing_instance(self):
        with patch.object(Path, "open", side_effect=PermissionError("Cannot open lock file")):
            with self.assertRaises(PermissionError):
                with launcher.instance_lock(self.data):
                    self.fail("Failed file open must not enter the locked section")

    def test_noncontention_lock_error_is_not_an_already_running_error(self):
        target = "msvcrt.locking" if os.name == "nt" else "fcntl.flock"
        with patch(target, side_effect=OSError(errno.EIO, "Device failure")):
            with self.assertRaises(OSError) as caught:
                with launcher.instance_lock(self.data):
                    self.fail("I/O failure must not enter the locked section")
        self.assertNotIsInstance(caught.exception, launcher.AlreadyRunning)

    def test_metadata_finds_the_authenticated_existing_custom_port(self):
        state = self.status_server()
        self.metadata(state["url"])
        found = launcher.find_running_instance(self.data, 1)
        self.assertEqual(found, state["url"])
        self.assertEqual(state["requests"], ["/api/status"])

    def test_metadata_token_mismatch_never_opens_that_dashboard(self):
        state = self.status_server()
        self.metadata(state["url"], token="another-instance-token")
        self.assertIsNone(launcher.find_running_instance(self.data, state["port"]))

    def test_metadata_requires_an_instance_token_in_status_response(self):
        state = self.status_server(dict(scheduler_running=True, checking=False, last_check=None))
        self.metadata(state["url"])
        self.assertIsNone(launcher.find_running_instance(self.data, state["port"]))

    def test_metadata_retries_a_server_that_is_still_starting(self):
        self.metadata("http://localhost:9123/")
        status = {"app_id": "naver-stock-close-alert", "instance_token": "test-instance-token"}
        with patch.object(launcher, "_read_status", side_effect=[None, None, status]) as read, \
             patch.object(launcher.time, "sleep") as sleep:
            self.assertEqual(launcher.find_running_instance(self.data, 8765), "http://127.0.0.1:9123")
            self.assertEqual(read.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_metadata_does_not_accept_an_http_error(self):
        state = self.status_server(code=503)
        self.metadata(state["url"])
        self.assertIsNone(launcher.find_running_instance(self.data, state["port"]))

    def test_invalid_metadata_url_is_never_used_or_downgraded_to_legacy(self):
        invalid_urls = ["https://127.0.0.1:8765", "http://example.invalid:8765",
                        "http://127.0.0.1", "http://127.0.0.1:0",
                        "http://127.0.0.1:65536", "http://user@127.0.0.1:8765",
                        "http://127.0.0.1:8765/other", "http://127.0.0.1:8765?token=x",
                        "http://127.0.0.1:8765#fragment"]
        for url in invalid_urls:
            with self.subTest(url=url):
                self.metadata(url)
                with patch.object(launcher, "_read_status") as read, patch.object(launcher.time, "sleep"):
                    self.assertIsNone(launcher.find_running_instance(self.data, 8765))
                    read.assert_not_called()

    def test_malformed_metadata_does_not_fall_back_to_an_unverified_port(self):
        (self.data / "dashboard.json").write_text("not-json", encoding="utf-8")
        with patch.object(launcher, "_read_status") as read, patch.object(launcher.time, "sleep"):
            self.assertIsNone(launcher.find_running_instance(self.data, 8765))
            read.assert_not_called()

    def test_metadata_with_invalid_value_types_is_ignored_safely(self):
        records = [[], None, {"url": 123, "token": "token"},
                   {"url": "http://127.0.0.1:8765", "token": []}]
        for record in records:
            with self.subTest(record=record):
                (self.data / "dashboard.json").write_text(json.dumps(record), encoding="utf-8")
                with patch.object(launcher, "_read_status") as read, patch.object(launcher.time, "sleep"):
                    self.assertIsNone(launcher.find_running_instance(self.data, 8765))
                    read.assert_not_called()

    def test_legacy_instance_without_metadata_is_recognized_from_its_original_status(self):
        state = self.status_server(dict(scheduler_running=True, checking=False, last_check=None))
        self.assertEqual(launcher.find_running_instance(self.data, state["port"]), state["url"])

    def test_legacy_fallback_rejects_unrelated_or_malformed_status(self):
        invalid = [{}, {"checking": False, "scheduler_running": True},
                   {"checking": "false", "scheduler_running": True, "last_check": None},
                   {"checking": False, "scheduler_running": 1, "last_check": None},
                   {"checking": False, "scheduler_running": True, "last_check": []},
                   {"app_id": "another-app", "checking": False, "scheduler_running": True, "last_check": None},
                   {"app_id": "naver-stock-close-alert", "checking": False, "scheduler_running": True, "last_check": None}]
        for status in invalid:
            with self.subTest(status=status), patch.object(launcher, "_read_status", return_value=status), patch.object(launcher.time, "sleep"):
                self.assertIsNone(launcher.find_running_instance(self.data, 8765))

    def test_duplicate_interactive_launch_opens_existing_page_and_exits_successfully(self):
        existing = "http://127.0.0.1:9123"
        with patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
             patch.object(launcher, "load_env"), \
             patch.object(launcher, "instance_lock", side_effect=launcher.AlreadyRunning("Already running")), \
             patch.object(launcher, "find_running_instance", return_value=existing), \
             patch.object(launcher, "Service") as service, \
             patch.object(launcher.webbrowser, "open") as browser, \
             redirect_stdout(io.StringIO()) as output:
            result = launcher.main([])
        self.assertEqual(result, 0)
        browser.assert_called_once_with(existing)
        service.assert_not_called()
        self.assertIn(existing, output.getvalue())

    def test_duplicate_no_browser_launch_reports_url_without_opening_or_checking(self):
        existing = "http://127.0.0.1:9123"
        with patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
             patch.object(launcher, "load_env"), \
             patch.object(launcher, "instance_lock", side_effect=launcher.AlreadyRunning("Already running")), \
             patch.object(launcher, "find_running_instance", return_value=existing), \
             patch.object(launcher, "Service") as service, \
             patch.object(launcher.webbrowser, "open") as browser, \
             redirect_stdout(io.StringIO()) as output:
            result = launcher.main(["--no-browser"])
        self.assertEqual(result, 0)
        browser.assert_not_called()
        service.assert_not_called()
        self.assertIn(existing, output.getvalue())

    def test_duplicate_batch_launch_never_runs_a_second_check(self):
        for option in ("--once", "--check-only"):
            with self.subTest(option=option), \
                 patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
                 patch.object(launcher, "load_env"), \
                 patch.object(launcher, "instance_lock", side_effect=launcher.AlreadyRunning("Already running")), \
                 patch.object(launcher, "find_running_instance", return_value="http://127.0.0.1:9123"), \
                 patch.object(launcher, "Service") as service, \
                 patch.object(launcher.webbrowser, "open") as browser, \
                 redirect_stdout(io.StringIO()):
                result = launcher.main([option])
                self.assertEqual(result, 1)
                service.assert_not_called()
                browser.assert_not_called()

    def test_unreachable_existing_instance_has_a_useful_failure_without_new_checks(self):
        with patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
             patch.object(launcher, "load_env"), \
             patch.object(launcher, "instance_lock", side_effect=launcher.AlreadyRunning("Already running")), \
             patch.object(launcher, "find_running_instance", return_value=None), \
             patch.object(launcher, "Service") as service, \
             patch.object(launcher.webbrowser, "open") as browser, \
             redirect_stdout(io.StringIO()) as output:
            result = launcher.main([])
        self.assertEqual(result, 1)
        self.assertTrue(output.getvalue().strip())
        service.assert_not_called()
        browser.assert_not_called()

    def test_ordinary_os_error_does_not_try_to_reuse_an_instance(self):
        with patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
             patch.object(launcher, "load_env"), \
             patch.object(launcher, "instance_lock", side_effect=PermissionError("Data directory denied")), \
             patch.object(launcher, "find_running_instance") as discover, \
             patch.object(launcher, "Service") as service, \
             patch.object(launcher.webbrowser, "open") as browser, \
             redirect_stdout(io.StringIO()) as output:
            result = launcher.main([])
        self.assertEqual(result, 1)
        discover.assert_not_called()
        service.assert_not_called()
        browser.assert_not_called()
        self.assertIn("Data directory denied", output.getvalue())

    def test_first_interactive_launch_writes_actual_bound_port_and_cleans_metadata(self):
        with patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
             patch.object(launcher, "load_env"), \
             patch.object(launcher, "Service") as service_type, \
             patch.object(launcher, "make_server") as make_server, \
             patch.object(launcher.threading, "Thread") as worker_type, \
             patch.object(launcher.webbrowser, "open") as browser, \
             patch.object(launcher.secrets, "token_urlsafe", return_value="new-token-for-test"), \
             redirect_stdout(io.StringIO()) as output:
            server = make_server.return_value
            server.server_port = 9125

            def interrupt(**kwargs):
                metadata = json.loads((self.data / "dashboard.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata, {"url": "http://127.0.0.1:9125", "token": "new-token-for-test"})
                self.assertEqual(service_type.return_value.instance_token, "new-token-for-test")
                raise KeyboardInterrupt()

            server.serve_forever.side_effect = interrupt
            self.assertEqual(launcher.main(["--no-browser", "--port", "0"]), 0)
            service_type.return_value.check.assert_not_called()
            worker_type.return_value.start.assert_called_once()
            server.server_close.assert_called_once()
            browser.assert_not_called()
            self.assertIn("http://127.0.0.1:9125", output.getvalue())
            self.assertFalse((self.data / "dashboard.json").exists())

    def test_first_batch_launch_preserves_send_mode_and_does_not_start_dashboard(self):
        for option, send in (("--once", True), ("--check-only", False)):
            with self.subTest(option=option), \
                 patch.dict(os.environ, {"APP_DATA_DIR": str(self.data)}), \
                 patch.object(launcher, "load_env"), \
                 patch.object(launcher, "Service") as service_type, \
                 patch.object(launcher, "make_server") as make_server, \
                 patch.object(launcher.webbrowser, "open") as browser, \
                 redirect_stdout(io.StringIO()):
                service_type.return_value.check.return_value = {"results": [], "sent": 0, "errors": 0}
                self.assertEqual(launcher.main([option]), 0)
                service_type.return_value.check.assert_called_once_with(send=send)
                make_server.assert_not_called()
                browser.assert_not_called()
                self.assertFalse((self.data / "dashboard.json").exists())


if __name__ == "__main__":
    unittest.main()
