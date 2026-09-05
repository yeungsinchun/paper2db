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
MAIN_Q_RE = re.compile(r"^(\d{1,2})[.\(]")
SUBPART_ONLY_RE = re.compile(r"^\([a-z]", re.I)
PAPER_2_RE = re.compile(r"paper\s*2\b", re.I)
PAPER_2_TOPIC_RE = re.compile(
    r"astronomy\s+and\s+space|atomic\s+world|"
    r"energy\s+and\s+use\s+of\s+energy|medical\s+physics",
    re.I,
)
FRONT_MATTER_RE = re.compile(
    r"marking\s*scheme|markers['’]?\s*reference|model\s*answers|"
    r"method\s*marks|the\s*following\s*symbol",
    re.I,
)


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
    # Solution columns sometimes OCR "Section A" noise; Solution/Marks wins.
    if SOLUTION_HDR_RE.search(top) and MARKS_HDR_RE.search(top):
        return False
    if SECTION_B_RE.search(top) and SOLUTION_HDR_RE.search(top):
        return False
    if SECTION_A_RE.search(top) and not SECTION_B_RE.search(top):
        return True
    body = ocr_text(image.crop((0, 0, image.width, min(image.height, int(image.height * 0.45)))))
    if SOLUTION_HDR_RE.search(body) and (
        MARKS_HDR_RE.search(body) or SECTION_B_RE.search(body)
    ):
        return False
    if SECTION_A_RE.search(body) and re.search(r"question\s*no\.?\s*key", body, re.I):
        return True
    return False


def is_front_matter(image: Image.Image) -> bool:
    """General marking instructions (not Section A keys or Section B solutions)."""
    top = header_text(image)
    if SECTION_B_RE.search(top) or SECTION_A_RE.search(top):
        return False
    if SOLUTION_HDR_RE.search(top) and MARKS_HDR_RE.search(top):
        return False
    body = ocr_text(image.crop((0, 0, image.width, min(image.height, int(image.height * 0.35)))))
    return bool(FRONT_MATTER_RE.search(top) or FRONT_MATTER_RE.search(body))


def is_paper_2_page(image: Image.Image) -> bool:
    """True when we've left Paper 1B marking and hit Paper 2."""
    text = header_text(image)
    # Still on Paper 1B solution tables.
    if SECTION_B_RE.search(text) and (
        SOLUTION_HDR_RE.search(text) or MARKS_HDR_RE.search(text)
    ):
        # Paper 1B says "Paper 1 Section B"; Paper 2 says "Section B: Atomic World".
        if not PAPER_2_TOPIC_RE.search(text):
            return False
    if PAPER_2_RE.search(text) and not re.search(r"paper\s*1\b", text, re.I):
        return True
    if PAPER_2_TOPIC_RE.search(text):
        return True
    body = ocr_text(image.crop((0, 0, image.width, min(image.height, int(image.height * 0.25)))))
    if PAPER_2_TOPIC_RE.search(body):
        return True
    if PAPER_2_RE.search(body) and not re.search(r"paper\s*1\b", body, re.I):
        return True
    return False


def looks_like_section_b(image: Image.Image) -> bool:
    """True only for Paper 1B solution tables - not instruction front matter."""
    text = header_text(image)
    # Landscape A3 marking schemes put Sec A keys left / Sec B right on page 1:
    # "Section A" in the header must not veto a clear Section B / Solution column.
    if SECTION_A_RE.search(text) and not (
        SECTION_B_RE.search(text) or SOLUTION_HDR_RE.search(text)
    ):
        return False
    if FRONT_MATTER_RE.search(text) and not SECTION_B_RE.search(text):
        return False
    if SECTION_B_RE.search(text):
        return True
    if SOLUTION_HDR_RE.search(text) and MARKS_HDR_RE.search(text):
        return True
    # Sideways scans often OCR headers poorly even after rotation; Q labels are enough.
    return bool(find_main_question_ys(image, 12))


def split_answer_panels(image: Image.Image) -> list[Image.Image]:
    """Split two-up / landscape answer sheets into left/right panels."""
    w, h = image.size
    if w < int(h * 1.15):
        return [image]
    mid = w // 2
    # Prefer a gutter cut slightly left of mid so the right column keeps its
    # question numbers (often sit just left of the geometric midpoint).
    cut = max(int(w * 0.48), mid - 40)
    return [image.crop((0, 0, cut, h)), image.crop((cut, 0, w, h))]


def _ascii_alpha_score(text: str) -> float:
    if not text.strip():
        return 0.0
    alpha = sum(1 for c in text if c.isalpha() and c.isascii())
    score = alpha / max(1, len(text))
    if SECTION_A_RE.search(text) or SECTION_B_RE.search(text):
        score += 1.5
    if SOLUTION_HDR_RE.search(text) or MARKS_HDR_RE.search(text):
        score += 1.5
    if FRONT_MATTER_RE.search(text):
        score += 0.8
    return score


def detect_document_rotation(doc: fitz.Document, scale: float, sample: int = 4) -> int:
    """Pick a page rotation (0/90/270) that makes the most pages readable."""
    votes = {0: 0.0, 90: 0.0, 270: 0.0}
    for index in range(min(sample, len(doc))):
        raw = render_page(doc[index], min(scale, 1.5))
        for rot in (0, 90, 270):
            img = raw if rot == 0 else raw.rotate(rot, expand=True)
            votes[rot] += _ascii_alpha_score(header_text(img))
    best = max(votes, key=votes.get)
    # Only rotate when a sideways orientation is clearly better.
    if best != 0 and votes[best] >= votes[0] + 0.8:
        return best
    return 0


def apply_rotation(image: Image.Image, rot: int) -> Image.Image:
    if rot == 0:
        return image
    return image.rotate(rot, expand=True)


def normalize_answer_orientation(image: Image.Image) -> Image.Image:
    """Rotate sideways booklet scans (e.g. 2018) so English text is upright."""
    rot = 0
    candidates = (
        (0, image),
        (270, image.rotate(270, expand=True)),
        (90, image.rotate(90, expand=True)),
    )
    scored: list[tuple[float, int, Image.Image]] = []
    for r, cand in candidates:
        scored.append((_ascii_alpha_score(header_text(cand)), r, cand))
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_rot, best_img = scored[0]
    if best_rot != 0 and best_score >= scored[-1][0] + 0.35:
        return best_img
    if best_score >= 0.15:
        return best_img
    return image


def page_has_pdf_question_labels(page: fitz.Page, max_questions: int = 12) -> bool:
    """True when embedded PDF text has Paper 1B-style 'N.' / 'N(' labels."""
    return bool(
        find_main_question_ys_pdf(page, max_questions=max_questions, scale=1.0)
    )


def is_paper_2_pdf_text(page: fitz.Page) -> bool:
    """Detect Paper 2 from embedded text footers like '2020-DSE-PHY 2'."""
    text = page.get_text("text")[:800]
    if not text.strip():
        return False
    if re.search(r"DSE-PHY\s*1B\b|Paper\s*1\s*Section\s*B", text, re.I):
        return False
    return bool(re.search(r"DSE-PHY\s*2\b|Paper\s*2\b", text, re.I))


def find_main_question_ys_pdf(
    page: fitz.Page,
    *,
    max_questions: int,
    scale: float,
    x0_frac: float = 0.0,
    x1_frac: float = 1.0,
) -> list[tuple[int, int]]:
    """Question labels from embedded PDF text (when the marking scheme is text)."""
    page_text = page.get_text("text")
    # Ignore general marking-instruction pages (numbered rules, not LQ solutions).
    if FRONT_MATTER_RE.search(page_text[:1200]) and not SOLUTION_HDR_RE.search(page_text[:1200]):
        if not re.search(r"(?m)^\s*\d{1,2}\.\s*\(", page_text):
            return []
    rect = page.rect
    x0 = rect.x0 + rect.width * x0_frac
    x1 = rect.x0 + rect.width * x1_frac
    hits: dict[int, int] = {}
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                # Require "N." / "N(" - bare digits are too often marks or maths.
                match = MAIN_Q_RE.match(text) or re.match(r"^(\d{1,2})\($", text)
                if not match:
                    continue
                qn = int(match.group(1))
                if not (1 <= qn <= max_questions):
                    continue
                # Instruction lists use "2. 'A' Marks"; solutions use "2. (a)".
                if not re.search(
                    rf"(?m)^\s*{qn}\s*[.(]\s*\(|{qn}\s*\.\s*\(",
                    page_text,
                ):
                    continue
                bx0, by0, _bx1, _by1 = span["bbox"]
                # Labels sit in the far-left of each column.
                if bx0 < x0 or bx0 > x0 + (x1 - x0) * 0.35:
                    continue
                y_px = int(by0 * scale)
                prev = hits.get(qn)
                if prev is None or y_px < prev:
                    hits[qn] = y_px
    return sorted(hits.items(), key=lambda t: t[1])


def _label_hits_from_strip(
    strip: Image.Image, h: int, max_questions: int
) -> list[tuple[int, int, float]]:
    hits: list[tuple[int, int, float]] = []
    for row in ocr_tsv(strip, psm="6") + ocr_tsv(strip, psm="11"):
        text = row["text"]
        if SUBPART_ONLY_RE.match(text):
            continue
        # Prefer "1." / "1(" tokens; bare digits rejected.
        match = MAIN_Q_RE.match(text) or re.match(r"^(\d{1,2})\(", text)
        if not match or re.fullmatch(r"\d{1,2}", text):
            continue
        qn = int(match.group(1))
        if not (1 <= qn <= max_questions):
            continue
        if row["left"] > strip.width * 0.55:
            continue
        hits.append((qn, row["top"], row["conf"]))

    lines = ocr_text(strip).splitlines()
    # Require a subpart paren after "N." so "3. In a question..." (instructions) is ignored.
    line_re = re.compile(
        r"^\s*(\d{1,2})\s*[.)@]\s*\("
        r"|^(\d{1,2})\("
    )
    known = {qn for qn, _, _ in hits}
    for i, line in enumerate(lines):
        raw = line.strip()
        m = line_re.match(raw)
        qn: int | None = None
        conf = 20.0
        if m:
            qn = int(next(g for g in m.groups() if g))
        elif re.match(r"^[Ss5]{1,2}\s*[.)@]\s*\(", raw) and 5 not in known:
            qn = 5
            conf = 15.0
        if qn is None or not (1 <= qn <= max_questions) or qn in known:
            continue
        y = int((i / max(1, len(lines))) * h)
        hits.append((qn, y, conf))
        known.add(qn)
    return hits


def find_main_question_ys(
    image: Image.Image, max_questions: int
) -> list[tuple[int, int]]:
    """Return (question_number, y_px) for main LQ starts on one page."""
    # Huge scan pages OCR poorly at native resolution - work on a downscale.
    scale_back = 1.0
    work = image
    max_dim = max(image.size)
    if max_dim > 2200:
        scale_back = max_dim / 1800.0
        work = image.resize(
            (max(1, int(image.width / scale_back)), max(1, int(image.height / scale_back)))
        )

    w, h = work.size
    hits: list[tuple[int, int, float]] = []
    # Narrow strip: clean "1. (a)" labels. Wider strip: when labels sit farther in.
    for frac, cap in ((0.16, 140), (0.28, 320)):
        strip_w = max(90, min(cap, int(w * frac)))
        strip = work.crop((0, 0, strip_w, h))
        hits.extend(_label_hits_from_strip(strip, h, max_questions))

    best: dict[int, tuple[int, float]] = {}
    for qn, y, conf in hits:
        prev = best.get(qn)
        if prev is None or y < prev[0] - 8 or (abs(y - prev[0]) <= 8 and conf > prev[1]):
            best[qn] = (y, conf)
    return sorted(
        ((qn, int(y * scale_back)) for qn, (y, _c) in best.items()),
        key=lambda t: t[1],
    )


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
    """First sighting of each Qn, kept in ascending Q order with monotonic page position.

    Ignores out-of-order false positives (e.g. a stray "10." before real Q2).
    """
    ordered = sorted(
        (c for c in candidates if 1 <= c[0] <= max_questions),
        key=lambda t: (t[1], t[2], t[0]),
    )
    first: dict[int, tuple[int, int, int]] = {}
    for qn, page_i, y in ordered:
        if qn not in first:
            first[qn] = (qn, page_i, y)

    starts: list[tuple[int, int, int]] = []
    last_pos = (-1, -1)
    for qn in range(1, max_questions + 1):
        item = first.get(qn)
        if not item:
            continue
        pos = (item[1], item[2])
        if pos < last_pos:
            # This Q number appears earlier in the PDF than a lower Q - false hit.
            continue
        starts.append(item)
        last_pos = pos
    return starts


def ensure_leading_q1(
    starts: list[tuple[int, int, int]], pages: list[Image.Image]
) -> list[tuple[int, int, int]]:
    """If Section B begins without a detected '1.', treat page 0 as Q1."""
    if not pages:
        return starts
    if any(qn == 1 for qn, _, _ in starts):
        return starts
    header_y = int(pages[0].height * 0.10)
    q1 = (1, 0, header_y)
    if not starts:
        return [q1]
    first_q, first_page, first_y = starts[0]
    # Prepend Q1 whenever the first found label is later in the document.
    if first_page > 0 or first_y > header_y + 40:
        print(f"  infer missing Q1 at page_idx=0 y={header_y} (before Q{first_q})")
        return [q1, *starts]
    return starts


def fill_single_gaps(
    starts: list[tuple[int, int, int]], pages: list[Image.Image]
) -> list[tuple[int, int, int]]:
    """Insert a missing Qn when neighbors imply it owns an intervening page.

    Example: Q1 on page 0, Q3 on page 2, no Q2 -> place Q2 at top of page 1.
    """
    if len(starts) < 2 or not pages:
        return starts
    by_q = {qn: (qn, p, y) for qn, p, y in starts}
    extras: list[tuple[int, int, int]] = []
    ordered_q = sorted(by_q)
    for a, b in zip(ordered_q, ordered_q[1:]):
        if b != a + 2:
            continue
        mid = a + 1
        if mid in by_q:
            continue
        pa, ya = by_q[a][1], by_q[a][2]
        pb, yb = by_q[b][1], by_q[b][2]
        if pb <= pa:
            continue
        if pb == pa + 1:
            mid_page = pa
            mid_y = min(pages[pa].height - 40, ya + max(80, int(pages[pa].height * 0.35)))
        elif pb == pa + 2:
            # One intervening panel/page: missing Q usually starts at its top.
            mid_page = pa + 1
            mid_y = int(pages[mid_page].height * 0.08)
        else:
            mid_page = pb - 1
            mid_y = int(pages[mid_page].height * 0.08)
        extras.append((mid, mid_page, mid_y))
        print(f"  infer missing Q{mid} at page_idx={mid_page} y={mid_y}")
    if not extras:
        return starts
    merged = {qn: item for qn, item in by_q.items()}
    for item in extras:
        merged[item[0]] = item
    return sorted(merged.values(), key=lambda t: (t[1], t[2], t[0]))


def strip_answer_chrome(image: Image.Image) -> Image.Image:
    """Remove Solution/Marks/Remarks headers and page-number footers."""
    w, h = image.size
    top = 0
    bottom = int(h * 0.965)
    head = image.crop((0, 0, w, max(24, int(h * 0.12))))
    head_text = ocr_text(head).lower()
    # Only strip when the chrome words are present AND no main "N." label yet.
    has_chrome = bool(re.search(r"solution|marks|remarks|paper\s*1\s*section", head_text))
    has_q_label = bool(re.search(r"(?m)^\s*\d{1,2}\s*[.(]", head_text))
    if has_chrome and not has_q_label:
        top = int(h * 0.08)
        arr = np.asarray(head.convert("L"))
        row_dark = (arr < 80).mean(axis=1)
        rule_rows = np.where(row_dark > 0.35)[0]
        if len(rule_rows):
            top = max(top, int(rule_rows[-1] + 4))
    if bottom <= top + 30:
        return image
    return trim_whitespace(image.crop((0, top, w, bottom)))


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
    # Parallel list: (pdf_page_index, x0_frac, x1_frac) for native-text Q finding.
    panel_meta: list[tuple[int, float, float]] = []
    section_b_started = False
    seen_section_a = False
    doc_rot = detect_document_rotation(doc, scale)
    if doc_rot:
        print(f"  using page rotation {doc_rot}°")
    try:
        for index in range(len(doc)):
            if section_b_started and is_paper_2_pdf_text(doc[index]):
                print(f"  stop at Paper 2 page {index + 1}/{len(doc)} (pdf text)")
                break
            image = apply_rotation(render_page(doc[index], scale), doc_rot)
            if doc_rot == 0:
                image = normalize_answer_orientation(image)
            if is_paper_2_page(image) and section_b_started:
                print(f"  stop at Paper 2 page {index + 1}/{len(doc)}")
                break

            panels = split_answer_panels(image)
            n_panels = len(panels)
            pdf_labels = page_has_pdf_question_labels(doc[index], max_questions)
            for panel_i, panel in enumerate(panels):
                x0_frac = panel_i / n_panels
                x1_frac = (panel_i + 1) / n_panels
                label = (
                    f"page {index + 1}/{len(doc)}"
                    if n_panels == 1
                    else f"page {index + 1}/{len(doc)} {'LR'[panel_i]}"
                )
                if not section_b_started:
                    if is_section_a_keys(panel):
                        seen_section_a = True
                        print(f"  skip Section A {label}")
                        continue
                    if is_front_matter(panel) and not (
                        pdf_labels and re.search(r"(?m)^\s*\d{1,2}\.\s*\(", doc[index].get_text("text")[:2000] or "")
                    ):
                        print(f"  skip front-matter {label}")
                        continue
                    if looks_like_section_b(panel) or pdf_labels:
                        section_b_started = True
                    elif seen_section_a:
                        section_b_started = True
                    else:
                        print(f"  skip unmarked {label}")
                        continue
                else:
                    if is_paper_2_page(panel):
                        print(f"  stop at Paper 2 {label}")
                        break
                    if is_section_a_keys(panel):
                        print(f"  stop at late Section A {label}")
                        break
                pages.append(panel)
                panel_meta.append((index, x0_frac, x1_frac))
                print(f"  include answer {label} ({panel.width}x{panel.height})")
            else:
                continue
            break
    finally:
        # Keep doc open until after native-text label pass.
        pass

    # Fallback: scanned PDFs with weak OCR may never trip Section A/B headers.
    if not pages:
        print("  fallback: include all pages (Section B headers not OCR'd)")
        try:
            for index in range(len(doc)):
                image = normalize_answer_orientation(render_page(doc[index], scale))
                if is_paper_2_page(image):
                    print(f"  stop at Paper 2 page {index + 1}/{len(doc)}")
                    break
                for panel_i, panel in enumerate(split_answer_panels(image)):
                    n_panels = 2 if image.width >= int(image.height * 1.15) else 1
                    pages.append(panel)
                    panel_meta.append((index, panel_i / n_panels, (panel_i + 1) / n_panels))
                    print(
                        f"  include answer page {index + 1}/{len(doc)} "
                        f"({panel.width}x{panel.height})"
                    )
        finally:
            pass

    if not pages:
        doc.close()
        print(f"  WARNING: no Section B pages found in {source.name}")
        return 0

    candidates: list[tuple[int, int, int]] = []
    for page_index, image in enumerate(pages):
        pdf_i, x0_frac, x1_frac = panel_meta[page_index]
        pdf_hits: list[tuple[int, int]] = []
        if doc_rot == 0:
            pdf_hits = find_main_question_ys_pdf(
                doc[pdf_i],
                max_questions=max_questions,
                scale=scale,
                x0_frac=x0_frac,
                x1_frac=x1_frac,
            )
        ocr_hits = find_main_question_ys(image, max_questions)
        # Prefer embedded text when present; fall back to OCR.
        merged: dict[int, int] = {qn: y for qn, y in ocr_hits}
        for qn, y in pdf_hits:
            merged[qn] = y
        for qn, y in merged.items():
            candidates.append((qn, page_index, int(y)))
    doc.close()

    starts = longest_question_chain(candidates, max_questions)
    starts = ensure_leading_q1(starts, pages)
    starts = fill_single_gaps(starts, pages)
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
            h = image.height
            header_skip = int(h * 0.09)
            if page_index == start_page:
                # Keep a small pad above the "N." label; do not force header_skip
                # when the question starts near the top (that was cutting Q2).
                top = max(0, start_y - 12)
            else:
                top = header_skip
            if page_index == end_page and _nq is not None:
                bottom = max(top + 20, int(end_y) - 4)
            else:
                bottom = int(h * 0.965)
            bottom = min(bottom, image.height - 4)
            if bottom <= top + 20:
                continue
            parts.append(image.crop((0, top, image.width, bottom)))
        if not parts:
            continue
        combined = strip_answer_chrome(trim_whitespace(stitch_vertical(parts)))
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
