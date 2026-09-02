#!/usr/bin/env python3
"""Batch-preprocess all Year/LQ Paper 1B PDFs into processed/LQ/<year>/."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from preprocess_lq import process_one  # noqa: E402

LQ_SRC = ROOT / "Year" / "LQ"
OUT = ROOT / "processed" / "LQ"
PRE = ROOT / "preprocessed" / "LQ"


def year_key(path: Path) -> tuple:
    name = path.stem.lower()
    if name.startswith("ppp"):
        return (1, "ppp")
    if name.startswith("sapp"):
        return (1, "sapp")
    m = re.match(r"(20\d{2})", name)
    return (0, m.group(1)) if m else (2, name)


def folder_name(path: Path) -> str:
    name = path.stem.lower()
    if name.startswith("ppp"):
        return "ppp"
    if name.startswith("sapp"):
        return "sapp"
    m = re.match(r"(20\d{2})", name)
    return m.group(1) if m else path.stem


def main() -> None:
    PRE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(LQ_SRC.glob("*.pdf"), key=year_key)
    total = 0
    for pdf in pdfs:
        year = folder_name(pdf)
        print(f"\n=== {pdf.name} -> {year} ===")
        # Keep a copy under preprocessed/LQ for the MC/LQ split layout.
        shutil.copy2(pdf, PRE / pdf.name)
        n = process_one(
            pdf,
            OUT / year,
            cover_pages=1,
            scale=0.0,  # auto: native scan DPI, capped
            max_questions=12,
            max_scale=9.0,
            pages_dir=PRE / year / "pages",
        )
        total += n
    print(f"\nDone. {total} LQ PNGs across {len(pdfs)} papers.")


if __name__ == "__main__":
    main()
