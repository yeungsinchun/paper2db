#!/usr/bin/env python3
"""Build per-section LQ review PDFs under classified/lq/.

For each syllabus section listed in any row's AllSections (primary or not):
  - combined.pdf   - whole source Paper 1B pages (year then Q; A4); same name
                     as the MC section PDFs
  - answers.pdf    - marking-scheme answer crops packed on A4 (same order; skips missing)
  - performance.pdf - candidate-performance notes as text pages

A cross-topic LQ (e.g. alpha penetration + activity from half-life) therefore
appears in every section it tests, matching the PNG copies classify_lq_*
already place there. Sections with no rows get their stale PDFs removed so an
old build cannot linger, and the pre-rename questions.pdf is removed everywhere.
Overwrites existing PDFs by default.
"""
from __future__ import annotations

import argparse
import csv
import json
import textwrap
from collections import defaultdict
from pathlib import Path

import pymupdf as fitz

from classify_mc_llm import SECTION_BY_NUM, SECTIONS, year_key
from png_pdf import place_pngs_on_a4, section_heading_title
from lq_pdf_review import write_section_questions_pdf

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIED_LQ = ROOT / "classified" / "lq"
CSV_PATH = CLASSIFIED_LQ / "classification.csv"
PERF_PATH = CLASSIFIED_LQ / "candidate_performance.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--overwrite", action="store_true", default=True)
    return p.parse_args()


def load_rows() -> list[dict]:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    rows.sort(key=lambda r: (year_key(r["Year"]), int(r["Question"])))
    return rows


def load_performance() -> dict[str, dict[str, str]]:
    if not PERF_PATH.is_file():
        return {}
    return json.loads(PERF_PATH.read_text(encoding="utf-8"))


def section_dir(section_num: int) -> Path:
    book, folder, _name = SECTION_BY_NUM[section_num]
    return CLASSIFIED_LQ / book / folder


def row_sections(row: dict) -> list[int]:
    """Every section a row belongs to, primary first."""
    primary = int(row["Primary"])
    listed = [int(x) for x in (row.get("AllSections") or "").split(";") if x]
    return [primary] + [sec for sec in listed if sec != primary]


def rows_by_section(rows: list[dict]) -> dict[int, list[dict]]:
    by_section: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        for sec in row_sections(row):
            by_section[sec].append(row)
    return by_section


SECTION_PDFS = ("combined.pdf", "answers.pdf", "performance.pdf")
# Name the question PDF carried before it was aligned with classified/mc/.
LEGACY_SECTION_PDFS = ("questions.pdf",)


def remove_stale_pdfs(out_dir: Path, names: tuple[str, ...] = SECTION_PDFS) -> list[Path]:
    removed = []
    for name in names:
        path = out_dir / name
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def resolve_answer_png(row: dict) -> Path | None:
    year, q = row["Year"], row["Question"]
    candidates = [
        ROOT / (row.get("AnswerPNG") or ""),
        section_dir(int(row["Primary"])) / f"{year}-q{q}-ans.png",
        ROOT / "reconstructed" / "lq" / year / "ans" / f"q{q}.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def write_image_pdf(paths: list[Path], dest: Path, *, title: str | None = None) -> int:
    """Stack images on portrait A4. Optional chapter heading on the first page."""
    if not paths:
        return 0
    doc = fitz.open()
    try:
        place_pngs_on_a4(doc, paths, title=title)
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest, garbage=4, deflate=True)
    finally:
        doc.close()
    return len(paths)


def write_performance_pdf(
    items: list[tuple[str, int, str]],
    dest: Path,
    *,
    section_label: str,
) -> int:
    """Render candidate-performance notes as wrapped text pages."""
    if not items:
        return 0
    doc = fitz.open()
    page_w, page_h = 595.0, 842.0
    margin = 40.0
    try:
        for year, qn, text in items:
            page = doc.new_page(width=page_w, height=page_h)
            y = margin
            header = f"{section_label}  |  {year} Q{qn}"
            page.insert_text((margin, y + 14), header, fontsize=13, fontname="helv")
            y += 28
            page.draw_line(
                fitz.Point(margin, y),
                fitz.Point(page_w - margin, y),
            )
            y += 16
            body = (text or "").strip() or "(No Section B candidate-performance note.)"
            # Simple wrap; keep paragraphs.
            for para in body.splitlines() or [body]:
                para = para.strip()
                if not para:
                    y += 10
                    continue
                for line in textwrap.wrap(para, width=92) or [""]:
                    if y > page_h - margin - 16:
                        page = doc.new_page(width=page_w, height=page_h)
                        y = margin
                    page.insert_text(
                        (margin, y + 10),
                        line,
                        fontsize=10,
                        fontname="helv",
                    )
                    y += 14
                y += 6
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest, garbage=4, deflate=True)
    finally:
        doc.close()
    return len(items)


def main() -> None:
    args = parse_args()
    rows = load_rows()
    perf = load_performance()

    by_section = rows_by_section(rows)

    written = 0
    for num, _book, _folder, name in SECTIONS:
        items = by_section.get(num) or []
        out_dir = section_dir(num)
        heading = section_heading_title(num, name)
        for stale in remove_stale_pdfs(out_dir, LEGACY_SECTION_PDFS):
            print(f"Removed stale {stale.relative_to(ROOT)} (renamed to combined.pdf)")
        if not items:
            for stale in remove_stale_pdfs(out_dir):
                print(f"Removed stale {stale.relative_to(ROOT)} (no LQ in section)")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        label = f"S{num:02d} {name}"

        a_paths: list[Path] = []
        perf_items: list[tuple[str, int, str]] = []
        for row in items:
            year, qn = row["Year"], int(row["Question"])
            a_png = resolve_answer_png(row)
            if a_png:
                a_paths.append(a_png)
            note = (perf.get(str(year)) or {}).get(str(qn), "")
            perf_items.append((str(year), qn, note))

        q_pdf = out_dir / "combined.pdf"
        a_pdf = out_dir / "answers.pdf"
        p_pdf = out_dir / "performance.pdf"
        if (
            not args.overwrite
            and q_pdf.is_file()
            and a_pdf.is_file()
            and p_pdf.is_file()
        ):
            print(f"Keeping {out_dir.relative_to(ROOT)}")
            continue

        pdf_items = [(row["Year"], int(row["Question"])) for row in items]
        nq = write_section_questions_pdf(pdf_items, q_pdf, title=heading)
        na = write_image_pdf(a_paths, a_pdf)
        np_ = write_performance_pdf(perf_items, p_pdf, section_label=label)
        written += 1
        print(
            f"{label}: combined.pdf ({nq}), answers.pdf ({na}), "
            f"performance.pdf ({np_}) -> {out_dir.relative_to(ROOT)}"
        )

    print(f"\nDone: {written} sections with 3 PDFs each")


if __name__ == "__main__":
    main()
