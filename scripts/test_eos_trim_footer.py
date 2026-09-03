#!/usr/bin/env python3
"""Focused behavioral tests for EOS trim gating and footer matching.

Exercises public helpers with synthetic images/PDF pages and asserts observable
crop / detection outcomes (not source-string snapshots).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from preprocess_mc import FOOTER_RE, detect_footer_top  # noqa: E402
from split_mc import find_end_of_section_top, trim_end_of_section  # noqa: E402


def _font(size: int = 28):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _page(texts: list[tuple[str, float, float]], page_h=842.0, page_w=595.0) -> fitz.Page:
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    for text, x, y in texts:
        page.insert_text((x, y), text, fontsize=14, fontname="helv")
    return page


class EosTrimFooterTests(unittest.TestCase):
    def test_short_centered_bottom_band_is_not_trimmed(self):
        """Ink-fallback false cut: short centered bottom band must stay intact."""
        image = Image.new("RGB", (600, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 560, 200), fill=(20, 20, 20))
        draw.rectangle((180, 300, 420, 330), fill=(10, 10, 10))
        self.assertIsNone(find_end_of_section_top(image))
        trimmed = trim_end_of_section(image)
        self.assertEqual(trimmed.size, image.size)

    def test_end_of_section_ocr_line_is_trimmed(self):
        image = Image.new("RGB", (600, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 560, 220), fill=(30, 30, 30))
        draw.text((160, 340), "END OF SECTION A", fill="black", font=_font())
        cut = find_end_of_section_top(image)
        self.assertIsNotNone(cut)
        trimmed = trim_end_of_section(image)
        self.assertLess(trimmed.height, image.height)
        self.assertGreater(trimmed.height, 200)

    def test_footer_re_rejects_loose_phy_and_1a(self):
        for text in (
            "physical quantity",
            "physics concept",
            "1A",
            "Option 1A only",
            "the phy",
        ):
            self.assertIsNone(FOOTER_RE.search(text), text)

    def test_footer_re_accepts_real_footer_phrases(self):
        for text in (
            "DSE-PHY",
            "DSE PHY",
            "PHY 1A",
            "END OF SECTION A",
            "END OF PAPER",
        ):
            self.assertIsNotNone(FOOTER_RE.search(text), text)

    def test_detect_footer_ignores_physical_and_bare_1a(self):
        page = _page(
            [
                ("33. The physical quantity is measured in 1A units.", 50, 700),
                ("A. yes   B. no   C. maybe", 50, 730),
            ]
        )
        self.assertIsNone(
            detect_footer_top(page, scale=2.0, left=40.0, right=550.0, bottom_limit=820.0)
        )

    def test_detect_footer_finds_end_of_section(self):
        page = _page(
            [
                ("33. Final question body text here.", 50, 650),
                ("A. 1   B. 2   C. 3   D. 4", 50, 680),
                ("END OF SECTION A", 180, 780),
            ]
        )
        footer_y = detect_footer_top(page, scale=2.0, left=40.0, right=550.0, bottom_limit=820.0)
        self.assertIsNotNone(footer_y)
        self.assertGreater(footer_y, 700)

    def test_detect_footer_finds_dse_phy(self):
        page = _page(
            [
                ("33. Question text.", 50, 700),
                ("2024-DSE-PHY 1A", 200, 800),
            ]
        )
        footer_y = detect_footer_top(page, scale=2.0, left=40.0, right=550.0, bottom_limit=820.0)
        self.assertIsNotNone(footer_y)

    def test_committed_last_questions_have_no_eos_banner(self):
        import io
        import subprocess

        eos_re = re.compile(r"end\s*of\s*(section|paper)", re.I)
        samples = [("2024", 33), ("2025", 33), ("2012", 36), ("pp", 36)]
        root = ROOT.parent
        for year, number in samples:
            path = root / "output" / year / f"q{number}.png"
            self.assertTrue(path.exists(), path)
            image = Image.open(path)
            band = image.crop((0, max(0, image.height - 100), image.width, image.height))
            buf = io.BytesIO()
            band.save(buf, format="PNG")
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "6"],
                input=buf.getvalue(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            text = result.stdout.decode("utf-8", errors="replace")
            self.assertIsNone(eos_re.search(text), f"{path}: {text!r}")


if __name__ == "__main__":
    unittest.main()
