"""Credential persistence tests using generated test data, never account secrets."""
import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_alert.credentials import CredentialError, PasswordStore


class PasswordStoreTests(unittest.TestCase):
    username = "test-sender@example.invalid"
    host = "smtp.example.invalid"
    password = "testonlyabcdefgh"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "credentials" / "smtp.json"
        self.store = PasswordStore(self.path)

    def save(self, password=None):
        self.store.set(password or self.password, self.username, self.host)

    def test_missing_file_is_unconfigured_and_does_not_create_a_file(self):
        self.assertEqual(self.store.get(self.username, self.host), "")
        self.assertFalse(self.store.configured(self.username, self.host))
        self.assertFalse(self.path.exists())

    def test_password_round_trip_survives_store_restart(self):
        self.save()
        restarted = PasswordStore(self.path)
        self.assertTrue(restarted.configured(self.username, self.host))
        self.assertEqual(restarted.get(self.username, self.host), self.password)

    def test_overwrite_replaces_the_previous_password(self):
        self.save()
        replacement = "newfakepasswordx"
        self.store.set(replacement, self.username, self.host)
        restarted = PasswordStore(self.path)
        self.assertEqual(restarted.get(self.username, self.host), replacement)
        self.assertNotEqual(restarted.get(self.username, self.host), self.password)

    def test_password_is_bound_to_smtp_account_and_host(self):
        self.save()
        self.assertEqual(self.store.get("someone-else@example.invalid", self.host), "")
        self.assertEqual(self.store.get(self.username, "other.example.invalid"), "")
        self.assertFalse(self.store.configured("someone-else@example.invalid", self.host))

    def test_smtp_host_matching_is_case_insensitive(self):
        self.store.set(self.password, self.username, self.host.upper())
        self.assertEqual(self.store.get(self.username, self.host), self.password)
        self.assertEqual(self.store.get(self.username, self.host.upper()), self.password)

    def test_clear_persists_a_marker_without_a_secret(self):
        self.save()
        self.store.clear()
        restarted = PasswordStore(self.path)
        self.assertEqual(restarted.get(self.username, self.host), "")
        self.assertFalse(restarted.configured(self.username, self.host))
        marker = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(marker["platform"], "cleared")
        self.assertEqual(marker["secret"], "")
        self.assertEqual(marker["username"], "")
        self.assertNotIn(self.password, self.path.read_text(encoding="utf-8"))

    def test_clear_before_first_save_also_persists_marker(self):
        self.store.clear()
        self.assertTrue(self.path.is_file())
        self.assertEqual(PasswordStore(self.path).get(self.username, self.host), "")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["platform"], "cleared")

    def test_a_new_password_can_replace_a_clear_marker(self):
        self.store.clear()
        self.save()
        self.assertTrue(PasswordStore(self.path).configured(self.username, self.host))
        self.assertEqual(self.store.get(self.username, self.host), self.password)

    def test_corrupt_records_raise_sanitized_errors(self):
        self.path.parent.mkdir(parents=True)
        malformed = ["not-json " + self.password, "null", "[]", '{"version":99}',
                     json.dumps(dict(version=1, platform="private-file", username=self.username,
                                     host=self.host, secret="!invalid-base64!")),
                     json.dumps(dict(version=1, platform="private-file", username=self.username,
                                     host=self.host))]
        for content in malformed:
            with self.subTest(content_type=content[:20]):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(CredentialError) as caught:
                    self.store.get(self.username, self.host)
                message = str(caught.exception)
                self.assertNotIn(self.password, message)
                self.assertNotIn(str(self.path), message)
                self.assertNotIn("Traceback", message)

    def test_oversized_credential_record_is_rejected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("x" * 32769, encoding="utf-8")
        with self.assertRaises(CredentialError):
            self.store.get(self.username, self.host)

    def test_corrupt_encoded_secret_has_a_sanitized_error(self):
        self.save()
        record = json.loads(self.path.read_text(encoding="utf-8"))
        record["secret"] = base64.b64encode(b"\xff\xfe\xfd").decode("ascii")
        self.path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(CredentialError) as caught:
            self.store.get(self.username, self.host)
        self.assertNotIn(self.password, str(caught.exception))
        self.assertNotIn(str(self.path), str(caught.exception))

    def test_record_for_another_platform_is_not_used_as_a_password(self):
        self.path.parent.mkdir(parents=True)
        foreign = "private-file" if os.name == "nt" else "windows-dpapi"
        self.path.write_text(json.dumps(dict(version=1, platform=foreign, username=self.username,
                                             host=self.host, secret=base64.b64encode(self.password.encode()).decode())), encoding="utf-8")
        with self.assertRaises(CredentialError) as caught:
            self.store.get(self.username, self.host)
        self.assertNotIn(self.password, str(caught.exception))

    def test_failed_atomic_replace_keeps_old_password_and_removes_temporary(self):
        self.save()
        original_bytes = self.path.read_bytes()
        with patch("stock_alert.credentials.os.replace", side_effect=PermissionError("sensitive implementation path")):
            with self.assertRaises(CredentialError) as caught:
                self.store.set("replacementfake1", self.username, self.host)
        self.assertNotIn("sensitive implementation path", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), original_bytes)
        self.assertEqual(self.store.get(self.username, self.host), self.password)
        self.assertEqual(list(self.path.parent.glob(".smtp-*")), [])

    @unittest.skipUnless(os.name == "nt", "Windows protects saved credentials with DPAPI")
    def test_windows_file_contains_ciphertext_rather_than_plaintext_or_plain_base64(self):
        self.save()
        raw = self.path.read_bytes()
        record = json.loads(raw.decode("utf-8"))
        self.assertEqual(record["platform"], "windows-dpapi")
        self.assertNotIn(self.password.encode(), raw)
        decoded = base64.b64decode(record["secret"], validate=True)
        self.assertNotIn(self.password.encode(), decoded)
        self.assertEqual(self.store.get(self.username, self.host), self.password)

    @unittest.skipUnless(os.name != "nt", "POSIX fallback requires a private mode-0600 file")
    def test_posix_credential_file_is_readable_only_by_its_owner(self):
        self.save()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.store.get(self.username, self.host), self.password)


if __name__ == "__main__":
    unittest.main()
