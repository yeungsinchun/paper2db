"""Behavioral check: LQ question PNGs are whole-page stacks."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
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

    def test_year_review_labels_first_question_on_each_start_page(self) -> None:
        review = load_module("lq_pdf_review", ROOT / "scripts" / "lq_pdf_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp) / "paper"
            paper.mkdir()
            src = fitz.open()
            try:
                for index in range(10):
                    page = src.new_page(width=500, height=700)
                    page.insert_text((40, 80), f"PAGE-{index}", fontsize=14)
                src.save(paper / "2012p1b.pdf")
            finally:
                src.close()
            out = Path(tmp) / "lq" / "2012"
            out.mkdir(parents=True)
            (out / "starts.json").write_text(
                json.dumps(
                    {
                        "questions": [
                            {"q": 1, "page_from": 0, "page_to": 0},
                            {"q": 2, "page_from": 0, "page_to": 1},
                            {"q": 5, "page_from": 6, "page_to": 7},
                            {"q": 6, "page_from": 8, "page_to": 8},
                            {"q": 7, "page_from": 9, "page_to": 9},
                            {"q": 9, "page_from": 4, "page_to": 4},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            review.PAPER_LQ = paper
            review.OUTPUT_LQ = Path(tmp) / "lq"
            review.write_year_review_pdfs("2012")
            document = fitz.open(out / "combined.pdf")
            try:
                texts = [page.get_text() for page in document]
                self.assertIn("2012 Q1", texts[0])
                self.assertNotIn("2012 Q2", texts[0])
                self.assertIn("2012 Q5", texts[6])
                self.assertIn("2012 Q6", texts[8])
                self.assertIn("2012 Q7", texts[9])
                self.assertIn("2012 Q9", texts[4])
            finally:
                document.close()

    def test_empty_section_questions_pdf_returns_without_error(self) -> None:
        review = load_module("lq_pdf_review", ROOT / "scripts" / "lq_pdf_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "questions.pdf"
            written = review.write_section_questions_pdf([("2099", 1)], dest)
            self.assertEqual(written, 0)
            self.assertFalse(dest.is_file())

    def test_year_review_skips_cover_when_starts_pages_is_one_less(self) -> None:
        review = load_module("lq_pdf_review", ROOT / "scripts" / "lq_pdf_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp) / "paper"
            paper.mkdir()
            src = fitz.open()
            try:
                for index in range(11):
                    page = src.new_page(width=500, height=700)
                    page.insert_text((40, 80), f"PAGE-{index}", fontsize=14)
                src.save(paper / "2012p1b.pdf")
            finally:
                src.close()
            out = Path(tmp) / "lq" / "2012"
            out.mkdir(parents=True)
            (out / "starts.json").write_text(
                json.dumps(
                    {
                        "pages": 10,
                        "questions": [
                            {"q": 1, "page_from": 0, "page_to": 0},
                            {"q": 2, "page_from": 0, "page_to": 1},
                            {"q": 9, "page_from": 4, "page_to": 5},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            review.PAPER_LQ = paper
            review.OUTPUT_LQ = Path(tmp) / "lq"
            review.write_year_review_pdfs("2012")
            combined = fitz.open(out / "combined.pdf")
            try:
                texts = [page.get_text() for page in combined]
                self.assertIn("PAGE-0", texts[0])
                self.assertNotIn("2012 Q1", texts[0])
                self.assertIn("2012 Q1", texts[1])
                self.assertIn("PAGE-1", texts[1])
                self.assertIn("2012 Q9", texts[5])
                self.assertIn("PAGE-5", texts[5])
            finally:
                combined.close()
            questions = fitz.open(out / "questions.pdf")
            try:
                first = questions[0].get_text()
                self.assertIn("2012 Q1", first)
                self.assertIn("PAGE-1", first)
                self.assertNotIn("PAGE-0", first)
            finally:
                questions.close()

    def test_sync_classified_copies_question_png_not_answer_crop(self) -> None:
        crop = load_module("crop_lq_from_pages", ROOT / "scripts" / "crop_lq_from_pages.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "output" / "lq" / "2099"
            src_dir.mkdir(parents=True)
            Image.new("RGB", (50, 80), (255, 255, 255)).save(src_dir / "q1.png")
            classified = (
                root
                / "classified"
                / "lq"
                / "01_Heat_and_Gases"
                / "03_Change_of_State"
            )
            classified.mkdir(parents=True)
            Image.new("RGB", (20, 10), (0, 0, 0)).save(classified / "2099-q1-ans.png")
            csv_path = root / "classified" / "lq" / "classification.csv"
            csv_path.write_text(
                "Year,Question,Primary,AllSections,Reason,PNG,AnswerPNG\n"
                "2099,1,3,3,test,output/lq/2099/q1.png,output/lq/2099/ans/q1.png\n",
                encoding="utf-8",
            )
            crop.ROOT = root
            copied = crop.sync_classified_question_pngs(["2099"])
            self.assertEqual(copied, 1)
            with Image.open(classified / "2099-q1.png") as question:
                self.assertEqual(question.size, (50, 80))
            with Image.open(classified / "2099-q1-ans.png") as answer:
                self.assertEqual(answer.size, (20, 10))


class TestCommittedLqWholePages(unittest.TestCase):
    def test_2026_pdf_offset_skips_cover(self) -> None:
        review = load_module("lq_pdf_review", ROOT / "scripts" / "lq_pdf_review.py")
        source = review.lq_source_pdf("2026")
        self.assertIsNotNone(source)
        document = fitz.open(source)
        try:
            self.assertEqual(review.pdf_page_offset("2026", len(document)), 1)
        finally:
            document.close()

    def test_2026_q1_is_full_exam_page_not_part_a_ycrop(self) -> None:
        path = ROOT / "output" / "lq" / "2026" / "q1.png"
        self.assertTrue(path.is_file())
        image = Image.open(path)
        width, height = image.size
        image.close()
        # Leftover y-crop was 2065x1024 (wider than tall, part (a) only).
        self.assertGreater(height, width)
        self.assertGreater(height, 2000)

    def test_2012_q9_includes_continuation_page(self) -> None:
        Image.MAX_IMAGE_PIXELS = 250_000_000
        path = ROOT / "output" / "lq" / "2012" / "q9.png"
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            width, height = image.size
        # Two stacked exam pages. A single leftover page is ~1.4x width.
        self.assertGreater(height / width, 2.0)

    def test_2026_combined_does_not_label_cover_as_q1(self) -> None:
        path = ROOT / "output" / "lq" / "2026" / "combined.pdf"
        self.assertTrue(path.is_file())
        document = fitz.open(path)
        try:
            self.assertNotIn("2026 Q1", document[0].get_text())
            self.assertIn("2026 Q1", document[1].get_text())
        finally:
            document.close()

    def test_2012_questions_pdf_q9_starts_on_exam_page_not_previous(self) -> None:
        questions = fitz.open(ROOT / "output" / "lq" / "2012" / "questions.pdf")
        try:
            texts = [page.get_text() for page in questions]
            q9 = next(i for i, text in enumerate(texts) if "2012 Q9" in text)
            # Continuation page is unlabeled and immediately follows.
            self.assertGreaterEqual(len(questions), q9 + 2)
            self.assertNotIn("2012 Q9", questions[q9 + 1].get_text())
        finally:
            questions.close()


if __name__ == "__main__":
    unittest.main()
