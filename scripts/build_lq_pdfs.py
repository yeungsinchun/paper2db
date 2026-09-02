#!/usr/bin/env python3
"""Combine each by-topic/.../lq/*.png into ../lq.pdf (one page per question)."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
BY_TOPIC = ROOT / "by-topic"


def sort_png(path: Path) -> tuple:
    m = re.match(r"([0-9a-z]+)_q(\d+)\.png$", path.name, re.I)
    if not m:
        return (2, path.name, 0)
    year, qn = m.group(1).lower(), int(m.group(2))
    # Numeric years first, then ppp/sapp-style labels.
    if year.isdigit():
        return (0, int(year), qn)
    return (1, year, qn)


def append_pngs(document: fitz.Document, paths: list[Path]) -> None:
    for path in paths:
        image = fitz.open(path)
        try:
            rect = image[0].rect
            page = document.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, filename=str(path))
        finally:
            image.close()


def build_one(lq_dir: Path) -> Path | None:
    pngs = sorted(lq_dir.glob("*.png"), key=sort_png)
    if not pngs:
        return None
    out = lq_dir.parent / "lq.pdf"
    doc = fitz.open()
    append_pngs(doc, pngs)
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    # Prefer lq.pdf; drop legacy name if present.
    legacy = lq_dir.parent / "lq_all.pdf"
    if legacy.exists():
        legacy.unlink()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=BY_TOPIC,
        help="by-topic root (default: dse/by-topic)",
    )
    args = parser.parse_args()
    root: Path = args.root
    built = 0
    for lq_dir in sorted(root.rglob("lq")):
        if not lq_dir.is_dir():
            continue
        out = build_one(lq_dir)
        if out is None:
            continue
        n = len(list(lq_dir.glob("*.png")))
        rel = out.relative_to(root)
        print(f"{n:2d}  {rel}")
        built += 1
    print(f"Wrote {built} lq.pdf files under {root}")


if __name__ == "__main__":
    main()
