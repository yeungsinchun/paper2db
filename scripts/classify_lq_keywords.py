#!/usr/bin/env python3
"""Keyword-classify LQ crops into the 27 MC sections (no LLM required).

OCRs each whole page stack (cache classified/lq/ocr_cache/<year>/qN.<w>x<h>.txt,
keyed by PNG size so a re-crop is re-read). Book 5 questions list every
radioactivity section they test (classify_book5); other books keep the
single-primary scoring. Writes:
  classified/lq/classification.csv
  classified/lq/llm_classifications.json
  classified/lq/<book>/<section>/ year-qN.png (+ optional answer copy)
  classified/lq_classification.csv
  classified/lq_classification.json

Top-level classified/lq_classification.* is the split naming contract for LQ;
nested classified/lq/classification.csv feeds build_lq_lavish_review.py.
--years merges into those existing nested/top-level rows and only replaces
section PNG copies for the selected years.
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

Image.MAX_IMAGE_PIXELS = 250_000_000  # 2-page 4916px-wide stacks exceed the default bomb limit

from classify_mc_llm import SECTION_BY_NUM, SECTIONS, BOOK_NAMES, year_key

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_LQ = ROOT / "reconstructed" / "lq"
CLASSIFIED_LQ = ROOT / "classified" / "lq"
OCR_CACHE = CLASSIFIED_LQ / "ocr_cache"
OCR_MAX_WIDTH = 2500

# Book 5 gate: any of these marks a radioactivity / nuclear question. Generic
# words ("fusion" of ice, "activity" of a bungee jumper, "radiation" as heat
# transfer) deliberately stay out so they cannot pull other books here.
BOOK5_CONTEXT = (
    "radioactiv",
    "radioisotope",
    "radionuclide",
    "nuclide",
    "isotope",
    "half-life",
    "half life",
    "half-lives",
    "decay constant",
    "decay series",
    "nuclear equation",
    "nuclear reaction",
    "nuclear fusion",
    "nuclear fission",
    "fission",
    "undergoes decay",
    "decays to",
    "decays into",
    "decays by",
    "decays with",
    "undergoes a-decay",
    "undergoes α-decay",
    "undergoes alpha decay",
    "undergoes @ decay",
    "undergoes a decay",
    "background radiation",
)

# Book 5 cues, weighted by how strongly a phrase shows that a *part* tests the
# section (question wording scores 3-5; passing mentions score 1-2).
# A section is listed when its total reaches BOOK5_MIN[section].
BOOK5_CUES: dict[int, list[tuple[str, float]]] = {
    25: [
        # nature of alpha/beta/gamma, decay equations, detectors, safety
        ("nuclear equation", 4),
        ("equation for the decay", 4),
        ("equation to represent the decay", 4),
        ("kind of decay", 4),
        ("kind of radiation", 4),
        ("type of radiation", 4),
        ("type(s) of radiation", 4),
        ("types of radiation", 4),
        ("radiation emitted", 3),
        ("are emitted", 3),
        ("penetrating", 4),
        ("penetration", 4),
        ("penetrate", 4),
        ("ionizing", 4),
        ("ionising", 4),
        ("ionization", 3),
        ("ionisation", 3),
        ("background radiation", 4),
        ("spark counter", 4),
        ("geiger", 4),
        ("gm tube", 4),
        ("cloud chamber", 4),
        ("photographic film", 3),
        ("detected", 2),
        ("detects", 2),
        ("radiation dose", 2),
        ("radiation exposure", 2),
        ("protective", 2),
        ("bare hands", 3),
        ("handled", 2),
        ("casing", 2),
        ("shield", 3),
        ("shielded", 3),
        ("shielding", 3),
        ("stopped by", 3),
        ("absorbed by", 3),
        ("safe to", 3),
        ("it is safe", 3),
        ("decay series", 2),
        ("decay chain", 2),
        ("a-particle", 2),
        ("α-particle", 2),
        ("a particle", 2),
        ("@ particle", 2),
        ("α particle", 2),
        ("alpha particle", 2),
        ("b-particle", 2),
        ("β-particle", 2),
        ("f-particle", 2),
        ("beta particle", 2),
        ("gamma", 2),
        ("isotope", 1),
        ("stable", 1),
    ],
    26: [
        # rate of decay and uses
        ("half-life", 4),
        ("half life", 4),
        ("half-lives", 4),
        ("decay constant", 4),
        ("dating", 3),
        ("age of the", 3),
        ("count rate", 3),
        ("undecayed", 2),
        ("activity", 2),
        ("bq", 2),
        ("tracer", 3),
    ],
    27: [
        # nuclear energy: fission/fusion, mass-energy
        ("fission", 5),
        ("fusion", 5),
        ("binding energy", 5),
        ("mass defect", 5),
        ("energy released in the decay", 5),
        ("chain reaction", 4),
        ("nuclear reaction", 4),
        ("nuclear energy", 3),
        ("bombard", 3),
        ("bombarded", 3),
        ("bombards", 3),
        ("energy released", 2),
        ("mev", 2),
    ],
}
BOOK5_MIN = {25: 4.0, 26: 4.0, 27: 5.0}

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
    # "moment" alone matches "at the moment shown"; keep the mechanics phrasings.
    7: [("moment of", 3), ("moments", 3), ("turning effect", 3), ("torque", 3), ("centre of gravity", 3), ("equilibrium", 2), ("pulley", 2), ("two forces", 1)],
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
        ("energy conversion", 3),
        ("efficiency", 2),
        ("power", 1),
    ],
    9: [("momentum", 4), ("impulse", 3), ("collision", 3), ("conservation of momentum", 4)],
    10: [("projectile", 4), ("horizontal range", 3), ("projected", 2), ("angle of projection", 3), ("time of flight", 3)],
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
    13: [("wavelength", 2), ("transverse", 2), ("longitudinal", 2), ("wave speed", 2), ("displacement-distance", 3), ("air particles", 2), ("amplitude", 1), ("frequency", 1)],
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
    21: [
        ("ohm", 2),
        ("series", 1),
        ("parallel", 1),
        ("resistance", 2),
        ("internal resistance", 4),
        ("circuit", 1),
        ("circuit diagram", 3),
        ("voltage across", 2),
        ("kilowatt", 2),
        ("electrical power", 2),
    ],
    22: [("mains", 3), ("fuse", 2), ("domestic", 3), ("live wire", 3), ("neutral wire", 3), ("earth wire", 3), ("a.c.", 2)],
    23: [("electromagnet", 3), ("magnetic field", 2), ("motor effect", 3), ("force on a current", 3), ("solenoid", 2)],
    24: [("induction", 3), ("faraday", 3), ("lenz", 3), ("transformer", 3), ("induced emf", 4), ("generator", 2)],
    # Book 5 (25-27) is resolved by classify_book5(); see BOOK5_CUES.
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", nargs="*", default=None)
    return p.parse_args()


def ocr_cache_path(png: Path, year: str, qn: int) -> Path:
    """Cache file for one whole-page stack, keyed by the PNG geometry.

    A re-cropped qN.png (new page range, new DPI) changes size and therefore
    gets a fresh OCR pass instead of silently reusing text from an older crop.
    """
    with Image.open(png) as image:
        width, height = image.size
    return OCR_CACHE / year / f"q{qn}.{width}x{height}.txt"


def ocr_png(path: Path, cache_path: Path) -> str:
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    image = Image.open(path).convert("L")
    w, h = image.size
    # OCR the whole page stack: later parts (e.g. a half-life sub-question at
    # the end of a radioactivity LQ) decide sections just as much as the stem.
    if w > OCR_MAX_WIDTH:
        image = image.resize((OCR_MAX_WIDTH, int(h * OCR_MAX_WIDTH / w)))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    text = result.stdout.decode("utf-8", errors="ignore")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in cache_path.parent.glob(f"{cache_path.name.split('.', 1)[0]}.*txt"):
        if stale != cache_path:
            stale.unlink()
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
    # "air resistance" is a mechanics phrase, not a circuit cue.
    if "air resistance" in low and scores[21]:
        scores[21] = max(0.0, scores[21] - 2.0)
    # Molecular KE belongs to gas law, not work-energy.
    if ("gas molecule" in low or "kinetic theory" in low or "monatomic" in low) and scores[8]:
        scores[8] = max(0.0, scores[8] - 2.0)

    ranked = sorted((kv for kv in scores.items() if kv[1] > 0), key=lambda kv: (-kv[1], kv[0]))
    return ranked


def is_book5(text_low: str) -> bool:
    return any(phrase_hits(text_low, p) for p in BOOK5_CONTEXT)


def score_book5(text: str) -> dict[int, float]:
    low = text.lower()
    scores = {sec: 0.0 for sec in BOOK5_CUES}
    for sec, cues in BOOK5_CUES.items():
        for phrase, weight in cues:
            if phrase_hits(low, phrase):
                scores[sec] += weight
    return scores


def classify_book5(text: str) -> tuple[list[int], str]:
    """Sections 25-27 for a radioactivity / nuclear question.

    Every section whose cues reach BOOK5_MIN is listed (a Book 5 LQ usually
    tests two of them, e.g. alpha penetration + activity from half-life);
    primary is the latest listed section per the curriculum rule.
    """
    low = text.lower()
    scores = score_book5(text)
    listed = [sec for sec, sc in scores.items() if sc >= BOOK5_MIN[sec]]
    if not listed:
        listed = [max(scores, key=lambda sec: (scores[sec], sec))]
    sections = sorted(listed, reverse=True)
    primary = sections[0]
    hits = [p for p, _w in BOOK5_CUES[primary] if phrase_hits(low, p)][:4]
    reason = f"book5 keywords: {', '.join(hits) if hits else 'score-based'}"
    for sec in sections[1:]:
        extra = [p for p, _w in BOOK5_CUES[sec] if phrase_hits(low, p)][:3]
        reason += f"; also S{sec} ({', '.join(extra)})"
    return sections, reason


def classify_text(text: str) -> tuple[list[int], str]:
    """Classify into curriculum sections.

    Curriculum rule: if a question needs both Sx and Sy with x < y, primary is Sy
    (students meet the later topic later). List both only when a significant part
    is answerable with Sx alone (approximated by a strong exclusive lower-section score).
    Book 5 (radioactivity) goes through classify_book5, which lists every
    tested section rather than only the primary.
    """
    low = text.lower()
    if is_book5(low):
        return classify_book5(text)
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


NESTED_CSV_FIELDS = [
    "Year",
    "Question",
    "Primary",
    "AllSections",
    "Reason",
    "PNG",
    "AnswerPNG",
]
TOP_CSV_FIELDS = [
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
]


def nested_row_key(row: dict) -> tuple[str, int]:
    return (str(row["Year"]), int(row["Question"]))


def merge_nested_rows(existing: list[dict], updates: list[dict]) -> list[dict]:
    by_key = {nested_row_key(row): row for row in existing}
    for row in updates:
        by_key[nested_row_key(row)] = row
    return sorted(
        by_key.values(),
        key=lambda row: (year_key(str(row["Year"])), int(row["Question"])),
    )


def clear_section_pngs(years: set[str] | None) -> None:
    for _n, book, folder, _name in SECTIONS:
        folder_path = CLASSIFIED_LQ / book / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        for old in folder_path.glob("*.png"):
            if years is None:
                old.unlink()
                continue
            year_part = old.name.split("-q", 1)[0]
            if year_part in years:
                old.unlink()


def build_detailed_rows(rows: list[dict], perf: dict) -> list[dict]:
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
    return detailed


def main() -> None:
    args = parse_args()
    CLASSIFIED_LQ.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    touched_years = set(args.years) if args.years else None
    clear_section_pngs(touched_years)

    jobs = []
    for year_dir in sorted(OUTPUT_LQ.iterdir(), key=lambda p: year_key(p.name)):
        if not year_dir.is_dir():
            continue
        if args.years and year_dir.name not in args.years:
            continue
        for png in sorted(year_dir.glob("q*.png"), key=lambda p: int(p.stem[1:])):
            jobs.append((year_dir.name, png, int(png.stem[1:])))

    new_rows = []
    new_decisions = {}
    for year, png, qn in jobs:
        text = ocr_png(png, ocr_cache_path(png, year, qn))
        sections, reason = classify_text(text)
        ans = f"reconstructed/lq/{year}/ans/q{qn}.png"
        row = {
            "Year": year,
            "Question": qn,
            "Primary": sections[0],
            "AllSections": ";".join(str(s) for s in sections),
            "Reason": reason,
            "PNG": f"reconstructed/lq/{year}/q{qn}.png",
            "AnswerPNG": ans,
        }
        new_rows.append(row)
        new_decisions[f"{year}-q{qn}"] = {"sections": sections, "reason": reason}
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
    decisions_path = CLASSIFIED_LQ / "llm_classifications.json"
    if touched_years is not None and csv_path.is_file():
        with csv_path.open(encoding="utf-8") as fh:
            existing_rows = list(csv.DictReader(fh))
        rows = merge_nested_rows(existing_rows, new_rows)
        existing_decisions = {}
        if decisions_path.is_file():
            existing_decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        decisions = {**existing_decisions, **new_decisions}
    else:
        rows = new_rows
        decisions = new_decisions

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=NESTED_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    decisions_path.write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    perf: dict = {}
    perf_path = CLASSIFIED_LQ / "candidate_performance.json"
    if perf_path.is_file():
        perf = json.loads(perf_path.read_text(encoding="utf-8"))
    detailed = build_detailed_rows(rows, perf)
    lq_json = ROOT / "classified" / "lq_classification.json"
    lq_json.write_text(json.dumps(detailed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lq_csv = ROOT / "classified" / "lq_classification.csv"
    with lq_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TOP_CSV_FIELDS)
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
