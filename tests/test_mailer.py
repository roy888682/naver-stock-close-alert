"""SMTP transport and delivery-uncertainty checks without sending email."""

import os
import smtplib
import unittest
from unittest.mock import Mock, patch, sentinel

from stock_alert.mailer import MailError, send_mail


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(smtp_host="smtp.example.com", smtp_port=465,
                             smtp_security="ssl", smtp_username="sender@example.com",
                             smtp_from="sender@example.com", recipient="recipient@example.com")
        self.smtp = Mock(spec=smtplib.SMTP)
        self.smtp.send_message.return_value = {}
        self.patches = [
            patch.dict(os.environ, {"SMTP_PASSWORD": "test-only-app-pass"}, clear=True),
            patch("stock_alert.mailer.ssl.create_default_context", return_value=sentinel.trusted_context),
            patch("stock_alert.mailer.smtplib.SMTP_SSL", return_value=self.smtp),
            patch("stock_alert.mailer.smtplib.SMTP", return_value=self.smtp),
        ]
        self.environment, self.context, self.ssl_constructor, self.plain_constructor = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_ssl_transport_uses_tls_and_explicit_envelope(self):
        message_id = send_mail(self.settings, "종가 알림", "종가 100 USD")
        self.ssl_constructor.assert_called_once_with("smtp.example.com", 465, timeout=25, context=sentinel.trusted_context)
        self.plain_constructor.assert_not_called()
        self.smtp.starttls.assert_not_called()
        self.context.assert_called_once_with()
        self.smtp.login.assert_called_once_with("sender@example.com", "test-only-app-pass")
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "recipient@example.com")
        self.assertEqual(message["Subject"], "종가 알림")
        self.assertIn("종가 100 USD", message.get_content())
        self.assertEqual(message_id, message["Message-ID"])
        self.assertEqual(self.smtp.send_message.call_args.kwargs,
                         dict(from_addr="sender@example.com", to_addrs=["recipient@example.com"]))
        self.assertNotIn("test-only-app-pass", message.as_string())

    def test_starttls_happens_before_authentication(self):
        self.settings.update(smtp_security="starttls", smtp_port=587)
        send_mail(self.settings, "test", "body")
        self.plain_constructor.assert_called_once_with("smtp.example.com", 587, timeout=25)
        self.ssl_constructor.assert_not_called()
        self.smtp.starttls.assert_called_once_with(context=sentinel.trusted_context)
        calls = [call[0] for call in self.smtp.mock_calls]
        self.assertLess(calls.index("starttls"), calls.index("login"))
        self.assertLess(calls.index("login"), calls.index("send_message"))
        self.assertEqual(self.smtp.ehlo.call_count, 2)

    def test_missing_password_fails_before_connecting(self):
        os.environ["SMTP_PASSWORD"] = ""
        with self.assertRaises(MailError) as caught:
            send_mail(self.settings, "test", "body")
        self.assertFalse(caught.exception.uncertain)
        self.ssl_constructor.assert_not_called()
        self.plain_constructor.assert_not_called()

    def test_explicit_web_password_takes_precedence_over_environment(self):
        self.settings["smtp_password"] = "saved-web-test-only"
        send_mail(self.settings, "test", "body")
        self.smtp.login.assert_called_once_with("sender@example.com", "saved-web-test-only")

    def test_explicitly_cleared_password_does_not_fall_back_to_environment(self):
        self.settings["smtp_password"] = ""
        with self.assertRaises(MailError):
            send_mail(self.settings, "test", "body")
        self.ssl_constructor.assert_not_called()

    def test_auth_error_is_sanitized_and_safe_to_retry(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"test-only-app-pass private-server-details")
        with self.assertRaises(MailError) as caught:
            send_mail(self.settings, "test", "body")
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn("test-only-app-pass", str(caught.exception))
        self.assertNotIn("private-server-details", str(caught.exception))
        self.smtp.send_message.assert_not_called()

    def test_accepted_message_stays_successful_when_quit_disconnects(self):
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected("connection closed after accepted DATA")
        message_id = send_mail(self.settings, "test", "body")
        self.assertTrue(message_id.startswith("<"))
        self.smtp.send_message.assert_called_once()
        self.smtp.close.assert_called_once()

    def test_connection_lost_during_data_is_uncertain(self):
        self.smtp.send_message.side_effect = TimeoutError("private transport trace")
        with self.assertRaises(MailError) as caught:
            send_mail(self.settings, "test", "body")
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn("private transport trace", str(caught.exception))
        self.smtp.send_message.assert_called_once()

    def test_explicit_data_rejection_is_safe_to_retry(self):
        self.smtp.send_message.side_effect = smtplib.SMTPDataError(554, b"message rejected")
        with self.assertRaises(MailError) as caught:
            send_mail(self.settings, "test", "body")
        self.assertFalse(caught.exception.uncertain)

    def test_connection_failure_before_data_is_safe_to_retry(self):
        self.ssl_constructor.side_effect = TimeoutError("private connection detail")
        with self.assertRaises(MailError) as caught:
            send_mail(self.settings, "test", "body")
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn("private connection detail", str(caught.exception))
        self.smtp.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
