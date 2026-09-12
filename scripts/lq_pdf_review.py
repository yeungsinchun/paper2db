#!/usr/bin/env python3
"""Build LQ review PDFs by splitting source Paper 1B PDFs onto A4 pages.

No PNG raster and no within-page crop. starts.json page_from..page_to are
exam-page indices (optional cover and 2-up spreads), not raw PDF pages.
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


def load_starts_meta(year: str) -> dict:
    path = OUTPUT_LQ / year / "starts.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_starts(year: str) -> list[dict]:
    return list(load_starts_meta(year).get("questions") or [])


def question_range(year: str, qn: int) -> tuple[int, int] | None:
    for item in load_starts(year):
        if int(item["q"]) == qn:
            return int(item["page_from"]), int(item["page_to"])
    return None


def source_page_layout(exam_pages: object, src_len: int) -> tuple[int, int]:
    """Infer (cover_pages, exam_pages_per_source_page) from starts.json vs PDF."""
    if src_len <= 0:
        raise SystemExit("source PDF has no pages")
    if not isinstance(exam_pages, int):
        return 0, 1
    if exam_pages == src_len:
        return 0, 1
    if exam_pages == src_len - 1:
        return 1, 1
    if exam_pages == src_len * 2:
        return 0, 2
    if exam_pages == (src_len - 1) * 2:
        return 1, 2
    raise SystemExit(
        f"starts.json pages={exam_pages} does not match source PDF "
        f"({src_len} pages) as 1-up or 2-up with optional cover"
    )


def starts_source_layout(year: str, src_len: int) -> tuple[int, int]:
    try:
        return source_page_layout(load_starts_meta(year).get("pages"), src_len)
    except SystemExit as exc:
        raise SystemExit(f"{year}: {exc}") from exc


def starts_exam_count(year: str, src_len: int) -> int:
    pages = load_starts_meta(year).get("pages")
    cover, spread = starts_source_layout(year, src_len)
    if isinstance(pages, int):
        return pages
    return (src_len - cover) * spread


def pdf_page_offset(year: str, src_len: int) -> int:
    """Cover pages skipped by preprocess_lq / export_pdf_pages."""
    cover, _spread = starts_source_layout(year, src_len)
    return cover


def map_exam_page(year: str, exam_index: int, src_len: int) -> tuple[int, int | None]:
    """Map a starts.json exam-page index to (source PDF page, spread half).

    half is 0=left, 1=right on a 2-up source page, or None for a full page.
    """
    cover, spread = starts_source_layout(year, src_len)
    if exam_index < 0:
        raise SystemExit(f"{year}: exam page {exam_index} is negative")
    pno = cover + exam_index // spread
    if pno >= src_len:
        raise SystemExit(
            f"{year}: exam page {exam_index} maps outside source PDF "
            f"({src_len} pages)"
        )
    half = exam_index % spread if spread > 1 else None
    return pno, half


def require_exam_span(
    year: str, page_from: int, page_to: int, src_len: int
) -> None:
    exam_count = starts_exam_count(year, src_len)
    if page_from < 0 or page_to < page_from or page_to >= exam_count:
        raise SystemExit(
            f"{year}: page range {page_from}..{page_to} is outside "
            f"{exam_count} exam pages"
        )
    for exam_index in range(page_from, page_to + 1):
        map_exam_page(year, exam_index, src_len)


def _spread_clip(page: fitz.Page, half: int | None) -> fitz.Rect | None:
    if half is None:
        return None
    rect = page.rect
    mid = rect.x0 + rect.width / 2.0
    if half == 0:
        return fitz.Rect(rect.x0, rect.y0, mid, rect.y1)
    return fitz.Rect(mid, rect.y0, rect.x1, rect.y1)


def _append_exam_page(
    document: fitz.Document,
    src: fitz.Document,
    year: str,
    exam_index: int,
    src_len: int,
    *,
    title: str | None = None,
    label: str | None = None,
) -> None:
    pno, half = map_exam_page(year, exam_index, src_len)
    append_pdf_page_a4(
        document,
        src,
        pno,
        title=title,
        label=label,
        clip=_spread_clip(src[pno], half),
    )


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
        src_len = len(src)
        cover, _spread = starts_source_layout(year, src_len)
        exam_count = starts_exam_count(year, src_len)
        for item in questions:
            require_exam_span(
                year, int(item["page_from"]), int(item["page_to"]), src_len
            )
        first_q_page = {}
        for item in questions:
            qn = int(item["q"])
            page_from = int(item["page_from"])
            if page_from not in first_q_page:
                first_q_page[page_from] = qn
        combined = fitz.open()
        try:
            if cover:
                append_pdf_page_a4(combined, src, 0)
            for exam_index in range(exam_count):
                qn = first_q_page.get(exam_index)
                label = f"{year} Q{qn}" if qn is not None else None
                _append_exam_page(
                    combined, src, year, exam_index, src_len, label=label
                )
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
                for exam_index in range(page_from, page_to + 1):
                    label = f"{year} Q{qn}" if exam_index == page_from else None
                    _append_exam_page(
                        questions_pdf, src, year, exam_index, src_len, label=label
                    )
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
                src_len = len(src)
                require_exam_span(year, page_from, page_to, src_len)
                for exam_index in range(page_from, page_to + 1):
                    heading = title if (title and not title_used) else None
                    label = f"{year} Q{qn}" if exam_index == page_from else None
                    _append_exam_page(
                        document,
                        src,
                        year,
                        exam_index,
                        src_len,
                        title=heading,
                        label=label,
                    )
                    title_used = True
                    written += 1
            finally:
                src.close()
        if written == 0:
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
