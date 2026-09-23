#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify LQ (Paper 1B) questions into the same 27 sections as MC.

Reads crops from tests/reconstructed/lq/<year>/qN.png, writes nested LQ outputs only:
  metadata/lq/llm_classifications.json
  tests/sections/lq/classification.csv
  tests/sections/lq/<book>/<section>/ year-qN.png (+ optional answer copy)

Also writes top-level tests/sections/lq_classification.csv|json (same builder as
classify_lq_keywords.py).

--replay applies the tracked metadata/lq/llm_classifications.json verbatim with
no API call and without rewriting it; it fails if the file is missing or lacks
a decision for any selected question (used by ./pipeline --replay-classifications).
Any LLM failure aborts before write_outputs so nested outputs stay unchanged.

Env: same as classify_mc_llm.py (LLM_API_KEY / OPENAI_API_KEY / TOGETHER_API_KEY).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
import urllib.error
from pathlib import Path

import classify_lq_keywords as keyword_classifier
from classify_mc_llm import (
    SECTION_BY_NUM,
    SECTIONS,
    SYSTEM_PROMPT as MC_SYSTEM,
    chat_json,
    llm_config,
    normalize_sections,
    year_key,
)

LQ_SECTION_LIMIT = 3

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_LQ = ROOT / "tests" / "reconstructed" / "lq"
CLASSIFIED_LQ = ROOT / "tests" / "sections" / "lq"
OCR_CACHE = CLASSIFIED_LQ / "ocr_cache"
METADATA_LQ = ROOT / "metadata" / "lq"

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
    p.add_argument(
        "--replay",
        action="store_true",
        help="Apply tracked metadata/lq/llm_classifications.json read-only; "
        "fail if missing or incomplete (no LLM call)",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.2)
    return p.parse_args()


def ensure_tree() -> None:
    CLASSIFIED_LQ.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    METADATA_LQ.mkdir(parents=True, exist_ok=True)
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


def _ocr_one(args: tuple[str, str, int]) -> dict:
    year, png_path, number = args
    png = Path(png_path)
    cache = keyword_classifier.ocr_cache_path(png, str(year), number)
    text = keyword_classifier.ocr_png(png, cache)
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
        "Statement": cleaned,
        "PNG": f"tests/reconstructed/lq/{year}/q{number}.png",
        "AnswerPNG": f"tests/reconstructed/lq/{year}/ans/q{number}.png",
    }


def finalize_sections(record: dict, raw_sections: object, reason: str) -> tuple[list[int], str]:
    sections = normalize_sections(raw_sections, limit=LQ_SECTION_LIMIT)
    if not sections:
        raise ValueError(f"bad sections in {raw_sections!r}")
    return keyword_classifier.apply_book5_listings(
        str(record.get("Statement") or ""), sections, reason
    )


def classify_one(record: dict) -> dict:
    user = (
        f"Year {record['Year']} Q{record['Question']}\n\n"
        f"{record['Statement'][:1800]}\n\n"
        'JSON only: {"sections":[<primary>, ...], "reason":"<one short sentence>"}'
    )
    parsed = chat_json(SYSTEM_PROMPT, user)
    reason = str(parsed.get("reason") or "").strip()[:240]
    sections, reason = finalize_sections(record, parsed.get("sections"), reason)
    return {
        "sections": sections,
        "reason": reason,
    }


def write_outputs(
    rows: list[dict],
    touched_years: set[str] | None = None,
    touched_keys: set[tuple[str, int]] | None = None,
    *,
    write_decisions: bool = True,
) -> None:
    # Clear previous section copies (keep ocr_cache / json).
    for _n, book, folder, _name in SECTIONS:
        folder_path = CLASSIFIED_LQ / book / folder
        for old in folder_path.glob("*.png"):
            match = re.fullmatch(r"(.+)-q(\d+)(?:-ans)?\.png", old.name)
            old_key = (match.group(1), int(match.group(2))) if match else None
            if (
                touched_keys is not None
                and old_key in touched_keys
                or touched_keys is None
                and (touched_years is None or old.name.split("-q", 1)[0] in touched_years)
            ):
                old.unlink()

    csv_path = CLASSIFIED_LQ / "classification.csv"
    decisions_path = METADATA_LQ / "llm_classifications.json"
    new_rows = rows
    new_decisions = {
        f"{r['Year']}-q{r['Question']}": {
            "sections": [int(x) for x in r["AllSections"].split(";") if x],
            "reason": r["Reason"],
        }
        for r in new_rows
    }
    if (touched_years is not None or touched_keys is not None) and csv_path.is_file():
        with csv_path.open(encoding="utf-8") as fh:
            existing_rows = list(csv.DictReader(fh))
        existing_decisions = {}
        if decisions_path.is_file():
            existing_decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        if touched_keys is not None:
            rows = keyword_classifier.merge_nested_rows(existing_rows, new_rows)
            decisions = {**existing_decisions, **new_decisions}
        else:
            rows, decisions = keyword_classifier.replace_touched_years(
                existing_rows,
                existing_decisions,
                new_rows,
                new_decisions,
                touched_years,
            )
    else:
        decisions = new_decisions

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
        for row in new_rows:
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

    if write_decisions:
        decisions_path.write_text(
            json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    keyword_classifier.write_top_level(rows, CLASSIFIED_LQ, CLASSIFIED_LQ.parent)
    print(f"Wrote {csv_path} ({len(rows)} rows)")


def replay_rows(records: list[dict], decisions_path: Path) -> list[dict]:
    """Nested rows from tracked decisions, verbatim; exit (never fall back) on gaps."""
    if not decisions_path.is_file():
        raise SystemExit(f"--replay: missing tracked decisions {decisions_path}")
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    missing = [
        f"{rec['Year']}-q{rec['Question']}"
        for rec in records
        if f"{rec['Year']}-q{rec['Question']}" not in decisions
    ]
    if missing:
        raise SystemExit(
            f"--replay: {decisions_path} has no decision for {len(missing)} question(s): "
            + ", ".join(missing[:20])
        )
    print(f"Replaying tracked decisions from {decisions_path}")
    rows = []
    for rec in records:
        d = decisions[f"{rec['Year']}-q{rec['Question']}"]
        sections = [int(s) for s in d["sections"]]
        if not sections or any(s not in SECTION_BY_NUM for s in sections):
            raise SystemExit(
                f"--replay: bad sections {d['sections']!r} for {rec['Year']}-q{rec['Question']}"
            )
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
    return rows


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
    touched_years = set(args.years) if args.years and args.limit is None else None
    touched_keys = (
        {(str(record["Year"]), int(record["Question"])) for record in records}
        if args.limit is not None
        else None
    )

    if getattr(args, "replay", False):
        write_outputs(
            replay_rows(records, METADATA_LQ / "llm_classifications.json"),
            touched_years,
            touched_keys,
            write_decisions=False,
        )
        return

    if args.from_json:
        decisions = json.loads(args.from_json.read_text(encoding="utf-8"))
        rows = []
        for rec in records:
            key = f"{rec['Year']}-q{rec['Question']}"
            d = decisions[key]
            sections, reason = finalize_sections(rec, d["sections"], d.get("reason", ""))
            rows.append(
                {
                    "Year": rec["Year"],
                    "Question": rec["Question"],
                    "Primary": sections[0],
                    "AllSections": ";".join(str(s) for s in sections),
                    "Reason": reason,
                    "PNG": rec["PNG"],
                    "AnswerPNG": rec["AnswerPNG"],
                }
            )
        write_outputs(rows, touched_years, touched_keys)
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
    write_outputs(rows, touched_years, touched_keys)


if __name__ == "__main__":
    main()
