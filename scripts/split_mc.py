#!/usr/bin/env python3
"""Split an MC paper into per-question PNG files.

With --source and a .meta.json sidecar from preprocess_mc.py, crops the original
PDF at anchor positions (no blue dots). Without --source, crops the anchored PDF
(legacy behaviour).
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from typing import Any

import pymupdf as fitz
import numpy as np
from PIL import Image

from png_pdf import combine_pngs_to_pdf

# Extra space above the anchor so the first line (e.g. the top of "m") is not clipped.
# Kept in sync with --pad-top (pts * scale): larger top pad than the previous
# question's bottom gap pulls the prior question's last lines into this crop.
TOP_PAD_PX = 24


def trim_question_image(
    image: Image.Image,
    *,
    pad: int = 10,
    left_pad: int = 24,
    preserve_left: bool = False,
    preserve_right: bool = False,
    preserve_top: bool = False,
    dark_threshold: int = 235,
    min_row_fraction: float = 0.015,
    min_col_fraction: float = 0.012,
) -> Image.Image:
    """Crop to ink bounds; ignores large blank gaps between marker splits.

    When splitting from page meta, pass preserve_left+preserve_right so every
    question on a page keeps the same page crop width (short stems must not
    shrink horizontally and look oversized). Pass preserve_top so a sparse
    question number in the left rail is not trimmed away before denser diagram
    ink (e.g. PP Q6).

    Row ink ignores the far-right edge (scan binding bars) so last-on-page
    questions are not kept tall by thin vertical artifacts.
    """
    arr = np.asarray(image.convert("L"))
    h, w = arr.shape
    if h < 4 or w < 4:
        return image

    # Ignore right-edge scan noise when judging row content / bottom trim.
    row_limit = max(1, int(w * 0.97))

    def row_content(y: int) -> bool:
        return float((arr[y, :row_limit] < dark_threshold).mean()) >= min_row_fraction

    def col_content(x: int) -> bool:
        return float((arr[:, x] < dark_threshold).mean()) >= min_col_fraction

    content_rows = [y for y in range(h) if row_content(y)]
    content_cols = [x for x in range(w) if col_content(x)]
    if not content_rows or not content_cols:
        return image

    # Page-turn arrows sit in the bottom-right only ("go to next page").
    # Prefer body ink (left 85%) so those arrows do not keep a tall footer.
    body_limit = max(1, int(w * 0.85))

    def body_row(y: int) -> bool:
        return float((arr[y, :body_limit] < dark_threshold).mean()) >= min_row_fraction

    body_rows = [y for y in content_rows if body_row(y)]
    bottom_rows = body_rows if body_rows else content_rows

    # Drop tiny trailing islands after a tall whitespace gap (page-turn
    # arrows, scan bars at the page foot).
    if len(bottom_rows) >= 2:
        islands: list[tuple[int, int]] = []
        start = bottom_rows[0]
        prev = bottom_rows[0]
        for y in bottom_rows[1:]:
            if y - prev > 40:
                islands.append((start, prev))
                start = y
            prev = y
        islands.append((start, prev))
        while len(islands) > 1:
            s, e = islands[-1]
            if (e - s) < 55 and s > int(h * 0.65):
                islands.pop()
                continue
            break
        bottom_rows = [y for y in bottom_rows if y <= islands[-1][1]]

    if preserve_top:
        top = 0
    else:
        top = max(0, content_rows[0] - pad)
    bottom = min(h, bottom_rows[-1] + 1 + pad)
    if preserve_left:
        left = 0
    else:
        left = max(0, content_cols[0] - left_pad)
    if preserve_right:
        right = w
    else:
        right = min(w, content_cols[-1] + 1 + pad)
    if bottom <= top or right <= left:
        return image
    return image.crop((left, top, right, bottom))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("anchored", type=Path, help="Anchored PDF from preprocess_mc.py")
    parser.add_argument("output_dir", type=Path, help="Directory for q1.png, q2.png, ...")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Original PDF to render (uses .meta.json sidecar; no blue dots)",
    )
    parser.add_argument(
        "--meta",
        type=Path,
        default=None,
        help="Sidecar JSON from preprocess_mc (default: <pdf>.meta.json)",
    )
    parser.add_argument("--questions", type=int, default=36)
    parser.add_argument("--scale", type=float, default=2.0, help="Render scale for PNG output")
    parser.add_argument("--gutter", type=float, default=14.0, help="Left margin (PDF pts) to drop markers")
    parser.add_argument(
        "--left-margin",
        type=float,
        default=0.0,
        help="Extra whitespace (PDF pts) kept left of the anchor (default: 0, crop at the anchor)",
    )
    parser.add_argument(
        "--pad-top",
        type=float,
        default=12.0,
        help="Padding (PDF pts) between this question's bottom and the next anchor",
    )
    parser.add_argument("--pad-bottom", type=float, default=6.0, help="Padding below each question end")
    return parser.parse_args()


def _is_marker_blue(rgb: tuple[int, int, int]) -> bool:
    red, green, blue = rgb
    return blue > 150 and blue > red + 40 and blue > green + 30


def find_marker_ys(image: Image.Image, max_x: int = 80) -> list[float]:
    """Return marker centre y positions in image pixel coordinates, top to bottom."""
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
            if 8 <= width <= 24 and 8 <= height <= 24 and abs(width - height) <= 5 and len(points) >= 40:
                density = len(points) / float(width * height)
                if density < 0.55:
                    continue
                centres.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    if not centres:
        return []
    min_x = min(cx for cx, _cy in centres)
    centres = [(cx, cy) for cx, cy in centres if cx <= min_x + 3.0]
    centres.sort(key=lambda item: item[1])
    return [cy for _cx, cy in centres]


def render_page(page: fitz.Page, scale: float) -> Image.Image:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    del pixmap
    return image


def render_clip(
    page: fitz.Page,
    left: float,
    top: float,
    right: float,
    bottom: float,
    scale: float,
) -> Image.Image:
    clip = fitz.Rect(left, top, right, bottom)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    del pixmap
    return image


def stitch_vertical(parts: list[Image.Image]) -> Image.Image:
    if len(parts) == 1:
        return parts[0]
    width = max(part.width for part in parts)
    height = sum(part.height for part in parts)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    y = 0
    for part in parts:
        canvas.paste(part, (0, y))
        y += part.height
    return canvas


def load_meta(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def clip_end_of_section(
    page: fitz.Page,
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    gap: float = 4.0,
) -> float:
    """Raise bottom so 'END OF SECTION A' (and similar) is not included."""
    markers = (
        "END OF SECTION",
        "End of Section",
        "END OF PAPER",
        "End of Paper",
    )
    limit = bottom
    for marker in markers:
        for rect in page.search_for(marker):
            if rect.y0 < top or rect.y0 > bottom:
                continue
            if rect.x1 < left - 20 or rect.x0 > right + 20:
                continue
            limit = min(limit, float(rect.y0) - gap)
    return max(top + 1.0, limit)



def strip_end_matter_banner(image: Image.Image) -> Image.Image:
    """Drop trailing 'END OF SECTION A' / 'END OF PAPER' from a question PNG.

    Scanned papers often have no PDF text layer, so search_for cannot see the
    banner. OCR the lower part of the crop and cut above the banner line.
    Require the full phrase - matching lone END/SECTION/PAPER false-triggered
    on normal stems (e.g. 2014 Q1) and deleted the options.
    """
    import io
    import subprocess

    w, h = image.size
    if h < 40 or w < 40:
        return image
    # Only the lower third can hold the end banner.
    y0 = int(h * 0.55)
    strip = image.crop((0, y0, w, h))
    buf = io.BytesIO()
    strip.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    blob = result.stdout.decode("utf-8", errors="ignore").upper()
    if "END OF SECTION" not in blob and "END OF PAPER" not in blob:
        return image

    # Locate the banner line via TSV tops when possible.
    result_tsv = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    rows = result_tsv.stdout.decode("utf-8", errors="ignore").splitlines()
    cut_y: int | None = None
    if len(rows) >= 2:
        header = rows[0].split("\t")
        try:
            text_i = header.index("text")
            top_i = header.index("top")
            conf_i = header.index("conf")
        except ValueError:
            text_i = top_i = conf_i = -1
        if text_i >= 0:
            for line in rows[1:]:
                cols = line.split("\t")
                if len(cols) <= max(text_i, top_i, conf_i):
                    continue
                try:
                    conf = float(cols[conf_i])
                except ValueError:
                    continue
                if conf < 0:
                    continue
                word = (cols[text_i] or "").strip().upper()
                if word != "END":
                    continue
                try:
                    top = int(float(cols[top_i]))
                except ValueError:
                    continue
                abs_y = y0 + top
                cut_y = abs_y if cut_y is None else min(cut_y, abs_y)
    if cut_y is None or cut_y < 20:
        cut_y = int(h * 0.82)
    return image.crop((0, 0, w, max(20, cut_y - 4)))



def split_from_source(
    source: Path,
    meta: dict[str, Any],
    output_dir: Path,
    *,
    questions: int,
    scale: float,
    pad_top: float,
    left_margin: float = 0.0,
) -> None:
    anchors: list[dict[str, Any]] = meta["anchors"]
    if len(anchors) != questions:
        raise SystemExit(f"Meta has {len(anchors)} anchors; expected {questions}.")
    page_crops = {int(page["source_page"]): page for page in meta["pages"]}

    document = fitz.open(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("q*.png"):
        old.unlink()

    try:
        for index, anchor in enumerate(anchors):
            number = int(anchor["n"])
            start_page = int(anchor["source_page"])
            start_x = float(anchor["x"])
            start_y = float(anchor["y"])

            if index + 1 < len(anchors):
                next_anchor = anchors[index + 1]
                end_page = int(next_anchor["source_page"])
                end_y = float(next_anchor["y"])
                # If the next question is only a short header-band into a later
                # page, this question ended on the previous page - do not pull
                # the next page's header into the crop (inflates bottom margin).
                while end_page > start_page:
                    next_top = float(page_crops[end_page]["top"])
                    if end_y - next_top > 120.0:
                        break
                    end_page -= 1
                    if end_page == start_page:
                        end_y = float(page_crops[start_page]["bottom"])
                    else:
                        end_y = float(page_crops[end_page]["bottom"])
            else:
                last_page = max(page_crops)
                end_page = last_page
                end_y = float(page_crops[last_page]["bottom"])

            parts: list[Image.Image] = []
            # Same vertical gap above this anchor as below the previous question
            # (pad_top), so crops abut with no overlap / no leaked prior lines.
            top_pad_pts = pad_top
            start_crop = page_crops[start_page]
            # Keep every strip of this question at the start-page left/right so
            # width matches siblings that share the starting page (multi-page
            # tails must not adopt a different page's crop width).
            # Use page crop left (not marker_x) so the printed question number
            # and any leading * / # stay inside the clip.
            left = float(start_crop.get("left", start_crop.get("marker_x", start_x))) - left_margin
            right = float(start_crop["right"])
            for page_index in range(start_page, end_page + 1):
                crop = page_crops[page_index]
                if page_index == start_page:
                    top = max(0.0, start_y - top_pad_pts)
                else:
                    top = float(crop["top"])
                if page_index == end_page and index + 1 < len(anchors) and end_page == int(
                    next_anchor["source_page"]
                ):
                    # Keep only a tiny gap above the next number so the last
                    # option line is not clipped (pad_top is for the next
                    # question's top; sharing it here cut 2016 Q9 option D).
                    bottom = max(top + 1.0, end_y - 4.0)
                else:
                    bottom = float(crop["bottom"])
                bottom = clip_end_of_section(
                    document[page_index], left, top, right, bottom
                )
                if bottom <= top or right <= left:
                    continue
                parts.append(render_clip(document[page_index], left, top, right, bottom, scale))

            if not parts:
                raise SystemExit(f"Question {number} produced an empty crop.")

            combined = stitch_vertical(parts)
            # Keep full page left/right from meta so short questions match
            # sibling width on the same page; only trim vertical whitespace.
            combined = trim_question_image(
                combined,
                preserve_left=True,
                preserve_right=True,
                preserve_top=True,
            )
            combined = strip_end_matter_banner(combined)
            out_path = output_dir / f"q{number}.png"
            combined.save(out_path, format="PNG")
            print(f"Wrote {out_path} ({combined.width}x{combined.height})")
            del combined, parts
            gc.collect()
    finally:
        document.close()

    print(f"Wrote {questions} questions to {output_dir} (from original PDF)")


def split_from_anchored(
    anchored: Path,
    output_dir: Path,
    *,
    questions: int,
    scale: float,
    gutter: float,
    left_margin: float,
    pad_top: float,
    pad_bottom: float,
) -> None:
    document = fitz.open(anchored)
    gutter_px = int(gutter * scale)
    left_margin_px = int(left_margin * scale)
    pad_top_px = pad_top * scale
    pad_bottom_px = pad_bottom * scale

    markers: list[tuple[int, float]] = []
    page_images: list[Image.Image] = []
    for page_index, page in enumerate(document):
        image = render_page(page, scale)
        page_images.append(image)
        ys = find_marker_ys(image)
        for y in ys:
            markers.append((page_index, y))
        print(f"page {page_index + 1}: {len(ys)} markers")

    if len(markers) != questions:
        document.close()
        raise SystemExit(f"Found {len(markers)} markers; expected {questions}.")

    output_dir.mkdir(parents=True, exist_ok=True)

    for number, (page_index, y) in enumerate(markers, start=1):
        parts: list[Image.Image] = []
        start_page, start_y = page_index, y
        if number < questions:
            end_page, end_y = markers[number]
        else:
            end_page, end_y = len(page_images) - 1, float(page_images[-1].height)

        for p in range(start_page, end_page + 1):
            image = page_images[p]
            if p == start_page:
                top = max(0, int(start_y - pad_top_px))
            else:
                top = 0
            if p == end_page and number < questions:
                bottom = max(top + 1, int(end_y - pad_top_px))
            elif p == end_page:
                bottom = image.height
            else:
                bottom = image.height
            if bottom <= top:
                continue
            left = max(0, gutter_px - left_margin_px)
            crop = image.crop((left, top, image.width, bottom))
            if pad_bottom_px > 0 and p == end_page and number < questions:
                pass
            parts.append(crop)

        if not parts:
            document.close()
            raise SystemExit(f"Question {number} produced an empty crop.")

        combined = stitch_vertical(parts)
        combined = trim_question_image(combined)
        out_path = output_dir / f"q{number}.png"
        combined.save(out_path, format="PNG")
        print(f"Wrote {out_path} ({combined.width}x{combined.height})")
        del combined, parts
        gc.collect()

    document.close()
    for image in page_images:
        image.close()
    print(f"Wrote {questions} questions to {output_dir}")


def main() -> None:
    args = parse_args()
    meta_path = args.meta or args.anchored.with_suffix(".meta.json")

    if args.source is not None:
        if not meta_path.is_file():
            raise SystemExit(f"Meta file not found: {meta_path} (run preprocess_mc.py first)")
        if not args.source.is_file():
            raise SystemExit(f"Source PDF not found: {args.source}")
        meta = load_meta(meta_path)
        question_count = int(meta.get("questions", args.questions))
        split_from_source(
            args.source,
            meta,
            args.output_dir,
            questions=question_count,
            scale=args.scale,
            pad_top=args.pad_top,
            left_margin=args.left_margin,
        )
        combine_pngs_to_pdf(args.output_dir, overwrite=True)
        return

    split_from_anchored(
        args.anchored,
        args.output_dir,
        questions=args.questions,
        scale=args.scale,
        gutter=args.gutter,
        left_margin=args.left_margin,
        pad_top=args.pad_top,
        pad_bottom=args.pad_bottom,
    )
    combine_pngs_to_pdf(args.output_dir, overwrite=True)


if __name__ == "__main__":
    main()
