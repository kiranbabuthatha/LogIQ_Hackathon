"""Read-only integration test against the existing local SereneDB dataset.

Run with: PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
Counts below refer to log messages, not confirmed incidents.
"""

import unittest

from serene_log_insights.tools.error_trends import get_error_trends


class ErrorTrendsIntegrationTest(unittest.TestCase):
    def test_fatal_message_trends(self):
        rows = get_error_trends(level="FATAL")

        dates = [row["date_raw"] for row in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), len(set(dates)))
        self.assertTrue(all(row["log_count"] >= 0 for row in rows))
        self.assertEqual(sum(row["log_count"] for row in rows), 347)
        counts_by_date = {row["date_raw"]: row["log_count"] for row in rows}
        self.assertEqual(counts_by_date["2005.06.14"], 128)


if __name__ == "__main__":
    unittest.main()
