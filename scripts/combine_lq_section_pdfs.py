#!/usr/bin/env python3
"""Build per-section LQ review PDFs under classified/lq/.

For each syllabus section that has at least one primary LQ:
  - questions.pdf  - question crops (year order)
  - answers.pdf    - marking-scheme answer crops (same order; skips missing)
  - performance.pdf - candidate-performance notes as text pages

Skips empty sections. Overwrites existing PDFs by default.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import textwrap
from collections import defaultdict
from pathlib import Path

import pymupdf as fitz

from classify_mc_llm import BOOK_NAMES, SECTION_BY_NUM, SECTIONS, year_key

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIED_LQ = ROOT / "classified" / "lq"
CSV_PATH = CLASSIFIED_LQ / "classification.csv"
PERF_PATH = CLASSIFIED_LQ / "candidate_performance.json"

YEAR_RE = re.compile(r"^(?P<year>\d{4}|pp|sap)$", re.I)


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


def resolve_question_png(row: dict) -> Path | None:
    year, q = row["Year"], row["Question"]
    candidates = [
        ROOT / (row.get("PNG") or ""),
        section_dir(int(row["Primary"])) / f"{year}-q{q}.png",
        ROOT / "output" / "lq" / year / f"q{q}.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def resolve_answer_png(row: dict) -> Path | None:
    year, q = row["Year"], row["Question"]
    candidates = [
        ROOT / (row.get("AnswerPNG") or ""),
        section_dir(int(row["Primary"])) / f"{year}-q{q}-ans.png",
        ROOT / "output" / "lq" / year / "ans" / f"q{q}.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def write_image_pdf(paths: list[Path], dest: Path, *, title: str | None = None) -> int:
    """One PDF page per image. Optional title page if title set."""
    if not paths:
        return 0
    doc = fitz.open()
    try:
        if title:
            page = doc.new_page(width=595, height=842)
            page.insert_text((40, 60), title, fontsize=16, fontname="helv")
            page.insert_text(
                (40, 86),
                f"{len(paths)} item(s)",
                fontsize=11,
                fontname="helv",
            )
        for path in paths:
            image = fitz.open(path)
            try:
                rect = image[0].rect
                # Cap very tall crops so PDF viewers stay usable.
                max_h = 2000.0
                width, height = rect.width, rect.height
                if height > max_h:
                    scale = max_h / height
                    width *= scale
                    height *= scale
                page = doc.new_page(width=width, height=height)
                page.insert_image(page.rect, filename=str(path))
            finally:
                image.close()
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

    by_primary: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_primary[int(row["Primary"])].append(row)

    written = 0
    for num, _book, _folder, name in SECTIONS:
        items = by_primary.get(num) or []
        if not items:
            continue
        out_dir = section_dir(num)
        out_dir.mkdir(parents=True, exist_ok=True)
        label = f"S{num:02d} {name}"

        q_paths: list[Path] = []
        a_paths: list[Path] = []
        perf_items: list[tuple[str, int, str]] = []
        for row in items:
            year, qn = row["Year"], int(row["Question"])
            q_png = resolve_question_png(row)
            if q_png:
                q_paths.append(q_png)
            a_png = resolve_answer_png(row)
            if a_png:
                a_paths.append(a_png)
            note = (perf.get(str(year)) or {}).get(str(qn), "")
            perf_items.append((str(year), qn, note))

        q_pdf = out_dir / "questions.pdf"
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

        nq = write_image_pdf(q_paths, q_pdf)
        na = write_image_pdf(a_paths, a_pdf)
        np_ = write_performance_pdf(perf_items, p_pdf, section_label=label)
        written += 1
        print(
            f"{label}: questions.pdf ({nq}), answers.pdf ({na}), "
            f"performance.pdf ({np_}) -> {out_dir.relative_to(ROOT)}"
        )

    print(f"\nDone: {written} sections with 3 PDFs each")


if __name__ == "__main__":
    main()
