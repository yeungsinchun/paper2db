"""Detect HKDSE data / formulae sheets so they are not treated as exam questions.

Matches the official Paper 1B title plus the MC preprocess phrases. Once a
sheet page is found, every later page is treated as part of the same trailing
insert (continuation pages often list constants without repeating the title).
"""
from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Sequence
from typing import Any

import pymupdf as fitz
from PIL import Image

FORMULA_SHEET_PHRASES = (
    "formula sheet",
    "data sheet",
    "physical constants",
    "list of data",
    "formulae and relationships",
)
SCAN_WATERMARK_RE = re.compile(r"provided by|dse\.life", re.I)


def is_formula_sheet_text(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in FORMULA_SHEET_PHRASES)


def _ocr_png_bytes(data: bytes) -> str:
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="ignore")


def ocr_image_text(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return _ocr_png_bytes(buf.getvalue())


def ocr_pdf_page_text(page: fitz.Page, scale: float = 1.5) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    try:
        return ocr_image_text(image).lower()
    finally:
        del image, pixmap


def is_formula_sheet_page(page: fitz.Page, *, ocr: bool = True) -> bool:
    native = page.get_text("text")
    if is_formula_sheet_text(native):
        return True
    if not ocr:
        return False
    stripped = native.strip()
    # Born-digital question pages already have extractable text; skip OCR.
    # Scanned papers usually only expose a short watermark.
    if (
        stripped
        and not SCAN_WATERMARK_RE.search(stripped)
        and len(stripped) >= 6
    ):
        return False
    return is_formula_sheet_text(ocr_pdf_page_text(page))


def is_formula_sheet_image(image: Image.Image) -> bool:
    return is_formula_sheet_text(ocr_image_text(image))


def extend_trailing_hits(flags: Sequence[bool]) -> set[int]:
    """Include the first hit and every page after it (trailing sheet pages)."""
    for index, hit in enumerate(flags):
        if hit:
            return set(range(index, len(flags)))
    return set()


def formula_pdf_indices(doc: fitz.Document, *, from_index: int = 0) -> set[int]:
    flags = [False] * len(doc)
    start = max(0, from_index)
    for index in range(start, len(doc)):
        flags[index] = is_formula_sheet_page(doc[index])
    return extend_trailing_hits(flags)


def exported_index_to_pdf(png_i: int, n_exported: int, n_pdf: int) -> list[int]:
    """Map an exported page index (starts.json) onto source PDF page index(es)."""
    if n_pdf <= 0 or png_i < 0:
        return []
    if n_exported == n_pdf:
        return [png_i] if png_i < n_pdf else []
    if n_exported == n_pdf - 1:
        pdf_i = png_i + 1
        return [pdf_i] if 0 <= pdf_i < n_pdf else []
    if n_exported == (n_pdf - 1) * 2:
        pdf_i = (png_i // 2) + 1
        return [pdf_i] if 0 <= pdf_i < n_pdf else []
    if n_exported == n_pdf * 2:
        pdf_i = png_i // 2
        return [pdf_i] if 0 <= pdf_i < n_pdf else []
    return [png_i] if png_i < n_pdf else []


def pdf_index_to_exported(pdf_i: int, n_exported: int, n_pdf: int) -> list[int]:
    """Map a source PDF page onto exported page index(es)."""
    if n_pdf <= 0 or pdf_i < 0:
        return []
    if n_exported == n_pdf:
        return [pdf_i] if pdf_i < n_exported else []
    if n_exported == n_pdf - 1:
        png_i = pdf_i - 1
        return [png_i] if 0 <= png_i < n_exported else []
    if n_exported == (n_pdf - 1) * 2:
        content = pdf_i - 1
        if content < 0:
            return []
        return [i for i in (content * 2, content * 2 + 1) if 0 <= i < n_exported]
    if n_exported == n_pdf * 2:
        return [i for i in (pdf_i * 2, pdf_i * 2 + 1) if 0 <= i < n_exported]
    return [pdf_i] if pdf_i < n_exported else []


def exported_formula_indices(
    pdf_indices: set[int], n_exported: int, n_pdf: int
) -> set[int]:
    found: set[int] = set()
    for pdf_i in pdf_indices:
        found.update(pdf_index_to_exported(pdf_i, n_exported, n_pdf))
    if not found:
        return set()
    return set(range(min(found), n_exported))


def exported_range_to_pdf_pages(
    page_from: int, page_to: int, n_exported: int, n_pdf: int
) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for png_i in range(page_from, page_to + 1):
        for pdf_i in exported_index_to_pdf(png_i, n_exported, n_pdf):
            if pdf_i not in seen:
                seen.add(pdf_i)
                pages.append(pdf_i)
    return pages


def clip_question_ranges(
    questions: Sequence[dict[str, Any]], formula_exported: set[int]
) -> list[dict[str, Any]]:
    """Drop formula/data pages from each question's page_from..page_to."""
    clipped: list[dict[str, Any]] = []
    for item in questions:
        page_from = int(item["page_from"])
        page_to = int(item["page_to"])
        if page_from in formula_exported:
            continue
        new_to = page_to
        for page in range(page_from, page_to + 1):
            if page in formula_exported:
                new_to = page - 1
                break
        if new_to < page_from:
            continue
        updated = dict(item)
        updated["page_to"] = new_to
        clipped.append(updated)
    return clipped


def refresh_starts_meta(meta: dict[str, Any], doc: fitz.Document | None) -> dict[str, Any]:
    """Clip starts.json ranges and record formula/data sheet page indices."""
    questions = [dict(item) for item in (meta.get("questions") or [])]
    n_exported = int(meta.get("pages") or 0)
    if not n_exported and questions:
        n_exported = max(int(item["page_to"]) for item in questions) + 1
    formula_pdf = {int(i) for i in (meta.get("formula_pdf_pages") or [])}
    formula_png = {int(i) for i in (meta.get("formula_pages") or [])}
    if doc is not None and not formula_pdf and not formula_png:
        n_pdf = len(doc)
        from_index = 0
        if questions:
            last_from = max(int(item["page_from"]) for item in questions)
            mapped = exported_index_to_pdf(last_from, n_exported or n_pdf, n_pdf)
            if mapped:
                from_index = mapped[0]
        formula_pdf = formula_pdf_indices(doc, from_index=from_index)
        formula_png = exported_formula_indices(
            formula_pdf, n_exported or n_pdf, n_pdf
        )
    elif doc is not None and formula_pdf and not formula_png:
        formula_png = exported_formula_indices(
            formula_pdf, n_exported or len(doc), len(doc)
        )
    questions = clip_question_ranges(questions, formula_png)
    updated = dict(meta)
    updated["questions"] = questions
    updated["formula_pages"] = sorted(formula_png)
    updated["formula_pdf_pages"] = sorted(formula_pdf)
    if n_exported:
        updated["pages"] = n_exported
    return updated
