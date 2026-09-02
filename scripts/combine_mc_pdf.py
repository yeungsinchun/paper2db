#!/usr/bin/env python3
"""Combine processed MC question PNGs into one PDF (and optional per-year PDFs)."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pymupdf as fitz


YEAR_ORDER = [
    "2012",
    "2013",
    "2014",
    "2015",
    "2016",
    "2017",
    "2018",
    "2019",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
    "ppp",
    "sapp",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "MC",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Combined PDF path (default: processed/MC/all_mc.pdf)",
    )
    parser.add_argument(
        "--per-year",
        action="store_true",
        help="Also write processed/MC/{year}/all.pdf for each year",
    )
    return parser.parse_args()


def question_paths(year_dir: Path) -> list[Path]:
    files = list(year_dir.glob("q*.png"))
    def key(path: Path) -> int:
        match = re.fullmatch(r"q(\d+)\.png", path.name)
        return int(match.group(1)) if match else 10**9

    return sorted(files, key=key)


def append_pngs(document: fitz.Document, paths: list[Path]) -> int:
    count = 0
    for path in paths:
        image = fitz.open(path)
        try:
            rect = image[0].rect
            page = document.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, filename=str(path))
            count += 1
        finally:
            image.close()
    return count


def main() -> None:
    args = parse_args()
    processed = args.processed
    output = args.output or (processed / "all_mc.pdf")

    year_dirs = []
    for name in YEAR_ORDER:
        path = processed / name
        if path.is_dir():
            year_dirs.append(path)
    for path in sorted(processed.iterdir()):
        if path.is_dir() and path.name not in YEAR_ORDER and question_paths(path):
            year_dirs.append(path)

    combined = fitz.open()
    total = 0
    for year_dir in year_dirs:
        paths = question_paths(year_dir)
        if not paths:
            continue
        if args.per_year:
            year_pdf = fitz.open()
            n = append_pngs(year_pdf, paths)
            out = year_dir / "all.pdf"
            year_pdf.save(out, garbage=4, deflate=True)
            year_pdf.close()
            print(f"Wrote {out} ({n} pages)")
        n = append_pngs(combined, paths)
        total += n
        print(f"Added {year_dir.name}: {n} questions")

    output.parent.mkdir(parents=True, exist_ok=True)
    combined.save(output, garbage=4, deflate=True)
    combined.close()
    print(f"Wrote {output} ({total} pages)")


if __name__ == "__main__":
    main()
