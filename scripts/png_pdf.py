"""Combine generated page/question PNGs into one review PDF."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

QUESTION_PNG = re.compile(r"^q(\d+)\.png$", re.I)
PAGE_PNG = re.compile(r"^page(\d+)\.png$", re.I)


def _numeric_pngs(directory: Path, pattern: re.Pattern[str]) -> list[Path]:
    matched: list[tuple[int, Path]] = []
    for path in directory.glob("*.png"):
        found = pattern.fullmatch(path.name)
        if found:
            matched.append((int(found.group(1)), path))
    matched.sort(key=lambda item: item[0])
    return [path for _, path in matched]


def collect_review_pngs(directory: Path) -> list[Path]:
    """Prefer qN.png; otherwise pageNN.png. Numeric order."""
    questions = _numeric_pngs(directory, QUESTION_PNG)
    if questions:
        return questions
    return _numeric_pngs(directory, PAGE_PNG)


def combine_pngs_to_pdf(
    directory: Path,
    output: Path | None = None,
    *,
    overwrite: bool = False,
) -> Path | None:
    """Write a review PDF from PNGs. Skip if that PDF already exists unless overwrite."""
    paths = collect_review_pngs(directory)
    if not paths:
        return None
    dest = output or (directory / "combined.pdf")
    if dest.is_file() and not overwrite:
        print(f"Keeping existing {dest} (not duplicating)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
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
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()
    print(f"Wrote {dest} ({len(paths)} pages)")
    return dest
