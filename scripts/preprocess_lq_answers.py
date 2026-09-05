#!/usr/bin/env python3
"""Crop HKDSE Physics Paper 1B marking-scheme answers into per-question PNGs.

Answer PDFs usually start with Section A (MC keys) then Section B (LQ
solutions). Each Section B question is a table row-block with Solution /
Marks / Remarks - we crop the full page width so remarks stay included.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
from pathlib import Path

import pymupdf as fitz
import numpy as np
from PIL import Image

from png_pdf import combine_pngs_to_pdf

Image.MAX_IMAGE_PIXELS = 250_000_000

SECTION_A_RE = re.compile(r"section\s*a|question\s*no\.?\s*key", re.I)
SECTION_B_RE = re.compile(r"section\s*b", re.I)
SOLUTION_HDR_RE = re.compile(r"solution", re.I)
MARKS_HDR_RE = re.compile(r"marks|remarks", re.I)
MAIN_Q_RE = re.compile(r"^(\d{1,2})(?:\s*[\.(]|$)")
SUBPART_ONLY_RE = re.compile(r"^\([a-z]", re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Marking-scheme PDF (e.g. answer/2019ans.pdf)")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-questions", type=int, default=12)
    parser.add_argument("--scale", type=float, default=2.0)
    return parser.parse_args()


def render_page(page: fitz.Page, scale: float) -> Image.Image:
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def ocr_text(image: Image.Image, psm: str = "6") -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", f"--psm", psm],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="ignore")


def ocr_tsv(image: Image.Image, psm: str = "6") -> list[dict]:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", f"--psm", psm, "tsv"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    rows = list(csv.DictReader(result.stdout.decode("utf-8", errors="ignore").splitlines(), delimiter="\t"))
    out: list[dict] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if conf < 0:
            continue
        try:
            out.append(
                {
                    "text": text,
                    "left": int(float(row["left"])),
                    "top": int(float(row["top"])),
                    "conf": conf,
                }
            )
        except (KeyError, ValueError):
            continue
    return out


def header_text(image: Image.Image) -> str:
    band = image.crop((0, 0, image.width, max(40, int(image.height * 0.14))))
    return ocr_text(band)


def is_section_a_keys(image: Image.Image) -> bool:
    top = header_text(image)
    if SECTION_A_RE.search(top):
        return True
    body = ocr_text(image.crop((0, 0, image.width, min(image.height, int(image.height * 0.45)))))
    if SECTION_A_RE.search(body):
        return True
    return False


def looks_like_section_b(image: Image.Image) -> bool:
    text = header_text(image)
    if SECTION_A_RE.search(text):
        return False
    if SECTION_B_RE.search(text):
        return True
    if SOLUTION_HDR_RE.search(text) and MARKS_HDR_RE.search(text):
        return True
    return bool(find_main_question_ys(image, 12))


def find_main_question_ys(
    image: Image.Image, max_questions: int
) -> list[tuple[int, int]]:
    """Return (question_number, y_px) for main LQ starts on one page."""
    w, h = image.size
    strip = image.crop((0, 0, max(80, int(w * 0.24)), h))
    hits: list[tuple[int, int, float]] = []
    for row in ocr_tsv(strip, psm="6") + ocr_tsv(strip, psm="11"):
        text = row["text"]
        if SUBPART_ONLY_RE.match(text):
            continue
        match = MAIN_Q_RE.match(text) or re.match(r"^(\d{1,2})\(", text)
        if not match:
            continue
        qn = int(match.group(1))
        if not (1 <= qn <= max_questions):
            continue
        if row["left"] > strip.width * 0.7:
            continue
        hits.append((qn, row["top"], row["conf"]))

    # Line OCR fallback for "3. (a)" / "1(a)(i)"
    lines = ocr_text(strip).splitlines()
    line_re = re.compile(r"^\s*(\d{1,2})\s*[.)]\s*(?:\(|[A-Za-z])|^(\d{1,2})\(")
    known = {qn for qn, _, _ in hits}
    for i, line in enumerate(lines):
        m = line_re.match(line.strip())
        if not m:
            continue
        qn = int(m.group(1) or m.group(2))
        if not (1 <= qn <= max_questions) or qn in known:
            continue
        y = int((i / max(1, len(lines))) * h)
        hits.append((qn, y, 20.0))
        known.add(qn)

    best: dict[int, tuple[int, float]] = {}
    for qn, y, conf in hits:
        prev = best.get(qn)
        if prev is None or y < prev[0] - 8 or (abs(y - prev[0]) <= 8 and conf > prev[1]):
            best[qn] = (y, conf)
    return sorted(((qn, y) for qn, (y, _c) in best.items()), key=lambda t: t[1])


def trim_whitespace(image: Image.Image, pad: int = 12) -> Image.Image:
    arr = np.asarray(image.convert("L"))
    ink = arr < 235
    if not ink.any():
        return image
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    top = max(0, int(rows[0]) - pad)
    bottom = min(arr.shape[0], int(rows[-1]) + 1 + pad)
    left = max(0, int(cols[0]) - pad)
    right = min(arr.shape[1], int(cols[-1]) + 1 + pad)
    return image.crop((left, top, right, bottom))


def stitch_vertical(parts: list[Image.Image]) -> Image.Image:
    width = max(p.width for p in parts)
    height = sum(p.height for p in parts)
    out = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for part in parts:
        out.paste(part, (0, y))
        y += part.height
    return out


def longest_question_chain(
    candidates: list[tuple[int, int, int]], max_questions: int
) -> list[tuple[int, int, int]]:
    """Return every detected main-Q start in reading order (gaps allowed)."""
    first: dict[int, tuple[int, int, int]] = {}
    for qn, page_i, y in candidates:
        if not (1 <= qn <= max_questions):
            continue
        if qn not in first:
            first[qn] = (qn, page_i, y)
    return sorted(first.values(), key=lambda t: (t[1], t[2], t[0]))


def process_answers(
    source: Path,
    output_dir: Path,
    *,
    max_questions: int = 12,
    scale: float = 2.0,
) -> int:
    doc = fitz.open(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("q*.png"):
        old.unlink()

    pages: list[Image.Image] = []
    section_b_started = False
    seen_section_a = False
    try:
        for index in range(len(doc)):
            image = render_page(doc[index], scale)
            if not section_b_started:
                if is_section_a_keys(image):
                    seen_section_a = True
                    print(f"  skip Section A page {index + 1}/{len(doc)}")
                    continue
                if looks_like_section_b(image) or seen_section_a:
                    section_b_started = True
                else:
                    print(f"  skip front-matter page {index + 1}/{len(doc)}")
                    continue
            # Once Section B has started, keep every following page (never drop
            # mid-paper pages - OCR false "Section A" hits used to punch holes).
            pages.append(image)
            print(f"  include answer page {index + 1}/{len(doc)} ({image.width}x{image.height})")
    finally:
        doc.close()

    if not pages:
        print(f"  WARNING: no Section B pages found in {source.name}")
        return 0

    candidates: list[tuple[int, int, int]] = []
    for page_index, image in enumerate(pages):
        for qn, y in find_main_question_ys(image, max_questions):
            candidates.append((qn, page_index, int(y)))

    starts = longest_question_chain(candidates, max_questions)
    if not starts:
        print(f"  WARNING: no LQ answer labels found in {source.name}")
        return 0

    print(f"  detected answers Q{starts[0][0]}-Q{starts[-1][0]} ({len(starts)})")

    ends: list[tuple[int | None, int, int]] = [
        (qn, page_i, y) for qn, page_i, y in starts[1:]
    ]
    ends.append((None, len(pages) - 1, pages[-1].height))

    written = 0
    for (qn, start_page, start_y), (_nq, end_page, end_y) in zip(starts, ends):
        parts: list[Image.Image] = []
        for page_index in range(start_page, end_page + 1):
            image = pages[page_index]
            top = max(0, start_y - 6) if page_index == start_page else 0
            if page_index == end_page and _nq is not None:
                bottom = max(top + 20, int(end_y) - 4)
            else:
                bottom = image.height
            bottom = min(bottom, image.height - 8)
            if bottom <= top + 20:
                continue
            parts.append(image.crop((0, top, image.width, bottom)))
        if not parts:
            continue
        combined = trim_whitespace(stitch_vertical(parts))
        out = output_dir / f"q{qn}.png"
        combined.save(out, format="PNG")
        print(f"  Wrote {out.name} ({combined.width}x{combined.height})")
        written += 1

    combine_pngs_to_pdf(output_dir, overwrite=True)
    return written


def main() -> None:
    args = parse_args()
    if not args.source.is_file():
        raise SystemExit(f"Missing {args.source}")
    print(f"Answers: {args.source.name}")
    count = process_answers(
        args.source,
        args.output_dir,
        max_questions=args.max_questions,
        scale=args.scale,
    )
    print(f"Wrote {count} answer crops -> {args.output_dir}")


if __name__ == "__main__":
    main()
