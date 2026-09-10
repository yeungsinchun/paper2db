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
YEAR_Q_PNG = re.compile(r"^(?P<year>\d{4}|pp|sap)[_-]q(?P<q>\d+)$", re.I)
YEAR_DIR = re.compile(r"^(?P<year>\d{4}|pp|sap)$", re.I)
BARE_Q_PNG = re.compile(r"^q(?P<q>\d+)$", re.I)
LABEL_BAND = 16.0


def png_item_label(path: Path) -> str | None:
    """Year and question from a crop filename: 2012_q36.png -> 2012 Q36."""
    stem = path.stem
    found = YEAR_Q_PNG.fullmatch(stem)
    if found:
        return f"{found.group('year')} Q{int(found.group('q'))}"
    found = BARE_Q_PNG.fullmatch(stem)
    if found:
        q = int(found.group("q"))
        for parent in path.parents:
            year = YEAR_DIR.fullmatch(parent.name)
            if year:
                return f"{year.group('year')} Q{q}"
        return f"Q{q}"
    return None


ITEM_GAP = 12.0


def _png_size(path: Path) -> tuple[float, float]:
    image = fitz.open(path)
    try:
        rect = image[0].rect
        return rect.width, rect.height
    finally:
        image.close()


def _scale_png(src_w: float, src_h: float, *, max_w: float, max_h: float) -> tuple[float, float]:
    scale = min(max_w / src_w, max_h / src_h, 1.0)
    return src_w * scale, src_h * scale


class A4Flow:
    """Stack crops top to bottom on portrait A4; start a new page when one does not fit."""

    def __init__(self, document: fitz.Document, title: str | None = None) -> None:
        self.document = document
        self.title = title
        self.page: fitz.Page | None = None
        self.y = A4_MARGIN
        self._wrote_title = False

    def _new_page(self) -> None:
        self.page = self.document.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        self.y = A4_MARGIN
        if self.title and not self._wrote_title:
            self.page.insert_text(
                (A4_MARGIN, A4_MARGIN + 12),
                self.title,
                fontsize=16,
                fontname="helv",
            )
            self.y = A4_MARGIN + 28
            self._wrote_title = True

    def _insert_label_top_right(self, label: str) -> None:
        assert self.page is not None
        width = fitz.get_text_length(label, fontname="helv", fontsize=11)
        x = A4_WIDTH - A4_MARGIN - width
        self.page.insert_text(
            (x, self.y + 10),
            label,
            fontsize=11,
            fontname="helv",
        )

    def add(self, path: Path) -> None:
        label = png_item_label(path)
        src_w, src_h = _png_size(path)
        printable_w = A4_WIDTH - 2 * A4_MARGIN
        full_h = A4_HEIGHT - 2 * A4_MARGIN
        label_h = LABEL_BAND if label else 0.0
        dest_w, dest_h = _scale_png(src_w, src_h, max_w=printable_w, max_h=full_h - label_h)
        block_h = dest_h + label_h
        remaining = A4_HEIGHT - A4_MARGIN - self.y
        if self.page is None or block_h > remaining + 0.5:
            self._new_page()
            remaining = A4_HEIGHT - A4_MARGIN - self.y
            dest_w, dest_h = _scale_png(
                src_w, src_h, max_w=printable_w, max_h=remaining - label_h
            )
        assert self.page is not None
        if label:
            self._insert_label_top_right(label)
            self.y += label_h
        self.page.insert_image(
            fitz.Rect(A4_MARGIN, self.y, A4_MARGIN + dest_w, self.y + dest_h),
            filename=str(path),
        )
        self.y += dest_h + ITEM_GAP


def place_pngs_on_a4(
    document: fitz.Document,
    paths: list[Path],
    *,
    title: str | None = None,
) -> None:
    """Place PNGs one after another on portrait A4 pages, after an optional title."""
    flow = A4Flow(document, title=title)
    for path in paths:
        flow.add(path)


def append_pdf_page_a4(
    document: fitz.Document,
    src: fitz.Document,
    pno: int,
    *,
    title: str | None = None,
    label: str | None = None,
) -> None:
    """Copy one source PDF page onto a new portrait A4 page (no raster crop)."""
    page = document.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    header = 0.0
    if title:
        page.insert_text(
            (A4_MARGIN, A4_MARGIN + 12),
            title,
            fontsize=16,
            fontname="helv",
        )
        header = 28.0
    if label:
        width = fitz.get_text_length(label, fontname="helv", fontsize=11)
        page.insert_text(
            (A4_WIDTH - A4_MARGIN - width, A4_MARGIN + 12),
            label,
            fontsize=11,
            fontname="helv",
        )
        header = max(header, 28.0)
    printable = fitz.Rect(
        A4_MARGIN,
        A4_MARGIN + header,
        A4_WIDTH - A4_MARGIN,
        A4_HEIGHT - A4_MARGIN,
    )
    src_page = src[pno]
    src_w, src_h = src_page.rect.width, src_page.rect.height
    scale = min(printable.width / src_w, printable.height / src_h)
    dest_w, dest_h = src_w * scale, src_h * scale
    target = fitz.Rect(
        printable.x0,
        printable.y0,
        printable.x0 + dest_w,
        printable.y0 + dest_h,
    )
    page.show_pdf_page(target, src, pno)


def section_heading_title(section_num: int, section_name: str) -> str:
    """Session heading for a classified section bank, e.g. ch25 Radiation and Radioactivity."""
    return f"ch{section_num} {section_name}"


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
        place_pngs_on_a4(document, paths)
        page_count = document.page_count
        document.save(dest, garbage=4, deflate=True)
    finally:
        document.close()
    print(f"Wrote {dest} ({page_count} pages, {len(paths)} questions)")
    return dest
