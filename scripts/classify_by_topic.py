#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify MC questions into chapters/subtopics and sync by-topic folders + notes MC banks.

Each question gets 1 topic, or 2 when a second topic scores close (cross-topic).
Never deletes existing teaching notes under ``notes/``.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "processed" / "MC"
JSON_PATH = PROCESSED / "mc_questions.json"
OUT_ROOT = ROOT.parent / "notes"
NOTES_SHARED = OUT_ROOT / "_notes-shared" / "preamble.tex"

# Secondary topic only when nearly tied with the primary (true cross-topic).
SECOND_RATIO = 0.88
SECOND_GAP = 4
SECOND_MIN = 26

CHAPTERS = [
    "Heat and Gases",
    "Force and Motion",
    "Wave Motion",
    "Electricity and Magnetism",
    "Radiation",
]

SUBTOPICS: dict[str, list[str]] = {
    "Heat and Gases": [
        "Temperature and thermal equilibrium",
        "Specific heat capacity",
        "Latent heat and change of state",
        "Heat transfer processes",
        "Gas laws",
        "Kinetic theory of gases",
        "Internal energy and thermodynamics",
    ],
    "Force and Motion": [
        "Kinematics",
        "Newtons laws and forces",
        "Moments and statics",
        "Momentum and collisions",
        "Work energy and power",
        "Projectile motion",
        "Circular motion",
        "Gravitation",
    ],
    "Wave Motion": [
        "Wave properties and propagation",
        "Reflection refraction diffraction interference",
        "Sound",
        "Light reflection and refraction",
        "Lenses and optical instruments",
        "Wave nature of light",
        "Electromagnetic spectrum",
    ],
    "Electricity and Magnetism": [
        "Electrostatics",
        "Electric circuits",
        "Electrical energy and domestic electricity",
        "Magnetic fields and motors",
        "Electromagnetic induction",
        "Alternating current and transformers",
    ],
    "Radiation": [
        "Atomic models",
        "Radioactivity and nuclear radiation",
        "Nuclear reactions and binding energy",
        "X-rays and radiation applications",
        "Photoelectric effect and photons",
    ],
}

RULES: list[tuple[str, str, list[str], int]] = []


def add(chapter: str, subtopic: str, keywords: list[str], weight: int = 10) -> None:
    RULES.append((chapter, subtopic, [k.lower() for k in keywords], weight))


# --- Radiation (high priority; distinctive) ---
add("Radiation", "Photoelectric effect and photons", [
    "photoelectric", "work function", "photon", "einstein", "threshold frequency",
    "electron-volt", "stopping potential",
], 40)
add("Radiation", "X-rays and radiation applications", [
    "x-ray", "x ray", "xray", "ct scan", "radiograph", "bremsstrahlung",
], 38)
add("Radiation", "Nuclear reactions and binding energy", [
    "nuclear reaction", "binding energy", "fission", "fusion", "mass defect",
    "bombards", "bombard", "emitted particle", "nuclear equation",
], 36)
add("Radiation", "Radioactivity and nuclear radiation", [
    "radioactive", "radioactivity", "half-life", "half life", "alpha", "beta",
    "gamma", "decay", "nuclide", "isotope", "geiger", "activity of",
    "background radiation", "count rate", "becquerel",
], 34)
add("Radiation", "Atomic models", [
    "rutherford", "bohr", "atomic model", "nucleus of an atom", "orbiting electron",
    "energy level", "spectral line", "thompson", "plum pudding",
], 32)

# --- Electricity & Magnetism ---
add("Electricity and Magnetism", "Alternating current and transformers", [
    "transformer", "alternating current", "a.c.", "ac supply", "rms", "mains",
    "turn ratio", "primary coil", "secondary coil",
], 30)
add("Electricity and Magnetism", "Electromagnetic induction", [
    "induced emf", "induced e.m.f", "electromagnetic induction", "faraday",
    "lenz", "magnetic flux", "flux linkage", "generator", "dynamo",
    "changing magnetic", "coil is moved",
], 28)
add("Electricity and Magnetism", "Magnetic fields and motors", [
    "magnetic field", "magnet", "solenoid", "electromagnet", "motor",
    "force on a current", "lorentz", "cathode ray", "right-hand grip",
    "Fleming", "compass", "soft iron",
], 26)
add("Electricity and Magnetism", "Electrostatics", [
    "electrostatic", "coulomb", "point charge", "electric field", "electric potential",
    "charged sphere", "insulator becomes charged", "attract", "repel",
    "van de graaff", "electric force between", "permittivity",
], 24)
add("Electricity and Magnetism", "Electrical energy and domestic electricity", [
    "kilowatt", "kwh", "fuse", "earthing", "live wire", "neutral wire",
    "household", "domestic", "power rating", "electricity bill", "circuit breaker",
], 22)
add("Electricity and Magnetism", "Electric circuits", [
    "resistor", "resistance", "ohm", "current", "ammeter", "voltmeter",
    "circuit", "battery", "emf", "e.m.f", "kirchhoff", "series", "parallel",
    "potentiometer", "rheostat", "filament", "diode", "led", "cell is",
], 20)

# --- Wave Motion ---
add("Wave Motion", "Electromagnetic spectrum", [
    "electromagnetic spectrum", "infrared", "ultraviolet", "microwave",
    "radio wave", "visible light spectrum",
], 28)
add("Wave Motion", "Wave nature of light", [
    "diffraction of light", "interference of light", "young", "double slit",
    "polarization", "polarisation", "coherent", "path difference",
    "diffraction grating", "lines per mm", "line per mm", "laser beam",
    "slit width", "bright spot", "first-order", "second-order",
    "pattern on the screen", "pattern on the", "fringe spacing",
], 26)
add("Wave Motion", "Lenses and optical instruments", [
    "lens", "focal length", "convex lens", "concave lens", "magnification",
    "real image", "virtual image", "optical instrument", "microscope", "telescope",
], 24)
add("Wave Motion", "Light reflection and refraction", [
    "snell", "refractive index", "refraction of light", "reflection of light",
    "mirror", "critical angle", "total internal reflection", "ray diagram",
    "incident ray", "angle of incidence",
], 22)
add("Wave Motion", "Sound", [
    "sound", "ultrasound", "echo", "loudness", "pitch", "audible",
    "tuning fork", "decibel", "sonar",
], 22)
add("Wave Motion", "Reflection refraction diffraction interference", [
    "ripple tank", "diffraction", "interference", "superposition",
    "constructive", "destructive", "node", "antinode", "standing wave",
    "stationary wave", "water wave",
], 20)
add("Wave Motion", "Wave properties and propagation", [
    "wavelength", "frequency", "wave speed", "transverse", "longitudinal",
    "displacement-distance", "displacement-time", "amplitude", "period of the wave",
    "wavefront", "progressive wave",
], 18)

# --- Heat and Gases ---
add("Heat and Gases", "Kinetic theory of gases", [
    "kinetic theory", "rms speed", "root-mean-square", "molecular speed",
    "average kinetic", "molecules collide", "ideal gas molecules",
    "molecular motion", "exerts a pressure", "kinetic theory to explain",
], 26)
add("Heat and Gases", "Gas laws", [
    "ideal gas", "boyle", "charles", "pressure law", "pV =", "pv=",
    "gas constant", "absolute temperature", "kelvin", "gas is heated",
    "volume of the gas", "pressure of the gas", "fixed mass of gas",
    "gas pressure", "pressure in the balloon", "pressure in the",
    "pressure of a gas", "pressure of", "pressure inside", "pressure on its",
    "pressure and volume", "number of air molecules", "number of gas molecules",
    "mass of one mole of helium", "weather balloon", "helium gas", "helium",
    "trapped air", "pressurized air", "volume is doubled", "mass of gas",
    "gas tank", "amount of gas", "high-pressure steam", "high-pressure",
    "mol",
], 24)
add("Heat and Gases", "Latent heat and change of state", [
    "latent heat", "melting", "boiling", "fusion", "vaporization", "vapourisation",
    "evaporation", "condensation", "solidification", "change of state",
    "melting point", "boiling point",
], 24)
add("Heat and Gases", "Heat transfer processes", [
    "conduction", "convection", "radiation", "greenhouse", "emissivity",
    "black body", "heat transfer", "thermal conductivity", "insulator of heat",
], 22)
add("Heat and Gases", "Specific heat capacity", [
    "specific heat", "heat capacity", "temperature rise", "heated by",
    "calorimeter", "thermal capacity",
], 20)
add("Heat and Gases", "Internal energy and thermodynamics", [
    "internal energy", "first law", "thermodynamic", "work done by the gas",
    "adiabatic", "isothermal",
], 20)
add("Heat and Gases", "Temperature and thermal equilibrium", [
    "thermal equilibrium", "temperature", "thermometer", "celsius",
    "thermal contact", "hotter", "cooler", "heat flow",
], 14)

# --- Force and Motion ---
add("Force and Motion", "Gravitation", [
    "gravitation", "gravitational", "planet", "satellite", "orbit",
    "escape speed", "g-field", "newton's law of gravitation", "kepler",
    "earth's mass", "mars", "moon",
], 26)
add("Force and Motion", "Circular motion", [
    "circular motion", "centripetal", "angular speed", "uniform circular",
    "roundabout", "banked", "horizontal circle",
    "pendulum", "simple pendulum", "pendulum bob",
], 24)
add("Force and Motion", "Projectile motion", [
    "projectile", "projected horizontally", "projected at an angle",
    "trajectory", "range of", "time of flight",
], 24)
add("Force and Motion", "Momentum and collisions", [
    "momentum", "collision", "impulse", "elastic collision", "inelastic",
    "conserve momentum", "rate of change of momentum",
], 22)
add("Force and Motion", "Work energy and power", [
    "kinetic energy", "potential energy", "work done", "power",
    "mechanical energy", "efficiency", "joule", "watt",
], 20)
add("Force and Motion", "Moments and statics", [
    "moment", "torque", "pivot", "equilibrium", "centre of gravity",
    "center of mass", "couple", "lever",
], 20)
add("Force and Motion", "Newtons laws and forces", [
    "this is a misconception", "misconception because",
    "newton", "net force", "resultant force", "friction", "acceleration",
    "mass and weight", "action and reaction", "free body", "tension",
    "normal force", "f = ma", "f=ma", "upthrust", "action and reaction pair",
    "external force",
], 32)
add("Force and Motion", "Kinematics", [
    "velocity", "speed", "displacement", "distance-time", "velocity-time",
    "acceleration", "uniform motion", "ticker", "instantaneous",
], 12)


def keyword_hit(blob: str, keyword: str) -> bool:
    if " " in keyword or "." in keyword or "-" in keyword or "/" in keyword:
        return keyword in blob
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", blob) is not None


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s./=+*-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def score_topics(record: dict) -> list[tuple[str, str, int]]:
    blob = normalize(
        " ".join(
            [
                record.get("Question statement") or "",
                " ".join((record.get("Option") or {}).values()),
            ]
        )
    )
    scored: list[tuple[str, str, int]] = []
    for chapter, subtopic, keywords, weight in RULES:
        hits = sum(1 for kw in keywords if keyword_hit(blob, kw))
        if hits == 0:
            continue
        scored.append((chapter, subtopic, weight + hits * 3))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored


def classify_multi(record: dict) -> list[tuple[str, str, int]]:
    """Return 1 topic, or 2 when a second topic is competitively scored."""
    scored = score_topics(record)
    if not scored:
        return [("Force and Motion", "Kinematics", 0)]

    primary = scored[0]
    chosen = [primary]
    threshold = max(int(primary[2] * SECOND_RATIO), primary[2] - SECOND_GAP)
    for chapter, subtopic, score in scored[1:]:
        if (chapter, subtopic) == (primary[0], primary[1]):
            continue
        if score >= threshold and score >= SECOND_MIN:
            chosen.append((chapter, subtopic, score))
            break
    return chosen


def png_path(record: dict) -> Path:
    """Always prefer processed/MC/<year>/qN.png (never a by-topic copy)."""
    year = record["Year"]
    qn = record["Question"]
    canonical = PROCESSED / str(year) / f"q{qn}.png"
    if canonical.exists():
        return canonical
    rel = record.get("PNG") or ""
    alt = ROOT / rel if rel else None
    if alt is not None and alt.exists():
        return alt
    return canonical


def sort_key(record: dict):
    year = record["Year"]
    year_key = (0, int(year)) if isinstance(year, int) or str(year).isdigit() else (1, str(year))
    return (*year_key, int(record["Question"]))


def append_pngs(document: fitz.Document, paths: list[Path]) -> int:
    count = 0
    for path in paths:
        image = fitz.open(path)
        try:
            rect = image[0].rect
            page = document.new_page(width=rect.width, height=rect.height)
            page.insert_image(page.rect, filename=str(path))
            count += 1
        finally:
            image.close()
    return count


def dest_name(record: dict) -> str:
    return f"{record['Year']}_q{record['Question']}.png"


def latex_escape(text: str) -> str:
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("#", "\\#")
        .replace("_", "\\_")
    )


def write_mc_bank(chapter: str, subtopic: str, items: list[dict]) -> Path:
    """Write notes/mc-bank.tex containing every assigned MC for this topic."""
    notes_dir = OUT_ROOT / chapter / subtopic / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    bank = notes_dir / "mc-bank.tex"

    lines = [
        "% !TEX program = lualatex",
        "% AUTO-GENERATED by scripts/classify_by_topic.py — do not edit by hand.",
        "\\documentclass[11pt,a4paper]{article}",
        "\\input{../../../_notes-shared/preamble.tex}",
        "",
        "\\pagestyle{fancy}",
        "\\fancyhf{}",
        f"\\fancyhead[L]{{\\small {latex_escape(chapter)}}}",
        f"\\fancyhead[R]{{\\small {latex_escape(subtopic)}}}",
        "\\fancyfoot[C]{\\thepage}",
        "\\renewcommand{\\headrulewidth}{0.4pt}",
        "",
        f"\\title{{\\textbf{{{latex_escape(subtopic)}}}\\\\[0.35em]"
        f"\\large {latex_escape(chapter)} — MC bank}}",
        "\\date{}",
        "",
        "\\begin{document}",
        "\\maketitle",
        f"\\noindent All past-paper MC assigned to this topic "
        f"({len(items)} question{'s' if len(items) != 1 else ''}). "
        "Cross-topic questions may also appear in another topic's bank.",
        "\\bigskip",
        "",
    ]
    for record in items:
        year = record["Year"]
        qn = record["Question"]
        ans = record.get("Correct Option") or "?"
        pct = record.get("Correct percentage")
        pct_s = f", {pct}\\%" if pct is not None else ""
        png = f"../questions/{dest_name(record)}"
        lines.append(
            f"\\mcpractice{{{year}}}{{{qn}}}{{{png}}}"
        )
        lines.append(
            f"% key: {ans}{pct_s}"
        )
        lines.append("")

    lines.append("\\end{document}")
    lines.append("")
    bank.write_text("\n".join(lines), encoding="utf-8")
    return bank


def sync_topic_folder(chapter: str, subtopic: str, items: list[dict]) -> list[Path]:
    """Refresh questions/ + all.pdf; leave teaching notes untouched."""
    folder = OUT_ROOT / chapter / subtopic
    qdir = folder / "questions"
    qdir.mkdir(parents=True, exist_ok=True)

    # Remove previous synced PNGs (topic root leftovers + questions/)
    for path in list(folder.glob("*_q*.png")) + list(qdir.glob("*_q*.png")):
        path.unlink()

    copied: list[Path] = []
    for record in sorted(items, key=sort_key):
        src = png_path(record)
        if not src.exists():
            print(f"missing png: {src}")
            continue
        dest = qdir / dest_name(record)
        shutil.copy2(src, dest)
        copied.append(dest)

    pdf_path = folder / "all.pdf"
    if copied:
        doc = fitz.open()
        append_pngs(doc, copied)
        doc.save(pdf_path, garbage=4, deflate=True)
        doc.close()
    elif pdf_path.exists():
        pdf_path.unlink()

    write_mc_bank(chapter, subtopic, [r for r in sorted(items, key=sort_key) if png_path(r).exists()])
    return copied


def main() -> None:
    records = json.loads(JSON_PATH.read_text())
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    classification: list[dict] = []

    n_one = n_two = n_fallback = 0
    for record in records:
        topics = classify_multi(record)
        if len(topics) == 1:
            n_one += 1
            if topics[0][2] == 0:
                n_fallback += 1
        else:
            n_two += 1

        topic_rows = []
        for i, (chapter, subtopic, score) in enumerate(topics):
            buckets[(chapter, subtopic)].append(record)
            topic_rows.append(
                {
                    "Chapter": chapter,
                    "Subtopic": subtopic,
                    "score": score,
                    "primary": i == 0,
                }
            )

        primary = topics[0]
        classification.append(
            {
                "Year": record["Year"],
                "Question": record["Question"],
                "Chapter": primary[0],
                "Subtopic": primary[1],
                "score": primary[2],
                "Topics": topic_rows,
                "PNG": record.get("PNG"),
            }
        )

    # Ensure folder tree exists (do NOT wipe notes).
    for chapter in CHAPTERS:
        for subtopic in SUBTOPICS[chapter]:
            (OUT_ROOT / chapter / subtopic / "questions").mkdir(parents=True, exist_ok=True)
            (OUT_ROOT / chapter / subtopic / "notes").mkdir(parents=True, exist_ok=True)

    summary: dict[str, dict[str, int]] = {}
    for chapter in CHAPTERS:
        summary[chapter] = {}
        for subtopic in SUBTOPICS[chapter]:
            items = buckets.get((chapter, subtopic), [])
            # Deduplicate if same record somehow listed twice
            uniq = {(r["Year"], r["Question"]): r for r in items}
            items = list(uniq.values())
            copied = sync_topic_folder(chapter, subtopic, items)
            summary[chapter][subtopic] = len(copied)
            print(f"{chapter} / {subtopic}: {len(copied)}")

    # Chapter-level concatenated PDFs (unique pages; a dual-assigned Q appears once per chapter if both topics share chapter)
    for chapter in CHAPTERS:
        seen: set[str] = set()
        paths: list[Path] = []
        for subtopic in SUBTOPICS[chapter]:
            for path in sorted((OUT_ROOT / chapter / subtopic / "questions").glob("*_q*.png")):
                if path.name in seen:
                    continue
                seen.add(path.name)
                paths.append(path)
        out = OUT_ROOT / chapter / "all.pdf"
        if paths:
            doc = fitz.open()
            append_pngs(doc, paths)
            doc.save(out, garbage=4, deflate=True)
            doc.close()
            print(f"Chapter PDF {chapter}: {len(paths)} pages -> {out}")
        elif out.exists():
            out.unlink()

    index_path = OUT_ROOT / "classification.json"
    index_path.write_text(json.dumps(classification, indent=2, ensure_ascii=False) + "\n")
    summary_path = OUT_ROOT / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    # Enrich mc_questions.json
    by_id = {(c["Year"], c["Question"]): c for c in classification}
    enriched = []
    for record in records:
        meta = by_id[(record["Year"], record["Question"])]
        src = png_path(record)
        try:
            png_rel = str(src.relative_to(ROOT))
        except ValueError:
            png_rel = record.get("PNG") or f"processed/MC/{record['Year']}/q{record['Question']}.png"
        enriched.append(
            {
                **{
                    k: v
                    for k, v in record.items()
                    if k not in ("Chapter", "Subtopic", "Topics", "PNG")
                },
                "PNG": png_rel,
                "Chapter": meta["Chapter"],
                "Subtopic": meta["Subtopic"],
                "Topics": meta["Topics"],
            }
        )
    JSON_PATH.write_text(json.dumps(enriched, indent=2, ensure_ascii=False) + "\n")

    placements = sum(summary[ch][st] for ch in summary for st in summary[ch])
    print(f"Updated {JSON_PATH}")
    print(f"Wrote {index_path}")
    print(f"Questions: {len(classification)}")
    print(f"  one topic:  {n_one}")
    print(f"  two topics: {n_two}")
    print(f"  fallback (no keyword hit → Kinematics): {n_fallback}")
    print(f"  unassigned: 0")
    print(f"  total placements in notes MC banks: {placements}")
    print("Each topic has notes/mc-bank.tex with ALL its assigned questions.")


if __name__ == "__main__":
    main()
