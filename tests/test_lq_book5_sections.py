"""Regression: Book 5 LQs land in every radioactivity section they test.

Chapter 25 (Radiation and Radioactivity) used to hold no primary LQ at all:
the keyword scorer only kept the latest-scoring section, so an alpha-penetration
part beside a half-life part was filed under ch26 only, and combine_lq_section_pdfs
only built the section PDF for primary rows, leaving ch25's PDF stale (rotated,
cut off). Fixtures are the tesseract text of the real Paper 1B stacks.
"""
from __future__ import annotations

import csv
import io
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "lq_ocr"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Sections each Book 5 LQ tests, primary (latest) first. Read off the source
# papers: e.g. 2014 Q10 (a) alpha source handled bare-handed -> ch25,
# (b) activity from half-life -> ch26.
EXPECTED_BOOK5: dict[tuple[str, int], list[int]] = {
    ("2012", 11): [27, 26, 25],
    ("2013", 9): [26],
    ("2014", 10): [26, 25],
    ("2015", 10): [27],
    ("2016", 9): [26, 25],
    ("2017", 10): [26, 25],
    ("2018", 10): [26, 25],
    ("2019", 10): [27, 26],
    ("2020", 10): [27],
    ("2021", 9): [26, 25],
    ("2022", 10): [27],
    ("2023", 9): [26, 25],
    ("2024", 13): [26, 25],
    ("2025", 12): [26, 25],
    ("2026", 12): [26, 25],
    ("pp", 14): [27, 26, 25],
}
EXPECTED_CH25 = sorted(key for key, secs in EXPECTED_BOOK5.items() if 25 in secs)


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_text(year: str, qn: int) -> str:
    return (FIXTURES / f"{year}-q{qn}.txt").read_text(encoding="utf-8")


class TestBook5Classifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import classify_lq_keywords as kw

        cls.kw = kw

    def test_every_book5_lq_lists_each_section_it_tests(self) -> None:
        for (year, qn), expected in EXPECTED_BOOK5.items():
            with self.subTest(year=year, q=qn):
                sections, reason = self.kw.classify_text(fixture_text(year, qn))
                self.assertEqual(sections, expected, reason)

    def test_ch25_problem_list_matches_source_papers(self) -> None:
        listed = sorted(
            key
            for key in EXPECTED_BOOK5
            if 25 in self.kw.classify_text(fixture_text(*key))[0]
        )
        self.assertEqual(listed, EXPECTED_CH25)
        self.assertIn(("2014", 10), listed)

    def test_bungee_activity_is_not_radioactivity(self) -> None:
        # 2013 Q6 says "an activity that involves jumping" - no Book 5 context.
        text = fixture_text("2013", 6)
        self.assertFalse(self.kw.is_book5(text.lower()))
        sections, _reason = self.kw.classify_text(text)
        self.assertTrue(all(sec < 25 for sec in sections), sections)

    def test_latent_heat_fusion_is_not_book5(self) -> None:
        text = "Calculate the energy needed to melt the ice. Latent heat of fusion of ice = 3.34 x 10^5 J/kg."
        self.assertFalse(self.kw.is_book5(text.lower()))
        self.assertEqual(self.kw.classify_text(text)[0], [3])

    def test_ocr_cache_is_keyed_by_png_geometry(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "q10.png"
            Image.new("RGB", (30, 40), "white").save(png)
            with mock.patch.object(self.kw, "OCR_CACHE", Path(tmp) / "ocr_cache"):
                cache = self.kw.ocr_cache_path(png, "2014", 10)
                self.assertEqual(cache.name, "q10.30x40.txt")
                # A stale cache from an older crop is dropped when the new one is written.
                cache.parent.mkdir(parents=True)
                stale = cache.parent / "q10.txt"
                stale.write_text("old band text", encoding="utf-8")
                fake = mock.Mock(stdout=b"fresh text")
                with mock.patch.object(self.kw.subprocess, "run", return_value=fake):
                    self.assertEqual(self.kw.ocr_png(png, cache), "fresh text")
                self.assertFalse(stale.exists())
                self.assertEqual(cache.read_text(encoding="utf-8"), "fresh text")
                # Cached text is reused without another tesseract call.
                with mock.patch.object(self.kw.subprocess, "run") as run:
                    self.assertEqual(self.kw.ocr_png(png, cache), "fresh text")
                    run.assert_not_called()


class TestLlmBook5Listings(unittest.TestCase):
    def test_stubbed_llm_uses_whole_stack_ocr_for_book5_sections(self) -> None:
        import classify_lq_llm as lq
        from PIL import Image

        def fake_chat_json(_system: str, user: str) -> dict:
            if "Year 2014 Q10" in user:
                return {"sections": [26], "reason": "dominant: activity / half-life"}
            if "Year 2012 Q11" in user:
                return {
                    "sections": [27, 25],
                    "reason": "nuclear energy plus decay",
                }
            raise AssertionError(f"unexpected prompt: {user[:80]!r}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = []
            fixtures = iter((fixture_text("2014", 10), fixture_text("2012", 11)))

            def fake_tesseract(*_args, **kwargs):
                with Image.open(io.BytesIO(kwargs["input"])) as image:
                    self.assertEqual(image.size, (30, 3000))
                return mock.Mock(stdout=next(fixtures).encode("utf-8"))

            with (
                mock.patch.object(lq.keyword_classifier, "OCR_CACHE", root / "ocr_cache"),
                mock.patch.object(
                    lq.keyword_classifier.subprocess,
                    "run",
                    side_effect=fake_tesseract,
                ) as run,
            ):
                for year, qn in (("2014", 10), ("2012", 11)):
                    png = root / year / f"q{qn}.png"
                    png.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (30, 3000), "white").save(png)
                    records.append(lq._ocr_one((year, str(png), qn)))

                with mock.patch.object(lq, "chat_json", side_effect=fake_chat_json):
                    q2014, q2012 = [lq.classify_one(record) for record in records]
                self.assertEqual(run.call_count, 2)

        self.assertEqual(q2014["sections"], EXPECTED_BOOK5[("2014", 10)])
        self.assertIn(25, q2014["sections"])
        self.assertEqual(q2012["sections"], EXPECTED_BOOK5[("2012", 11)])
        self.assertEqual(q2012["sections"], [27, 26, 25])
        self.assertEqual(lq.normalize_sections([27, 26, 25]), [27, 26])
        self.assertEqual(
            lq.normalize_sections([27, 26, 25], limit=3),
            [27, 26, 25],
        )


class TestBook5ListingsKeepCallerSections(unittest.TestCase):
    """apply_book5_listings must not drop sections the LLM backend returned."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.kw = load_module("classify_lq_keywords", SCRIPTS / "classify_lq_keywords.py")

    def test_non_book5_primary_survives_with_book5_listed_after(self) -> None:
        text = fixture_text("2014", 10)
        self.assertTrue(self.kw.is_book5(text.lower()))
        sections, reason = self.kw.apply_book5_listings(text, [21], "llm: circuit")
        self.assertEqual(sections[0], 21)
        self.assertEqual(sections, [21, 26, 25])
        self.assertTrue(reason.startswith("llm: circuit; "))

    def test_book5_primary_is_resorted_and_extra_section_kept(self) -> None:
        text = fixture_text("2014", 10)
        sections, _reason = self.kw.apply_book5_listings(text, [25, 8], "llm")
        self.assertEqual(sections, [26, 25, 8])

    def test_keyword_path_unchanged(self) -> None:
        text = fixture_text("2014", 10)
        self.assertEqual(self.kw.apply_book5_listings(text, [], "")[0], EXPECTED_BOOK5[("2014", 10)])
        self.assertEqual(self.kw.classify_text(text)[0], EXPECTED_BOOK5[("2014", 10)])


def _write_paper(path: Path, pages: int, *, landscape_rotated: bool = False) -> None:
    """Fake Paper 1B: page 0 is a cover, then exam pages with a page mark."""
    doc = fitz.open()
    try:
        for index in range(pages):
            if landscape_rotated:
                page = doc.new_page(width=700, height=500)
                page.set_rotation(270)
            else:
                page = doc.new_page(width=500, height=700)
            page.insert_text((40, 80), f"PAGE-{index}", fontsize=14)
        doc.save(path)
    finally:
        doc.close()


class TestSectionPdfsIncludeEveryListedSection(unittest.TestCase):
    def test_non_primary_section_gets_combined_pdf_and_stale_pdfs_are_removed(self) -> None:
        combine = load_module("combine_lq_section_pdfs", SCRIPTS / "combine_lq_section_pdfs.py")
        review = load_module("lq_pdf_review", SCRIPTS / "lq_pdf_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper" / "lq"
            paper.mkdir(parents=True)
            _write_paper(paper / "2014p1b.pdf", 4)
            lq = root / "reconstructed" / "lq" / "2014"
            lq.mkdir(parents=True)
            (lq / "starts.json").write_text(
                json.dumps(
                    {
                        "pages": 3,
                        "questions": [{"q": 10, "page_from": 1, "page_to": 2}],
                    }
                ),
                encoding="utf-8",
            )
            classified = root / "classified" / "lq"
            classified.mkdir(parents=True)
            with (classified / "classification.csv").open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["Year", "Question", "Primary", "AllSections", "Reason", "PNG", "AnswerPNG"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "Year": "2014",
                        "Question": 10,
                        "Primary": 26,
                        "AllSections": "26;25",
                        "Reason": "test",
                        "PNG": "reconstructed/lq/2014/q10.png",
                        "AnswerPNG": "reconstructed/lq/2014/ans/q10.png",
                    }
                )
            ch27 = classified / "05_Radioactivity_and_Nuclear_Energy" / "27_Nuclear_Energy"
            ch27.mkdir(parents=True)
            (ch27 / "combined.pdf").write_bytes(b"%PDF-1.4 stale")
            # Pre-rename name left by an older build in a section that stays populated.
            ch25 = classified / "05_Radioactivity_and_Nuclear_Energy" / "25_Radiation_and_Radioactivity"
            ch25.mkdir(parents=True)
            (ch25 / "questions.pdf").write_bytes(b"%PDF-1.4 legacy")

            review.PAPER_LQ = paper
            review.OUTPUT_LQ = root / "reconstructed" / "lq"
            with (
                mock.patch.object(combine, "ROOT", root),
                mock.patch.object(combine, "CLASSIFIED_LQ", classified),
                mock.patch.object(combine, "CSV_PATH", classified / "classification.csv"),
                mock.patch.object(combine, "PERF_PATH", classified / "candidate_performance.json"),
                mock.patch.object(combine, "write_section_questions_pdf", review.write_section_questions_pdf),
                mock.patch.object(combine, "parse_args", return_value=mock.Mock(overwrite=True)),
            ):
                combine.main()

            ch26 = classified / "05_Radioactivity_and_Nuclear_Energy" / "26_Rate_of_Decay_and_Uses_of_Radionuclides"
            for folder in (ch25, ch26):
                with self.subTest(folder=folder.name):
                    document = fitz.open(folder / "combined.pdf")
                    try:
                        self.assertEqual(len(document), 2)
                        self.assertIn("2014 Q10", document[0].get_text())
                        self.assertIn("PAGE-2", document[0].get_text())
                        self.assertIn("PAGE-3", document[1].get_text())
                    finally:
                        document.close()
            self.assertIn("ch25 Radiation and Radioactivity", fitz.open(ch25 / "combined.pdf")[0].get_text())
            self.assertFalse((ch27 / "combined.pdf").exists(), "stale PDF of an empty section must go")
            self.assertFalse((ch25 / "questions.pdf").exists(), "pre-rename questions.pdf must go")

    def test_rotated_scan_is_packed_upright_and_complete(self) -> None:
        review = load_module("lq_pdf_review", SCRIPTS / "lq_pdf_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper" / "lq"
            paper.mkdir(parents=True)
            _write_paper(paper / "2026p1b.pdf", 4, landscape_rotated=True)
            lq = root / "reconstructed" / "lq" / "2026"
            lq.mkdir(parents=True)
            (lq / "starts.json").write_text(
                json.dumps({"pages": 3, "questions": [{"q": 12, "page_from": 1, "page_to": 2}]}),
                encoding="utf-8",
            )
            review.PAPER_LQ = paper
            review.OUTPUT_LQ = root / "reconstructed" / "lq"
            dest = root / "questions.pdf"
            written = review.write_section_questions_pdf([("2026", 12)], dest, title="ch25 test")
            self.assertEqual(written, 2)
            document = fitz.open(dest)
            try:
                self.assertEqual(len(document), 2)
                for page in document:
                    self.assertEqual((page.rect.width, page.rect.height), (595.0, 842.0))
                    # The placed page keeps its on-screen (portrait) orientation
                    # and its whole height fits inside the A4 printable area.
                    drawn = page.get_text("dict")
                    blocks = [b for b in drawn["blocks"] if b.get("type") == 0]
                    self.assertTrue(blocks)
                    for block in blocks:
                        x0, y0, x1, y1 = block["bbox"]
                        self.assertLess(y1, page.rect.height - 30)
                        self.assertGreaterEqual(x0, 30)
                texts = [page.get_text() for page in document]
                self.assertIn("PAGE-2", texts[0])
                self.assertIn("PAGE-3", texts[1])
            finally:
                document.close()


def _generated_bank_is_current() -> bool:
    """True when classified/ was produced from the reconstructed/ layout.

    classification.csv is generated output whose PNG column names the crop it
    was built from; rows still pointing at output/ come from an older build.
    """
    csv_path = ROOT / "classified" / "lq" / "classification.csv"
    if not csv_path.is_file() or not (ROOT / "reconstructed" / "lq" / "2026" / "starts.json").is_file():
        return False
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return bool(rows) and all(row["PNG"].startswith("reconstructed/") for row in rows)


@unittest.skipUnless(
    _generated_bank_is_current(),
    "classified/ and reconstructed/ are generated (gitignored); run ./pipeline first",
)
class TestGeneratedCh25Bank(unittest.TestCase):
    """Checks the real classify-lq + section-pdfs output when it has been built."""

    CH25 = (
        ROOT
        / "classified"
        / "lq"
        / "05_Radioactivity_and_Nuclear_Energy"
        / "25_Radiation_and_Radioactivity"
    )

    def _rows(self) -> dict[tuple[str, int], dict]:
        with (ROOT / "classified" / "lq" / "classification.csv").open(encoding="utf-8") as fh:
            return {(r["Year"], int(r["Question"])): r for r in csv.DictReader(fh)}

    def test_classification_lists_ch25_for_every_expected_problem(self) -> None:
        rows = self._rows()
        for key in EXPECTED_CH25:
            with self.subTest(key=key):
                self.assertIn("25", rows[key]["AllSections"].split(";"))
        for key, expected in EXPECTED_BOOK5.items():
            with self.subTest(key=key):
                self.assertEqual(int(rows[key]["Primary"]), expected[0])

    def test_ch25_folder_and_combined_pdf_cover_every_expected_problem(self) -> None:
        review = load_module("lq_pdf_review", SCRIPTS / "lq_pdf_review.py")
        expected_pages = 0
        for year, qn in EXPECTED_CH25:
            self.assertTrue((self.CH25 / f"{year}-q{qn}.png").is_file(), (year, qn))
            span = review.question_range(year, qn)
            assert span is not None
            expected_pages += span[1] - span[0] + 1
        document = fitz.open(self.CH25 / "combined.pdf")
        try:
            self.assertEqual(len(document), expected_pages)
            joined = "\n".join(page.get_text() for page in document)
            for year, qn in EXPECTED_CH25:
                self.assertIn(f"{year} Q{qn}", joined)
            for index, page in enumerate(document):
                self.assertEqual((page.rect.width, page.rect.height), (595.0, 842.0))
                for info in page.get_image_info():
                    # 2-up scans (2015/2023/2025) place the whole spread and clip
                    # one half, so judge the part that is visible on the page.
                    visible = fitz.Rect(info["bbox"]) & page.rect
                    self.assertGreater(visible.height, visible.width, f"page {index} is sideways")
        finally:
            document.close()
