"""Combine generated page/question PNGs into one review PDF."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

QUESTION_PNG = re.compile(r"^q(\d+)\.png$", re.I)
PAGE_PNG = re.compile(r"^page(\d+)\.png$", re.I)

A4_WIDTH = 595.0
A4_HEIGHT = 842.0
A4_MARGIN = 36.0
# Rotate only when a landscape source would be a thin unreadable strip on portrait A4.
MIN_PORTRAIT_FIT_HEIGHT = 72.0


def insert_png_on_a4(document: fitz.Document, path: Path) -> None:
    """Place one PNG on a new A4 page, scaled to fit, aspect ratio preserved."""
    image = fitz.open(path)
    try:
        rect = image[0].rect
        src_w, src_h = rect.width, rect.height
        page_w, page_h = A4_WIDTH, A4_HEIGHT
        printable_w = page_w - 2 * A4_MARGIN
        printable_h = page_h - 2 * A4_MARGIN
        scale = min(printable_w / src_w, printable_h / src_h)
        if src_w > src_h and src_h * scale < MIN_PORTRAIT_FIT_HEIGHT:
            page_w, page_h = A4_HEIGHT, A4_WIDTH
            printable_w = page_w - 2 * A4_MARGIN
            printable_h = page_h - 2 * A4_MARGIN
            scale = min(printable_w / src_w, printable_h / src_h)
        dest_w, dest_h = src_w * scale, src_h * scale
        x0 = (page_w - dest_w) / 2
        y0 = (page_h - dest_h) / 2
        page = document.new_page(width=page_w, height=page_h)
        page.insert_image(
            fitz.Rect(x0, y0, x0 + dest_w, y0 + dest_h),
            filename=str(path),
        )
    finally:
        image.close()


def section_heading_title(section_num: int, section_name: str) -> str:
    """Session heading for a classified section bank, e.g. ch25 Radiation and Radioactivity."""
    return f"ch{section_num} {section_name}"


def insert_session_heading(
    document: fitz.Document,
    title: str,
    subtitle: str | None = None,
) -> None:
    """A4 first page with the section session heading."""
    page = document.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.insert_text((40, 60), title, fontsize=16, fontname="helv")
    if subtitle:
        page.insert_text((40, 86), subtitle, fontsize=11, fontname="helv")


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
            insert_png_on_a4(document, path)
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()
    print(f"Wrote {dest} ({len(paths)} pages)")
    return dest
