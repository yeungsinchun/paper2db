#!/usr/bin/env python3
"""Build LQ question PNGs as whole exam page stacks (no within-page crop).

Captain rule: long questions use full page_from..page_to pages only.
Answer ruling stays on the page; marking-scheme answer crops are separate
under ans/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from png_pdf import combine_pngs_to_pdf  # noqa: E402
from preprocess_lq import crisp_scan, load_page_pngs, stitch_vertical  # noqa: E402

Image.MAX_IMAGE_PIXELS = 250_000_000


def stack_question_pages(
    pages: list[Image.Image], page_from: int, page_to: int
) -> Image.Image | None:
    if page_from < 0 or page_to >= len(pages) or page_to < page_from:
        return None
    parts = [pages[index].convert("RGB") for index in range(page_from, page_to + 1)]
    return stitch_vertical(parts)


def build_year(year_dir: Path) -> int:
    meta_path = year_dir / "starts.json"
    pages_dir = year_dir / "pages"
    if not meta_path.is_file() or not pages_dir.is_dir():
        print(f"  skip {year_dir.name}: missing pages/starts")
        return 0
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pages = load_page_pngs(pages_dir)
    if not pages:
        return 0
    questions = meta.get("questions") or []
    if not questions:
        return 0

    for old in year_dir.glob("q*.png"):
        old.unlink()

    written = 0
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

    combine_pngs_to_pdf(year_dir, output=year_dir / "questions.pdf", overwrite=True)
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
