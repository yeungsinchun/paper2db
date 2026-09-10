#!/usr/bin/env python3
"""Build LQ review PDFs by splitting source Paper 1B PDFs onto A4 pages.

No PNG raster and no within-page crop. page_from..page_to from starts.json
are whole exam pages.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymupdf as fitz

from png_pdf import append_pdf_page_a4

ROOT = Path(__file__).resolve().parents[1]
PAPER_LQ = ROOT / "paper" / "lq"
OUTPUT_LQ = ROOT / "output" / "lq"


def paper_year_label(stem: str) -> str:
    name = stem.lower()
    if name.startswith("sapp"):
        return "sap"
    if name.startswith("ppp"):
        return "pp"
    match = re.search(r"(20\d{2})", name)
    if match:
        return match.group(1)
    raise SystemExit(f"Cannot derive year label from {stem!r}")


def lq_source_pdf(year: str) -> Path | None:
    for path in sorted(PAPER_LQ.glob("*.pdf")):
        if paper_year_label(path.stem) == year:
            return path
    return None


def load_starts(year: str) -> list[dict]:
    path = OUTPUT_LQ / year / "starts.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("questions") or [])


def question_range(year: str, qn: int) -> tuple[int, int] | None:
    for item in load_starts(year):
        if int(item["q"]) == qn:
            return int(item["page_from"]), int(item["page_to"])
    return None


def write_year_review_pdfs(year: str) -> None:
    source = lq_source_pdf(year)
    if source is None or not source.is_file():
        print(f"skip LQ PDF review {year}: missing paper/lq PDF")
        return
    questions = load_starts(year)
    out_dir = OUTPUT_LQ / year
    out_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(source)
    try:
        combined = fitz.open()
        first_q_page = {}
        for item in questions:
            qn = int(item["q"])
            page_from = int(item["page_from"])
            if page_from not in first_q_page:
                first_q_page[page_from] = qn
        try:
            for pno in range(len(src)):
                qn = first_q_page.get(pno)
                label = f"{year} Q{qn}" if qn is not None else None
                append_pdf_page_a4(combined, src, pno, label=label)
            dest = out_dir / "combined.pdf"
            combined.save(dest, garbage=4, deflate=True)
            print(f"Wrote {dest} ({combined.page_count} A4 pages from {source.name})")
        finally:
            combined.close()

        questions_pdf = fitz.open()
        try:
            for item in sorted(questions, key=lambda item: int(item["q"])):
                qn = int(item["q"])
                page_from = int(item["page_from"])
                page_to = int(item["page_to"])
                if page_from >= len(src):
                    continue
                page_to = min(page_to, len(src) - 1)
                for pno in range(page_from, page_to + 1):
                    label = f"{year} Q{qn}" if pno == page_from else None
                    append_pdf_page_a4(questions_pdf, src, pno, label=label)
            dest = out_dir / "questions.pdf"
            questions_pdf.save(dest, garbage=4, deflate=True)
            print(
                f"Wrote {dest} ({questions_pdf.page_count} A4 pages, "
                f"{len(questions)} questions)"
            )
        finally:
            questions_pdf.close()
    finally:
        src.close()


def write_section_questions_pdf(
    items: list[tuple[str, int]],
    dest: Path,
    *,
    title: str | None = None,
) -> int:
    """Concatenate whole LQ exam pages for (year, q) items, year then Q order."""
    if not items:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    written = 0
    title_used = False
    try:
        for year, qn in items:
            source = lq_source_pdf(year)
            span = question_range(year, qn)
            if source is None or span is None:
                continue
            page_from, page_to = span
            src = fitz.open(source)
            try:
                if page_from >= len(src):
                    continue
                page_to = min(page_to, len(src) - 1)
                for pno in range(page_from, page_to + 1):
                    heading = title if (title and not title_used) else None
                    label = f"{year} Q{qn}" if pno == page_from else None
                    append_pdf_page_a4(
                        document, src, pno, title=heading, label=label
                    )
                    title_used = True
                    written += 1
            finally:
                src.close()
        if written == 0:
            document.close()
            return 0
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", default=None)
    args = parser.parse_args()
    years = args.years
    if not years:
        years = sorted(
            path.name for path in OUTPUT_LQ.iterdir() if path.is_dir()
        )
    for year in years:
        write_year_review_pdfs(year)


if __name__ == "__main__":
    main()
