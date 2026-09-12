#!/usr/bin/env python3
"""Build LQ question PNGs as whole exam page stacks (no within-page crop).

Captain rule: long questions use full page_from..page_to pages only.
Answer ruling stays on the page; marking-scheme answer crops are separate
under ans/. Trailing data/formulae sheets are dropped from the stack.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from formula_sheet import exported_index_to_pdf, refresh_starts_meta  # noqa: E402
from lq_pdf_review import lq_source_pdf, write_year_review_pdfs  # noqa: E402
from preprocess_lq import (  # noqa: E402
    crisp_scan,
    doc_has_jpeg_scans,
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


def build_year(year_dir: Path) -> int:
    meta_path = year_dir / "starts.json"
    if not meta_path.is_file():
        print(f"  skip {year_dir.name}: missing starts")
        return 0
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_ranges = {
        int(item["q"]): (int(item["page_from"]), int(item["page_to"]))
        for item in (meta.get("questions") or [])
    }
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
        for item in questions:
            question = int(item["q"])
            new_range = (int(item["page_from"]), int(item["page_to"]))
            dest = year_dir / f"q{question}.png"
            if old_ranges.get(question) == new_range and dest.is_file():
                written += 1
                continue
            stacked = stack_question_from_pdf(year_dir.name, item, meta)
            if stacked is None:
                if dest.is_file():
                    written += 1
                else:
                    print(
                        f"  skip {year_dir.name} q{question}: "
                        f"no pages/ and no source PDF stack"
                    )
                continue
            stacked.save(dest, format="PNG", optimize=False)
            print(
                f"  {year_dir.name} q{question}.png "
                f"{stacked.size[0]}x{stacked.size[1]} "
                f"(pages {new_range[0]}..{new_range[1]}, from source PDF)"
            )
            written += 1

    write_year_review_pdfs(year_dir.name)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", default=None)
    args = parser.parse_args()
    root = ROOT / "output" / "lq"
    total = 0
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir():
            continue
        if args.years and year_dir.name not in args.years:
            continue
        print(f"=== {year_dir.name} ===")
        total += build_year(year_dir)
    print(f"Wrote {total} whole-page LQ question PNGs")


if __name__ == "__main__":
    main()
