#!/usr/bin/env python3
"""Crop scanned DSE MC papers and add blue question anchors."""
from __future__ import annotations

import argparse
import csv
import gc
import io
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageOps

MARKER_RGB = (13, 77, 242)
STRIP_LEFT = 25.0
STRIP_RIGHT = 120.0
RAIL_TOLERANCE = 12.0
# Crop / marker placement relative to the per-page question-number rail.
# Scanned pages shift left/right, so absolute --left/--right alone drift vs "N.".
LEFT_PAD = 36.0  # crop starts this far left of the number rail
ANCHOR_GAP = 16.0  # marker centre sits this far left of the leftmost number ink
RIGHT_FROM_RAIL = 455.0  # fallback / cap: crop right = rail_x + this
RIGHT_PAD = 18.0  # keep this much whitespace past detected content ink
FIRST_LABEL_PAD = 20.0  # crop starts this far above the first question number on a page
MARKER_LEFT_PAD = 8.0  # keep this much crop to the left of the marker centre
FOOTER_GAP = 2.0  # crop bottom this far above detected footer text
FOOTER_RE = re.compile(r"(dse[- ]?phy|phy\s*1a|section\s*a)", re.I)
LABEL_RE = re.compile(r"[#*]?(\d{1,2})([.,])?")
SUBPART_RE = re.compile(r"^[A-Za-z]\d")
Q1_CONFUSION_RE = re.compile(r"^[lIi|\|][.,]$")


def _normalize_label_token(text: str) -> str:
    """Repair common OCR mistakes on left-margin question numbers."""
    text = text.strip()
    if SUBPART_RE.match(text):
        return ""
    if Q1_CONFUSION_RE.fullmatch(text):
        return "1."
    # "11." is often read as "1]" or "1}".
    if re.fullmatch(r"1[\]}]", text):
        return "11."
    # Leading junk before digits / asterisk.
    text = re.sub(r"^[^0-9#*]+", "", text)
    return text


def _parse_label_token(text: str) -> tuple[int, str | None] | None:
    raw = text.strip()
    if SUBPART_RE.match(raw):
        return None
    text = _normalize_label_token(raw)
    if not text:
        return None
    match = LABEL_RE.fullmatch(text)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _y_bounds(page: fitz.Page) -> tuple[float, float]:
    height = page.rect.height
    return 15.0, max(735.0, height - 28.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=36)
    parser.add_argument("--cover-pages", type=int, default=1)
    parser.add_argument("--scale", type=float, default=5.0)
    parser.add_argument("--top", type=float, default=65.0)
    parser.add_argument("--bottom", type=float, default=779.0)
    parser.add_argument("--left", type=float, default=35.0, help="Fallback crop left when a page has no rail x")
    parser.add_argument("--right", type=float, default=555.0, help="Fallback absolute crop right")
    parser.add_argument("--left-pad", type=float, default=LEFT_PAD, help="Crop left = page rail x minus this")
    parser.add_argument("--anchor-gap", type=float, default=ANCHOR_GAP, help="Marker x = page rail x minus this")
    parser.add_argument(
        "--right-from-rail",
        type=float,
        default=RIGHT_FROM_RAIL,
        help="Fallback crop right = page rail x plus this",
    )
    parser.add_argument("--right-pad", type=float, default=RIGHT_PAD, help="Padding past detected content right edge")
    parser.add_argument("--label-size", type=float, default=7.0)
    parser.add_argument("--reference", type=Path, help="Existing anchored PDF used to recover validated anchor positions")
    parser.add_argument("--overrides", type=Path, help="JSON mapping question numbers to [page, x, y]")
    return parser.parse_args()


def _image_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def page_text(page: fitz.Page, scale: float = 1.5) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=_image_bytes(image),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    text = result.stdout.decode("utf-8", errors="ignore").lower()
    del result, image, pixmap
    gc.collect()
    return text


def is_formula_sheet(page: fitz.Page) -> bool:
    text = page_text(page)
    return "formula sheet" in text or "data sheet" in text or "physical constants" in text


def _parse_conf(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value >= 0 else 0.0


def _candidate_score(punct_score: int, confidence: float, y: float) -> tuple:
    # Prefer "N." over "N," over bare "N", then confidence, then topmost.
    return (punct_score, confidence, -y)


def _cluster_rail_x(xs: list[float], tolerance: float = RAIL_TOLERANCE) -> float | None:
    if not xs:
        return None
    ordered = sorted(xs)
    best: list[float] = []
    for i, start in enumerate(ordered):
        cluster = [start]
        for value in ordered[i + 1 :]:
            if value - start <= tolerance * 2 and value - cluster[-1] <= tolerance:
                cluster.append(value)
            elif value - start > tolerance * 2:
                break
        if len(cluster) > len(best) or (len(cluster) == len(best) and cluster and (not best or cluster[0] < best[0])):
            best = cluster
    if not best:
        return None
    return sum(best) / len(best)


def _strip_image(page: fitz.Page, scale: float) -> tuple[Image.Image, float]:
    """Render the left question-number strip with contrast boost."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    full = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    left_px = max(0, int(STRIP_LEFT * scale))
    right_px = min(full.width, int(STRIP_RIGHT * scale))
    strip = full.crop((left_px, 0, right_px, full.height))
    strip = ImageOps.autocontrast(strip.convert("L")).convert("RGB")
    del full, pixmap
    return strip, scale


def _ocr_label_rows(strip: Image.Image, scale: float, ocr_scale: float) -> list[dict[str, str]]:
    if abs(ocr_scale - scale) > 0.01:
        ratio = ocr_scale / scale
        ocr_img = strip.resize(
            (max(1, int(strip.width * ratio)), max(1, int(strip.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    else:
        ocr_img = strip
    data = _image_bytes(ocr_img)
    rows_out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for psm in ("11", "6", "4"):
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", psm, "tsv"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        rows = csv.DictReader(result.stdout.decode("utf-8").splitlines(), delimiter="\t")
        for row in rows:
            text = row.get("text", "").strip()
            if not text:
                continue
            key = (text, row.get("left", ""), row.get("top", ""), psm)
            if key in seen:
                continue
            seen.add(key)
            rows_out.append(row)
    return rows_out


def detect_labels(page: fitz.Page, scale: float, questions: int) -> list[tuple[int, float, float]]:
    """Return (number, x, y) labels on the vertical question-number rail."""
    y_min, y_max = _y_bounds(page)
    strip, render_scale = _strip_image(page, scale)
    best: dict[int, tuple[tuple, float, float]] = {}

    for ocr_scale in (render_scale, min(render_scale * 1.6, 8.0)):
        rows = _ocr_label_rows(strip, render_scale, ocr_scale)
        for row in rows:
            text = row["text"].strip()
            if not text or text.startswith("(") or text.endswith(")"):
                continue
            # TSV boxes are in the OCR image, which is scaled to ocr_scale.
            x = float(row["left"]) / ocr_scale + STRIP_LEFT
            y = float(row["top"]) / ocr_scale
            if not STRIP_LEFT <= x <= STRIP_RIGHT or not y_min <= y <= y_max:
                continue
            parsed = _parse_label_token(text)
            if not parsed:
                continue
            number, punct = parsed
            if not 1 <= number <= questions:
                continue
            punct_score = 2 if punct == "." else (1 if punct == "," else 0)
            confidence = _parse_conf(row.get("conf", "0"))
            score = _candidate_score(punct_score, confidence, y)
            previous = best.get(number)
            if previous is None or score > previous[0]:
                best[number] = (score, x, y)

    strip.close()
    gc.collect()

    if not best:
        return []

    rail_x = _cluster_rail_x([x for _score, x, _y in best.values()])
    if rail_x is None:
        return []

    filtered: list[tuple[int, float, float, tuple]] = []
    for number, (score, x, y) in best.items():
        if abs(x - rail_x) > RAIL_TOLERANCE:
            continue
        filtered.append((number, x, y, score))

    filtered.sort(key=lambda item: (item[0], -item[3][0], -item[3][1], item[2]))
    by_number: dict[int, tuple[int, float, float]] = {}
    for number, x, y, _score in filtered:
        by_number.setdefault(number, (number, rail_x, y))

    return [by_number[n] for n in sorted(by_number)]


def _patch_has_number(text: str, number: int) -> bool:
    compact = re.sub(r"\s+", "", text)
    parsed = _parse_label_token(compact)
    if parsed and parsed[0] == number:
        return True
    for token in text.split():
        parsed = _parse_label_token(token)
        if parsed and parsed[0] == number:
            return True
    return False


def number_is_beside_anchor(
    page: fitz.Page,
    number: int,
    x: float,
    y: float,
    scale: float = 4.0,
) -> bool:
    """True if the printed question number sits immediately to the bottom-right of (x, y)."""
    clip = fitz.Rect(
        max(0.0, x - 8.0),
        max(0.0, y - 6.0),
        min(page.rect.width, x + 40.0),
        min(page.rect.height, y + 20.0),
    )
    if clip.width < 4 or clip.height < 4:
        return False
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    data = _image_bytes(image)
    del image, pixmap
    for psm in ("8", "7"):
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", psm],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        text = result.stdout.decode("utf-8", errors="ignore").strip()
        if _patch_has_number(text, number):
            return True
    return False


def verify_labels_beside_numbers(
    source: fitz.Document,
    labels: dict[int, tuple[int, float, float]],
    page_hits: dict[int, list[tuple[int, float, float]]],
    questions: int,
) -> dict[int, tuple[int, float, float]]:
    """Keep only anchors whose question number is immediately next to the point.

    If the assigned hit fails, try other OCR hits for that number.
    """
    verified: dict[int, tuple[int, float, float]] = {}
    failed: list[int] = []
    for number in range(1, questions + 1):
        candidates: list[tuple[int, float, float]] = []
        if number in labels:
            candidates.append(labels[number])
        for page_index, hits in page_hits.items():
            for hit_n, x, y in hits:
                if hit_n != number:
                    continue
                pos = (page_index, x, y)
                if pos not in candidates:
                    candidates.append(pos)
        orig = labels.get(number)
        found = None
        ordered_candidates = []
        if orig is not None:
            ordered_candidates.append(orig)
        for page_index, x, y in candidates:
            if orig is not None and (page_index, x, y) == orig:
                continue
            ordered_candidates.append((page_index, x, y))
        previous = verified.get(number - 1)
        for page_index, x, y in ordered_candidates:
            if previous is not None and (page_index, y) <= (previous[0], previous[2]):
                continue
            if number_is_beside_anchor(source[page_index], number, x, y):
                found = (page_index, x, y)
                break
        if found is None:
            failed.append(number)
            if orig is not None:
                verified[number] = orig
        else:
            verified[number] = found
    return verified


def collect_labels(
    source: fitz.Document,
    content_pages: list[int],
    scale: float,
    questions: int,
    page_hits: dict[int, list[tuple[int, float, float]]] | None = None,
) -> dict[int, tuple[int, float, float]]:
    """Detect labels on each page, then assign in reading order (OCR hits only)."""
    labels: dict[int, tuple[int, float, float]] = {}
    if page_hits is None:
        page_hits = {}
        for page_index in content_pages:
            page_hits[page_index] = detect_labels(source[page_index], scale, questions)

    expected = 1
    last_pos = (-1, -1.0)
    for page_index in content_pages:
        hits = sorted(page_hits[page_index], key=lambda item: item[2])
        for number, x, y in hits:
            if number in labels or number < expected:
                continue
            if (page_index, y) <= last_pos:
                continue
            # Accept the next number, or a short OCR miss (e.g. Q1/Q2 misread but Q3 visible).
            if number > expected + 4:
                continue
            labels[number] = (page_index, x, y)
            expected = number + 1
            while expected in labels:
                expected += 1
            last_pos = (page_index, y)

    return _fill_same_page_gaps(labels, page_hits)


def merge_page_hits(
    source: fitz.Document,
    content_pages: list[int],
    questions: int,
    scales: tuple[float, ...] = (5.0, 3.0, 8.0),
) -> dict[int, list[tuple[int, float, float]]]:
    """Run OCR at several scales and keep the best hit per question per page."""
    merged: dict[int, dict[int, tuple[int, float, float]]] = {pi: {} for pi in content_pages}
    for page_index in content_pages:
        for scale in scales:
            for number, x, y in detect_labels(source[page_index], scale, questions):
                merged[page_index].setdefault(number, (number, x, y))
    return {pi: list(hits.values()) for pi, hits in merged.items()}


def _fill_same_page_gaps(
    labels: dict[int, tuple[int, float, float]],
    page_hits: dict[int, list[tuple[int, float, float]]],
) -> dict[int, tuple[int, float, float]]:
    """Fill missing numbers from neighbouring OCR hits (same page, else adjacent pages)."""
    filled = dict(labels)
    if not filled:
        return filled
    questions = max(filled)
    for number in range(1, questions + 1):
        if number in filled:
            continue
        prevs = [k for k in filled if k < number]
        nexts = [k for k in filled if k > number]
        if not prevs or not nexts:
            continue
        prev_n = max(prevs)
        next_n = min(nexts)
        p_page, p_x, p_y = filled[prev_n]
        n_page, n_x, n_y = filled[next_n]
        if p_page == n_page:
            filled[number] = (p_page, (p_x + n_x) / 2.0, (p_y + n_y) / 2.0)
            continue
        if n_page < p_page:
            continue
        # Next question is low on its page: this one likely starts that page.
        if n_y > 160.0:
            filled[number] = (n_page, n_x, max(70.0, n_y - 90.0))
        else:
            filled[number] = (p_page, p_x, p_y + 70.0)
    return filled


def enforce_label_order(
    labels: dict[int, tuple[int, float, float]],
    questions: int,
) -> dict[int, tuple[int, float, float]]:
    """Drop only individual labels that break increasing (page, y) order."""
    ordered: dict[int, tuple[int, float, float]] = {}
    previous: tuple[int, float] | None = None
    rejected: list[int] = []
    for number in range(1, questions + 1):
        if number not in labels:
            continue
        page_index, x, y = labels[number]
        key = (page_index, y)
        if previous is not None and key <= previous:
            rejected.append(number)
            continue
        ordered[number] = (page_index, x, y)
        previous = key
    if rejected:
        print(f"Rejected out-of-order labels: {rejected}")
    return ordered


def _span_pages(
    labels: dict[int, tuple[int, float, float]],
    questions: int,
) -> set[int]:
    """Pages that sit between consecutive question anchors (multi-page question bodies)."""
    pages: set[int] = set()
    for number in range(1, questions):
        if number not in labels or (number + 1) not in labels:
            continue
        start_page = labels[number][0]
        end_page = labels[number + 1][0]
        if end_page > start_page + 1:
            pages.update(range(start_page + 1, end_page))
    return pages


def detect_footer_top(page: fitz.Page, scale: float, left: float, right: float, bottom_limit: float) -> float | None:
    """Return approximate y of footer text if found near the bottom."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    # Search a band just above bottom_limit; keep it tall enough for scanned footers.
    search_top = max(0.0, bottom_limit - 120.0)
    crop = image.crop(
        (
            int(left * scale),
            int(search_top * scale),
            int(min(right, page.rect.width) * scale),
            int(min(bottom_limit, page.rect.height) * scale),
        )
    )
    if crop.height < 8 or crop.width < 8:
        del image, pixmap
        return None
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
        input=_image_bytes(crop),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    footer_y: float | None = None
    rows = csv.DictReader(result.stdout.decode("utf-8").splitlines(), delimiter="\t")
    for row in rows:
        text = row["text"].strip()
        if not text or not FOOTER_RE.search(text):
            # Also catch bare year-DSE style tokens split across cells.
            lower = text.lower()
            if "dse" not in lower and "phy" not in lower and "1a" not in lower:
                continue
        y = search_top + float(row["top"]) / scale
        footer_y = y if footer_y is None else min(footer_y, y)
    del result, crop, image, pixmap
    gc.collect()
    return footer_y


def detect_content_right(
    page: fitz.Page,
    scale: float,
    left: float,
    top: float,
    bottom: float,
) -> float | None:
    """Rightmost x whose column has enough dark pixels (ignores sparse edge speckles)."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    left_px = max(0, int(left * scale))
    top_px = max(0, int(top * scale))
    bottom_px = min(image.height, int(bottom * scale))
    right_px = min(image.width, int(page.rect.width * scale))
    if bottom_px - top_px < 8 or right_px - left_px < 8:
        del image, pixmap
        return None
    crop = image.crop((left_px, top_px, right_px, bottom_px))
    pixels = crop.load()
    rows = list(range(0, crop.height, 2))
    if not rows:
        del crop, image, pixmap
        return None
    # ~1.5% of subsampled rows must be dark — high enough to skip dust, low enough
    # to keep long text endings like "Which" / "respectively".
    min_dark = max(3, int(len(rows) * 0.015))
    content_right: float | None = None
    for x in range(crop.width):
        dark = 0
        for y in rows:
            red, green, blue = pixels[x, y]
            if (red + green + blue) / 3.0 < 205:
                dark += 1
                if dark >= min_dark:
                    content_right = left + (x + 1) / scale
                    break
    del crop, image, pixmap
    gc.collect()
    return content_right


def detect_crop_top(
    page: fitz.Page,
    scale: float,
    left: float,
    right: float,
    first_question_y: float,
    pad: float = FIRST_LABEL_PAD,
) -> float:
    """Crop top just above the first question, below any leaked header line."""
    default_top = max(0.0, first_question_y - pad)
    search_top = max(0.0, first_question_y - 70.0)
    if first_question_y - search_top < 10:
        return default_top
    # Only inspect the left text column - full-width scans pick up diagrams
    # (e.g. sample paper Q1 figure) and hide the header/question gap.
    scan_right = min(right, left + 220.0, page.rect.width)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    crop = image.crop(
        (
            int(left * scale),
            int(search_top * scale),
            int(scan_right * scale),
            int(first_question_y * scale),
        )
    )
    if crop.height < 4 or crop.width < 8:
        del image, pixmap
        return default_top
    pixels = crop.load()
    min_dark = max(3, int(crop.width * 0.015))
    ink_rows: list[bool] = []
    for y in range(crop.height):
        dark = 0
        for x in range(0, crop.width, 2):
            red, green, blue = pixels[x, y]
            if (red + green + blue) / 3.0 < 210:
                dark += 1
                if dark >= min_dark:
                    break
        ink_rows.append(dark >= min_dark)

    # Find the lowest whitespace gap above the question (header ends, then blank).
    min_gap_px = max(3, int(3.0 * scale))
    gap_end = None  # last index of a qualifying white run (closest to question)
    run = 0
    for index, is_ink in enumerate(ink_rows):
        if not is_ink:
            run += 1
        else:
            if run >= min_gap_px:
                gap_end = index - 1
            run = 0
    if run >= min_gap_px:
        gap_end = len(ink_rows) - 1

    del crop, image, pixmap
    gc.collect()
    if gap_end is None:
        return default_top

    # Place top near the bottom of that gap (just above the question).
    top = search_top + (gap_end + 1) / scale - 1.0
    # Never sit closer than *pad* to the first number (first-on-page labels
    # were getting cropped through at 4pt).
    top = max(0.0, min(first_question_y - pad, top))
    if top < search_top + 2:
        return default_top
    return top


def number_ink_top(
    page: fitz.Page,
    x: float,
    y: float,
    scale: float = 4.0,
) -> float:
    """Top of the printed question-number ink near (x, y), in PDF points."""
    clip = fitz.Rect(
        max(0.0, x - 2.0),
        max(0.0, y - 12.0),
        min(page.rect.width, x + 30.0),
        min(page.rect.height, y + 14.0),
    )
    if clip.width < 4 or clip.height < 4:
        return y
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    pixels = image.load()
    min_dark = max(2, int(image.width * 0.04))
    ink_y: float | None = None
    for row in range(image.height):
        dark = 0
        for col in range(0, image.width, 1):
            red, green, blue = pixels[col, row]
            if (red + green + blue) / 3.0 < 140:
                dark += 1
                if dark >= min_dark:
                    ink_y = clip.y0 + row / scale
                    break
        if ink_y is not None:
            break
    del image, pixmap
    return ink_y if ink_y is not None else y


def number_ink_left(
    page: fitz.Page,
    x: float,
    y: float,
    scale: float = 4.0,
) -> float:
    """Left edge of printed number ink near (x, y), including a leading * or #."""
    clip = fitz.Rect(
        max(0.0, x - 20.0),
        max(0.0, y - 4.0),
        min(page.rect.width, x + 24.0),
        min(page.rect.height, y + 16.0),
    )
    if clip.width < 4 or clip.height < 4:
        return x
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    pixels = image.load()
    min_dark = max(2, int(image.height * 0.08))
    ink_x: float | None = None
    for col in range(image.width):
        dark = 0
        for row in range(image.height):
            red, green, blue = pixels[col, row]
            if (red + green + blue) / 3.0 < 140:
                dark += 1
                if dark >= min_dark:
                    ink_x = clip.x0 + col / scale
                    break
        if ink_x is not None:
            break
    del image, pixmap
    return ink_x if ink_x is not None else x


def load_overrides(path: Path | None) -> dict[int, tuple[int, float, float]]:
    if path is None:
        return {}
    raw = json.loads(path.read_text())
    return {int(k): (int(v[0]), float(v[1]), float(v[2])) for k, v in raw.items()}


def _is_marker_blue(rgb: tuple[int, int, int]) -> bool:
    red, green, blue = rgb
    return blue > 150 and blue > red + 40 and blue > green + 30


def recover_reference_labels(
    path: Path,
    questions: int,
    source_pages: list[int],
    source_top: float,
) -> dict[int, tuple[int, float, float]]:
    reference = fitz.open(path)
    candidates: list[tuple[int, float]] = []
    for page_index, page in enumerate(reference):
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        pixels = image.load()
        visited: set[tuple[int, int]] = set()
        components: list[tuple[float, float, int]] = []
        for y in range(image.height):
            for x in range(min(80, image.width)):
                if (x, y) in visited:
                    continue
                if not _is_marker_blue(pixels[x, y]):
                    continue
                stack = [(x, y)]
                visited.add((x, y))
                points: list[tuple[int, int]] = []
                while stack:
                    px, py = stack.pop()
                    points.append((px, py))
                    for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                        if not (0 <= nx < min(80, image.width) and 0 <= ny < image.height):
                            continue
                        if (nx, ny) in visited:
                            continue
                        if _is_marker_blue(pixels[nx, ny]):
                            visited.add((nx, ny))
                            stack.append((nx, ny))
                xs = [px for px, _ in points]
                ys = [py for _, py in points]
                width = max(xs) - min(xs) + 1
                height = max(ys) - min(ys) + 1
                if 6 <= width <= 20 and 6 <= height <= 20 and len(points) >= 40:
                    components.append(
                        (sum(xs) / len(xs), sum(ys) / len(ys), len(points))
                    )
        components.sort(key=lambda item: item[1])
        for _x, y, _size in components:
            if page_index >= len(source_pages):
                raise SystemExit(f"Reference has an unexpected output page {page_index + 1}.")
            candidates.append((source_pages[page_index], y + source_top))
        del image, pixmap
    reference.close()
    if len(candidates) != questions:
        raise SystemExit(f"Reference contains {len(candidates)} anchors; expected {questions}.")
    labels: dict[int, tuple[int, float, float]] = {}
    for number, (page_index, y) in enumerate(candidates, start=1):
        labels[number] = (page_index, 0.0, y)
    return labels


def find_marker_centres(image: Image.Image, max_x: int = 60) -> list[tuple[float, float]]:
    pixels = image.load()
    visited: set[tuple[int, int]] = set()
    centres: list[tuple[float, float]] = []
    width_limit = min(max_x, image.width)
    for y in range(image.height):
        for x in range(width_limit):
            if (x, y) in visited or not _is_marker_blue(pixels[x, y]):
                continue
            stack = [(x, y)]
            visited.add((x, y))
            points: list[tuple[int, int]] = []
            while stack:
                px, py = stack.pop()
                points.append((px, py))
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if not (0 <= nx < width_limit and 0 <= ny < image.height):
                        continue
                    if (nx, ny) in visited or not _is_marker_blue(pixels[nx, ny]):
                        continue
                    visited.add((nx, ny))
                    stack.append((nx, ny))
            xs = [px for px, _ in points]
            ys = [py for _, py in points]
            width = max(xs) - min(xs) + 1
            height = max(ys) - min(ys) + 1
            # Circles only (exclude blue digit glyphs).
            if 8 <= width <= 20 and 8 <= height <= 20 and abs(width - height) <= 4 and len(points) >= 40:
                # Prefer filled-circle density over hollow digit strokes.
                density = len(points) / float(width * height)
                if density < 0.55:
                    continue
                centres.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    if not centres:
        return []
    # Keep the leftmost rail only (dots sit left of blue labels).
    min_x = min(cx for cx, _cy in centres)
    centres = [(cx, cy) for cx, cy in centres if cx <= min_x + 3.0]
    centres.sort(key=lambda item: item[1])
    return centres


def validate_marker_alignment(path: Path) -> None:
    """Reject output pages whose marker centres do not share one vertical rail."""
    document = fitz.open(path)
    try:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            centres = find_marker_centres(image, max_x=80)
            if not centres:
                raise SystemExit(f"No markers found on output page {page_number}.")
            xs = [cx for cx, _cy in centres]
            if max(xs) - min(xs) > 2.0:
                raise SystemExit(
                    f"Marker rail is not aligned on output page {page_number}: {[round(x, 2) for x in xs]}."
                )
            del image, pixmap
    finally:
        document.close()


def main() -> None:
    args = parse_args()
    source = fitz.open(args.source)
    overrides = load_overrides(args.overrides)
    if overrides and set(overrides) == set(range(1, args.questions + 1)):
        content_pages = sorted({page_index for page_index, _x, _y in overrides.values()})
    else:
        excluded_pages = {
            index
            for index in range(args.cover_pages, len(source))
            if is_formula_sheet(source[index])
        }
        content_pages = [
            index for index in range(args.cover_pages, len(source)) if index not in excluded_pages
        ]
        if overrides:
            # Ensure override pages are included even if formula-sheet heuristic misfires.
            for page_index, _x, _y in overrides.values():
                if page_index not in content_pages:
                    content_pages.append(page_index)
            content_pages = sorted(content_pages)

    labels: dict[int, tuple[int, float, float]] = {}
    page_hits: dict[int, list[tuple[int, float, float]]] = {}
    if args.reference:
        if overrides:
            raise SystemExit("Use either --reference or --overrides, not both.")
        labels = recover_reference_labels(args.reference, args.questions, content_pages, args.top)
    elif overrides and set(overrides) == set(range(1, args.questions + 1)):
        labels = dict(overrides)
    else:
        page_hits = merge_page_hits(source, content_pages, args.questions)
        labels = collect_labels(
            source, content_pages, args.scale, args.questions, page_hits=page_hits
        )

    # Manual fixes only fill gaps - stale overrides must not overwrite good OCR.
    if overrides:
        for number, pos in overrides.items():
            labels.setdefault(int(number), (int(pos[0]), float(pos[1]), float(pos[2])))
    labels = enforce_label_order(labels, args.questions)
    labels = _fill_same_page_gaps(labels, page_hits)
    missing = sorted(set(range(1, args.questions + 1)) - labels.keys())
    if (
        missing
        and set(missing).issubset({34, 35, 36})
        and labels.get(33)
        and not (set(missing) & set(range(1, 34)))
    ):
        args.questions = 33
        labels = {number: labels[number] for number in range(1, 34) if number in labels}
        missing = sorted(set(range(1, args.questions + 1)) - labels.keys())
        print("Note: no Q34-Q36 in this PDF; segmenting 33 questions.")
    if missing:
        raise SystemExit(f"Could not locate question labels: {missing}. Add them to --overrides.")

    before_verify = dict(labels)
    labels = verify_labels_beside_numbers(source, labels, page_hits, args.questions)
    ordered = enforce_label_order(labels, args.questions)
    if len(ordered) < args.questions:
        print(
            "WARNING: beside-anchor check broke question order; keeping OCR labels."
        )
        labels = before_verify
    else:
        labels = ordered
    missing = sorted(set(range(1, args.questions + 1)) - labels.keys())
    if missing:
        raise SystemExit(
            f"Could not locate question labels after number-beside-anchor check: {missing}."
        )

    output = fitz.open()
    by_page: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for number in range(1, args.questions + 1):
        page_index, x, y = labels[number]
        if page_index not in content_pages:
            raise SystemExit(f"Question {number} points to an excluded page: {page_index}.")
        by_page[page_index].append((number, x, y))
    anchor_count = sum(len(anchors) for anchors in by_page.values())
    if anchor_count != args.questions:
        raise SystemExit(f"Expected {args.questions} anchors, found {anchor_count}.")

    page_crops: dict[int, dict[str, float]] = {}
    anchor_by_number: dict[int, dict[str, float | int]] = {}

    crop_pages = sorted(set(by_page) | _span_pages(labels, args.questions))
    for page_index in crop_pages:
        page = source[page_index]
        scale = args.scale
        page_labels = by_page.get(page_index, [])
        detected = detect_labels(page, scale, args.questions)
        # One vertical rail per page from detected (or override) number x positions.
        rail_xs = sorted(x for _number, x, _y in page_labels if x > 0)
        if not rail_xs:
            rail_xs = sorted(x for _number, x, _y in detected if x > 0)
        if rail_xs:
            mid = rail_xs[len(rail_xs) // 2]
            rail_x = mid
        else:
            rail_x = args.left + args.left_pad
        left = max(0.0, rail_x - args.left_pad)
        provisional_right = min(page.rect.width, rail_x + args.right_from_rail)
        if provisional_right - left < 80.0:
            left = max(0.0, args.left)
            provisional_right = min(page.rect.width, args.right)

        if page_labels:
            _first_number, first_x, first_y = min(page_labels, key=lambda item: item[2])
            last_question_y = max(y for _number, _x, y in page_labels)
            first_question_y = min(first_y, number_ink_top(page, first_x, first_y))
        elif detected:
            ys = [y for _number, _x, y in detected]
            first_question_y = min(ys)
            last_question_y = max(ys)
        else:
            first_question_y = args.top + 40.0
            last_question_y = args.bottom - 40.0
        top = detect_crop_top(
            page,
            min(scale, 2.0),
            left,
            provisional_right,
            first_question_y,
            pad=FIRST_LABEL_PAD,
        )
        top = min(top, first_question_y - FIRST_LABEL_PAD)
        top = max(0.0, top)

        # Search for the printed footer near the true page bottom (not --bottom alone).
        # Old --bottom=779 sat above the footer on some scans and clipped figure labels.
        footer_limit = min(page.rect.height, max(args.bottom, last_question_y + 80.0) + 80.0)
        footer_limit = min(page.rect.height, max(footer_limit, page.rect.height - 2.0))
        footer_y = detect_footer_top(page, min(scale, 2.5), left, provisional_right, footer_limit)
        if footer_y is not None and footer_y > last_question_y + 20:
            bottom = footer_y - FOOTER_GAP
        else:
            bottom = min(page.rect.height - 8.0, max(args.bottom, last_question_y + 120.0))
        bottom = max(bottom, last_question_y + 24.0)
        bottom = min(bottom, page.rect.height - 4.0)
        if top >= bottom:
            raise SystemExit(f"Question area is outside crop bounds on page {page_index + 1}.")

        content_right = detect_content_right(
            page,
            min(scale, 2.0),
            left + 20.0,
            top,
            bottom,
        )
        if content_right is not None:
            right = min(page.rect.width - 2.0, content_right + args.right_pad)
        else:
            right = provisional_right
        # Never keep a huge empty right band past the rail-relative fallback.
        right = min(right, provisional_right)
        right = max(right, left + 80.0)

        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        draw = ImageDraw.Draw(image)
        label_left = rail_x
        for _number, x, y in page_labels:
            label_left = min(label_left, number_ink_left(page, x if x > 0 else rail_x, y))
        marker_x = label_left - args.anchor_gap
        left = min(left, marker_x - MARKER_LEFT_PAD)
        left = max(0.0, left)
        marker_x = max(left + MARKER_LEFT_PAD, marker_x)
        page_crops[page_index] = {
            "source_page": page_index,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "marker_x": marker_x,
        }
        for number, _x, y in page_labels:
            clamped_y = min(max(y, top + 4.0), bottom - 4.0)
            anchor_by_number[number] = {
                "n": number,
                "source_page": page_index,
                "x": marker_x,
                "y": clamped_y,
            }
        if not page_labels:
            print(
                f"page {page_index + 1}: rail_x={rail_x:.1f} left={left:.1f} right={right:.1f} "
                f"top={top:.1f} bottom={bottom:.1f} (continuation page)"
            )
            del image, draw, pixmap
            gc.collect()
            continue
        marker_radius = 2.2 * scale
        for _number, _x, y in page_labels:
            cx = marker_x * scale
            cy = min(max(y, top + 4.0), bottom - 4.0) * scale
            # Circles only — blue digits collide with printed "N." and confuse split crops.
            draw.ellipse(
                (cx - marker_radius, cy - marker_radius, cx + marker_radius, cy + marker_radius),
                fill=MARKER_RGB,
            )
        image = image.crop((int(left * scale), int(top * scale), int(right * scale), int(bottom * scale)))
        output_page = output.new_page(width=right - left, height=bottom - top)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        output_page.insert_image(output_page.rect, stream=buffer.getvalue())
        print(
            f"page {page_index + 1}: rail_x={rail_x:.1f} left={left:.1f} right={right:.1f} "
            f"top={top:.1f} bottom={bottom:.1f} footer={None if footer_y is None else round(footer_y, 1)} "
            f"marker_x={marker_x:.1f} questions={len(page_labels)} "
            f"first_pad={first_question_y - top:.1f}"
        )
        del buffer, image, draw, pixmap
        gc.collect()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.save(args.output, garbage=4, deflate=True)
    output.close()
    validate_marker_alignment(args.output)

    meta_path = args.output.with_suffix(".meta.json")
    meta = {
        "questions": args.questions,
        "pages": [page_crops[index] for index in sorted(page_crops)],
        "anchors": [anchor_by_number[n] for n in range(1, args.questions + 1)],
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Wrote {meta_path}")

    source.close()
    print(f"Wrote {args.output} with {len(by_page)} pages and {args.questions} anchors.")


if __name__ == "__main__":
    main()
