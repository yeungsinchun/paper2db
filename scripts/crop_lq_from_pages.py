#!/usr/bin/env python3
"""Build LQ question PNGs as whole exam page stacks (no within-page crop).

Captain rule: long questions use full page_from..page_to pages only.
Answer ruling stays on the page; marking-scheme answer crops are separate
under ans/. Trailing data/formulae sheets and blank insert pages are dropped
from the stack. Existing qN.png files are always rewritten so a stale
within-page y-crop cannot survive a range-unchanged rebuild.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from classify_mc_llm import SECTION_BY_NUM  # noqa: E402
from formula_sheet import exported_index_to_pdf, refresh_starts_meta  # noqa: E402
from lq_pdf_review import lq_source_pdf, write_year_review_pdfs  # noqa: E402
from preprocess_lq import (  # noqa: E402
    crisp_scan,
    doc_has_jpeg_scans,
    export_pdf_pages,
    load_page_pngs,
    native_scale,
    render_display,
    split_spread,
    stitch_vertical,
)

Image.MAX_IMAGE_PIXELS = 250_000_000


def stack_question_pages(
    pages: list[Image.Image], page_from: int, page_to: int
) -> Image.Image | None:
    if page_from < 0 or page_to >= len(pages) or page_to < page_from:
        return None
    parts = [pages[index].convert("RGB") for index in range(page_from, page_to + 1)]
    return stitch_vertical(parts)


def stack_question_from_pdf(
    year: str, item: dict, meta: dict
) -> Image.Image | None:
    source = lq_source_pdf(year)
    if source is None or not source.is_file():
        return None
    page_from = int(item["page_from"])
    page_to = int(item["page_to"])
    doc = fitz.open(source)
    try:
        if doc.needs_pass:
            doc.authenticate("")
        n_pdf = len(doc)
        n_exported = int(meta.get("pages") or 0) or n_pdf
        cover = 1 if n_exported in {n_pdf - 1, (n_pdf - 1) * 2} else 0
        probe = doc[min(cover, n_pdf - 1)]
        scale = native_scale(probe, cap=9.0)
        jpeg = doc_has_jpeg_scans(doc)
        parts: list[Image.Image] = []
        for png_i in range(page_from, page_to + 1):
            pdf_ids = exported_index_to_pdf(png_i, n_exported, n_pdf)
            if not pdf_ids:
                continue
            image = render_display(doc[pdf_ids[0]], scale, doc=doc)
            split = split_spread(image)
            if len(split) == 2:
                part = split[png_i % 2]
            else:
                part = split[0]
            parts.append(crisp_scan(part, enabled=not jpeg))
        if not parts:
            return None
        return stitch_vertical(parts)
    finally:
        doc.close()


def _source_page_count(source: Path) -> int:
    document = fitz.open(source)
    try:
        return len(document)
    finally:
        document.close()


def cover_pages_for_starts(year_dir: Path, source: Path) -> int:
    """Match preprocess_lq cover skip to starts.json page count when possible."""
    meta_path = year_dir / "starts.json"
    if not meta_path.is_file():
        return 1
    expected = json.loads(meta_path.read_text(encoding="utf-8")).get("pages")
    if not isinstance(expected, int):
        return 1
    pdf_pages = _source_page_count(source)
    if expected in {pdf_pages - 1, (pdf_pages - 1) * 2}:
        return 1
    if expected in {pdf_pages, pdf_pages * 2}:
        return 0
    return 1


def ensure_year_pages(year_dir: Path) -> list[Image.Image]:
    """Load pages/, exporting from the source Paper 1B PDF if they are missing.

    Does not rewrite starts.json.
    """
    existing = load_page_pngs(year_dir / "pages")
    if existing:
        return existing
    year = year_dir.name
    source = lq_source_pdf(year)
    if source is None or not source.is_file():
        print(f"  skip {year}: missing paper/lq PDF")
        return []
    cover_pages = cover_pages_for_starts(year_dir, source)
    print(f"  exporting pages from {source.name} (cover_pages={cover_pages})")
    export_pdf_pages(source, year_dir / "pages", cover_pages=cover_pages)
    pages = load_page_pngs(year_dir / "pages")
    meta_path = year_dir / "starts.json"
    if meta_path.is_file() and pages:
        expected = json.loads(meta_path.read_text(encoding="utf-8")).get("pages")
        if isinstance(expected, int) and len(pages) != expected:
            print(
                f"  warning {year}: exported {len(pages)} pages, "
                f"starts.json pages={expected}"
            )
    return pages


def sync_classified_question_pngs(years: list[str] | None = None) -> int:
    """Copy whole-page qN.png into classified section folders. Leave *-ans.png."""
    csv_path = ROOT / "classified" / "lq" / "classification.csv"
    if not csv_path.is_file():
        return 0
    copied = 0
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            year = row["Year"]
            if years and year not in years:
                continue
            question = row["Question"]
            src = ROOT / "tests" / "reconstructed" / "lq" / year / f"q{question}.png"
            if not src.is_file():
                continue
            sections = [int(item) for item in row["AllSections"].split(";") if item]
            for section in sections:
                book, folder, _name = SECTION_BY_NUM[section]
                dest = (
                    ROOT
                    / "classified"
                    / "lq"
                    / book
                    / folder
                    / f"{year}-q{question}.png"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
    print(f"Synced {copied} classified LQ question PNGs")
    return copied


def build_year(year_dir: Path) -> int:
    meta_path = year_dir / "starts.json"
    if not meta_path.is_file():
        print(f"  skip {year_dir.name}: missing starts")
        return 0
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    source = lq_source_pdf(year_dir.name)
    doc = fitz.open(source) if source is not None and source.is_file() else None
    try:
        meta = refresh_starts_meta(meta, doc)
    finally:
        if doc is not None:
            doc.close()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    questions = meta.get("questions") or []
    if not questions:
        return 0

    pages_dir = year_dir / "pages"
    pages = load_page_pngs(pages_dir) if pages_dir.is_dir() else []
    written = 0
    if pages:
        for old in year_dir.glob("q*.png"):
            old.unlink()
        for item in questions:
            question = int(item["q"])
            page_from = int(item["page_from"])
            page_to = int(item["page_to"])
            stacked = stack_question_pages(pages, page_from, page_to)
            if stacked is None:
                print(
                    f"  skip {year_dir.name} q{question}: "
                    f"bad page range {page_from}..{page_to} (n={len(pages)})"
                )
                continue
            stacked = crisp_scan(stacked, enabled=True)
            dest = year_dir / f"q{question}.png"
            stacked.save(dest, format="PNG", optimize=False)
            print(
                f"  {year_dir.name} q{question}.png "
                f"{stacked.size[0]}x{stacked.size[1]} "
                f"(pages {page_from}..{page_to})"
            )
            written += 1
    else:
        for old in year_dir.glob("q*.png"):
            old.unlink()
        for item in questions:
            question = int(item["q"])
            dest = year_dir / f"q{question}.png"
            stacked = stack_question_from_pdf(year_dir.name, item, meta)
            if stacked is None:
                print(
                    f"  skip {year_dir.name} q{question}: "
                    f"no pages/ and no source PDF stack"
                )
                continue
            stacked.save(dest, format="PNG", optimize=False)
            print(
                f"  {year_dir.name} q{question}.png "
                f"{stacked.size[0]}x{stacked.size[1]} "
                f"(pages {int(item['page_from'])}..{int(item['page_to'])}, from source PDF)"
            )
            written += 1

    write_year_review_pdfs(year_dir.name)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", default=None)
    parser.add_argument(
        "--pages-only",
        action="store_true",
        help="Export missing pages/ without rewriting starts.json or qN.png",
    )
    args = parser.parse_args()
    root = ROOT / "tests" / "reconstructed" / "lq"
    total = 0
    selected: list[str] = []
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir():
            continue
        if args.years and year_dir.name not in args.years:
            continue
        selected.append(year_dir.name)
        print(f"=== {year_dir.name} ===")
        if args.pages_only:
            pages = ensure_year_pages(year_dir)
            print(f"  {len(pages)} page PNGs")
            continue
        total += build_year(year_dir)
    if args.pages_only:
        print(f"Exported pages/ for {len(selected)} years")
        return
    print(f"Wrote {total} whole-page LQ question PNGs")
    sync_classified_question_pngs(args.years)


if __name__ == "__main__":
    main()
