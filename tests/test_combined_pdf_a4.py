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


if __name__ == "__main__":
    unittest.main()
