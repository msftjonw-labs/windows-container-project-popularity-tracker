import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import weekly_report


class WeeklyReportTests(unittest.TestCase):
    def test_report_path_uses_generation_date_not_week_start(self):
        self.assertEqual(
            weekly_report.weekly_report_path(date(2026, 10, 22)),
            weekly_report.REPORT_DIRECTORY
            / "windows-container-project-popularity-tracker-2026-10-22.pdf",
        )
        self.assertEqual(
            weekly_report.REPORT_DIRECTORY,
            Path(weekly_report.__file__).resolve().parent.parent / "report-history",
        )

    def test_report_path_defaults_to_current_pacific_date(self):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                self.assertEqual(tz, weekly_report.PACIFIC_STANDARD_TIME)
                return cls(2026, 10, 22, tzinfo=tz)

        with patch.object(weekly_report, "datetime", FixedDateTime):
            self.assertEqual(
                weekly_report.weekly_report_path().name,
                "windows-container-project-popularity-tracker-2026-10-22.pdf",
            )

    def test_report_creates_history_directory_and_dated_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            report_directory = Path(directory) / "report-history"
            with patch.object(weekly_report, "REPORT_DIRECTORY", report_directory):
                result = weekly_report.generate_weekly_report(
                    [("org/project", 20, ["Top 3"])], date(2026, 10, 22)
                )
            self.assertEqual(
                result,
                report_directory
                / "windows-container-project-popularity-tracker-2026-10-22.pdf",
            )
            self.assertTrue(result.read_bytes().startswith(b"%PDF-1.4"))

    def test_week_start_uses_monday_and_pacific_date(self):
        self.assertEqual(weekly_report.week_start(date(2026, 10, 22)), date(2026, 10, 19))
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, tzinfo=tz)

        with patch.object(weekly_report, "datetime", FixedDateTime):
            self.assertEqual(weekly_report.week_start(), date(2026, 10, 26))

    def test_report_contains_ordered_scores_and_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weekly.pdf"
            with patch.object(weekly_report, "weekly_report_path", return_value=path):
                result = weekly_report.generate_weekly_report([
                    ("org/second", 15, []),
                    ("org/first", 20, ["Top 3", "Previous Week's Top 3"]),
                ], date(2026, 10, 22))

            content = result.read_bytes()
            self.assertTrue(content.startswith(b"%PDF-1.4"))
            self.assertTrue(content.endswith(b"%%EOF\n"))
            self.assertIn(b"Weekly Project Popularity Ranking", content)
            self.assertIn(b"Week of 2026-10-19", content)
            self.assertLess(content.index(b"org/first"), content.index(b"org/second"))
            self.assertIn(b"Previous Week's Top 3", content)
            self.assertIn(b"(None)", content)

    def test_report_splits_long_rankings_across_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weekly.pdf"
            with patch.object(weekly_report, "weekly_report_path", return_value=path):
                weekly_report.generate_weekly_report([
                    (f"org/project-{index}", 100 - index, ["Top 3"])
                    for index in range(50)
                ], date(2026, 10, 22))

            content = path.read_bytes()
            self.assertEqual(content.count(b"/Type /Page "), 3)
            self.assertIn(b"(50) Tj ET", content)


if __name__ == "__main__":
    unittest.main()
