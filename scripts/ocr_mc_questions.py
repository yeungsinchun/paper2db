#!/usr/bin/env python3
"""OCR processed MC question PNGs and merge with answer keys into JSON."""
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

OPTION_SPLIT = re.compile(r"(?:^|\n)\s*([A-D])\s*[.．、)]\s*", re.M)
LEADING_NUM = re.compile(r"^\s*\*?\s*\d{1,2}\s*[.．、)]\s*")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "MC",
    )
    parser.add_argument(
        "--keys",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "MC" / "answer_keys.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "MC" / "mc_questions.json",
    )
    parser.add_argument("--years", nargs="*", default=None, help="Optional subset of years")
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


def clean_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_question(ocr_text: str) -> tuple[str, dict[str, str]]:
    text = clean_text(ocr_text)
    # Normalize common OCR confusions for option markers.
    text = re.sub(r"(?m)^[ \t]*C[cC]\s*[.．、)]\s*", "C. ", text)
    text = re.sub(r"(?m)^[ \t]*[Cc]\s*[.．、)]\s*", "C. ", text)
    text = re.sub(r"(?m)^[ \t]*[Oo0]\s*[.．、)]\s*", "D. ", text)
    text = re.sub(r"(?m)^[ \t]*[Bb]\s*[.．、)]\s*", "B. ", text)
    text = re.sub(r"(?m)^[ \t]*[Aa]\s*[.．、)]\s*", "A. ", text)
    # Also fix inline "Cc." that lost its newline.
    text = re.sub(r"(?<![A-Za-z])Cc\s*[.．]\s+", "\nC. ", text)

    parts = OPTION_SPLIT.split("\n" + text)
    if len(parts) < 3:
        statement = LEADING_NUM.sub("", text).strip()
        return statement, {"A": "", "B": "", "C": "", "D": ""}

    statement = parts[0].lstrip("\n")
    statement = LEADING_NUM.sub("", statement).strip()
    options: dict[str, str] = {"A": "", "B": "", "C": "", "D": ""}
    # parts = [pre, 'A', a_text, 'B', b_text, ...]
    i = 1
    while i + 1 < len(parts):
        letter = parts[i].upper()
        body = parts[i + 1].strip()
        body = re.sub(r"\n+", " ", body).strip()
        if letter in options and not options[letter]:
            options[letter] = body
        i += 2
    return statement, options


def question_paths(year_dir: Path) -> list[Path]:
    files = list(year_dir.glob("q*.png"))
    def key(path: Path) -> int:
        match = re.fullmatch(r"q(\d+)\.png", path.name)
        return int(match.group(1)) if match else 10**9
    return sorted(files, key=key)


def main() -> None:
    args = parse_args()
    keys_root = {}
    if args.keys.exists():
        keys_root = json.loads(args.keys.read_text())

    years = args.years or [y for y in YEAR_ORDER if (args.processed / y).is_dir()]
    records = []
    for year in years:
        year_dir = args.processed / year
        if not year_dir.is_dir():
            continue
        year_keys = keys_root.get(year, {})
        paths = question_paths(year_dir)
        cache_dir = args.processed / "ocr_cache" / year
        print(f"OCR {year}: {len(paths)} questions", flush=True)
        for path in paths:
            number = int(re.fullmatch(r"q(\d+)\.png", path.name).group(1))
            text = ocr_png(path, cache_dir=cache_dir)
            statement, options = parse_question(text)
            key = year_keys.get(str(number), {})
            rel_png = Path("processed") / "MC" / year / path.name
            record = {
                "Year": int(year) if year.isdigit() else year,
                "Question": number,
                "Question statement": statement,
                "Option": options,
                "Correct percentage": key.get("Correct percentage"),
                "Correct Option": key.get("Correct Option"),
                "PNG": rel_png.as_posix(),
            }
            if key.get("deleted"):
                record["deleted"] = True
            records.append(record)
            if number % 10 == 0 or number == 1:
                print(f"  {year} q{number} ok", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {args.output} ({len(records)} records)")


if __name__ == "__main__":
    main()
