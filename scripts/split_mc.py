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
TOP_PAD_PX = 10


def trim_question_image(
    image: Image.Image,
    *,
    pad: int = 10,
    left_pad: int = 24,
    preserve_left: bool = False,
    dark_threshold: int = 235,
    min_row_fraction: float = 0.008,
    min_col_fraction: float = 0.012,
) -> Image.Image:
    """Crop to ink bounds; ignores large blank gaps between marker splits."""
    arr = np.asarray(image.convert("L"))
    h, w = arr.shape
    if h < 4 or w < 4:
        return image

    def row_content(y: int) -> bool:
        return float((arr[y] < dark_threshold).mean()) >= min_row_fraction

    def col_content(x: int) -> bool:
        return float((arr[:, x] < dark_threshold).mean()) >= min_col_fraction

    content_rows = [y for y in range(h) if row_content(y)]
    content_cols = [x for x in range(w) if col_content(x)]
    if not content_rows or not content_cols:
        return image

    top = max(0, content_rows[0] - pad)
    bottom = min(h, content_rows[-1] + 1 + pad)
    if preserve_left:
        left = 0
    else:
        left = max(0, content_cols[0] - left_pad)
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
        default=4.0,
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
            else:
                last_page = max(page_crops)
                end_page = last_page
                end_y = float(page_crops[last_page]["bottom"])

            parts: list[Image.Image] = []
            top_pad_pts = TOP_PAD_PX / scale
            for page_index in range(start_page, end_page + 1):
                crop = page_crops[page_index]
                # Crop at the anchor rail (original PDF, no blue dots).
                rail_left = float(crop.get("marker_x", start_x))
                left = rail_left - left_margin
                if page_index == start_page:
                    top = max(0.0, start_y - top_pad_pts)
                else:
                    top = float(crop["top"])
                right = float(crop["right"])
                if page_index == end_page and index + 1 < len(anchors) and end_page == int(
                    next_anchor["source_page"]
                ):
                    bottom = max(top + 1.0, end_y - pad_top)
                else:
                    bottom = float(crop["bottom"])
                if bottom <= top or right <= left:
                    continue
                parts.append(render_clip(document[page_index], left, top, right, bottom, scale))

            if not parts:
                raise SystemExit(f"Question {number} produced an empty crop.")

            combined = stitch_vertical(parts)
            combined = trim_question_image(combined, preserve_left=True)
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
