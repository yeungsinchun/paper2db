#!/usr/bin/env python3
"""Extract Paper 1A MC keys and correct-% from marking-scheme PDFs."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageOps

KEY_RE = re.compile(
    r"(?P<n>\d{1,2})\s*[.,;:]?\s*(?P<k>[A-Da-d*＊])\s*(?:[\(（]\s*(?P<p>\d{1,3})\s*[\)）])?",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--answers",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "Year" / "Answer",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "MC" / "answer_keys.json",
    )
    return parser.parse_args()


def ocr_image(image: Image.Image, psm: str = "6") -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", f"--psm", psm],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="ignore")


def render_page(page: fitz.Page, scale: float = 2.5) -> Image.Image:
    # Honour page rotation so sideways scans OCR correctly.
    matrix = fitz.Matrix(scale, scale)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if page.rotation in (90, 270):
        # get_pixmap already applies rotation in recent PyMuPDF; keep as-is.
        pass
    return image


def parse_keys(text: str, max_q: int = 36) -> dict[int, dict]:
    keys: dict[int, dict] = {}
    for match in KEY_RE.finditer(text):
        number = int(match.group("n"))
        if not 1 <= number <= max_q:
            continue
        raw = match.group("k")
        if raw in "*＊":
            keys[number] = {"Correct Option": None, "Correct percentage": None, "deleted": True}
            continue
        option = raw.upper()
        if option not in "ABCD":
            continue
        pct = int(match.group("p")) if match.group("p") else None
        if pct is not None and not 0 <= pct <= 100:
            pct = None
        prev = keys.get(number)
        # Prefer entries that include a percentage.
        if prev is None or (pct is not None and prev.get("Correct percentage") is None):
            keys[number] = {"Correct Option": option, "Correct percentage": pct, "deleted": False}
    return keys


def score_keys(keys: dict[int, dict]) -> int:
    return sum(1 for v in keys.values() if v.get("Correct Option") or v.get("deleted"))


def extract_from_pdf(path: Path) -> dict[int, dict]:
    document = fitz.open(path)
    best: dict[int, dict] = {}
    best_text = ""
    try:
        for page in document:
            image = render_page(page, scale=2.5)
            # Also try a contrast-boosted copy for faded scans.
            variants = [image, ImageOps.autocontrast(image)]
            for variant in variants:
                for psm in ("6", "4"):
                    text = ocr_image(variant, psm=psm)
                    # Prefer pages that mention Section A / Key table.
                    bonus = 5 if re.search(r"Section\s*A|Question\s*No|答案|Key", text, re.I) else 0
                    keys = parse_keys(text)
                    if score_keys(keys) + bonus > score_keys(best) + (5 if "Section A" in best_text else 0):
                        # Merge rather than replace if similar size — take denser.
                        if score_keys(keys) >= score_keys(best):
                            best = keys
                            best_text = text
            # Merge any additional high-confidence hits into best.
            for variant in variants[:1]:
                keys = parse_keys(ocr_image(variant, psm="6"))
                for number, payload in keys.items():
                    if number not in best:
                        best[number] = payload
                    elif best[number].get("Correct percentage") is None and payload.get("Correct percentage") is not None:
                        best[number] = payload
    finally:
        document.close()
    return best


# Hand-verified patches where OCR is unreliable (from marking-scheme page images).
MANUAL_KEYS: dict[str, dict[int, dict]] = {
    "2012": {
        1: {"Correct Option": "C", "Correct percentage": 63},
        2: {"Correct Option": "A", "Correct percentage": 81},
        3: {"Correct Option": "A", "Correct percentage": 38},
        4: {"Correct Option": "D", "Correct percentage": 70},
        5: {"Correct Option": "A", "Correct percentage": 54},
        6: {"Correct Option": "D", "Correct percentage": 41},
        7: {"Correct Option": "A", "Correct percentage": 63},
        8: {"Correct Option": "C", "Correct percentage": 36},
        9: {"Correct Option": "B", "Correct percentage": 84},
        10: {"Correct Option": "C", "Correct percentage": 55},
        11: {"Correct Option": "A", "Correct percentage": 58},
        12: {"Correct Option": "C", "Correct percentage": 63},
        13: {"Correct Option": "D", "Correct percentage": 67},
        14: {"Correct Option": "D", "Correct percentage": 58},
        15: {"Correct Option": "B", "Correct percentage": 73},
        16: {"Correct Option": "D", "Correct percentage": 77},
        17: {"Correct Option": "B", "Correct percentage": 78},
        18: {"Correct Option": "D", "Correct percentage": 62},
        19: {"Correct Option": "A", "Correct percentage": 76},
        20: {"Correct Option": "A", "Correct percentage": 65},
        21: {"Correct Option": "A", "Correct percentage": 40},
        22: {"Correct Option": "D", "Correct percentage": 54},
        23: {"Correct Option": "C", "Correct percentage": 61},
        24: {"Correct Option": "B", "Correct percentage": 86},
        25: {"Correct Option": "B", "Correct percentage": 49},
        26: {"Correct Option": "B", "Correct percentage": 58},
        27: {"Correct Option": "B", "Correct percentage": 69},
        28: {"Correct Option": "B", "Correct percentage": 42},
        29: {"Correct Option": "D", "Correct percentage": 45},
        30: {"Correct Option": "C", "Correct percentage": 53},
        31: {"Correct Option": "B", "Correct percentage": 47},
        32: {"Correct Option": "A", "Correct percentage": 47},
        33: {"Correct Option": "C", "Correct percentage": 40},
        34: {"Correct Option": "C", "Correct percentage": 37},
        35: {"Correct Option": "C", "Correct percentage": 65},
        36: {"Correct Option": "D", "Correct percentage": 51},
    },
    "2014": {
        1: {"Correct Option": "D", "Correct percentage": 83},
        2: {"Correct Option": "A", "Correct percentage": 55},
        3: {"Correct Option": "C", "Correct percentage": 75},
        4: {"Correct Option": "A", "Correct percentage": 54},
        5: {"Correct Option": "B", "Correct percentage": 37},
        6: {"Correct Option": "D", "Correct percentage": 45},
        7: {"Correct Option": "C", "Correct percentage": 26},
        8: {"Correct Option": "C", "Correct percentage": 54},
        9: {"Correct Option": "B", "Correct percentage": 91},
        10: {"Correct Option": "B", "Correct percentage": 66},
        11: {"Correct Option": "B", "Correct percentage": 29},
        12: {"Correct Option": "B", "Correct percentage": 58},
        13: {"Correct Option": "B", "Correct percentage": 74},
        14: {"Correct Option": "A", "Correct percentage": 80},
        15: {"Correct Option": "C", "Correct percentage": 78},
        16: {"Correct Option": "A", "Correct percentage": 76},
        17: {"Correct Option": "B", "Correct percentage": 53},
        18: {"Correct Option": "C", "Correct percentage": 41},
        19: {"Correct Option": "A", "Correct percentage": 76},
        20: {"Correct Option": "C", "Correct percentage": 44},
        26: {"Correct Option": "A", "Correct percentage": 58},
        27: {"Correct Option": "D", "Correct percentage": 22},
        28: {"Correct Option": "D", "Correct percentage": 68},
        29: {"Correct Option": "B", "Correct percentage": 28},
        30: {"Correct Option": "B", "Correct percentage": 57},
        31: {"Correct Option": "D", "Correct percentage": 54},
        32: {"Correct Option": "A", "Correct percentage": 61},
        33: {"Correct Option": "C", "Correct percentage": 56},
    },
    "2018": {
        1: {"Correct Option": None, "Correct percentage": None, "deleted": True},
        2: {"Correct Option": "A", "Correct percentage": 55},
        3: {"Correct Option": "D", "Correct percentage": 64},
        4: {"Correct Option": "A", "Correct percentage": 11},
        5: {"Correct Option": "A", "Correct percentage": 38},
        6: {"Correct Option": "C", "Correct percentage": 21},
        7: {"Correct Option": "B", "Correct percentage": 71},
        8: {"Correct Option": "A", "Correct percentage": 37},
        9: {"Correct Option": "A", "Correct percentage": 58},
        10: {"Correct Option": "D", "Correct percentage": 58},
        11: {"Correct Option": "B", "Correct percentage": 51},
        12: {"Correct Option": "A", "Correct percentage": 70},
        13: {"Correct Option": "C", "Correct percentage": 65},
        14: {"Correct Option": "D", "Correct percentage": 72},
        15: {"Correct Option": "C", "Correct percentage": 32},
        16: {"Correct Option": "B", "Correct percentage": 51},
        17: {"Correct Option": "D", "Correct percentage": 81},
        18: {"Correct Option": "C", "Correct percentage": 73},
        19: {"Correct Option": "B", "Correct percentage": 62},
        20: {"Correct Option": "D", "Correct percentage": 87},
        21: {"Correct Option": "C", "Correct percentage": 39},
        22: {"Correct Option": "A", "Correct percentage": 55},
        23: {"Correct Option": "C", "Correct percentage": 69},
        24: {"Correct Option": "B", "Correct percentage": 51},
        25: {"Correct Option": "A", "Correct percentage": 51},
        26: {"Correct Option": "C", "Correct percentage": 75},
        27: {"Correct Option": "C", "Correct percentage": 62},
        28: {"Correct Option": "D", "Correct percentage": 36},
        29: {"Correct Option": "D", "Correct percentage": 87},
        30: {"Correct Option": "B", "Correct percentage": 46},
        31: {"Correct Option": "D", "Correct percentage": 74},
        32: {"Correct Option": "B", "Correct percentage": 40},
        33: {"Correct Option": "A", "Correct percentage": 62},
    },
    "2019": {
        1: {"Correct Option": "D", "Correct percentage": None},
        2: {"Correct Option": "B", "Correct percentage": None},
        3: {"Correct Option": "A", "Correct percentage": None},
        4: {"Correct Option": "D", "Correct percentage": None},
        5: {"Correct Option": "A", "Correct percentage": None},
        6: {"Correct Option": "C", "Correct percentage": None},
        7: {"Correct Option": "B", "Correct percentage": None},
        8: {"Correct Option": "D", "Correct percentage": None},
        9: {"Correct Option": "B", "Correct percentage": None},
        10: {"Correct Option": "A", "Correct percentage": None},
        11: {"Correct Option": "B", "Correct percentage": None},
        12: {"Correct Option": "A", "Correct percentage": None},
        13: {"Correct Option": "C", "Correct percentage": None},
        14: {"Correct Option": "C", "Correct percentage": None},
        15: {"Correct Option": "A", "Correct percentage": None},
        16: {"Correct Option": "C", "Correct percentage": None},
        17: {"Correct Option": "B", "Correct percentage": None},
        18: {"Correct Option": "A", "Correct percentage": None},
        19: {"Correct Option": "D", "Correct percentage": None},
        26: {"Correct Option": "C", "Correct percentage": None},
        27: {"Correct Option": "D", "Correct percentage": None},
        28: {"Correct Option": "D", "Correct percentage": None},
        29: {"Correct Option": "C", "Correct percentage": None},
        30: {"Correct Option": "B", "Correct percentage": None},
        31: {"Correct Option": "D", "Correct percentage": None},
        32: {"Correct Option": "B", "Correct percentage": None},
        33: {"Correct Option": "C", "Correct percentage": None},
    },
    "2023": {
        1: {"Correct Option": "B", "Correct percentage": 61},
        2: {"Correct Option": "D", "Correct percentage": 50},
        3: {"Correct Option": "C", "Correct percentage": 78},
        4: {"Correct Option": "A", "Correct percentage": 60},
        5: {"Correct Option": "B", "Correct percentage": 54},
        6: {"Correct Option": "D", "Correct percentage": 70},
        7: {"Correct Option": "B", "Correct percentage": 58},
        8: {"Correct Option": "C", "Correct percentage": 53},
        9: {"Correct Option": "C", "Correct percentage": 45},
        10: {"Correct Option": "A", "Correct percentage": 31},
        11: {"Correct Option": "B", "Correct percentage": 51},
        12: {"Correct Option": "C", "Correct percentage": 44},
        13: {"Correct Option": "D", "Correct percentage": 37},
        14: {"Correct Option": "C", "Correct percentage": 88},
        15: {"Correct Option": "D", "Correct percentage": 50},
        16: {"Correct Option": "B", "Correct percentage": 61},
        17: {"Correct Option": "A", "Correct percentage": 71},
        18: {"Correct Option": "B", "Correct percentage": 64},
        19: {"Correct Option": "A", "Correct percentage": 67},
        26: {"Correct Option": "D", "Correct percentage": 68},
        27: {"Correct Option": "B", "Correct percentage": 31},
        28: {"Correct Option": "C", "Correct percentage": 30},
        29: {"Correct Option": "A", "Correct percentage": 55},
        30: {"Correct Option": "B", "Correct percentage": 50},
        31: {"Correct Option": "C", "Correct percentage": 71},
        32: {"Correct Option": "D", "Correct percentage": 56},
        33: {"Correct Option": "D", "Correct percentage": 46},
    },
}


def main() -> None:
    args = parse_args()
    answers = args.answers
    result: dict[str, dict[str, dict]] = {}

    for pdf in sorted(answers.glob("*ans.pdf")):
        year = pdf.name.replace("ans.pdf", "")
        print(f"Extracting {pdf.name} ...", flush=True)
        keys = extract_from_pdf(pdf)
        # Apply / merge manual patches (manual wins).
        if year in MANUAL_KEYS:
            keys.update(MANUAL_KEYS[year])
        # Drop entries that are clearly wrong (option missing and not deleted).
        cleaned = {
            str(n): {
                "Correct Option": payload.get("Correct Option"),
                "Correct percentage": payload.get("Correct percentage"),
                **({"deleted": True} if payload.get("deleted") else {}),
            }
            for n, payload in sorted(keys.items())
            if payload.get("Correct Option") or payload.get("deleted")
        }
        result[year] = cleaned
        print(f"  -> {len(cleaned)} keys", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
