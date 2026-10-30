"""Exercise durable state using real local Git remotes, without credentials."""
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.action_state import StateError, restore, save


class ActionStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "runner"
        self.repo.mkdir()
        self.run_git("init", "--bare", str(self.remote), cwd=self.root)
        self.run_git("init", "--initial-branch=main", cwd=self.repo)
        self.original_cwd = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(self.cleanup)
        self.run_git("remote", "add", "origin", str(self.remote))
        Path("README.md").write_text("Application source", encoding="utf-8")
        self.run_git("add", "README.md")
        self.run_git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Source")
        self.run_git("push", "origin", "main")
        self.database = Path("data/alerts.db")
        self.marker = Path("data/github-state.json")

    def cleanup(self):
        os.chdir(self.original_cwd)
        self.temporary.cleanup()

    def run_git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def create_database(self, status="sent"):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("CREATE TABLE settings(key TEXT, value TEXT);"
                             "CREATE TABLE rules(id INTEGER PRIMARY KEY);"
                             "CREATE TABLE alerts(id INTEGER PRIMARY KEY, status TEXT);")
            db.execute("INSERT INTO alerts(status) VALUES (?)", (status,))

    def test_missing_branch_initializes_and_round_trip_preserves_pending_state(self):
        restore(self.database, self.marker)
        self.assertIsNone(json.loads(self.marker.read_text())["head"])
        self.create_database("pending")
        Path("data/smtp-credential.json").write_text("must never be published")
        save(self.database, self.marker)
        files = self.run_git("ls-tree", "--name-only", "alert-state", cwd=self.remote).decode().splitlines()
        self.assertEqual(files, ["alerts.db"])
        self.database.unlink()
        restore(self.database, self.marker)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT status FROM alerts").fetchone()[0], "pending")

    def test_second_save_is_a_descendant_and_does_not_replace_main(self):
        main_head = self.run_git("rev-parse", "main").strip()
        restore(self.database, self.marker)
        self.create_database()
        save(self.database, self.marker)
        old = self.run_git("rev-parse", "alert-state", cwd=self.remote).strip()
        restore(self.database, self.marker)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE alerts SET status='uncertain'")
        save(self.database, self.marker)
        self.assertEqual(self.run_git("rev-parse", "alert-state^", cwd=self.remote).strip(), old)
        self.assertEqual(self.run_git("rev-parse", "main", cwd=self.remote).strip(), main_head)

    def test_multiple_checkpoints_without_restore_advance_the_lease(self):
        restore(self.database, self.marker)
        self.create_database("pending")
        save(self.database, self.marker)
        pending_head = self.run_git("rev-parse", "alert-state", cwd=self.remote).decode().strip()
        self.assertEqual(json.loads(self.marker.read_text())["head"], pending_head)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE alerts SET status='sent'")
        save(self.database, self.marker)
        sent_head = self.run_git("rev-parse", "alert-state", cwd=self.remote).decode().strip()
        self.assertNotEqual(sent_head, pending_head)
        self.assertEqual(json.loads(self.marker.read_text())["head"], sent_head)
        self.assertEqual(self.run_git("rev-parse", "alert-state^", cwd=self.remote).decode().strip(), pending_head)
        # A final unchanged save succeeds without an extra commit.
        save(self.database, self.marker)
        self.assertEqual(self.run_git("rev-parse", "alert-state", cwd=self.remote).decode().strip(), sent_head)

    def test_transport_failure_is_not_treated_as_first_run(self):
        self.run_git("remote", "set-url", "origin", str(self.root / "missing.git"))
        with self.assertRaises(StateError):
            restore(self.database, self.marker)
        self.assertFalse(self.marker.exists())

    def test_remote_concurrent_change_rejects_stale_save(self):
        restore(self.database, self.marker)
        self.create_database()
        save(self.database, self.marker)
        restore(self.database, self.marker)
        old_marker = self.marker.read_bytes()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE alerts SET status='uncertain'")
        save(self.database, self.marker)
        self.marker.write_bytes(old_marker)
        with self.assertRaisesRegex(StateError, "다른 실행"):
            save(self.database, self.marker)

    def test_corrupt_database_does_not_overwrite_remote_state(self):
        restore(self.database, self.marker)
        self.create_database()
        save(self.database, self.marker)
        remote_head = self.run_git("rev-parse", "alert-state", cwd=self.remote).strip()
        restore(self.database, self.marker)
        self.database.write_bytes(b"not a sqlite database")
        with self.assertRaises(StateError):
            save(self.database, self.marker)
        self.assertEqual(self.run_git("rev-parse", "alert-state", cwd=self.remote).strip(), remote_head)

    def test_no_save_without_successful_restore(self):
        self.create_database()
        with self.assertRaises(StateError):
            save(self.database, self.marker)


if __name__ == "__main__":
    unittest.main()
