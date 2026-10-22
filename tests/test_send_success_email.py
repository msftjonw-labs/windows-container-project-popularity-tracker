import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import send_success_email as notifier


class SuccessEmailTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_FROM": "tracker@example.com",
            "SMTP_TO": "first@example.com, second@example.com",
            "SMTP_USERNAME": "tracker",
            "SMTP_PASSWORD": "test-password",
            "GITHUB_REPOSITORY": "owner/tracker",
            "GITHUB_WORKFLOW": "Popularity Tracker",
            "GITHUB_RUN_ID": "123",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.smtp = Mock()
        self.smtp.send_message.return_value = {}
        smtp_patch = patch.object(notifier.smtplib, "SMTP")
        self.starttls = smtp_patch.start()
        self.addCleanup(smtp_patch.stop)
        ssl_patch = patch.object(notifier.smtplib, "SMTP_SSL")
        self.implicit_tls = ssl_patch.start()
        self.addCleanup(ssl_patch.stop)
        self.starttls.return_value.__enter__.return_value = self.smtp
        self.implicit_tls.return_value.__enter__.return_value = self.smtp
        self.report_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.report_dir.cleanup)
        self.report_path = Path(self.report_dir.name) / "weekly-report.pdf"
        self.report_path.write_bytes(b"%PDF-1.4 test")
        report_patch = patch.object(notifier, "weekly_report_path", return_value=self.report_path)
        report_patch.start()
        self.addCleanup(report_patch.stop)

    def test_starttls_authentication_and_message(self):
        notifier.main()
        self.starttls.assert_called_once_with("smtp.example.com", 587, timeout=30)
        self.implicit_tls.assert_not_called()
        self.assertEqual(
            [call[0] for call in self.smtp.method_calls],
            ["starttls", "login", "send_message"],
        )
        context = self.smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.smtp.login.assert_called_once_with("tracker", "test-password")
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "Popularity Tracker: sync succeeded")
        self.assertEqual(message["From"], "tracker@example.com")
        self.assertEqual(message["To"], "first@example.com, second@example.com")
        self.assertIn(
            "https://github.com/owner/tracker/actions/runs/123",
            message.get_body(preferencelist=("plain",)).get_content(),
        )
        attachment = list(message.iter_attachments())[0]
        self.assertEqual(attachment.get_filename(), self.report_path.name)
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(attachment.get_payload(decode=True), b"%PDF-1.4 test")
        self.assertEqual(self.smtp.send_message.call_args.kwargs, {
            "from_addr": "tracker@example.com",
            "to_addrs": ["first@example.com", "second@example.com"],
        })

    def test_implicit_tls_and_custom_port(self):
        with patch.dict(os.environ, {"SMTP_SECURITY": "ssl", "SMTP_PORT": "2465"}):
            notifier.main()
        self.starttls.assert_not_called()
        self.assertEqual(self.implicit_tls.call_args.args, ("smtp.example.com", 2465))
        self.assertTrue(self.implicit_tls.call_args.kwargs["context"].check_hostname)
        self.smtp.starttls.assert_not_called()

    def test_implicit_tls_default_port(self):
        with patch.dict(os.environ, {"SMTP_SECURITY": "ssl", "SMTP_PORT": ""}):
            notifier.main()
        self.assertEqual(self.implicit_tls.call_args.args[1], 465)

    def test_unconfigured_notification_is_skipped(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            notifier.main()
        self.starttls.assert_not_called()
        self.implicit_tls.assert_not_called()

    def test_missing_report_prevents_delivery(self):
        missing_report = Path(self.report_dir.name) / "missing.pdf"
        with patch.object(notifier, "weekly_report_path", return_value=missing_report):
            with self.assertRaisesRegex(FileNotFoundError, "Weekly ranking PDF not found"):
                notifier.main()
        self.starttls.assert_not_called()
        self.implicit_tls.assert_not_called()
        self.smtp.send_message.assert_not_called()

    def test_authentication_is_optional(self):
        with patch.dict(os.environ, {"SMTP_USERNAME": "", "SMTP_PASSWORD": ""}):
            notifier.main()
        self.smtp.login.assert_not_called()
        self.smtp.send_message.assert_called_once()

    def test_email_address_as_host_fails_before_connecting(self):
        for security in ("starttls", "ssl"):
            with self.subTest(security=security), patch.dict(os.environ, {
                "SMTP_HOST": " tracker@gmail.com ",
                "SMTP_SECURITY": security,
                "SMTP_PORT": "",
            }):
                with self.assertRaisesRegex(
                    ValueError, r"SMTP_HOST.*hostname, not an email address.*smtp\.gmail\.com"
                ):
                    notifier.main()
        self.starttls.assert_not_called()
        self.implicit_tls.assert_not_called()
        self.smtp.login.assert_not_called()
        self.smtp.send_message.assert_not_called()

    def test_invalid_configuration_fails_before_connecting(self):
        for settings in (
            {"SMTP_FROM": ""},
            {"SMTP_TO": " , "},
            {"SMTP_SECURITY": "none"},
            {"SMTP_PORT": "invalid"},
            {"SMTP_PORT": "0"},
            {"SMTP_PORT": "65536"},
            {"SMTP_USERNAME": ""},
            {"SMTP_PASSWORD": ""},
            {"SMTP_FROM": "tracker@example.com\nBcc: other@example.com"},
            {"SMTP_TO": "first@example.com\nBcc: other@example.com"},
        ):
            with self.subTest(settings=settings), patch.dict(os.environ, settings):
                with self.assertRaises(ValueError):
                    notifier.main()
        self.starttls.assert_not_called()
        self.implicit_tls.assert_not_called()

    def test_tls_failure_prevents_authentication_and_delivery(self):
        self.smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("No TLS")
        with self.assertRaises(smtplib.SMTPNotSupportedError):
            notifier.main()
        self.smtp.login.assert_not_called()
        self.smtp.send_message.assert_not_called()

    def test_authentication_failure_prevents_delivery(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Rejected")
        with self.assertRaises(smtplib.SMTPAuthenticationError):
            notifier.main()
        self.smtp.send_message.assert_not_called()

    def test_delivery_failure_is_propagated(self):
        self.smtp.send_message.side_effect = smtplib.SMTPException("Delivery failed")
        with self.assertRaises(smtplib.SMTPException):
            notifier.main()

    def test_partial_recipient_rejection_fails(self):
        self.smtp.send_message.return_value = {"second@example.com": (550, b"Rejected")}
        with self.assertRaisesRegex(RuntimeError, "refused"):
            notifier.main()


if __name__ == "__main__":
    unittest.main()
