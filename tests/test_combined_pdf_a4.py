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
            _write_png(directory / "q1.png", (1100, 420))
            _write_png(directory / "q2.png", (400, 900))
            dest = png_pdf.combine_pngs_to_pdf(directory, overwrite=True)
            self.assertIsNotNone(dest)
            document = fitz.open(dest)
            try:
                self.assertEqual(len(document), 2)
                for page in document:
                    width, height = page.rect.width, page.rect.height
                    self.assertEqual(width, A4_WIDTH)
                    self.assertEqual(height, A4_HEIGHT)
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
                self.assertEqual(len(document), 2)
                for page in document:
                    self.assertEqual(page.rect.width, A4_WIDTH)
                    self.assertEqual(page.rect.height, A4_HEIGHT)
            finally:
                document.close()

    def test_extreme_landscape_rotates_to_a4_landscape(self) -> None:
        png_pdf = load_module("png_pdf", SCRIPTS / "png_pdf.py")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_png(directory / "q1.png", (2000, 40))
            dest = png_pdf.combine_pngs_to_pdf(directory, overwrite=True)
            document = fitz.open(dest)
            try:
                self.assertEqual(len(document), 1)
                page = document[0]
                self.assertEqual(page.rect.width, A4_HEIGHT)
                self.assertEqual(page.rect.height, A4_WIDTH)
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
                self.assertIn(SECTION_25_HEADING, page.get_text())
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
                self.assertIn("ch25 Radiation and Radioactivity", document[0].get_text())
                self.assertIn("2012 Q36", document[0].get_text())
                self.assertIn("2012 Q36", document[1].get_text())
            finally:
                document.close()
            lq_pdf = directory / "questions.pdf"
            lq.write_image_pdf(
                [lq_png], lq_pdf, title="ch25 Radiation and Radioactivity"
            )
            document = fitz.open(lq_pdf)
            try:
                self.assertIn("ch25 Radiation and Radioactivity", document[0].get_text())
                self.assertIn("2026 Q12", document[0].get_text())
                self.assertIn("2026 Q12", document[1].get_text())
            finally:
                document.close()


if __name__ == "__main__":
    unittest.main()
