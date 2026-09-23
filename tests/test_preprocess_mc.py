"""MC anchor preprocessing helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preprocess_mc import trailing_formula_run  # noqa: E402


class TestTrailingFormulaRun(unittest.TestCase):
    def test_early_mention_of_data_sheet_is_not_excluded(self) -> None:
        # sapp1a.pdf: page 1 instructions mention the data sheet; page 12 is it.
        self.assertEqual(trailing_formula_run([1, 12], 14), {12, 13})

    def test_contiguous_trailing_run_kept(self) -> None:
        self.assertEqual(trailing_formula_run([13, 14], 15), {13, 14})
        self.assertEqual(trailing_formula_run([14], 15), {14})
        self.assertEqual(trailing_formula_run([], 15), set())

    def test_unheaded_continuation_pages_excluded(self) -> None:
        # 2025p1a.pdf: page 13 is headed "List of data, formulae and
        # relationships"; page 14 continues the equation list without it.
        self.assertEqual(trailing_formula_run([13], 15), {13, 14})


if __name__ == "__main__":
    unittest.main()
