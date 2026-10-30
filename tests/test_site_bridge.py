"""A dated Site snapshot enables delivery; unrelated pushes never do."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import site_bridge as bridge


NOW = datetime(2026, 10, 29, 22, 0, tzinfo=timezone.utc)


def request(**changes):
    return {"version": 1, "site_id": bridge.SITE_ID, "request_date": "2026-10-30", **changes}


@contextmanager
def cwd(path):
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


class RequestTests(unittest.TestCase):
    def test_only_changed_request_and_current_korean_date_enable_send(self):
        self.assertEqual(bridge.decide_action("push", changed_paths=[bridge.REQUEST_FILE], request=request(), now=NOW), "check")
        self.assertEqual(bridge.decide_action("push", changed_paths=["watchlist.json"], request=request(), now=NOW), "check-only")
        self.assertEqual(bridge.decide_action("push", changed_paths=[], request=request(), now=NOW), "check-only")

    def test_request_fails_closed_for_wrong_site_version_shape_or_date(self):
        invalid = [None, [], {}, request(site_id="other-site"), request(version=True), request(version=2),
                   request(request_date="2026-10-29"), request(request_date="2026-10-31"),
                   request(request_date="2026-02-30"), request(request_date="2026-1-1"),
                   request(request_date="$(touch secret)"), request(extra="untrusted")]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                bridge.decide_action("push", changed_paths=[bridge.REQUEST_FILE], request=payload, now=NOW)

    def test_korean_midnight_uses_seoul_day_and_naive_time_is_rejected(self):
        before_midnight = datetime(2026, 10, 29, 14, 59, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            bridge.validate_request(request(), before_midnight)
        bridge.validate_request(request(request_date="2026-10-29"), before_midnight)
        with self.assertRaises(ValueError):
            bridge.validate_request(request(), datetime(2026, 10, 30))

    def test_manual_utilities_are_whitelisted_and_other_events_rejected(self):
        self.assertEqual(bridge.decide_action("workflow_dispatch"), "check-only")
        for action in bridge.ACTIONS:
            self.assertEqual(bridge.decide_action("workflow_dispatch", {"action": action}), action)
        for inputs in ([], {"action": None}, {"action": "check\nmalicious=value"}, {"action": "schedule"}):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                bridge.decide_action("workflow_dispatch", inputs)
        with self.assertRaises(ValueError):
            bridge.decide_action("schedule")


class GitAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.event = self.directory / "event.json"
        self.config = self.directory / "watchlist.json"
        self.output = self.directory / "output.txt"
        self.event.write_text(json.dumps({"inputs": {"action": "check-only"}}), encoding="utf-8")
        self.config.write_text(json.dumps({"version": 1, "rules": []}), encoding="utf-8")

    def cli(self, **changes):
        values = {"--event": self.event, "--event-name": "workflow_dispatch", "--config": self.config, "--output": self.output, **changes}
        arguments = [str(value) for item in values.items() for value in item]
        capture = io.StringIO()
        with redirect_stdout(capture):
            code = bridge.main(arguments)
        return code, json.loads(capture.getvalue())

    def test_cli_outputs_only_validated_fixed_action(self):
        code, result = self.cli()
        self.assertEqual((code, result), (0, {"action": "check-only"}))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "action=check-only\n")

    def test_invalid_config_or_action_never_writes_action_output(self):
        self.config.write_text('{"version":1,"rules":[{}]}', encoding="utf-8")
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("error", result)
        self.assertFalse(self.output.exists())
        self.event.write_text(json.dumps({"inputs": {"action": "check\nSMTP_PASSWORD=value"}}), encoding="utf-8")
        self.assertEqual(self.cli()[0], 1)
        self.assertFalse(self.output.exists())

    def test_stale_request_fails_before_any_runner_output(self):
        path = self.directory / bridge.REQUEST_FILE
        path.write_text(json.dumps(request(request_date="2000-01-01")), encoding="utf-8")
        with patch.object(bridge, "changed_paths", return_value=[bridge.REQUEST_FILE]):
            code, result = self.cli(**{"--event-name": "push", "--commit": "a" * 40, "--request": path})
        self.assertEqual(code, 1)
        self.assertIn("오늘 한국 날짜", result["error"])
        self.assertFalse(self.output.exists())

    def test_changed_paths_read_exact_commit_including_initial_commit(self):
        def git(*args):
            return subprocess.check_output(["git", "-c", "user.name=Bridge test", "-c", "user.email=test@example.invalid", *args],
                                           cwd=self.directory, stderr=subprocess.DEVNULL).decode("utf-8").strip()
        git("init", "-q")
        git("add", "watchlist.json")
        git("commit", "-qm", "Initial config")
        first = git("rev-parse", "HEAD")
        path = self.directory / bridge.REQUEST_FILE
        path.write_text(json.dumps(request()), encoding="utf-8")
        git("add", bridge.REQUEST_FILE)
        git("commit", "-qm", "Site request")
        second = git("rev-parse", "HEAD")
        with cwd(self.directory):
            self.assertEqual(bridge.changed_paths(first), ["watchlist.json"])
            self.assertEqual(bridge.changed_paths(second), [bridge.REQUEST_FILE])
            with self.assertRaises(ValueError):
                bridge.changed_paths("HEAD; touch secret")


if __name__ == "__main__":
    unittest.main()
