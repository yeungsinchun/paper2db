#!/usr/bin/env python3
"""Write per-section question + answer PDFs under classified/.

For each section folder:
  - combined.pdf: question PNGs sorted easiest -> hardest by Correct %
    (higher % first). Items without a known % (pp/sap/2022/2026, deleted,
    missing keys) are placed last.
  - answer.pdf: same order, one page per question listing year, Q#, answer,
    and correct percentage.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIED = ROOT / "classified"
DEFAULT_KEYS = ROOT / "processed" / "MC" / "answer_keys.json"

PNG_RE = re.compile(r"^(?P<year>\d{4}|pp|sap)_q(?P<q>\d+)\.png$", re.I)
YEAR_RANK = {
    "2012": 0, "2013": 1, "2014": 2, "2015": 3, "2016": 4, "2017": 5, "2018": 6,
    "2019": 7, "2020": 8, "2021": 9, "2022": 10, "2023": 11, "2024": 12,
    "2025": 13, "2026": 14, "pp": 15, "sap": 16,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys", type=Path, default=DEFAULT_KEYS)
    parser.add_argument("--classified", type=Path, default=CLASSIFIED)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=True,
        help="Overwrite existing PDFs (default: yes)",
    )
    return parser.parse_args()


def load_keys(path: Path) -> dict[tuple[str, int], dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, int], dict] = {}
    for year, questions in raw.items():
        for q_str, payload in questions.items():
            out[(str(year), int(q_str))] = dict(payload)
    return out


def load_percentages(keys: dict[tuple[str, int], dict]) -> dict[tuple[str, int], int | None]:
    out: dict[tuple[str, int], int | None] = {}
    for key, payload in keys.items():
        pct = payload.get("Correct percentage")
        if payload.get("deleted"):
            pct = None
        out[key] = pct if isinstance(pct, int) else None
    return out


def section_dirs(classified: Path) -> list[Path]:
    dirs: list[Path] = []
    for book in sorted(classified.iterdir()):
        if not book.is_dir() or book.name.startswith(".") or book.name in {
            "ocr_cache",
        }:
            continue
        if not re.match(r"^\d", book.name):
            continue
        for section in sorted(book.iterdir()):
            if section.is_dir() and re.match(r"^\d{2}_", section.name):
                dirs.append(section)
    return dirs


def pngs_in_section(section: Path) -> list[Path]:
    return sorted(p for p in section.glob("*.png") if PNG_RE.fullmatch(p.name))


def sort_key(
    path: Path,
    percentages: dict[tuple[str, int], int | None],
) -> tuple:
    match = PNG_RE.fullmatch(path.name)
    assert match
    year = match.group("year")
    q = int(match.group("q"))
    pct = percentages.get((year, q))
    # Easiest first: higher % first. Missing % -> last.
    has_pct = 0 if pct is not None else 1
    neg_pct = -(pct if pct is not None else 0)
    return (has_pct, neg_pct, YEAR_RANK.get(year, 99), q)


def write_combined(paths: list[Path], dest: Path) -> None:
    document = fitz.open()
    try:
        for path in paths:
            image = fitz.open(path)
            try:
                rect = image[0].rect
                page = document.new_page(width=rect.width, height=rect.height)
                page.insert_image(page.rect, filename=str(path))
            finally:
                image.close()
        dest.parent.mkdir(parents=True, exist_ok=True)
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()


def write_answer_pdf(
    paths: list[Path],
    dest: Path,
    keys: dict[tuple[str, int], dict],
    section_label: str,
) -> None:
    """One summary page listing every question in the same hard->easy order."""
    document = fitz.open()
    try:
        page_width, page_height = 595.0, 842.0  # A4
        margin = 40.0
        y = margin
        page = document.new_page(width=page_width, height=page_height)
        title = f"Answers - {section_label}"
        page.insert_text((margin, y + 14), title, fontsize=14, fontname="helv")
        y += 28
        page.insert_text(
            (margin, y + 11),
            "Order matches combined.pdf (easiest -> hardest by correct %).",
            fontsize=9,
            fontname="helv",
        )
        y += 22
        header = f"{'#':<4} {'Year':<6} {'Q':<4} {'Ans':<5} {'Correct %':<10}"
        page.insert_text((margin, y + 10), header, fontsize=10, fontname="cour")
        y += 16
        page.draw_line(fitz.Point(margin, y), fitz.Point(page_width - margin, y))
        y += 12

        for index, path in enumerate(paths, start=1):
            match = PNG_RE.fullmatch(path.name)
            assert match
            year = match.group("year")
            q = int(match.group("q"))
            payload = keys.get((year, q), {})
            ans = payload.get("Correct Option") or "?"
            if payload.get("deleted"):
                ans = "DEL"
            pct = payload.get("Correct percentage")
            pct_s = f"{pct}%" if isinstance(pct, int) else "-"
            line = f"{index:<4} {year:<6} {q:<4} {str(ans):<5} {pct_s:<10}"
            if y > page_height - margin - 16:
                page = document.new_page(width=page_width, height=page_height)
                y = margin
            page.insert_text((margin, y + 10), line, fontsize=10, fontname="cour")
            y += 14

        dest.parent.mkdir(parents=True, exist_ok=True)
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()


def main() -> None:
    args = parse_args()
    if not args.keys.is_file():
        raise SystemExit(
            f"Missing answer keys: {args.keys}\nRun scripts/extract_answer_keys.py first."
        )

    keys = load_keys(args.keys)
    percentages = load_percentages(keys)
    known = sum(1 for v in percentages.values() if v is not None)
    print(f"Loaded {len(keys)} keys ({known} with correct %)")

    total_pdfs = 0
    total_pages = 0
    missing_examples: list[str] = []
    for section in section_dirs(args.classified):
        pngs = pngs_in_section(section)
        if not pngs:
            continue
        ordered = sorted(pngs, key=lambda p: sort_key(p, percentages))
        combined = section / "combined.pdf"
        answers = section / "answer.pdf"
        if combined.is_file() and answers.is_file() and not args.overwrite:
            print(f"Keeping {combined.relative_to(args.classified)}")
            continue
        write_combined(ordered, combined)
        section_label = section.name.replace("_", " ")
        write_answer_pdf(ordered, answers, keys, section_label)
        total_pdfs += 1
        total_pages += len(ordered)

        with_pct = 0
        for path in ordered:
            m = PNG_RE.fullmatch(path.name)
            assert m
            pct = percentages.get((m.group("year"), int(m.group("q"))))
            if pct is not None:
                with_pct += 1
            elif len(missing_examples) < 12:
                missing_examples.append(path.name)
        rel = combined.relative_to(args.classified)
        print(
            f"Wrote {rel} + answer.pdf ({len(ordered)} pages, {with_pct} with %)"
        )

    print(f"\nDone: {total_pdfs} section pairs, {total_pages} question pages")
    if missing_examples:
        print(
            "Examples without correct % (placed last):",
            ", ".join(missing_examples),
        )


if __name__ == "__main__":
    main()
