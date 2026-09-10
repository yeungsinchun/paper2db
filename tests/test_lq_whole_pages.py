"""Behavioral check: LQ question PNGs are whole-page stacks."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLqWholePages(unittest.TestCase):
    def test_build_year_stacks_full_pages_not_y_crops(self) -> None:
        crop = load_module("crop_lq_from_pages", ROOT / "scripts" / "crop_lq_from_pages.py")
        with tempfile.TemporaryDirectory() as tmp:
            year_dir = Path(tmp) / "2099"
            pages_dir = year_dir / "pages"
            pages_dir.mkdir(parents=True)
            # Two distinct pages: top half black vs bottom half black markers.
            page0 = Image.new("RGB", (200, 300), (255, 255, 255))
            for x in range(200):
                page0.putpixel((x, 10), (0, 0, 0))
            page1 = Image.new("RGB", (200, 300), (255, 255, 255))
            for x in range(200):
                page1.putpixel((x, 290), (0, 0, 0))
            page0.save(pages_dir / "page000.png")
            page1.save(pages_dir / "page001.png")
            (year_dir / "starts.json").write_text(
                json.dumps(
                    {
                        "questions": [
                            {"q": 1, "page_from": 0, "page_to": 1, "y": 50},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            written = crop.build_year(year_dir)
            self.assertEqual(written, 1)
            out = Image.open(year_dir / "q1.png")
            # Whole pages stacked → ~600px tall, not a y-crop from mid-page.
            self.assertEqual(out.size, (200, 600))
            self.assertEqual(out.getpixel((0, 10)), (0, 0, 0))
            self.assertEqual(out.getpixel((0, 590)), (0, 0, 0))


    def test_lq_review_pdf_is_a4_whole_pages_not_png_crop(self) -> None:
        png_pdf = load_module("png_pdf", ROOT / "scripts" / "png_pdf.py")
        with tempfile.TemporaryDirectory() as tmp:
            src_path = Path(tmp) / "src.pdf"
            doc = fitz.open()
            page = doc.new_page(width=500, height=700)
            page.insert_text((40, 80), "TOP-MARK", fontsize=14)
            page.insert_text((40, 650), "BOTTOM-MARK", fontsize=14)
            page = doc.new_page(width=500, height=700)
            page.insert_text((40, 80), "PAGE-TWO", fontsize=14)
            doc.save(src_path)
            doc.close()
            dest = Path(tmp) / "out.pdf"
            document = fitz.open()
            src_doc = fitz.open(src_path)
            try:
                png_pdf.append_pdf_page_a4(
                    document,
                    src_doc,
                    0,
                    title="ch25 Radiation and Radioactivity",
                    label="2026 Q12",
                )
                png_pdf.append_pdf_page_a4(document, src_doc, 1)
                document.save(dest)
            finally:
                src_doc.close()
                document.close()
            out = fitz.open(dest)
            try:
                self.assertEqual(len(out), 2)
                self.assertEqual(out[0].rect.width, 595.0)
                self.assertEqual(out[0].rect.height, 842.0)
                text0 = out[0].get_text()
                self.assertIn("ch25 Radiation and Radioactivity", text0)
                self.assertIn("2026 Q12", text0)
                self.assertIn("TOP-MARK", text0)
                self.assertIn("BOTTOM-MARK", text0)
                self.assertIn("PAGE-TWO", out[1].get_text())
            finally:
                out.close()


if __name__ == "__main__":
    unittest.main()
