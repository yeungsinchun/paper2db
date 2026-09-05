#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split HKDSE Physics Paper 1B (LQ) into upright full pages (+ optional crops).

Default: export exam pages and starts.json (no within-page question crops).
Answer crops stay in preprocess_lq_answers.py.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import subprocess
from pathlib import Path

import pymupdf as fitz
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from png_pdf import combine_pngs_to_pdf

# Hi-res Paper 1B crops can exceed Pillow's default ~89 MP limit.
Image.MAX_IMAGE_PIXELS = 250_000_000

QNUM_RE = re.compile(r"^(\d{1,2})\s*[.??)]?\s*$")
# Figure labels like "7.1" / "7.2" are not question numbers.
FIG_LABEL_RE = re.compile(r"^\d{1,2}\.\d")
SUBPART_RE = re.compile(r"^\([a-z]\)$", re.I)


def _normalize_qnum_text(text: str) -> str:
    return text.strip().lstrip("\"'*#").strip()


def match_question_number(text: str) -> re.Match[str] | None:
    """Return a question-number match, ignoring figure sub-labels (e.g. 7.1)."""
    text = _normalize_qnum_text(text)
    if not text or FIG_LABEL_RE.match(text):
        return None
    return (
        QNUM_RE.fullmatch(text)
        or re.fullmatch(r"(\d{1,2})[.,)]", text)
        or re.match(r"^(\d{1,2})[.,)](?!\d)", text)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--cover-pages", type=int, default=1)
    parser.add_argument(
        "--scale",
        type=float,
        default=0.0,
        help="Render scale (PDF pts → px). 0 = auto native DPI, capped.",
    )
    parser.add_argument(
        "--max-scale",
        type=float,
        default=9.0,
        help="Cap for auto native scale (memory / file size). ~8.3 ≈ 600 dpi.",
    )
    parser.add_argument("--max-questions", type=int, default=12)
    return parser.parse_args()


def native_scale(page: fitz.Page, cap: float = 9.0) -> float:
    """Match embedded scan DPI using each image's *placed* bbox (handles rotation).

    Old logic used page.rect vs raw pixel size and systematically undersampled
    landscape scans (e.g. 2016 @300 dpi rendered ~2.95× instead of ~4.17×).
    Papers like 2022 also place a soft full-page backdrop under sharper overlays —
    we take the highest DPI among meaningful images so overlays stay crisp.
    """
    infos = page.get_image_info(xrefs=True)
    if not infos:
        return min(3.5, cap)

    page_area = max(abs(page.rect.width * page.rect.height), 1.0)
    scales: list[float] = []
    for info in infos:
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        if w < 80 or h < 80:
            continue
        bw = abs(info["bbox"][2] - info["bbox"][0])
        bh = abs(info["bbox"][3] - info["bbox"][1])
        if bw < 5 or bh < 5:
            continue
        cover = (bw * bh) / page_area
        sx = w / bw
        sy = h / bh
        # Rotated placements can make sx/sy disagree; max avoids undersampling
        # (2019 content-stream rotation: min≈2.95, max≈5.89 = true native).
        scale = max(sx, sy)
        # Ignore tiny icons; keep figures and full-page rasters.
        if cover >= 0.02 or w * h >= 150_000:
            scales.append(scale)

    if not scales:
        return min(3.5, cap)
    # Prefer the sharpest layer present (backdrop may be softer than overlays).
    scale = max(scales)
    return float(max(2.5, min(scale, cap)))


def _page_image_layers(page: fitz.Page) -> list[dict]:
    page_area = max(abs(page.rect.width * page.rect.height), 1.0)
    layers = []
    for info in page.get_image_info(xrefs=True):
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        if w < 40 or h < 40:
            continue
        bw = abs(info["bbox"][2] - info["bbox"][0])
        bh = abs(info["bbox"][3] - info["bbox"][1])
        if bw < 5 or bh < 5:
            continue
        cover = (bw * bh) / page_area
        scale = max(w / bw, h / bh)
        layers.append({**info, "cover": cover, "scale": scale, "px": w * h})
    return layers


def apply_page_rotation(image: Image.Image, rot: int) -> Image.Image:
    """Rotate embedded scan to display orientation without resampling when possible."""
    rot = rot % 360
    if rot == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if rot == 270:
        return image.transpose(Image.Transpose.ROTATE_90)
    if rot == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    return image


def is_two_page_spread(image: Image.Image) -> bool:
    """Detect side-by-side two-page scans (landscape, much wider than tall).

    Portrait A4 at high DPI (e.g. 3508x4961) must NOT match - that used to
    bisect every page and invent fake question numbers.
    """
    w, h = image.size
    if w <= h:
        return False
    return w >= 3400 and h >= 2400 and (w / h) >= 1.25


def split_spread(image: Image.Image) -> list[Image.Image]:
    """Split a side-by-side spread into left and right exam pages."""
    if not is_two_page_spread(image):
        return [image]
    mid = image.width // 2
    return [
        image.crop((0, 0, mid, image.height)),
        image.crop((mid, 0, image.width, image.height)),
    ]


def expand_pdf_pages(
    doc: fitz.Document, scale: float, *, cover_pages: int = 0
) -> list[Image.Image]:
    """Render each PDF page at native scan resolution, split spreads, then scale."""
    pages: list[Image.Image] = []
    for page_index in range(cover_pages, len(doc)):
        page = doc[page_index]
        native_s = native_scale(page, cap=12.0)
        image = render_display(page, native_s, doc=doc)
        for part in split_spread(image):
            if scale > 0 and abs(scale - native_s) / native_s > 0.02:
                ratio = scale / native_s
                part = part.resize(
                    (max(1, int(part.width * ratio)), max(1, int(part.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            pages.append(part)
    return pages


def save_page_pngs(pages: list[Image.Image], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(pages):
        image.save(output_dir / f"page{index:03d}.png", format="PNG", optimize=False)


def load_page_pngs(pages_dir: Path) -> list[Image.Image]:
    """Load page000.png, page001.png, ... from a preprocessed pages folder."""
    paths = sorted(pages_dir.glob("page*.png"))
    if not paths:
        return []
    pages: list[Image.Image] = []
    for path in paths:
        image = Image.open(path)
        image.load()
        pages.append(image.convert("RGB"))
    return pages


def downscale_pages(pages: list[Image.Image], ratio: float) -> list[Image.Image]:
    out: list[Image.Image] = []
    for image in pages:
        out.append(
            image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        )
    return out


def export_pdf_pages(
    source: Path,
    pages_dir: Path,
    *,
    cover_pages: int = 1,
    scale: float = 0.0,
    max_scale: float = 9.0,
) -> int:
    """Render every exam page to pageNNN.png (step 1 of the LQ pipeline)."""
    doc = fitz.open(source)
    try:
        if doc.needs_pass:
            doc.authenticate("")
        if scale <= 0:
            probe = doc[min(cover_pages, len(doc) - 1)]
            scale = native_scale(probe, cap=max_scale)
        print(f"  page export scale={scale:.2f}")
        pages = expand_pdf_pages(doc, scale, cover_pages=cover_pages)
        save_page_pngs(pages, pages_dir)
        print(f"  saved {len(pages)} page PNGs -> {pages_dir}")
        return len(pages)
    finally:
        doc.close()


def _placement_rotation_deg(transform: tuple | list) -> int:
    """Snap the CTM image-x axis angle to 0/90/180/270."""
    a, b = float(transform[0]), float(transform[1])
    ang = math.degrees(math.atan2(b, a)) % 360.0
    return int(min((0, 90, 180, 270), key=lambda c: min(abs(ang - c), abs(ang - c - 360))))


def extract_fullpage_display(page: fitz.Page, doc: fitz.Document) -> Image.Image | None:
    """Copy the embedded full-page raster (no MuPDF resample) when safe.

    Photocopy JPEGs stay at native pixels; 1-bit CCITT stays sharp. Skip when the
    page composites a soft backdrop with sharper overlays (e.g. 2022), or when the
    placement CTM rotates/clips a two-page scan (2019/2020/2023) - get_pixmap
    handles that correctly and extract alone would stay sideways.
    """
    layers = _page_image_layers(page)
    if not layers:
        return None
    full = [L for L in layers if L["cover"] >= 0.5]
    if len(full) != 1:
        return None
    base = full[0]
    # Content-stream rotation (page.rotation may still be 0). 90/270 usually means
    # a landscape booklet scan clipped per PDF page - do not extract raw pixels.
    trot = _placement_rotation_deg(base.get("transform", (1, 0, 0, 1, 0, 0)))
    if trot in (90, 270):
        return None
    sharper = [
        L
        for L in layers
        if L is not base and L["px"] >= 80_000 and L["scale"] > base["scale"] * 1.15
    ]
    if sharper:
        return None
    xref = base.get("xref")
    if not xref:
        return None
    try:
        raw = doc.extract_image(xref)
    except Exception:
        return None
    image = Image.open(io.BytesIO(raw["image"]))
    image.load()
    rot = page.rotation % 360
    if rot:
        image = apply_page_rotation(image, rot)
    # Placement 180° with page.rotation 0 (e.g. 2017) leaves the raster inverted.
    if trot == 180:
        image = image.transpose(Image.Transpose.ROTATE_180)
    # Embedded rasters are sometimes stored rotated relative to the page
    # rect (page.rotation still 0). Align aspect ratio with the page.
    pw = max(abs(page.rect.width), 1.0)
    ph = max(abs(page.rect.height), 1.0)
    if (pw > ph) != (image.width > image.height):
        target = pw / ph

        def _aspect_score(im: Image.Image) -> float:
            return abs((im.width / max(im.height, 1)) - target)

        cand90 = image.transpose(Image.Transpose.ROTATE_90)
        cand270 = image.transpose(Image.Transpose.ROTATE_270)
        image = min((cand90, cand270), key=_aspect_score)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")
    return image


def render_display(
    page: fitz.Page, scale: float, doc: fitz.Document | None = None
) -> Image.Image:
    """Render the page as shown on screen (rotation applied)."""
    if doc is not None:
        native = native_scale(page, cap=12.0)
        # Full-page extract only at native (or denser) scale — keeps JPEG/CCITT pixels.
        if scale >= native * 0.90:
            extracted = extract_fullpage_display(page, doc)
            if extracted is not None:
                return extracted
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
        annots=False,
    )
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    del pixmap
    return image


def doc_has_jpeg_scans(doc: fitz.Document, sample_pages: int = 4) -> bool:
    """True if pages embed JPEG photocopies (must not bilevel-threshold)."""
    n = min(len(doc), max(1, sample_pages))
    for i in range(n):
        for xref, *_rest in doc[i].get_images(full=True)[:3]:
            try:
                raw = doc.extract_image(xref)
            except Exception:
                continue
            if raw.get("ext", "").lower() in {"jpeg", "jpg"}:
                return True
    return False


def crisp_scan(image: Image.Image, enabled: bool = True) -> Image.Image:
    """Snap *true* 1-bit scans back to pure B/W after MuPDF softens them.

    Disabled for JPEG photocopies — hard thresholding makes soft ink look like
    scratches. Only threshold when the raster is already essentially binary.
    """
    if not enabled:
        return image
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    # Soft antialias / JPEG fringes live in the mid band; abort if present.
    if float(((gray > 40) & (gray < 220)).mean()) > 0.01:
        return image
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    hist /= hist.sum()
    if hist[60:200].sum() > 0.008:
        return image
    bw = np.where(gray < 180, 0, 255).astype(np.uint8)
    return Image.fromarray(bw).convert("RGB")

def ocr_tsv(image: Image.Image) -> list[dict]:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    lines = result.stdout.decode("utf-8", errors="ignore").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        try:
            if int(row.get("level", "0")) != 5:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "text": text,
                    "left": float(row["left"]),
                    "top": float(row["top"]),
                    "conf": float(row["conf"]),
                }
            )
        except (KeyError, ValueError):
            continue
    return rows


def find_starts_on_image(image: Image.Image, max_questions: int) -> list[tuple[int, float]]:
    """Return (question_number, y_px) on one displayed page image."""
    w, h = image.size
    top0 = int(h * 0.035)
    hits: list[tuple[int, float, float]] = []

    # Two strip widths: narrow avoids body text; wide keeps "1. Stem..." intact.
    strips: list[Image.Image] = []
    for left_f, right_f in ((0.035, 0.20), (0.035, 0.32)):
        strip = image.crop((int(w * left_f), top0, int(w * right_f), int(h * 0.96)))
        strips.append(strip.resize((strip.width * 2, strip.height * 2)))

    for strip in strips:
        for row in ocr_tsv(strip):
            if row["conf"] < 15:
                continue
            text = _normalize_qnum_text(row["text"])
            match = match_question_number(text)
            if not match:
                starred = re.fullmatch(r"[*#]?(\d{1,2})\s*[.)]?", text)
                if starred:
                    match = starred
                else:
                    continue
            qn = int(match.group(1))
            if not (1 <= qn <= max_questions):
                continue
            if row["left"] > strip.width * 0.55:
                continue
            y = top0 + row["top"] / 2.0
            hits.append((qn, y, row["conf"]))

        buf = io.BytesIO()
        strip.save(buf, format="PNG")
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "6"],
            input=buf.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        lines = result.stdout.decode("utf-8", errors="ignore").splitlines()
        # After N./N,/N) require EOL or a real stem char - not dotted-line noise.
        line_q_re = re.compile(
            r"^[|\[\]Il\"'*#\s]*[*#]?\s*(\d{1,2})\s*[.,)](?:\s*$|\s+[^\s.·•])"
        )
        fig_re = re.compile(r"Figure\s+(\d{1,2})\s*\.\s*1\b", re.I)
        tsv_qns = {qn for qn, _, _ in hits}
        for i, line in enumerate(lines):
            stripped = line.strip()
            m = line_q_re.match(stripped)
            if m:
                qn = int(m.group(1))
                if 1 <= qn <= max_questions and qn not in tsv_qns:
                    y = top0 + (i / max(1, len(lines))) * (h * 0.92)
                    hits.append((qn, y, 25.0))
                    tsv_qns.add(qn)
                continue
            fm = fig_re.search(stripped)
            if fm:
                qn = int(fm.group(1))
                if 1 <= qn <= max_questions and qn not in tsv_qns:
                    # Question number sits just above "Figure N.1".
                    y = max(top0 + 20, top0 + (i / max(1, len(lines))) * (h * 0.92) - 40)
                    hits.append((qn, y, 18.0))
                    tsv_qns.add(qn)

    best: dict[int, tuple[float, float]] = {}
    for qn, y, conf in hits:
        prev = best.get(qn)
        if prev is None or y < prev[0] or (y == prev[0] and conf > prev[1]):
            best[qn] = (y, conf)
    return sorted(((qn, best[qn][0]) for qn in best), key=lambda t: t[1])


def find_question_starts(
    pages: list[Image.Image], cover_pages: int, max_questions: int
) -> list[tuple[int, int, float]]:
    """Global (qn, page_index, y_px) sequence starting at 1.

    Only accept the next expected number in reading order so OCR false hits
    (option digits, figure labels) cannot invent Q7 mid-paper.
    """
    by_page: dict[int, list[tuple[int, float]]] = {}
    for page_index in range(cover_pages, len(pages)):
        by_page[page_index] = find_starts_on_image(pages[page_index], max_questions)

    ordered: list[tuple[int, int, float]] = []
    expected = 1
    last_key = (-1, -1.0)
    for page_index in range(cover_pages, len(pages)):
        hits = sorted(by_page.get(page_index, []), key=lambda item: item[1])
        for qn, y in hits:
            if qn != expected:
                continue
            key = (page_index, y)
            if key <= last_key:
                continue
            ordered.append((qn, page_index, y))
            expected = qn + 1
            last_key = key
            if expected > max_questions:
                return ordered
    return ordered


def wipe_answer_lines(image: Image.Image) -> Image.Image:
    """Erase dotted answer lines without wiping body text.

    At hi-res, letter strokes look like many short dark runs and used to match the
    old dash heuristic — wiping deleted real stems. Require near-periodic gaps
    (true dotted lines) and keep dilation small.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    h, w, _ = arr.shape
    s = max(1.0, w / 1100.0)
    max_dash = max(3, int(round(3.5 * s)))
    min_long = max(12, int(round(14 * s)))
    min_dashes = max(12, int(round(8 * s)))  # do NOT scale as hard as width
    gray = arr.mean(axis=2)
    x0 = int(w * 0.08)
    x1 = int(w * 0.92)
    dark = gray[:, x0:x1] < 185
    wipe = np.zeros(h, dtype=bool)
    for y in range(h):
        row = dark[y]
        padded = np.concatenate(([False], row, [False]))
        changes = np.diff(padded.astype(np.int8))
        starts = np.where(changes == 1)[0]
        ends = np.where(changes == -1)[0]
        if len(starts) < min_dashes:
            continue
        lengths = ends - starts
        short = lengths <= max_dash
        if short.sum() < min_dashes or (lengths >= min_long).sum() > 0:
            continue
        density = float(row.mean())
        if not (0.015 <= density <= 0.12):
            continue
        # True dotted lines: gaps between dashes are similar. Text is irregular.
        gaps = starts[1:] - ends[:-1]
        if len(gaps) < min_dashes - 1:
            continue
        # Keep only short positive gaps (skip huge word-spacing holes).
        gaps = gaps[(gaps > 0) & (gaps <= max_dash * 6)]
        if len(gaps) < min_dashes - 1:
            continue
        gmean = float(gaps.mean())
        if gmean < 1:
            continue
        if float(gaps.std()) / gmean > 0.55:
            continue
        # Dash length should be comparable to / smaller than gap (dots, not letters).
        if float(lengths[short].mean()) > gmean * 1.35:
            continue
        wipe[y] = True
    dil = wipe.copy()
    pad = 1 if s < 2.5 else max(1, int(round(s * 0.35)))
    for y in np.where(wipe)[0]:
        dil[max(0, y - pad) : min(h, y + pad + 1)] = True
    out = arr.copy()
    out[dil, x0:x1] = 255
    return Image.fromarray(out)


def bleach_margins(image: Image.Image) -> Image.Image:
    img = image.copy()
    w, h = img.size
    left_w = max(10, int(w * 0.045))
    right_w = max(10, int(w * 0.045))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, left_w, h], fill=(255, 255, 255))
    draw.rectangle([w - right_w, 0, w, h], fill=(255, 255, 255))
    return img


def trim_whitespace(image: Image.Image, pad: int = 12, left_pad: int = 20) -> Image.Image:
    gray = ImageOps.invert(image.convert("L"))
    bbox = gray.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    return image.crop(
        (
            max(0, left - left_pad),
            max(0, top - pad),
            min(image.width, right + pad),
            min(image.height, bottom + pad),
        )
    )


def stitch_vertical(parts: list[Image.Image]) -> Image.Image:
    if len(parts) == 1:
        return parts[0]
    width = max(p.width for p in parts)
    height = sum(p.height for p in parts)
    out = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for part in parts:
        out.paste(part, (0, y))
        y += part.height
    return out


def content_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Crop box excluding outer margins (pixel coords)."""
    w, h = image.size
    return (int(w * 0.035), int(h * 0.05), int(w * 0.95), int(h * 0.93))


def crop_questions(
    pages: list[Image.Image],
    starts: list[tuple[int, int, float]],
    *,
    clean_answer_lines: bool = True,
) -> dict[int, Image.Image]:
    results: dict[int, Image.Image] = {}
    if not starts:
        return results
    ends = starts[1:] + [(None, len(pages) - 1, pages[-1].size[1] * 0.93)]  # type: ignore

    for (qn, start_page, start_y), (next_qn, end_page, end_y) in zip(starts, ends):
        parts: list[Image.Image] = []
        for page_index in range(start_page, end_page + 1):
            img = pages[page_index]
            left, top_m, right, bottom_m = content_box(img)
            top = int(start_y - 8) if page_index == start_page else top_m
            if page_index == end_page and next_qn is not None:
                bottom = int(end_y - 10)
            else:
                bottom = bottom_m
            top = max(top_m, top)
            bottom = min(bottom_m, bottom)
            if bottom <= top + 30:
                continue
            part = img.crop((left, top, right, bottom))
            if clean_answer_lines:
                part = wipe_answer_lines(part)
                part = bleach_margins(part)
            g = np.asarray(part.convert("L"))
            if float((g < 225).mean()) < 0.008 and page_index != start_page:
                continue
            parts.append(part)
        if not parts:
            continue
        results[qn] = trim_whitespace(stitch_vertical(parts))
    return results


def process_one(
    source: Path,
    output_dir: Path,
    cover_pages: int,
    scale: float,
    max_questions: int,
    max_scale: float = 9.0,
    pages_dir: Path | None = None,
    *,
    crop_questions_flag: bool = False,
) -> int:
    """Export LQ exam pages (and optionally per-question crops).

    Default is pages-only: split upright full pages + starts.json. Question
    crops are off unless crop_questions_flag=True (answers stay separately
    cropped from the marking scheme). Pages-only mode leaves existing q*.png
    crops untouched.
    """
    doc = fitz.open(source)
    try:
        if doc.needs_pass:
            doc.authenticate("")
        if scale <= 0:
            probe = doc[min(cover_pages, len(doc) - 1)]
            scale = native_scale(probe, cap=max_scale)
        detect_scale = max(2.5, scale * 0.55)
        print(f"  render scale={scale:.2f} (detect {detect_scale:.2f})")
        allow_crisp = not doc_has_jpeg_scans(doc)
        if not allow_crisp:
            print("  jpeg scans detected — keeping greyscale (no bilevel snap)")

        pages_out = pages_dir if pages_dir is not None else (output_dir / "pages")
        full_pages: list[Image.Image] | None = load_page_pngs(pages_out)
        if full_pages:
            print(f"  loaded {len(full_pages)} page PNGs from {pages_out}")
        else:
            full_pages = expand_pdf_pages(doc, scale, cover_pages=cover_pages)
            for i, image in enumerate(full_pages):
                full_pages[i] = crisp_scan(image, enabled=allow_crisp)
            save_page_pngs(full_pages, pages_out)
            print(f"  saved {len(full_pages)} page PNGs -> {pages_out}")

        detect_ratio = min(1.0, detect_scale / max(scale, 0.01))
        if detect_ratio < 0.95:
            detect_pages = downscale_pages(full_pages, detect_ratio)
        else:
            detect_pages = full_pages
        starts = find_question_starts(detect_pages, cover_pages=0, max_questions=max_questions)
        if not starts:
            print(f"  WARNING: no questions detected in {source.name}")
            return 0
        if len(starts) > max_questions:
            starts = starts[:max_questions]
        print(f"  detected Q{starts[0][0]}-Q{starts[-1][0]} ({len(starts)} questions)")

        # Scale y to full-page coordinates for metadata / optional crops.
        starts_scaled: list[tuple[int, int, float]] = []
        for qn, page_i, y in starts:
            det_h = max(detect_pages[page_i].size[1], 1)
            ren_h = full_pages[page_i].size[1]
            starts_scaled.append((qn, page_i, y * (ren_h / det_h)))

        output_dir.mkdir(parents=True, exist_ok=True)
        questions_meta: list[dict] = []
        for i, (qn, page_i, y) in enumerate(starts_scaled):
            if i + 1 < len(starts_scaled):
                next_page = starts_scaled[i + 1][1]
                # Full pages for this Q only - stop before the next question's page.
                end_page = next_page - 1 if next_page > page_i else page_i
            else:
                end_page = len(full_pages) - 1
            page_from = page_i
            page_to = max(page_from, end_page)
            questions_meta.append(
                {
                    "q": qn,
                    "page_from": page_from,
                    "page_to": page_to,
                    "y": round(float(y), 1),
                }
            )
        meta_path = output_dir / "starts.json"
        meta_path.write_text(
            json.dumps({"questions": questions_meta, "pages": len(full_pages)}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"  wrote {meta_path.name} ({len(questions_meta)} questions)")

        # Review PDF = full exam pages (not question crops).
        combine_pngs_to_pdf(pages_out, output=output_dir / "combined.pdf", overwrite=True)

        if not crop_questions_flag:
            return len(questions_meta)

        images = crop_questions(
            full_pages,
            starts_scaled,
            clean_answer_lines=allow_crisp,
        )
        for old in output_dir.glob("q*.png"):
            old.unlink()
        for qn, image in sorted(images.items()):
            image = crisp_scan(image, enabled=allow_crisp)
            image.save(output_dir / f"q{qn}.png", format="PNG", optimize=False)
        return len(images)
    finally:
        doc.close()


def main() -> None:
    args = parse_args()
    n = process_one(
        args.source,
        args.output_dir,
        cover_pages=args.cover_pages,
        scale=args.scale,
        max_questions=args.max_questions,
        max_scale=args.max_scale,
    )
    print(f"Wrote {n} questions -> {args.output_dir}")


if __name__ == "__main__":
    main()
