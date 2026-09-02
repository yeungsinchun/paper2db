#!/usr/bin/env python3
"""OCR processed LQ question PNGs into lq_questions.json."""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image

YEAR_ORDER = [
    "2012", "2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020",
    "2021", "2022", "2023", "2024", "2025", "2026", "ppp", "sapp",
]

LEADING_NUM = re.compile(r"^\s*\*?\s*\d{1,2}\s*[.??)]\s*")
MARKS_RE = re.compile(r"\(\s*\d+\s*marks?\s*\)", re.I)
MARGIN_RE = re.compile(r"answers written in the margins.*", re.I)
FOOTER_RE = re.compile(r"(?m)^.*(dse[- ]?phy|provided by dse).*$", re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "LQ",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "LQ" / "lq_questions.json",
    )
    parser.add_argument("--years", nargs="*", default=None)
    return parser.parse_args()


def ocr_png(path: Path, cache_dir: Path | None = None) -> str:
    cache_path = None
    if cache_dir is not None:
        cache_path = cache_dir / f"{path.stem}.txt"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
    image = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    text = result.stdout.decode("utf-8", errors="ignore")
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    return text


def strip_following_question(text: str, qn: int) -> str:
    """Drop OCR from the next LQ when a crop boundary includes the following question."""
    nxt = qn + 1
    cut = len(text)
    for pat in (
        rf"(?is)(?:^|\n)\s*{nxt}\s*[.)]\s*\([a-z]\)",
        rf"(?is)\b(?:figure|rigure)\s*{nxt}(?:\.\d)?\b",
        rf"(?is)\bcircuits?\s+in\s+(?:figure|rigure)\s*[/\.]?\s*{nxt}",
    ):
        m = re.search(pat, text)
        if m:
            cut = min(cut, m.start())
    # OCR-mangled bleed from the next circuit question (e.g. 2016 Q6 crop → Q7 tail).
    if qn != 7:
        m = re.search(r"(?is)\bcircuits?\s+in\s+(?:figure|rigure)\s*[/\.]", text)
        if m:
            cut = min(cut, m.start())
    return text[:cut].strip() if cut < len(text) else text


def clean_text(text: str, *, qn: int | None = None) -> str:
    text = text.replace("\r", "")
    text = FOOTER_RE.sub("", text)
    text = MARGIN_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = LEADING_NUM.sub("", text.strip(), count=1)
    if qn is not None:
        text = strip_following_question(text, qn)
    return text.strip()


def main() -> None:
    args = parse_args()
    years = args.years or [y for y in YEAR_ORDER if (args.processed / y).is_dir()]
    records = []
    for year in years:
        year_dir = args.processed / year
        paths = sorted(year_dir.glob("q*.png"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
        if not paths:
            continue
        cache_dir = args.processed / "ocr_cache" / year
        print(f"{year}: {len(paths)} questions")
        for path in paths:
            qn = int(re.findall(r"\d+", path.stem)[0])
            raw = ocr_png(path, cache_dir=cache_dir)
            statement = clean_text(raw, qn=qn)
            records.append(
                {
                    "Year": int(year) if year.isdigit() else year,
                    "Question": qn,
                    "Question statement": statement,
                    "PNG": f"processed/LQ/{year}/{path.name}",
                    "Type": "LQ",
                }
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} LQ records -> {args.output}")


if __name__ == "__main__":
    main()
