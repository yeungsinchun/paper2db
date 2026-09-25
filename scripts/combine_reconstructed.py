#!/usr/bin/env python3
"""Join every year's reconstructed paper into one PDF per paper type.

  tests/reconstructed/mc/combined.pdf  <- tests/reconstructed/mc/<year>/combined.pdf
  tests/reconstructed/lq/combined.pdf  <- tests/reconstructed/lq/<year>/combined.pdf

Years are appended in syllabus order (2012.. then pp, sap); the per-year PDFs
already carry year and question labels on portrait A4, so pages are copied
as they are. Years without a per-year combined.pdf are skipped and listed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf as fitz

from classify_mc_llm import year_key

ROOT = Path(__file__).resolve().parents[1]
RECONSTRUCTED = ROOT / "tests" / "reconstructed"
KINDS = ("mc", "lq")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=KINDS, nargs="+", default=list(KINDS))
    return parser.parse_args()


def year_dirs(kind_dir: Path) -> list[Path]:
    """Per-year folders under tests/reconstructed/<kind>/."""
    if not kind_dir.is_dir():
        return []
    found = [path for path in kind_dir.iterdir() if path.is_dir()]
    return sorted(found, key=lambda path: year_key(path.name))


def combine_kind(kind_dir: Path) -> tuple[Path | None, list[str]]:
    """Write kind_dir/combined.pdf from each year's combined.pdf; return (dest, skipped)."""
    dest = kind_dir / "combined.pdf"
    skipped: list[str] = []
    document = fitz.open()
    try:
        for year_dir in year_dirs(kind_dir):
            source = year_dir / "combined.pdf"
            if not source.is_file():
                skipped.append(year_dir.name)
                continue
            with fitz.open(source) as year_pdf:
                document.insert_pdf(year_pdf)
        if document.page_count == 0:
            return None, skipped
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()
    return dest, skipped


def main() -> None:
    args = parse_args()
    for kind in args.kind:
        kind_dir = RECONSTRUCTED / kind
        dest, skipped = combine_kind(kind_dir)
        if dest is None:
            print(f"skip {kind}: no per-year combined.pdf under {kind_dir.relative_to(ROOT)}")
            continue
        with fitz.open(dest) as done:
            print(f"Wrote {dest.relative_to(ROOT)} ({done.page_count} pages)")
        if skipped:
            print(f"  missing per-year combined.pdf: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
