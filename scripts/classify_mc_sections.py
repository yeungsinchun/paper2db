#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify output/ MC PNGs into Book 1-5 / Sections 1-27.

Writes:
  classified/<book>/<NN_section>/  (PNG copies; cross-topic Qs appear in each)
  classified/uncertain.csv
  classified/mc_classification.csv
  classified/mc_classification.json
  classified/summary.json
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
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
CLASSIFIED = ROOT / "classified"
OCR_CACHE = CLASSIFIED / "ocr_cache"

YEAR_ORDER = [
    "2012", "2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020",
    "2021", "2022", "2023", "2024", "2025", "2026", "pp", "sap",
]

OPTION_SPLIT = re.compile(r"(?:^|\n)\s*([A-D])\s*[.．、)]\s*", re.M)
LEADING_NUM = re.compile(r"^\s*\*?\s*\d{1,2}\s*[.．、)]\s*")

# (section_num, book_folder, section_folder, display_name)
SECTIONS: list[tuple[int, str, str, str]] = [
    (1, "01_Heat_and_Gases", "01_Temperature_and_Heat_Transfer", "Temperature and Heat Transfer"),
    (2, "01_Heat_and_Gases", "02_Heat_Capacity", "Heat Capacity"),
    (3, "01_Heat_and_Gases", "03_Change_of_State", "Change of State"),
    (4, "01_Heat_and_Gases", "04_Gas_Law_and_Kinetic_Theory", "Gas Law and Kinetic Theory"),
    (5, "02_Force_and_Motion", "05_Motion", "Motion"),
    (6, "02_Force_and_Motion", "06_Force", "Force"),
    (7, "02_Force_and_Motion", "07_More_about_Forces", "More about Forces"),
    (8, "02_Force_and_Motion", "08_Work_Energy_and_Power", "Work, Energy and Power"),
    (9, "02_Force_and_Motion", "09_Momentum", "Momentum"),
    (10, "02_Force_and_Motion", "10_Projectile_Motion", "Projectile Motion"),
    (11, "02_Force_and_Motion", "11_Uniform_Circular_Motion", "Uniform Circular Motion"),
    (12, "02_Force_and_Motion", "12_Gravitation", "Gravitation"),
    (13, "03A_Wave_Motion", "13_Wave_Motion", "Wave Motion"),
    (14, "03A_Wave_Motion", "14_Reflection_Refraction_and_Diffraction", "Reflection, Refraction and Diffraction"),
    (15, "03A_Wave_Motion", "15_Interference_and_Stationary_Wave", "Interference and Stationary Wave"),
    (16, "03A_Wave_Motion", "16_Light_and_Sound", "Light and Sound"),
    (17, "03B_Ray_Optics", "17_Reflection_of_Light", "Reflection of Light"),
    (18, "03B_Ray_Optics", "18_Refraction_of_Light", "Refraction of Light"),
    (19, "03B_Ray_Optics", "19_Lenses", "Lenses"),
    (20, "04_Electricity_and_Magnetism", "20_Electrostatics", "Electrostatics"),
    (21, "04_Electricity_and_Magnetism", "21_Circuit_and_Power", "Circuit and Power"),
    (22, "04_Electricity_and_Magnetism", "22_AC_and_Domestic_Electricity", "AC and Domestic Electricity"),
    (23, "04_Electricity_and_Magnetism", "23_Electromagnetism", "Electromagnetism"),
    (24, "04_Electricity_and_Magnetism", "24_Electromagnetic_Induction", "Electromagnetic Induction"),
    (25, "05_Radioactivity_and_Nuclear_Energy", "25_Radiation_and_Radioactivity", "Radiation and Radioactivity"),
    (26, "05_Radioactivity_and_Nuclear_Energy", "26_Rate_of_Decay_and_Uses_of_Radionuclides", "Rate of Decay and Uses of Radionuclides"),
    (27, "05_Radioactivity_and_Nuclear_Energy", "27_Nuclear_Energy", "Nuclear Energy"),
]

SECTION_BY_NUM = {n: (book, folder, name) for n, book, folder, name in SECTIONS}
BOOK_NAMES = {
    "01_Heat_and_Gases": "Heat and Gases",
    "02_Force_and_Motion": "Force and Motion",
    "03A_Wave_Motion": "Wave Motion",
    "03B_Ray_Optics": "Ray Optics",
    "04_Electricity_and_Magnetism": "Electricity and Magnetism",
    "05_Radioactivity_and_Nuclear_Energy": "Radioactivity and Nuclear Energy",
}

# Manual fixes when OCR fails or keywords are ambiguous. Values = section numbers (primary first).
OVERRIDES: dict[tuple, list[int]] = {
    (2012, 8): [6, 7],   # connected blocks + spring balance reading
    (2012, 15): [13],  # longitudinal travelling wave on slinky
    (2012, 17): [18, 14],  # refractive index via wavelength in medium
    (2013, 17): [13],  # longitudinal wave particles a-k
    (2013, 18): [15],  # stationary wave on string / vibrator
    (2014, 31): [25],  # nuclear decay chain / isotopes
    (2015, 12): [13],  # travelling wave on slinky frequency
    (2017, 14): [13],  # longitudinal wave particles E-N
    (2018, 3): [1],  # ice layer heat insulation
    (2018, 15): [13],  # transverse travelling wave P and Q
    (2019, 2): [3],  # fusion process heating curve (not nuclear)
    (2019, 3): [4],  # heated open gas container / molecules
    (2019, 4): [7],  # non-uniform plate CG / suspension
    (2019, 5): [6],  # horizontal F holds block on smooth incline
    (2019, 6): [10],  # projectile velocity 1 s before
    (2019, 7): [7],  # rope support for leaning tree / moments
    (2019, 10): [6],  # impulse changes path of ball
    (2020, 5): [9],  # headrest / impulse safety
    (2020, 10): [12],  # g on Neptune
    (2020, 11): [13],  # longitudinal wave particles a-k amplitude/speed
    (2020, 12): [13],  # particle KE vs t on a travelling wave
    (2020, 13): [18],  # refractive index X vs Y into Z
    (2020, 14): [14],  # diffraction grating order coincidence
    (2020, 17): [17],  # periscope mirrors
    (2021, 5): [6],  # resultant force by rough incline
    (2021, 19): [16],  # X-ray / microwave wavelength ratio
    (2022, 25): [21],  # charge = It / number of electrons
    (2023, 12): [7],  # metre rule moments equilibrium
    (2023, 22): [16],  # atmospheric absorption of EM spectrum
    (2024, 16): [13],  # travelling wave on slinky
    (2024, 21): [16],  # microwave EM wave properties
    (2025, 12): [12],  # weight vs distance from Earth centre
    (2025, 21): [16, 18],  # echoes / rainbows / mirages
    (2026, 3): [1],  # emitter of radiation / silvery surface
    (2026, 11): [12, 11],  # satellites in circular orbits
    (2026, 15): [13],  # longitudinal wave particles a-k
    ("pp", 16): [16],  # light as electromagnetic wave
    ("pp", 17): [13],  # longitudinal wave particle positions
    ("pp", 34): [25],  # alpha vs beta particles properties
    ("sap", 7): [5],  # speed-time graph distance
    ("sap", 10): [7],  # plank / spring balances moments
    ("sap", 16): [19],  # magnifying glass / lens
    ("sap", 25): [22],  # electricity cost kWh
}

# Bad / incomplete crops still assigned above; keep flagged for human review.
BAD_CROPS: set[tuple] = set()

# Multi-label: keep a second section when nearly tied.
SECOND_RATIO = 0.88
SECOND_GAP = 4
SECOND_MIN = 26
UNCERTAIN_SCORE = 22  # primary below this (or fallback) -> flag for review

RULES: list[tuple[int, list[str], int]] = []


def add(section: int, keywords: list[str], weight: int = 10) -> None:
    RULES.append((section, [k.lower() for k in keywords], weight))


# --- Book 5 (distinctive; score high) ---
add(27, [
    "fission", "fusion", "binding energy", "mass defect", "nuclear energy",
    "nuclear power", "nuclear reaction", "chain reaction", "uranium fuel",
    "bombards", "bombard", "nuclear equation", "mass-energy",
    "energy released", "mev", "mass of proton", "mass of neutron",
    "1u=", "atomic mass unit", "mass of 3he", "mass of he",
], 40)
add(26, [
    "half-life", "half life", "half–life", "decay constant", "activity of",
    "count rate", "becquerel", "uses of radio", "radioisotope", "radionuclide",
    "tracer", "radiocarbon", "carbon dating", "dating", "medical use",
    "sterilisation", "sterilization", "thickness gauge", "undecayed",
    "half lives", "half-lives",
], 38)
add(25, [
    "radioactive", "radioactivity", "alpha particle", "beta particle",
    "gamma ray", "gamma rays", "nuclide",
    "isotope", "geiger", "background radiation", "ionising", "ionizing",
    "ionizing radiation", "ionising radiation",
    "penetration", "cloud chamber", "radiation dose", "sievert",
    "rutherford", "atomic model", "nucleus of an atom", "a-particle",
    "α-particle", "α particle", "@-particle", "a and f particle",
    "a and £ particle", "£ particles", "yrays", "γ rays", "β particle",
    "α-emitter", "a-emitter", "deflection of a", "deflection of α",
    "neutron number", "mass number", "n-a plot", "n- a plot",
    "radioisotope", "gm counter", "gm tube",
    "penetrating power", "ionize air", "photographic film",
], 34)
# Avoid bare "decay" / "f particles" (matches "of particles" in wave OCR).
add(25, [
    "undergoes decay", "radioactive decay", "nuclear decay", "a decay",
    "α decay", "beta decay", "β decay", "decay chain", "decays to",
    "decays into",
], 36)

# --- Book 4 ---
add(24, [
    "induced emf", "induced e.m.f", "electromagnetic induction", "faraday",
    "lenz", "magnetic flux", "flux linkage", "generator", "dynamo",
    "changing magnetic", "coil is moved", "induced current",
], 32)
add(22, [
    "transformer", "alternating current", "a.c.", "ac supply", "rms", "mains",
    "turn ratio", "primary coil", "secondary coil", "kilowatt", "kwh",
    "fuse", "earthing", "live wire", "neutral wire", "household", "domestic",
    "circuit breaker", "electricity bill", "power rating", "cost of electricity",
    "kw h", "kW h",
], 30)
add(23, [
    "magnetic field", "magnet", "solenoid", "electromagnet", "motor",
    "force on a current", "lorentz", "cathode ray", "right-hand grip",
    "fleming", "compass", "soft iron", "current-carrying", "current carrying",
    "force on a wire", "magnetic force",
], 28)
add(20, [
    "electrostatic", "coulomb", "point charge", "electric field", "electric potential",
    "charged sphere", "insulator becomes charged", "van de graaff",
    "electric force between", "permittivity", "charging by", "electroscope",
    "like charges", "unlike charges", "charge is", "uncharged",
    "positively-charged", "negatively-charged", "charged rod", "charged objects",
    "charged conducting", "conducting spheres", "metal spheres",
    "brought close", "p and q repel",
], 26)
add(21, [
    "resistor", "resistance", "ohm", "ammeter", "voltmeter", "circuit",
    "battery", "kirchhoff", "series", "parallel", "potentiometer", "rheostat",
    "filament", "diode", "led", "current", "potential difference",
    "e.m.f", "emf", "power dissipated", "joule heating",
    "number of electrons", "steady d.c", "pass through the device",
], 22)

# --- Book 3B Ray Optics ---
add(19, [
    "lens", "focal length", "convex lens", "concave lens", "magnification",
    "real image", "virtual image", "optical instrument", "microscope",
    "telescope", "object distance", "image distance", "thin lens",
    "magnifying glass",
], 30)
add(18, [
    "snell", "refractive index", "refraction of light", "critical angle",
    "total internal reflection", "optical fibre", "optical fiber",
    "prism", "apparent depth", "ray diagram",
], 28)
add(17, [
    "plane mirror", "reflection of light", "image in a mirror", "mirror image",
    "law of reflection", "concave mirror", "convex mirror", "reflecting",
    "periscope",
], 26)

# --- Book 3A Wave Motion ---
add(15, [
    "interference", "superposition", "constructive", "destructive",
    "node", "antinode", "standing wave", "stationary wave", "path difference",
    "young", "double slit", "fringe", "coherent", "two pulses", "pulses of",
    "stretched string",
], 28)
add(14, [
    "ripple tank", "diffraction", "water wave", "wavefront",
    "reflection of wave", "refraction of wave", "barrier", "gap width",
    "reflected pulse", "pulse on a string", "fixed end", "free end",
], 26)
add(16, [
    "sound", "ultrasound", "echo", "loudness", "pitch", "audible",
    "tuning fork", "decibel", "sonar", "light year", "electromagnetic spectrum",
    "infrared", "ultraviolet", "microwave", "radio wave", "visible light",
    "photon", "photoelectric", "polarization", "polarisation",
    "x-ray", "x ray", "electromagnetic wave", "microwaves", "echoes",
    "rainbows", "mirages",
], 24)
add(13, [
    "wavelength", "frequency", "wave speed", "transverse", "longitudinal",
    "displacement-distance", "displacement-time", "amplitude", "period of the wave",
    "progressive wave", "wave motion", "oscillation",
], 20)
add(13, [
    "travelling wave", "traveling wave", "longitudinal wave", "transverse travelling",
    "transverse traveling", "slinky", "equilibrium positions of particles",
    "direction of travel", "direction of wave", "direction of propagation",
    "compression", "rarefaction", "particles a to", "momentarily at rest",
], 34)

# --- Book 2 Force and Motion ---
add(12, [
    "gravitation", "gravitational", "planet", "satellite", "orbit",
    "escape speed", "g-field", "newton's law of gravitation", "kepler",
    "earth's mass", "universal gravitation", "gravitational field",
    "neptune", "acceleration due to gravity", "earth's centre", "earth's center",
    "revolve around",
], 30)
add(11, [
    "circular motion", "centripetal", "angular speed", "uniform circular",
    "roundabout", "banked", "horizontal circle", "vertical circle",
    "circular platform", "rotating platform", "angular velocity",
], 28)
add(10, [
    "projectile", "projected horizontally", "projected at an angle",
    "trajectory", "time of flight", "range of the",
], 28)
add(9, [
    "momentum", "collision", "impulse", "elastic collision", "inelastic",
    "conserve momentum", "rate of change of momentum", "recoil",
    "explodes", "fragments", "explodes into", "headrest",
], 26)
add(8, [
    "kinetic energy", "potential energy", "work done", "mechanical energy",
    "efficiency", "power", "joule", "watt", "energy conversion",
], 24)
add(7, [
    "moment", "torque", "pivot", "centre of gravity", "center of mass",
    "couple", "lever", "resolve", "resolution of", "component of force",
    "resultant of two", "addition of force", "system of", "two forces",
    "force in a plane", "turning effect", "hinged", "gangplank",
    "smooth pegs", "light strings", "rigid rod", "selfie stick",
    "suspended by a light string", "mid-point", "metre rule", "meter rule",
    "spring balance", "spring balances", "uniform plank",
], 24)
add(6, [
    "newton", "net force", "resultant force", "friction", "free body",
    "tension", "normal force", "f = ma", "f=ma", "upthrust",
    "action and reaction", "external force", "mass and weight",
    "this is a misconception", "misconception because",
    "rough horizontal", "rough inclined", "inclined plane", "pulled along",
    "downward pulling", "weight w", "block of mass", "block of weight",
    "light inextensible string", "frictionless light pulley",
    "resultant force acting", "sliding down", "constant velocity",
], 22)
add(5, [
    "velocity-time", "distance-time", "displacement-time", "ticker",
    "instantaneous", "uniform acceleration", "uniform motion",
    "displacement", "velocity", "speed", "acceleration", "kinematics",
    "due east", "due north", "due west", "due south", "changes direction",
    "magnitude of the", "average velocity", "average speed",
    "m s", "ms-", "km h", "uniform deceleration", "speed of the car with time",
], 18)

# --- Book 1 Heat and Gases ---
add(4, [
    "ideal gas", "boyle", "charles", "pressure law", "pv =", "pV =",
    "gas constant", "absolute temperature", "kinetic theory", "rms speed",
    "root-mean-square", "molecular speed", "average kinetic",
    "molecules collide", "fixed mass of gas", "gas pressure",
    "weather balloon", "trapped air", "mol", "helium gas",
], 26)
add(3, [
    "latent heat", "melting", "boiling", "fusion", "vaporization",
    "vapourisation", "evaporation", "condensation", "solidification",
    "change of state", "melting point", "boiling point", "icy",
    "ice-cream", "ice cream", "remains constant during", "lake surface",
], 26)
add(2, [
    "specific heat", "heat capacity", "temperature rise", "calorimeter",
    "thermal capacity", "heated by an", "mcδθ", "mcΔT",
], 24)
add(1, [
    "conduction", "convection", "thermal radiation", "greenhouse",
    "emissivity", "black body", "heat transfer", "thermal conductivity",
    "thermal equilibrium", "thermometer", "celsius", "thermal contact",
    "hotter", "cooler", "heat flow", "temperature", "vacuum flask",
    "composite rod", "different materials", "temperature sensors",
    "cork stopper", "outer glass", "inner glass", "poorer emitter",
    "silvery surface", "heat insulation",
], 20)


def keyword_hit(blob: str, keyword: str) -> bool:
    if " " in keyword or "." in keyword or "-" in keyword or "/" in keyword or "=" in keyword:
        return keyword in blob
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", blob) is not None


def normalize(text: str) -> str:
    text = text.lower()
    # Preserve common OCR stand-ins for α/β/γ before stripping symbols.
    text = text.replace("£", " beta ")
    text = text.replace("β", " beta ")
    text = text.replace("α", " alpha ")
    text = text.replace("γ", " gamma ")
    text = text.replace("@-particle", " alpha-particle ")
    text = text.replace("@ particle", " alpha particle ")
    text = re.sub(r"[^\w\s./=+*-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def ocr_png(path: Path, cache_path: Path) -> str:
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
    text = re.sub(r"(?m)^[ \t]*C[cC]\s*[.．、)]\s*", "C. ", text)
    text = re.sub(r"(?m)^[ \t]*[Cc]\s*[.．、)]\s*", "C. ", text)
    text = re.sub(r"(?m)^[ \t]*[Oo0]\s*[.．、)]\s*", "D. ", text)
    text = re.sub(r"(?m)^[ \t]*[Bb]\s*[.．、)]\s*", "B. ", text)
    text = re.sub(r"(?m)^[ \t]*[Aa]\s*[.．、)]\s*", "A. ", text)
    text = re.sub(r"(?<![A-Za-z])Cc\s*[.．]\s+", "\nC. ", text)

    parts = OPTION_SPLIT.split("\n" + text)
    if len(parts) < 3:
        statement = LEADING_NUM.sub("", text).strip()
        return statement, {"A": "", "B": "", "C": "", "D": ""}

    statement = LEADING_NUM.sub("", parts[0].lstrip("\n")).strip()
    options: dict[str, str] = {"A": "", "B": "", "C": "", "D": ""}
    i = 1
    while i + 1 < len(parts):
        letter = parts[i].upper()
        body = re.sub(r"\n+", " ", parts[i + 1].strip()).strip()
        if letter in options and not options[letter]:
            options[letter] = body
        i += 2
    return statement, options


def _ocr_one(args: tuple[str, str, int]) -> dict:
    year, png_path, number = args
    path = Path(png_path)
    cache = OCR_CACHE / str(year) / f"q{number}.txt"
    text = ocr_png(path, cache)
    statement, options = parse_question(text)
    return {
        "Year": int(year) if str(year).isdigit() else year,
        "Question": number,
        "Question statement": statement,
        "Option": options,
        "PNG": f"output/{year}/q{number}.png",
        "OCR": text,
    }


def score_sections(record: dict) -> list[tuple[int, int]]:
    blob = normalize(
        " ".join(
            [
                record.get("Question statement") or "",
                " ".join((record.get("Option") or {}).values()),
            ]
        )
    )
    scored: list[tuple[int, int]] = []
    for section, keywords, weight in RULES:
        hits = sum(1 for kw in keywords if keyword_hit(blob, kw))
        if hits == 0:
            continue
        scored.append((section, weight + hits * 3))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def classify_multi(record: dict) -> list[tuple[int, int]]:
    key = (record["Year"], record["Question"])
    if key in OVERRIDES:
        return [(sec, 999) for sec in OVERRIDES[key]]

    scored = score_sections(record)
    if not scored:
        return [(5, 0)]  # fallback: Motion

    primary = scored[0]
    chosen = [primary]
    threshold = max(int(primary[1] * SECOND_RATIO), primary[1] - SECOND_GAP)
    for section, score in scored[1:]:
        if section == primary[0]:
            continue
        if score >= threshold and score >= SECOND_MIN:
            chosen.append((section, score))
            if len(chosen) >= 3:
                break
    return chosen


def dest_name(record: dict) -> str:
    return f"{record['Year']}_q{record['Question']}.png"


def ensure_tree() -> None:
    for _n, book, folder, _name in SECTIONS:
        (CLASSIFIED / book / folder).mkdir(parents=True, exist_ok=True)


def collect_jobs(years: list[str] | None) -> list[tuple[str, str, int]]:
    jobs = []
    for year in YEAR_ORDER:
        if years and year not in years:
            continue
        year_dir = OUTPUT / year
        if not year_dir.is_dir():
            continue
        for path in sorted(year_dir.glob("q*.png"), key=lambda p: int(re.fullmatch(r"q(\d+)\.png", p.name).group(1))):
            number = int(re.fullmatch(r"q(\d+)\.png", path.name).group(1))
            jobs.append((year, str(path), number))
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--skip-ocr", action="store_true", help="Reuse classified/mc_ocr.json if present")
    args = parser.parse_args()

    ensure_tree()
    ocr_json = CLASSIFIED / "mc_ocr.json"

    if args.skip_ocr and ocr_json.exists():
        records = json.loads(ocr_json.read_text())
        print(f"Loaded {len(records)} OCR records from {ocr_json}")
    else:
        jobs = collect_jobs(args.years)
        print(f"OCR {len(jobs)} questions with {args.workers} workers...", flush=True)
        records = []
        done = 0
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_ocr_one, job): job for job in jobs}
            for fut in as_completed(futures):
                records.append(fut.result())
                done += 1
                if done % 25 == 0 or done == len(jobs):
                    print(f"  OCR {done}/{len(jobs)}", flush=True)
        records.sort(
            key=lambda r: (
                (0, int(r["Year"])) if isinstance(r["Year"], int) or str(r["Year"]).isdigit() else (1, str(r["Year"])),
                int(r["Question"]),
            )
        )
        ocr_json.parent.mkdir(parents=True, exist_ok=True)
        # Persist without bulky raw OCR in the main export; keep OCR for debug separately
        slim = [{k: v for k, v in r.items() if k != "OCR"} for r in records]
        ocr_json.write_text(json.dumps(slim, indent=2, ensure_ascii=False) + "\n")
        (CLASSIFIED / "mc_ocr_full.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"Wrote {ocr_json}")

    # Clear previous question copies (keep tree)
    for _n, book, folder, _name in SECTIONS:
        for path in (CLASSIFIED / book / folder).glob("*_q*.png"):
            path.unlink()

    buckets: dict[int, list[dict]] = defaultdict(list)
    rows: list[dict] = []
    uncertain_rows: list[dict] = []
    n_one = n_multi = n_fallback = 0

    for record in records:
        chosen = classify_multi(record)
        if chosen[0][1] == 0:
            n_fallback += 1
        if len(chosen) == 1:
            n_one += 1
        else:
            n_multi += 1

        uncertain = chosen[0][1] < UNCERTAIN_SCORE
        bad_crop = (record["Year"], record["Question"]) in BAD_CROPS
        if bad_crop:
            uncertain = True
        section_nums = []
        section_names = []
        scores = []
        for section, score in chosen:
            buckets[section].append(record)
            book, folder, name = SECTION_BY_NUM[section]
            section_nums.append(str(section))
            section_names.append(name)
            scores.append(str(score))
            src = ROOT / record["PNG"]
            if src.exists():
                shutil.copy2(src, CLASSIFIED / book / folder / dest_name(record))

        primary = chosen[0][0]
        pbook, pfolder, pname = SECTION_BY_NUM[primary]
        row = {
            "Year": record["Year"],
            "Question": record["Question"],
            "PrimarySection": primary,
            "PrimaryName": pname,
            "PrimaryBook": BOOK_NAMES[pbook],
            "AllSections": ";".join(section_nums),
            "AllSectionNames": "; ".join(section_names),
            "Scores": ";".join(scores),
            "Uncertain": "yes" if uncertain else "no",
            "BadCrop": "yes" if bad_crop else "no",
            "PNG": record["PNG"],
            "StatementPreview": (record.get("Question statement") or "")[:160].replace("\n", " "),
        }
        rows.append(row)
        if uncertain:
            uncertain_rows.append(row)

    unc_path = CLASSIFIED / "uncertain.csv"
    with unc_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(uncertain_rows[0].keys()) if uncertain_rows else list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(uncertain_rows)

    summary = {}
    for n, book, folder, name in SECTIONS:
        count = len({(r["Year"], r["Question"]) for r in buckets.get(n, [])})
        summary[f"{n:02d} {name}"] = count
        print(f"S{n:02d} {name}: {count}")

    (CLASSIFIED / "mc_classification.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    )
    (CLASSIFIED / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    mc_csv = CLASSIFIED / "mc_classification.csv"
    with mc_csv.open("w", newline="", encoding="utf-8") as f:
        fields = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"Questions: {len(rows)}")
    print(f"  one section:   {n_one}")
    print(f"  multi-section: {n_multi}")
    print(f"  fallback S5:   {n_fallback}")
    print(f"  uncertain:     {len(uncertain_rows)}")
    print(f"Uncertain: {unc_path}")
    print(f"JSON: {CLASSIFIED / 'mc_classification.json'}")
    print(f"MC CSV: {mc_csv}")


if __name__ == "__main__":
    main()
