#!/usr/bin/env python3
"""Extract Paper 1B (LQ) candidate-performance notes into JSON.

Reads OCR markdown under paper/performance/<year> performance.md and writes
classified/lq/candidate_performance.json keyed by year -> question -> text.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF_DIR = ROOT / "paper" / "performance"
OUT = ROOT / "classified" / "lq" / "candidate_performance.json"

SECTION_B_RE = re.compile(
    r"###\s*Section B\b.*?(?=^##\s+Paper 2\b|\Z)",
    re.I | re.M | re.S,
)
QUESTION_RE = re.compile(
    r"^####\s*Question\s+(\d+)\s*\n+(.*?)(?=^####\s*Question\s+\d+|\Z)",
    re.I | re.M | re.S,
)
PERF_LINE_RE = re.compile(
    r"\*\*Performance in General:\*\*\s*(.+?)(?=\n\n|\n\*\*|\Z)",
    re.I | re.S,
)
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_heading_questions(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for qm in QUESTION_RE.finditer(body):
        qn = qm.group(1)
        block = qm.group(2).strip()
        pm = PERF_LINE_RE.search(block)
        if pm:
            note = _clean(pm.group(1))
        else:
            note = re.sub(r"\*\*Popularity.*?\*\*:?\s*[^\n]*", "", block)
            note = _clean(note)
        if note:
            out[qn] = note
    return out


def parse_table_questions(body: str) -> dict[str, str]:
    """Parse | N | performance text | rows (ignore Popularity columns)."""
    out: dict[str, str] = {}
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if not cells[0].isdigit():
            continue
        # Two-col: Question Number | Performance
        # Three-col (Paper 2 style): Question | Popularity | Performance - skip here
        # For Section B LQ tables we only want 2-col rows.
        if len(cells) == 2:
            note = _clean(cells[1])
        elif len(cells) == 3 and not re.search(r"\d", cells[1]):
            # Rare: popularity blank / text in middle
            note = _clean(cells[2])
        elif len(cells) == 3 and cells[1].replace("%", "").replace(".", "").isdigit():
            # Paper 2 style with popularity - do not use in Section B parse
            continue
        else:
            note = _clean(cells[-1])
        if note and note.lower() not in {"performance in general", "---"}:
            out[cells[0]] = note
    return out


def parse_year_md(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = SECTION_B_RE.search(text)
    if not m:
        return {}
    body = m.group(0)
    out = parse_heading_questions(body)
    if not out:
        out = parse_table_questions(body)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", default=None)
    args = parser.parse_args()

    data: dict[str, dict[str, str]] = {}
    paths = sorted(PERF_DIR.glob("* performance.md"))
    for path in paths:
        m = re.match(r"(20\d{2})", path.name)
        if not m:
            continue
        year = m.group(1)
        if args.years and year not in args.years:
            continue
        notes = parse_year_md(path)
        if notes:
            data[year] = notes
            print(f"{year}: {len(notes)} LQ performance notes")
        else:
            print(f"{year}: no Section B notes found")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({sum(len(v) for v in data.values())} notes)")


if __name__ == "__main__":
    main()
