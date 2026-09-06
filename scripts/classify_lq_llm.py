#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify LQ (Paper 1B) questions into the same 27 sections as MC.

Reads crops from output/lq/<year>/qN.png, writes nested LQ outputs only:
  classified/lq/llm_classifications.json
  classified/lq/classification.csv
  classified/lq/<book>/<section>/ year-qN.png (+ optional answer copy)

Top-level classified/lq_classification.csv|json come from classify_lq_keywords.py.
Any LLM failure aborts before write_outputs so nested outputs stay unchanged.

Env: same as classify_mc_llm.py (LLM_API_KEY / OPENAI_API_KEY / TOGETHER_API_KEY).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
import time
import urllib.error
from pathlib import Path

from PIL import Image

from classify_mc_llm import (
    SECTION_BY_NUM,
    SECTIONS,
    SYSTEM_PROMPT as MC_SYSTEM,
    chat_json,
    llm_config,
    normalize_sections,
    year_key,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_LQ = ROOT / "output" / "lq"
CLASSIFIED_LQ = ROOT / "classified" / "lq"
OCR_CACHE = CLASSIFIED_LQ / "ocr_cache"

YEAR_ORDER = [
    "2012", "2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020",
    "2021", "2022", "2023", "pp",
]

SYSTEM_PROMPT = MC_SYSTEM.replace(
    "multiple-choice questions",
    "long / structured questions (Paper 1B)",
).replace(
    "Ignore OCR garbage; classify from the meaningful physics content.",
    "Ignore OCR garbage and dotted answer lines; classify from the stem and "
    "what the student is asked to find/explain. Prefer the dominant topic of "
    "the whole question (not a side formula).",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", nargs="*", default=None)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--from-json", type=Path, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.2)
    return p.parse_args()


def ensure_tree() -> None:
    CLASSIFIED_LQ.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    for _n, book, folder, _name in SECTIONS:
        (CLASSIFIED_LQ / book / folder).mkdir(parents=True, exist_ok=True)


def collect_jobs(years: list[str] | None) -> list[tuple[str, Path, int]]:
    if not OUTPUT_LQ.is_dir():
        return []
    jobs: list[tuple[str, Path, int]] = []
    for year_dir in sorted(OUTPUT_LQ.iterdir(), key=lambda p: year_key(p.name)):
        if not year_dir.is_dir():
            continue
        if years and year_dir.name not in years:
            continue
        for png in sorted(year_dir.glob("q*.png"), key=lambda p: int(p.stem[1:])):
            jobs.append((year_dir.name, png, int(png.stem[1:])))
    return jobs


def ocr_png(path: Path, cache_path: Path) -> str:
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    # LQ crops are tall; OCR a top band first (stem), fall back to full if thin.
    image = Image.open(path).convert("RGB")
    w, h = image.size
    band_h = min(h, max(900, int(h * 0.45)))
    crop = image.crop((0, 0, w, band_h))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    text = result.stdout.decode("utf-8", errors="ignore")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text


def _ocr_one(args: tuple[str, str, int]) -> dict:
    year, png_path, number = args
    cache = OCR_CACHE / str(year) / f"q{number}.txt"
    text = ocr_png(Path(png_path), cache)
    # Drop dotted-line OCR noise.
    lines = []
    for line in text.splitlines():
        if re.fullmatch(r"[\s.·•…eEwWm_~-]{6,}", line.strip()):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return {
        "Year": year,
        "Question": number,
        "Statement": cleaned[:2500],
        "PNG": f"output/lq/{year}/q{number}.png",
        "AnswerPNG": f"output/lq/{year}/ans/q{number}.png",
    }


def classify_one(record: dict) -> dict:
    user = (
        f"Year {record['Year']} Q{record['Question']}\n\n"
        f"{record['Statement'][:1800]}\n\n"
        'JSON only: {"sections":[<primary>, ...], "reason":"<one short sentence>"}'
    )
    parsed = chat_json(SYSTEM_PROMPT, user)
    sections = normalize_sections(parsed.get("sections"))
    if not sections:
        raise ValueError(f"bad sections in {parsed!r}")
    return {
        "sections": sections,
        "reason": str(parsed.get("reason") or "").strip()[:240],
    }


def write_outputs(rows: list[dict]) -> None:
    # Clear previous section copies (keep ocr_cache / json).
    for _n, book, folder, _name in SECTIONS:
        folder_path = CLASSIFIED_LQ / book / folder
        for old in folder_path.glob("*.png"):
            old.unlink()

    csv_path = CLASSIFIED_LQ / "classification.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "Year",
                "Question",
                "Primary",
                "AllSections",
                "Reason",
                "PNG",
                "AnswerPNG",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            primary = int(row["Primary"])
            for sec in [int(x) for x in row["AllSections"].split(";") if x]:
                book, folder, _name = SECTION_BY_NUM[sec]
                dest = CLASSIFIED_LQ / book / folder / f"{row['Year']}-q{row['Question']}.png"
                src = ROOT / row["PNG"]
                if src.is_file():
                    shutil.copy2(src, dest)
                ans_src = ROOT / row["AnswerPNG"]
                if ans_src.is_file():
                    ans_dest = (
                        CLASSIFIED_LQ
                        / book
                        / folder
                        / f"{row['Year']}-q{row['Question']}-ans.png"
                    )
                    shutil.copy2(ans_src, ans_dest)

    decisions = {
        f"{r['Year']}-q{r['Question']}": {
            "sections": [int(x) for x in r["AllSections"].split(";") if x],
            "reason": r["Reason"],
        }
        for r in rows
    }
    (CLASSIFIED_LQ / "llm_classifications.json").write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {csv_path} ({len(rows)} rows)")


def main() -> None:
    args = parse_args()
    ensure_tree()
    jobs = collect_jobs(args.years)
    if args.limit:
        jobs = jobs[: args.limit]
    if not jobs:
        raise SystemExit(f"No LQ crops under {OUTPUT_LQ}")

    print(f"OCR {len(jobs)} LQ questions...")
    records: list[dict] = []
    # Sequential OCR - ProcessPoolExecutor hung on macOS with large PNGs.
    for i, (y, p, n) in enumerate(jobs, 1):
        records.append(_ocr_one((y, str(p), n)))
        if i % 20 == 0 or i == len(jobs):
            print(f"  ocr {i}/{len(jobs)}")
    records.sort(key=lambda r: (year_key(str(r["Year"])), int(r["Question"])))

    if args.from_json:
        decisions = json.loads(args.from_json.read_text(encoding="utf-8"))
        rows = []
        for rec in records:
            key = f"{rec['Year']}-q{rec['Question']}"
            d = decisions[key]
            sections = [int(x) for x in d["sections"]]
            rows.append(
                {
                    "Year": rec["Year"],
                    "Question": rec["Question"],
                    "Primary": sections[0],
                    "AllSections": ";".join(str(s) for s in sections),
                    "Reason": d.get("reason", ""),
                    "PNG": rec["PNG"],
                    "AnswerPNG": rec["AnswerPNG"],
                }
            )
        write_outputs(rows)
        return

    key, base, model = llm_config()
    if not key:
        raise SystemExit("Set LLM_API_KEY (or OPENAI_API_KEY / TOGETHER_API_KEY)")
    print(f"LLM {model} @ {base} ({len(records)} questions)")

    rows: list[dict] = []
    failures: list[str] = []
    for i, rec in enumerate(records, 1):
        try:
            result = classify_one(rec)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as exc:
            label = f"{rec['Year']} Q{rec['Question']}"
            print(f"  FAIL {label}: {exc}")
            failures.append(label)
            continue
        sections = result["sections"]
        rows.append(
            {
                "Year": rec["Year"],
                "Question": rec["Question"],
                "Primary": sections[0],
                "AllSections": ";".join(str(s) for s in sections),
                "Reason": result["reason"],
                "PNG": rec["PNG"],
                "AnswerPNG": rec["AnswerPNG"],
            }
        )
        if i % 10 == 0 or i == len(records):
            print(f"  classified {i}/{len(records)}")
        time.sleep(args.sleep)

    if failures or len(rows) != len(records):
        raise SystemExit(
            f"Aborting write: {len(failures)} LLM failure(s) "
            f"({len(rows)}/{len(records)} succeeded); nested LQ outputs unchanged"
        )
    write_outputs(rows)


if __name__ == "__main__":
    main()
