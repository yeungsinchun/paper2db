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
    4: [
        ("ideal gas", 5),
        ("pV =", 4),
        ("pV=", 4),
        ("kinetic theory", 4),
        ("gas molecules", 4),
        ("monatomic gas", 4),
        ("r.m.s.", 4),
        ("rms speed", 4),
        ("avogadro", 3),
        ("number of molecules", 3),
        ("absolute temperature", 2),
        ("pressure of the gas", 3),
        ("boyle", 3),
        ("charles", 2),
        ("diffusion", 2),
        ("mole", 1),
    ],
    5: [
        ("velocity-time", 4),
        ("displacement-time", 4),
        ("uniformly accelerated", 3),
        ("free fall", 2),
        ("v-t graph", 3),
        ("s-t graph", 3),
    ],
    6: [
        ("newton", 3),
        ("friction", 3),
        ("resultant force", 4),
        ("f = ma", 4),
        ("free-body", 4),
        ("free body", 4),
        ("tension", 3),
        ("normal reaction", 3),
        ("protractor", 3),
        ("metal ball with a hook", 4),
        ("measure the acceleration", 3),
        ("accelerating frame", 3),
    ],
    7: [("moment", 3), ("torque", 3), ("centre of gravity", 3), ("equilibrium", 2), ("pulley", 2), ("two forces", 1)],
    8: [
        ("mechanical energy", 4),
        ("potential energy", 4),
        ("gravitational potential energy", 4),
        ("gravitational potential", 3),
        ("kinetic energy", 2),
        ("work done", 4),
        ("work-energy", 4),
        ("conservation of energy", 4),
        ("conservation of mechanical energy", 5),
        ("stopping distance", 4),
        ("height of release", 3),
        ("efficiency", 2),
        ("power", 1),
    ],
    9: [("momentum", 4), ("impulse", 3), ("collision", 3), ("conservation of momentum", 4)],
    10: [("projectile", 4), ("horizontal range", 3), ("projected", 2), ("angle of projection", 3)],
    11: [("centripetal", 4), ("circular motion", 3), ("angular speed", 2), ("period of revolution", 2)],
    12: [
        ("gravitation", 3),
        ("gravitational field", 3),
        ("gravitational force", 3),
        ("orbit", 2),
        ("satellite", 2),
        ("g-field", 2),
        ("weightlessness", 3),
        ("newton's law of gravitation", 4),
    ],
    13: [("wavelength", 2), ("transverse", 2), ("longitudinal", 2), ("wave speed", 2), ("amplitude", 1), ("frequency", 1)],
    14: [("diffraction", 4), ("refraction of water", 3), ("wavefront", 3), ("ripple tank", 3)],
    15: [("interference", 4), ("stationary wave", 4), ("standing wave", 4), ("young", 2), ("beats", 3), ("node", 2), ("antinode", 2)],
    16: [
        ("ultrasound", 3),
        ("doppler", 3),
        ("electromagnetic spectrum", 3),
        ("sound wave", 3),
        ("speed of sound", 4),
        ("microphone", 2),
    ],
    17: [("plane mirror", 3), ("reflection of light", 3), ("periscope", 3), ("image in a mirror", 2)],
    18: [("snell", 3), ("refractive index", 4), ("total internal reflection", 4), ("critical angle", 3), ("apparent depth", 3)],
    19: [("lens", 3), ("focal length", 4), ("convex lens", 3), ("concave lens", 3), ("magnification", 2), ("object distance", 2)],
    20: [("electrostatic", 3), ("coulomb", 3), ("point charge", 2), ("electric field", 2), ("potential difference due", 1)],
    21: [("ohm", 2), ("series", 1), ("parallel", 1), ("resistance", 2), ("circuit", 1), ("kilowatt", 2), ("electrical power", 2)],
    22: [("mains", 3), ("fuse", 2), ("domestic", 3), ("live wire", 3), ("neutral wire", 3), ("earth wire", 3), ("a.c.", 2)],
    23: [("electromagnet", 3), ("magnetic field", 2), ("motor effect", 3), ("force on a current", 3), ("solenoid", 2)],
    24: [("induction", 3), ("faraday", 3), ("lenz", 3), ("transformer", 3), ("induced emf", 4), ("generator", 2)],
    25: [
        ("alpha", 2),
        ("beta", 2),
        ("gamma", 2),
        ("radioactive", 3),
        ("ionization", 2),
        ("geiger", 3),
        ("a-decay", 4),
        ("α-decay", 4),
        ("alpha-decay", 4),
        ("beta-decay", 4),
        ("β-decay", 4),
        ("nuclear equation", 3),
    ],
    26: [("half-life", 4), ("half life", 4), ("activity", 2), ("decay constant", 3), ("tracer", 2)],
    27: [
        ("fission", 4),
        ("fusion", 3),
        ("binding energy", 4),
        ("mass defect", 4),
        ("nuclear energy", 3),
        ("energy released in the decay", 5),
        ("energy released", 3),
        ("nuclear equation", 4),
        ("mev", 2),
        ("radium", 2),
        ("uranium", 2),
        ("nucleus", 1),
    ],
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
    # Use most of the page - LQ stems often put key terms mid-question.
    band = image.crop((0, 0, w, min(h, max(1200, int(h * 0.7)))))
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


def phrase_hits(text_low: str, phrase: str) -> bool:
    """Match keyword phrases; use word boundaries for single tokens.

    Prevents 'mains' matching inside 'remains', 'fuse' inside 'confused', etc.
    """
    phrase = phrase.lower()
    if " " in phrase or any(c in phrase for c in ".=/-"):
        return phrase in text_low
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text_low))


def score_sections(text: str) -> list[tuple[int, float]]:
    low = text.lower()
    scores: dict[int, float] = defaultdict(float)
    for sec, kws in SECTION_KEYWORDS.items():
        for phrase, weight in kws:
            if phrase_hits(low, phrase):
                scores[sec] += weight

    # Combo boosts for setups the single-token list under-weights.
    # Pendulum + protractor to measure vehicle acceleration -> Force (S6), not Motion.
    if (
        ("protractor" in low or "metal ball" in low or "light string" in low)
        and "acceleration" in low
        and ("train" in low or "measure the acceleration" in low or "diagram" in low)
    ):
        scores[6] += 5
    if "inclined plane" in low and ("force" in low or "friction" in low or "motion sensor" in low):
        scores[6] += 2
    if "lift" in low and ("balance" in low or "apparent weight" in low or "reading" in low):
        scores[6] += 3
    if "stopping distance" in low or ("height of release" in low and "skier" in low):
        scores[8] += 5
    if "bullet" in low and ("trolley" in low or "plasticine" in low or "momentum" in low):
        scores[9] += 5
    if "speed of sound" in low or ("microphone" in low and "timer" in low):
        scores[16] += 5
    if "braking" in low or "brake pad" in low or "brake pads" in low:
        scores[6] += 3
        if "energy" in low or "work" in low or "heat" in low:
            scores[8] += 2
    if ("velocity" in low or "velocities" in low) and "graph" in low and "time" in low:
        scores[5] += 4
    if "describe the motion" in low and ("car" in low or "velocity" in low):
        scores[5] += 3
    # Molecular KE belongs to gas law, not work-energy.
    if ("gas molecule" in low or "kinetic theory" in low or "monatomic" in low) and scores[8]:
        scores[8] = max(0.0, scores[8] - 2.0)

    ranked = sorted((kv for kv in scores.items() if kv[1] > 0), key=lambda kv: (-kv[1], kv[0]))
    return ranked


def classify_text(text: str) -> tuple[list[int], str]:
    """Classify into curriculum sections.

    Curriculum rule: if a question needs both Sx and Sy with x < y, primary is Sy
    (students meet the later topic later). List both only when a significant part
    is answerable with Sx alone (approximated by a strong exclusive lower-section score).
    """
    low = text.lower()
    ranked = score_sections(text)
    if not ranked or ranked[0][1] < 2:
        if any(
            phrase_hits(low, p)
            for p in (
                "force",
                "tension",
                "friction",
                "newton",
                "protractor",
                "free-body",
                "free body",
            )
        ):
            return [6], "fallback: force cues"
        if any(phrase_hits(low, p) for p in ("wavelength", "wavefront", "interference", "diffraction")):
            return [13], "fallback: wave cues"
        if any(phrase_hits(low, p) for p in ("gas", "pressure", "mole", "kinetic theory")):
            return [4], "fallback: gas cues"
        return [5], "fallback: weak keyword match -> Motion"

    best_score = ranked[0][1]
    # Contenders: strong enough relative to the top score.
    contenders = [sec for sec, sc in ranked if sc >= 2 and sc >= best_score * 0.55]
    if not contenders:
        contenders = [ranked[0][0]]

    # Primary = largest section number among contenders (later in syllabus).
    primary = max(contenders)
    sections = [primary]

    # Dual-list a lower Sx only when it still scores strongly on its own.
    primary_score = next(sc for sec, sc in ranked if sec == primary)
    for sec, sc in ranked:
        if sec >= primary:
            continue
        if sc >= 4 and sc >= primary_score * 0.7:
            sections.append(sec)

    hits = [p for p, _w in SECTION_KEYWORDS[primary] if phrase_hits(low, p)][:4]
    reason = f"keywords: {', '.join(hits) if hits else 'score-based'}"
    if len(sections) > 1:
        reason += f"; dual with S{sections[1]} (significant earlier part)"
    elif len(contenders) > 1:
        reason += f"; primary=max({','.join('S'+str(s) for s in sorted(contenders))})"
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

    # Detailed review file at repo classified/lq_classification.json
    perf: dict = {}
    perf_path = CLASSIFIED_LQ / "candidate_performance.json"
    if perf_path.is_file():
        perf = json.loads(perf_path.read_text(encoding="utf-8"))
    sec_name = {n: name for n, _b, _f, name in SECTIONS}
    detailed = []
    for r in rows:
        year_raw = r["Year"]
        year = int(year_raw) if str(year_raw).isdigit() else year_raw
        q = int(r["Question"])
        primary = int(r["Primary"])
        all_secs = [int(x) for x in r["AllSections"].split(";") if x]
        book, _folder, pname = SECTION_BY_NUM[primary]
        detailed.append(
            {
                "Year": year,
                "Question": q,
                "PrimarySection": primary,
                "PrimaryName": pname,
                "PrimaryBook": BOOK_NAMES.get(book, book),
                "AllSections": ";".join(str(s) for s in all_secs),
                "AllSectionNames": "; ".join(sec_name[s] for s in all_secs),
                "Reason": r.get("Reason") or "",
                "PNG": r.get("PNG") or "",
                "AnswerPNG": r.get("AnswerPNG") or "",
                "CandidatePerformance": (perf.get(str(year_raw)) or {}).get(str(q), ""),
            }
        )
    lq_json = ROOT / "classified" / "lq_classification.json"
    lq_json.write_text(json.dumps(detailed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lq_csv = ROOT / "classified" / "lq_classification.csv"
    with lq_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "Year",
                "Question",
                "PrimarySection",
                "PrimaryName",
                "PrimaryBook",
                "AllSections",
                "AllSectionNames",
                "Reason",
                "PNG",
                "AnswerPNG",
                "CandidatePerformance",
            ],
        )
        writer.writeheader()
        writer.writerows(detailed)

    by = defaultdict(int)
    for r in rows:
        by[int(r["Primary"])] += 1
    print(f"Wrote {csv_path} ({len(rows)} LQ)")
    print(f"Wrote {lq_json}")
    print(f"Wrote {lq_csv}")
    for n, _b, _f, name in SECTIONS:
        if by[n]:
            print(f"  S{n:02d} {name}: {by[n]}")


if __name__ == "__main__":
    main()
