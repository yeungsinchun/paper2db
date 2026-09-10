"""Written combined.pdf pages use portrait A4 (or landscape only when needed)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
A4_WIDTH = 595.0
A4_HEIGHT = 842.0


def load_module(name: str, path: Path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, size: tuple[int, int]) -> None:
    Image.new("RGB", size, (255, 255, 255)).save(path)


class TestCombinedPdfA4(unittest.TestCase):
    def test_combine_pngs_to_pdf_uses_a4_pages(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_png(directory / "q1.png", (1100, 200))
            _write_png(directory / "q2.png", (400, 180))
            dest = png_pdf.combine_pngs_to_pdf(directory, overwrite=True)
            self.assertIsNotNone(dest)
            document = fitz.open(dest)
            try:
                self.assertEqual(len(document), 1)
                page = document[0]
                self.assertEqual(page.rect.width, A4_WIDTH)
                self.assertEqual(page.rect.height, A4_HEIGHT)
                self.assertIn("Q1", page.get_text())
                self.assertIn("Q2", page.get_text())
            finally:
                document.close()

    def test_write_combined_uses_a4_pages(self) -> None:
        section = load_module(
            "combine_section_pdfs", SCRIPTS / "combine_section_pdfs.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            paths = [
                directory / "2019_q1.png",
                directory / "2019_q2.png",
            ]
            for path in paths:
                _write_png(path, (828, 315))
            dest = directory / "combined.pdf"
            section.write_combined(paths, dest)
            document = fitz.open(dest)
            try:
                self.assertEqual(len(document), 1)
                page = document[0]
                self.assertEqual(page.rect.width, A4_WIDTH)
                self.assertEqual(page.rect.height, A4_HEIGHT)
                self.assertIn("2019 Q1", page.get_text())
                self.assertIn("2019 Q2", page.get_text())
            finally:
                document.close()

    def test_combined_questions_sorted_by_year_then_number(self) -> None:
        section = load_module(
            "combine_section_pdfs", SCRIPTS / "combine_section_pdfs.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            names = ["2019_q2.png", "2012_q36.png", "2012_q1.png", "pp_q1.png"]
            paths = [directory / name for name in names]
            for path in paths:
                _write_png(path, (828, 200))
            ordered = sorted(paths, key=section.sort_key)
            dest = directory / "combined.pdf"
            section.write_combined(ordered, dest)
            document = fitz.open(dest)
            try:
                labels = [
                    span["text"].strip()
                    for block in document[0].get_text("dict")["blocks"]
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                    if span.get("text", "").strip()
                ]
                self.assertEqual(labels, ["2012 Q1", "2012 Q36", "2019 Q2", "pp Q1"])
            finally:
                document.close()

    def test_question_that_does_not_fit_starts_next_page(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_png(directory / "q1.png", (400, 900))
            _write_png(directory / "q2.png", (400, 900))
            dest = png_pdf.combine_pngs_to_pdf(directory, overwrite=True)
            document = fitz.open(dest)
            try:
                self.assertGreaterEqual(len(document), 2)
                self.assertIn("Q1", document[0].get_text())
                self.assertIn("Q2", document[1].get_text())
                self.assertNotIn("Q2", document[0].get_text())
            finally:
                document.close()


SECTION_25_HEADING = "ch25 Radiation and Radioactivity"
MC_25 = (
    ROOT
    / "classified"
    / "mc"
    / "05_Radioactivity_and_Nuclear_Energy"
    / "25_Radiation_and_Radioactivity"
    / "combined.pdf"
)
LQ_25 = (
    ROOT
    / "classified"
    / "lq"
    / "05_Radioactivity_and_Nuclear_Energy"
    / "25_Radiation_and_Radioactivity"
    / "questions.pdf"
)


class TestSection25Heading(unittest.TestCase):
    def test_heading_title_format(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        self.assertEqual(
            png_pdf.section_heading_title(25, "Radiation and Radioactivity"),
            SECTION_25_HEADING,
        )

    def test_section_25_mc_and_lq_open_with_heading(self) -> None:
        self.assertTrue(MC_25.is_file(), MC_25)
        self.assertTrue(LQ_25.is_file(), LQ_25)
        for path in (MC_25, LQ_25):
            document = fitz.open(path)
            try:
                page = document[0]
                self.assertEqual(page.rect.width, A4_WIDTH)
                self.assertEqual(page.rect.height, A4_HEIGHT)
                text = page.get_text()
                self.assertIn(SECTION_25_HEADING, text)
                self.assertNotIn("Items:", text)
            finally:
                document.close()

    def test_bare_qn_label_uses_year_parent_directory(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        with tempfile.TemporaryDirectory() as tmp:
            mc_dir = Path(tmp) / "2012"
            ans_dir = Path(tmp) / "lq" / "2012" / "ans"
            pp_dir = Path(tmp) / "pp"
            mc_dir.mkdir()
            ans_dir.mkdir(parents=True)
            pp_dir.mkdir()
            mc_png = mc_dir / "q1.png"
            ans_png = ans_dir / "q1.png"
            pp_png = pp_dir / "q3.png"
            _write_png(mc_png, (400, 180))
            _write_png(ans_png, (400, 180))
            _write_png(pp_png, (400, 180))
            self.assertEqual(png_pdf.png_item_label(mc_png), "2012 Q1")
            self.assertEqual(png_pdf.png_item_label(ans_png), "2012 Q1")
            self.assertEqual(png_pdf.png_item_label(pp_png), "pp Q3")
            dest = png_pdf.combine_pngs_to_pdf(mc_dir, overwrite=True)
            document = fitz.open(dest)
            try:
                self.assertIn("2012 Q1", document[0].get_text())
                self.assertNotIn("Q1\n", document[0].get_text().replace("2012 Q1", ""))
            finally:
                document.close()

    def test_question_page_carries_year_and_question_number(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        section = load_module(
            "combine_section_pdfs", SCRIPTS / "combine_section_pdfs.py"
        )
        lq = load_module(
            "combine_lq_section_pdfs", SCRIPTS / "combine_lq_section_pdfs.py"
        )
        self.assertEqual(png_pdf.png_item_label(Path("2012_q36.png")), "2012 Q36")
        self.assertEqual(png_pdf.png_item_label(Path("2026-q12.png")), "2026 Q12")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            mc_png = directory / "2012_q36.png"
            lq_png = directory / "2026-q12.png"
            _write_png(mc_png, (828, 315))
            _write_png(lq_png, (600, 400))
            mc_pdf = directory / "combined.pdf"
            section.write_combined(
                [mc_png], mc_pdf, title="ch25 Radiation and Radioactivity"
            )
            document = fitz.open(mc_pdf)
            try:
                self.assertEqual(len(document), 1)
                page = document[0]
                self.assertIn("ch25 Radiation and Radioactivity", page.get_text())
                self.assertIn("2012 Q36", page.get_text())
                self._assert_label_top_right(page, "2012 Q36")
            finally:
                document.close()
            lq_pdf = directory / "questions.pdf"
            lq.write_image_pdf(
                [lq_png], lq_pdf, title="ch25 Radiation and Radioactivity"
            )
            document = fitz.open(lq_pdf)
            try:
                self.assertEqual(len(document), 1)
                page = document[0]
                self.assertIn("ch25 Radiation and Radioactivity", page.get_text())
                self.assertIn("2026 Q12", page.get_text())
                self._assert_label_top_right(page, "2026 Q12")
            finally:
                document.close()

    def test_title_page_crop_stays_within_bottom_margin(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        lq = load_module(
            "combine_lq_section_pdfs", SCRIPTS / "combine_lq_section_pdfs.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "2026-q12.png"
            _write_png(png, (2097, 7366))
            dest = Path(tmp) / "answers.pdf"
            lq.write_image_pdf(
                [png], dest, title="ch25 Radiation and Radioactivity"
            )
            document = fitz.open(dest)
            try:
                page = document[0]
                self.assertEqual(page.rect.width, A4_WIDTH)
                self.assertEqual(page.rect.height, A4_HEIGHT)
                images = page.get_image_info()
                self.assertTrue(images)
                self.assertLessEqual(
                    images[0]["bbox"][3], A4_HEIGHT - png_pdf.A4_MARGIN + 0.5
                )
                self.assertIn("ch25 Radiation and Radioactivity", page.get_text())
                self.assertIn("2026 Q12", page.get_text())
            finally:
                document.close()

    def test_classified_lq_answers_pdf_pages_are_a4(self) -> None:
        answers = sorted((ROOT / "classified" / "lq").glob("*/*/answers.pdf"))
        self.assertGreaterEqual(len(answers), 1)
        for path in answers:
            document = fitz.open(path)
            try:
                self.assertGreaterEqual(len(document), 1, path)
                page = document[0]
                self.assertEqual(page.rect.width, A4_WIDTH, path)
                self.assertEqual(page.rect.height, A4_HEIGHT, path)
            finally:
                document.close()

    def _assert_label_top_right(self, page: fitz.Page, label: str) -> None:
        midpoint = page.rect.width / 2
        found = False
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip() != label:
                        continue
                    found = True
                    self.assertGreater(span["bbox"][0], midpoint)
        self.assertTrue(found, label)


if __name__ == "__main__":
    unittest.main()
