#!/usr/bin/env python3
"""Crop full LQ questions from exported page PNGs + starts.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from png_pdf import combine_pngs_to_pdf  # noqa: E402
from preprocess_lq import (  # noqa: E402
    crop_questions,
    load_page_pngs,
    crisp_scan,
)

Image.MAX_IMAGE_PIXELS = 250_000_000


def crop_year(year_dir: Path) -> int:
    meta_path = year_dir / "starts.json"
    pages_dir = year_dir / "pages"
    if not meta_path.is_file() or not pages_dir.is_dir():
        print(f"  skip {year_dir.name}: missing pages/starts")
        return 0
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    pages = load_page_pngs(pages_dir)
    if not pages:
        return 0
    starts = [
        (int(q["q"]), int(q["page_from"]), float(q.get("y") or 0))
        for q in meta.get("questions", [])
    ]
    if not starts:
        return 0
    # crop_questions uses end at next start; page_to in meta is inclusive for
    # page stacks - y on next question is what matters for full-problem crops.
    images = crop_questions(pages, starts, clean_answer_lines=True)
    for old in year_dir.glob("q*.png"):
        old.unlink()
    for qn, image in sorted(images.items()):
        image = crisp_scan(image, enabled=True)
        image.save(year_dir / f"q{qn}.png", format="PNG", optimize=False)
        print(f"  {year_dir.name} q{qn}.png {image.size[0]}x{image.size[1]}")
    # Question review PDF from crops (answers stay under ans/).
    combine_pngs_to_pdf(year_dir, output=year_dir / "questions.pdf", overwrite=True)
    return len(images)


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
        total += crop_year(year_dir)
    print(f"Wrote {total} full-question crops")


if __name__ == "__main__":
    main()
