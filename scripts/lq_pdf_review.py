#!/usr/bin/env python3
"""Build LQ review PDFs by splitting source Paper 1B PDFs onto A4 pages.

No PNG raster and no within-page crop. page_from..page_to from starts.json
are whole exam pages. Trailing data/formulae sheets are omitted
(see formula_sheet.py).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymupdf as fitz

from formula_sheet import (
    exported_index_to_pdf,
    exported_range_to_pdf_pages,
    formula_pdf_indices,
)
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
        return {"questions": [], "pages": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("questions", [])
    return data


def load_starts(year: str) -> list[dict]:
    return list(load_starts_meta(year).get("questions") or [])


def exported_page_count(meta: dict, questions: list[dict] | None = None) -> int:
    questions = questions if questions is not None else list(meta.get("questions") or [])
    stored = int(meta.get("pages") or 0)
    if stored:
        return stored
    if not questions:
        return 0
    return max(int(item["page_to"]) for item in questions) + 1


def question_range(year: str, qn: int) -> tuple[int, int] | None:
    for item in load_starts(year):
        if int(item["q"]) == qn:
            return int(item["page_from"]), int(item["page_to"])
    return None


def skip_formula_pdf_pages(src: fitz.Document, meta: dict) -> set[int]:
    if "formula_pdf_pages" in meta:
        stored = {int(i) for i in (meta.get("formula_pdf_pages") or [])}
        if stored:
            first = min(stored)
            return set(range(first, len(src))) if first < len(src) else stored
        return set()
    n_exported = exported_page_count(meta)
    if "formula_pages" in meta:
        exported = {int(i) for i in (meta.get("formula_pages") or [])}
        if exported and n_exported:
            found: set[int] = set()
            for png_i in exported:
                found.update(exported_index_to_pdf(png_i, n_exported, len(src)))
            if found:
                return set(range(min(found), len(src)))
        return set()
    from_index = 0
    questions = list(meta.get("questions") or [])
    if questions:
        last_from = max(int(item["page_from"]) for item in questions)
        mapped = exported_index_to_pdf(last_from, n_exported or len(src), len(src))
        if mapped:
            from_index = mapped[0]
    return formula_pdf_indices(src, from_index=from_index)


def write_year_review_pdfs(year: str) -> None:
    source = lq_source_pdf(year)
    if source is None or not source.is_file():
        print(f"skip LQ PDF review {year}: missing paper/lq PDF")
        return
    meta = load_starts_meta(year)
    questions = list(meta.get("questions") or [])
    out_dir = OUTPUT_LQ / year
    out_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(source)
    try:
        n_exported = exported_page_count(meta, questions)
        formula_pdf = skip_formula_pdf_pages(src, meta)
        combined = fitz.open()
        first_q_pdf: dict[int, int] = {}
        for item in questions:
            qn = int(item["q"])
            mapped = exported_range_to_pdf_pages(
                int(item["page_from"]),
                int(item["page_from"]),
                n_exported or len(src),
                len(src),
            )
            if mapped and mapped[0] not in first_q_pdf:
                first_q_pdf[mapped[0]] = qn
        try:
            for pno in range(len(src)):
                if pno in formula_pdf:
                    continue
                qn = first_q_pdf.get(pno)
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
                pdf_pages = exported_range_to_pdf_pages(
                    page_from, page_to, n_exported or len(src), len(src)
                )
                first = True
                for pno in pdf_pages:
                    if pno in formula_pdf:
                        continue
                    label = f"{year} Q{qn}" if first else None
                    append_pdf_page_a4(questions_pdf, src, pno, label=label)
                    first = False
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
                meta = load_starts_meta(year)
                n_exported = exported_page_count(meta)
                formula_pdf = skip_formula_pdf_pages(src, meta)
                pdf_pages = exported_range_to_pdf_pages(
                    page_from, page_to, n_exported or len(src), len(src)
                )
                first = True
                for pno in pdf_pages:
                    if pno in formula_pdf:
                        continue
                    heading = title if (title and not title_used) else None
                    label = f"{year} Q{qn}" if first else None
                    append_pdf_page_a4(
                        document, src, pno, title=heading, label=label
                    )
                    title_used = True
                    first = False
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
