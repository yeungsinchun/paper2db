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
        self.assertEqual(trailing_formula_run([1, 12]), {12})

    def test_contiguous_trailing_run_kept(self) -> None:
        self.assertEqual(trailing_formula_run([13, 14]), {13, 14})
        self.assertEqual(trailing_formula_run([14]), {14})
        self.assertEqual(trailing_formula_run([]), set())


if __name__ == "__main__":
    unittest.main()
