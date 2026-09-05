#!/usr/bin/env python3
"""Keyword-classify LQ crops into the 27 MC sections (no LLM required).

Uses OCR under classified/lq/ocr_cache (or creates it). Writes the same
outputs as classify_lq_llm.py so build_lq_lavish_review.py can run.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from PIL import Image

from classify_mc_llm import SECTION_BY_NUM, SECTIONS, BOOK_NAMES, year_key

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_LQ = ROOT / "output" / "lq"
CLASSIFIED_LQ = ROOT / "classified" / "lq"
OCR_CACHE = CLASSIFIED_LQ / "ocr_cache"

# Weighted keyword cues per section (lowercase). Prefer distinctive phrases.
SECTION_KEYWORDS: dict[int, list[tuple[str, float]]] = {
    1: [("conduction", 3), ("convection", 3), ("radiation", 2), ("thermal equilibrium", 3), ("thermometer", 2), ("heat transfer", 3), ("thermal bag", 2), ("poor conductor", 2)],
    2: [("specific heat", 4), ("heat capacity", 4), ("calorimeter", 3), ("temperature rise", 2), ("mixing", 1)],
    3: [("latent heat", 4), ("fusion", 2), ("vaporization", 3), ("boiling", 2), ("melting", 2), ("evaporation", 2), ("steam", 1), ("ice at 0", 2)],
    4: [("ideal gas", 4), ("pV =", 3), ("kinetic theory", 3), ("absolute temperature", 2), ("mole", 1), ("pressure of the gas", 2), ("boyle", 3), ("charles", 2)],
    5: [("velocity-time", 3), ("acceleration", 1), ("free fall", 2), ("displacement-time", 3), ("uniformly accelerated", 3)],
    6: [("newton", 2), ("friction", 2), ("resultant force", 3), ("f = ma", 3), ("free-body", 2)],
    7: [("moment", 3), ("torque", 3), ("centre of gravity", 3), ("equilibrium", 2), ("pulley", 2), ("two forces", 1)],
    8: [("kinetic energy", 3), ("potential energy", 3), ("work done", 3), ("power", 1), ("efficiency", 2), ("mechanical energy", 3)],
    9: [("momentum", 4), ("impulse", 3), ("collision", 3), ("conservation of momentum", 4)],
    10: [("projectile", 4), ("horizontal range", 3), ("projected", 2), ("angle of projection", 3)],
    11: [("centripetal", 4), ("circular motion", 3), ("angular speed", 2), ("period of revolution", 2)],
    12: [("gravitation", 3), ("gravitational", 2), ("orbit", 2), ("satellite", 2), ("g-field", 2)],
    13: [("wavelength", 2), ("transverse", 2), ("longitudinal", 2), ("wave speed", 2), ("amplitude", 1), ("frequency", 1)],
    14: [("diffraction", 4), ("refraction of water", 3), ("wavefront", 3), ("ripple tank", 3)],
    15: [("interference", 4), ("stationary wave", 4), ("standing wave", 4), ("young", 2), ("beats", 3), ("node", 2), ("antinode", 2)],
    16: [("ultrasound", 3), ("doppler", 3), ("electromagnetic spectrum", 3), ("sound wave", 2)],
    17: [("plane mirror", 3), ("reflection of light", 3), ("periscope", 3), ("image in a mirror", 2)],
    18: [("snell", 3), ("refractive index", 4), ("total internal reflection", 4), ("critical angle", 3), ("apparent depth", 3)],
    19: [("lens", 3), ("focal length", 4), ("convex lens", 3), ("concave lens", 3), ("magnification", 2), ("object distance", 2)],
    20: [("electrostatic", 3), ("coulomb", 3), ("point charge", 2), ("electric field", 2), ("potential difference due", 1)],
    21: [("ohm", 2), ("series", 1), ("parallel", 1), ("resistance", 2), ("circuit", 1), ("kilowatt", 2), ("electrical power", 2)],
    22: [("mains", 3), ("fuse", 2), ("domestic", 3), ("live wire", 3), ("neutral wire", 3), ("earth wire", 3), ("a.c.", 2)],
    23: [("electromagnet", 3), ("magnetic field", 2), ("motor effect", 3), ("force on a current", 3), ("solenoid", 2)],
    24: [("induction", 3), ("faraday", 3), ("lenz", 3), ("transformer", 3), ("induced emf", 4), ("generator", 2)],
    25: [("alpha", 2), ("beta", 2), ("gamma", 2), ("radioactive", 3), ("ionization", 2), ("geiger", 3)],
    26: [("half-life", 4), ("half life", 4), ("activity", 2), ("decay constant", 3), ("tracer", 2)],
    27: [("fission", 4), ("fusion", 3), ("binding energy", 4), ("mass defect", 4), ("nuclear energy", 3)],
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", nargs="*", default=None)
    return p.parse_args()


def ocr_png(path: Path, cache_path: Path) -> str:
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    image = Image.open(path).convert("RGB")
    w, h = image.size
    band = image.crop((0, 0, w, min(h, max(900, int(h * 0.45)))))
    buf = io.BytesIO()
    band.save(buf, format="PNG")
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


def score_sections(text: str) -> list[tuple[int, float]]:
    low = text.lower()
    scores: dict[int, float] = defaultdict(float)
    reasons: dict[int, list[str]] = defaultdict(list)
    for sec, kws in SECTION_KEYWORDS.items():
        for phrase, weight in kws:
            if phrase in low:
                scores[sec] += weight
                reasons[sec].append(phrase)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked


def classify_text(text: str) -> tuple[list[int], str]:
    ranked = score_sections(text)
    if not ranked or ranked[0][1] < 2:
        return [5], "fallback: weak keyword match -> Motion"
    primary = ranked[0][0]
    sections = [primary]
    if len(ranked) > 1 and ranked[1][1] >= 3 and ranked[1][1] >= ranked[0][1] * 0.7:
        sections.append(ranked[1][0])
    hits = [p for p, _w in SECTION_KEYWORDS[primary] if p in text.lower()][:4]
    reason = f"keywords: {', '.join(hits) if hits else 'score-based'}"
    return sections, reason


def main() -> None:
    args = parse_args()
    CLASSIFIED_LQ.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    for _n, book, folder, _name in SECTIONS:
        (CLASSIFIED_LQ / book / folder).mkdir(parents=True, exist_ok=True)
        for old in (CLASSIFIED_LQ / book / folder).glob("*.png"):
            old.unlink()

    jobs = []
    for year_dir in sorted(OUTPUT_LQ.iterdir(), key=lambda p: year_key(p.name)):
        if not year_dir.is_dir():
            continue
        if args.years and year_dir.name not in args.years:
            continue
        for png in sorted(year_dir.glob("q*.png"), key=lambda p: int(p.stem[1:])):
            jobs.append((year_dir.name, png, int(png.stem[1:])))

    rows = []
    decisions = {}
    for year, png, qn in jobs:
        text = ocr_png(png, OCR_CACHE / year / f"q{qn}.txt")
        sections, reason = classify_text(text)
        preview = " ".join(text.split())[:220]
        ans = f"output/lq/{year}/ans/q{qn}.png"
        row = {
            "Year": year,
            "Question": qn,
            "Primary": sections[0],
            "AllSections": ";".join(str(s) for s in sections),
            "Reason": reason,
            "PNG": f"output/lq/{year}/q{qn}.png",
            "AnswerPNG": ans,
        }
        rows.append(row)
        decisions[f"{year}-q{qn}"] = {"sections": sections, "reason": reason}
        book, folder, _name = SECTION_BY_NUM[sections[0]]
        dest = CLASSIFIED_LQ / book / folder / f"{year}-q{qn}.png"
        shutil.copy2(png, dest)
        ans_path = ROOT / ans
        if ans_path.is_file():
            shutil.copy2(ans_path, CLASSIFIED_LQ / book / folder / f"{year}-q{qn}-ans.png")
        for sec in sections[1:]:
            book, folder, _name = SECTION_BY_NUM[sec]
            shutil.copy2(png, CLASSIFIED_LQ / book / folder / f"{year}-q{qn}.png")
            if ans_path.is_file():
                shutil.copy2(ans_path, CLASSIFIED_LQ / book / folder / f"{year}-q{qn}-ans.png")

    csv_path = CLASSIFIED_LQ / "classification.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Year", "Question", "Primary", "AllSections", "Reason", "PNG", "AnswerPNG"],
        )
        writer.writeheader()
        writer.writerows(rows)
    (CLASSIFIED_LQ / "llm_classifications.json").write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    by = defaultdict(int)
    for r in rows:
        by[int(r["Primary"])] += 1
    print(f"Wrote {csv_path} ({len(rows)} LQ)")
    for n, _b, _f, name in SECTIONS:
        if by[n]:
            print(f"  S{n:02d} {name}: {by[n]}")


if __name__ == "__main__":
    main()
