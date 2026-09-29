import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_week2 import covered_fraction
from civic_data import effective_date_from_source


class SourceCoverageTests(unittest.TestCase):
    def test_effective_date_comes_from_header_not_amendment_date(self):
        self.assertEqual(effective_date_from_source(None, '제목\n[시행 2025. 1. 31.] [개정 2024. 12. 30.]'), '2025-01-31')
        self.assertEqual(effective_date_from_source('20260101', '다른 본문'), '2026-01-01')
        self.assertIsNone(effective_date_from_source(None, '제목\n<개정 2024. 12. 30.>'))
        self.assertIsNone(effective_date_from_source(None, '본문\n'*10+'[시행 1990. 1. 1.]'))

    def test_union_and_clipping(self):
        self.assertEqual(covered_fraction(10, 30, [(0, 20), (15, 25)]), .75)
        self.assertEqual(covered_fraction(10, 30, [(0, 50), (10, 30)]), 1)
        self.assertEqual(covered_fraction(10, 30, [(30, 50)]), 0)
        self.assertEqual(covered_fraction(10, 30, []), 0)


if __name__ == '__main__':
    unittest.main()
