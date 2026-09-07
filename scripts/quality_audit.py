#!/usr/bin/env python3
"""Audit crop + classification quality for the paper2db pipeline.

Failure definitions (count toward the captain's <=5% manual-tuning budget):
  missing_crop       - classification row points at a missing question PNG
  missing_classified - no copy under classified/{mc,lq}/.../<year>_qN.png
  uncertain          - MC Uncertain=yes (needs human skim)
  tiny_crop          - crop width/height below usable threshold
  few_year_crops     - year folder has far fewer crops than a full paper
  override_tuned     - scripts/overrides_YYYY.json entry (human anchor tuning)

Not counted as failures (documented separately):
  missing_answer_png - LQ answer crop absent (often no ans PDF for that year)
  very_tall_lq       - LQ crops intentionally include answer lines (multi-page)

Exit code 0 always when writing a report; use --strict to exit 1 if the
manual-tuning rate exceeds --max-rate (default 0.05).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MC_CSV = ROOT / "classified" / "mc" / "classification.csv"
LQ_CSV = ROOT / "classified" / "lq" / "classification.csv"
OUT_JSON = ROOT / "classified" / "quality_audit.json"

BOOK_ORDER = {
    "Heat and Gases": 1,
    "Force and Motion": 2,
    "Wave Motion": 3,
    "Ray Optics": 3,
    "Electricity and Magnetism": 4,
    "Radioactivity and Nuclear Energy": 5,
}


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    return list(csv.DictReader(path.open(encoding="utf-8")))


def mc_years() -> list[str]:
    years: list[str] = []
    for directory in sorted((ROOT / "output").iterdir()):
        if not directory.is_dir():
            continue
        name = directory.name
        if name in {"lq"} or name.endswith("-intermediate"):
            continue
        if (directory / "q1.png").is_file():
            years.append(name)
    return years


def audit_mc_crops() -> dict:
    failures: list[dict] = []
    warnings: list[dict] = []
    heights: list[int] = []
    crop_count = 0
    for year in mc_years():
        pngs = sorted(
            (ROOT / "output" / year).glob("q*.png"),
            key=lambda path: int(path.stem[1:]),
        )
        if len(pngs) < 30:
            failures.append({"kind": "few_year_crops", "year": year, "n": len(pngs)})
        for png in pngs:
            with Image.open(png) as image:
                width, height = image.size
            heights.append(height)
            crop_count += 1
            if height < 60 or width < 200:
                failures.append(
                    {
                        "kind": "tiny_crop",
                        "year": year,
                        "q": png.stem,
                        "w": width,
                        "h": height,
                    }
                )
            elif height > 2200:
                warnings.append(
                    {
                        "kind": "very_tall_mc",
                        "year": year,
                        "q": png.stem,
                        "h": height,
                    }
                )
    return {
        "crop_count": crop_count,
        "years": len(mc_years()),
        "failures": failures,
        "warnings": warnings,
        "height_median": statistics.median(heights) if heights else None,
        "height_max": max(heights) if heights else None,
        "missing_combined_pdf": [
            year
            for year in mc_years()
            if not (ROOT / "output" / year / "combined.pdf").is_file()
        ],
    }


def audit_lq_crops() -> dict:
    failures: list[dict] = []
    warnings: list[dict] = []
    heights: list[int] = []
    crop_count = 0
    years = []
    for year_dir in sorted((ROOT / "output" / "lq").iterdir()):
        if not year_dir.is_dir():
            continue
        years.append(year_dir.name)
        pngs = sorted(
            year_dir.glob("q*.png"),
            key=lambda path: int(path.stem[1:]),
        )
        if len(pngs) < 8:
            failures.append(
                {"kind": "few_year_crops", "year": year_dir.name, "n": len(pngs)}
            )
        for png in pngs:
            with Image.open(png) as image:
                width, height = image.size
            heights.append(height)
            crop_count += 1
            if height < 80 or width < 200:
                failures.append(
                    {
                        "kind": "tiny_crop",
                        "year": year_dir.name,
                        "q": png.stem,
                        "w": width,
                        "h": height,
                    }
                )
            elif height > 5000:
                warnings.append(
                    {
                        "kind": "very_tall_lq",
                        "year": year_dir.name,
                        "q": png.stem,
                        "h": height,
                        "note": "often multi-page with answer lines; not a failure",
                    }
                )
    return {
        "crop_count": crop_count,
        "years": len(years),
        "failures": failures,
        "warnings": warnings,
        "height_median": statistics.median(heights) if heights else None,
        "height_max": max(heights) if heights else None,
        "missing_questions_pdf": [
            year
            for year in years
            if not (ROOT / "output" / "lq" / year / "questions.pdf").is_file()
        ],
    }


def audit_mc_classification(rows: list[dict[str, str]]) -> dict:
    uncertain = [
        {"year": row["Year"], "q": row["Question"], "section": row["PrimaryName"]}
        for row in rows
        if row.get("Uncertain", "").lower() in {"yes", "true", "1"}
    ]
    missing_crop = []
    for row in rows:
        png = ROOT / row["PNG"] if row.get("PNG") else None
        if png is None or not png.is_file():
            missing_crop.append(
                {"year": row["Year"], "q": row["Question"], "png": row.get("PNG", "")}
            )

    missing_classified = []
    for row in rows:
        matches = list(
            (ROOT / "classified" / "mc").rglob(f"{row['Year']}_q{row['Question']}.png")
        )
        if not matches:
            missing_classified.append({"year": row["Year"], "q": row["Question"]})

    by_year: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_year[row["Year"]].append(row)
    inversions = []
    for year, items in sorted(by_year.items()):
        items = sorted(items, key=lambda row: int(row["Question"]))
        books = [row["PrimaryBook"] for row in items]
        ranks = [BOOK_ORDER.get(book, 0) for book in books]
        for index in range(1, len(ranks)):
            if ranks[index] < ranks[index - 1] - 1:
                inversions.append(
                    {
                        "year": year,
                        "q_prev": items[index - 1]["Question"],
                        "book_prev": books[index - 1],
                        "q": items[index]["Question"],
                        "book": books[index],
                    }
                )

    return {
        "row_count": len(rows),
        "uncertain": uncertain,
        "missing_crop": missing_crop,
        "missing_classified": missing_classified,
        "book_order_inversions": inversions,
        "per_year": dict(Counter(row["Year"] for row in rows)),
    }


def audit_lq_classification(rows: list[dict[str, str]]) -> dict:
    missing_crop = []
    missing_classified = []
    missing_answer = []
    for row in rows:
        png = ROOT / row["PNG"] if row.get("PNG") else None
        if png is None or not png.is_file():
            missing_crop.append(
                {"year": row["Year"], "q": row["Question"], "png": row.get("PNG", "")}
            )
        matches = list(
            (ROOT / "classified" / "lq").rglob(f"{row['Year']}-q{row['Question']}.png")
        )
        if not matches:
            missing_classified.append({"year": row["Year"], "q": row["Question"]})
        answer = row.get("AnswerPNG") or ""
        answer_path = ROOT / answer if answer else None
        if not answer or answer_path is None or not answer_path.is_file():
            missing_answer.append(
                {
                    "year": row["Year"],
                    "q": row["Question"],
                    "answer": answer,
                }
            )
    return {
        "row_count": len(rows),
        "missing_crop": missing_crop,
        "missing_classified": missing_classified,
        "missing_answer_png": missing_answer,
        "per_year": dict(Counter(row["Year"] for row in rows)),
    }


def override_stats() -> dict:
    total = 0
    by_year: dict[str, int] = {}
    items: list[dict] = []
    for path in sorted((ROOT / "scripts").glob("overrides_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        year = path.stem.replace("overrides_", "")
        by_year[year] = len(payload)
        total += len(payload)
        for question in sorted(payload, key=lambda value: int(str(value))):
            items.append({"year": year, "q": str(question)})
    return {"total_questions": total, "by_year": by_year, "items": items}


def summarize(
    mc_crops: dict,
    lq_crops: dict,
    mc_class: dict,
    lq_class: dict,
    overrides: dict,
) -> dict:
    mc_failure_events = (
        list(mc_crops["failures"])
        + [{"kind": "uncertain", **item} for item in mc_class["uncertain"]]
        + [{"kind": "missing_crop", **item} for item in mc_class["missing_crop"]]
        + [
            {"kind": "missing_classified", **item}
            for item in mc_class["missing_classified"]
        ]
        + [
            {"kind": "override_tuned", "year": item["year"], "q": item["q"]}
            for item in overrides.get("items", [])
        ]
    )
    lq_failure_events = (
        list(lq_crops["failures"])
        + [{"kind": "missing_crop", **item} for item in lq_class["missing_crop"]]
        + [
            {"kind": "missing_classified", **item}
            for item in lq_class["missing_classified"]
        ]
    )

    mc_denom = max(mc_class["row_count"], mc_crops["crop_count"], 1)
    lq_denom = max(lq_class["row_count"], lq_crops["crop_count"], 1)
    mc_rate = len(mc_failure_events) / mc_denom
    lq_rate = len(lq_failure_events) / lq_denom
    combined_fail = len(mc_failure_events) + len(lq_failure_events)
    combined_denom = mc_denom + lq_denom
    combined_rate = combined_fail / combined_denom

    return {
        "failure_definitions": {
            "counted": [
                "missing_crop",
                "missing_classified",
                "uncertain",
                "tiny_crop",
                "few_year_crops",
                "override_tuned",
            ],
            "not_counted": [
                "missing_answer_png",
                "very_tall_lq",
                "book_order_inversion_warning",
            ],
        },
        "mc": {
            "questions": mc_denom,
            "failure_count": len(mc_failure_events),
            "manual_tuning_rate": round(mc_rate, 4),
            "failures": mc_failure_events,
            "book_order_inversions": mc_class["book_order_inversions"],
        },
        "lq": {
            "questions": lq_denom,
            "failure_count": len(lq_failure_events),
            "manual_tuning_rate": round(lq_rate, 4),
            "failures": lq_failure_events,
            "missing_answer_png_count": len(lq_class["missing_answer_png"]),
            "missing_answer_by_year": dict(
                Counter(item["year"] for item in lq_class["missing_answer_png"])
            ),
        },
        "combined": {
            "questions": combined_denom,
            "failure_count": combined_fail,
            "manual_tuning_rate": round(combined_rate, 4),
        },
        "overrides_historical": {
            "total_questions": overrides["total_questions"],
            "by_year": overrides["by_year"],
        },
        "passes_5pct_bar": combined_rate <= 0.05 and mc_rate <= 0.05 and lq_rate <= 0.05,
    }


def build_report() -> dict:
    mc_rows = load_csv(MC_CSV)
    lq_rows = load_csv(LQ_CSV)
    mc_crops = audit_mc_crops()
    lq_crops = audit_lq_crops()
    mc_class = audit_mc_classification(mc_rows)
    lq_class = audit_lq_classification(lq_rows)
    overrides = override_stats()
    summary = summarize(mc_crops, lq_crops, mc_class, lq_class, overrides)
    return {
        "mc_crops": mc_crops,
        "lq_crops": lq_crops,
        "mc_classification": {
            "row_count": mc_class["row_count"],
            "uncertain_count": len(mc_class["uncertain"]),
            "missing_crop_count": len(mc_class["missing_crop"]),
            "missing_classified_count": len(mc_class["missing_classified"]),
            "book_order_inversion_count": len(mc_class["book_order_inversions"]),
            "per_year": mc_class["per_year"],
            "uncertain": mc_class["uncertain"],
            "missing_crop": mc_class["missing_crop"],
            "missing_classified": mc_class["missing_classified"],
            "book_order_inversions": mc_class["book_order_inversions"],
        },
        "lq_classification": {
            "row_count": lq_class["row_count"],
            "missing_crop_count": len(lq_class["missing_crop"]),
            "missing_classified_count": len(lq_class["missing_classified"]),
            "missing_answer_png_count": len(lq_class["missing_answer_png"]),
            "per_year": lq_class["per_year"],
            "missing_crop": lq_class["missing_crop"],
            "missing_classified": lq_class["missing_classified"],
            "missing_answer_png": lq_class["missing_answer_png"],
        },
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUT_JSON,
        help="Write JSON report here (default: classified/quality_audit.json)",
    )
    parser.add_argument(
        "--max-rate",
        type=float,
        default=0.05,
        help="Manual-tuning rate ceiling for --strict (default 0.05)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if MC, LQ, or combined rate exceeds --max-rate",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(f"Wrote {args.output.relative_to(ROOT)}")
    print(
        f"MC  failures={summary['mc']['failure_count']}/"
        f"{summary['mc']['questions']} "
        f"({100 * summary['mc']['manual_tuning_rate']:.2f}%)"
    )
    print(
        f"LQ  failures={summary['lq']['failure_count']}/"
        f"{summary['lq']['questions']} "
        f"({100 * summary['lq']['manual_tuning_rate']:.2f}%)"
    )
    print(
        f"All failures={summary['combined']['failure_count']}/"
        f"{summary['combined']['questions']} "
        f"({100 * summary['combined']['manual_tuning_rate']:.2f}%)"
    )
    print(
        f"LQ missing answer crops (not counted): "
        f"{summary['lq']['missing_answer_png_count']}"
    )
    print(
        f"Override-tuned qs (counted toward budget): "
        f"{summary['overrides_historical']['total_questions']}"
    )
    print(f"Passes <=5% bar: {summary['passes_5pct_bar']}")
    if args.strict:
        rates = [
            summary["mc"]["manual_tuning_rate"],
            summary["lq"]["manual_tuning_rate"],
            summary["combined"]["manual_tuning_rate"],
        ]
        if any(rate > args.max_rate for rate in rates):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
