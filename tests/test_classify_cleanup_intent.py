"""Behavioral checks for classify-branch cleanup intent."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


class TestGitignorePages(unittest.TestCase):
    """gitignore must ignore LQ page-render caches (git is the consumer)."""

    def test_pages_paths_are_ignored(self) -> None:
        probe = "output/lq/2099/pages/page01.png"
        result = subprocess.run(
            ["git", "check-ignore", "-v", probe],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("output/**/pages/", result.stdout)

    def test_question_crops_and_combined_not_ignored(self) -> None:
        for path in (
            "output/lq/2024/q1.png",
            "output/lq/2024/combined.pdf",
            "output/lq/2026/q1.png",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "-v", path],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"{path} should not be gitignored: {result.stdout}",
            )


class TestClassificationSplitArtifacts(unittest.TestCase):
    """Shipped public classification contracts after the rename split."""

    def test_ambiguous_top_level_names_absent(self) -> None:
        self.assertFalse((ROOT / "classified" / "classification.csv").exists())
        self.assertFalse((ROOT / "classified" / "classification.json").exists())

    def test_mc_classification_list_contract(self) -> None:
        path = ROOT / "classified" / "mc_classification.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 100)
        required = {
            "Year",
            "Question",
            "PrimarySection",
            "PrimaryName",
            "Reason",
            "PNG",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        with (ROOT / "classified" / "mc_classification.csv").open(encoding="utf-8") as fh:
            csv_rows = list(csv.DictReader(fh))
        self.assertEqual(len(csv_rows), len(rows))

    def test_lq_classification_list_contract(self) -> None:
        path = ROOT / "classified" / "lq_classification.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 50)
        required = {
            "Year",
            "Question",
            "PrimarySection",
            "PrimaryName",
            "PNG",
            "AnswerPNG",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        nested = ROOT / "classified" / "lq" / "classification.csv"
        self.assertTrue(nested.is_file())
        with nested.open(encoding="utf-8") as fh:
            nested_rows = list(csv.DictReader(fh))
        self.assertEqual(len(nested_rows), len(rows))


class TestPaperRenameAndNewYears(unittest.TestCase):
    def test_2023_renamed_and_2024_2026_present(self) -> None:
        self.assertFalse((ROOT / "paper" / "lq" / "2023q1b.pdf").exists())
        for name in ("2023p1b.pdf", "2024p1b.pdf", "2025p1b.pdf", "2026p1b.pdf"):
            path = ROOT / "paper" / "lq" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 1000)


class TestMcLlmWriterOutputs(unittest.TestCase):
    """classify_mc_llm.apply_classifications writes only mc_* split names."""

    def test_apply_classifications_writes_mc_names_as_list(self) -> None:
        import classify_mc_llm as m

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in m.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            records = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "statement",
                }
            ]
            decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [2],
                    "reason": "heat capacity",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "preview",
                }
            ]
            with mock.patch.object(m, "CLASSIFIED", classified):
                m.apply_classifications(records, decisions)

            names = {p.name for p in classified.iterdir() if p.is_file()}
            self.assertIn("mc_classification.json", names)
            self.assertIn("mc_classification.csv", names)
            self.assertNotIn("classification.json", names)
            self.assertNotIn("classification.csv", names)
            payload = json.loads((classified / "mc_classification.json").read_text())
            self.assertIsInstance(payload, list)
            self.assertEqual(payload[0]["PrimarySection"], 2)
            self.assertEqual(payload[0]["Reason"], "heat capacity")


class TestPreprocessPreservesCrops(unittest.TestCase):
    """pages-only preprocess must not delete existing question crops."""

    def test_crop_questions_flag_false_keeps_q_png(self) -> None:
        import fitz
        from PIL import Image

        import preprocess_lq as pl

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "exam.pdf"
            doc = fitz.open()
            doc.new_page(width=200, height=300)
            doc.save(source)
            doc.close()

            output_dir = tmp_path / "out"
            output_dir.mkdir()
            sentinel = b"SENTINEL-CROP-BYTES-DO-NOT-DELETE"
            crop_path = output_dir / "q1.png"
            crop_path.write_bytes(sentinel)

            pages = [Image.new("RGB", (120, 240), "white")]

            with (
                mock.patch.object(pl, "load_page_pngs", return_value=pages),
                mock.patch.object(
                    pl, "find_question_starts", return_value=[(1, 0, 12.0)]
                ),
                mock.patch.object(pl, "combine_pngs_to_pdf"),
                mock.patch.object(pl, "doc_has_jpeg_scans", return_value=False),
            ):
                count = pl.process_one(
                    source,
                    output_dir,
                    cover_pages=0,
                    scale=2.0,
                    max_questions=10,
                    crop_questions_flag=False,
                )

            self.assertEqual(count, 1)
            self.assertTrue(crop_path.is_file())
            self.assertEqual(crop_path.read_bytes(), sentinel)
            self.assertTrue((output_dir / "starts.json").is_file())


if __name__ == "__main__":
    unittest.main()
