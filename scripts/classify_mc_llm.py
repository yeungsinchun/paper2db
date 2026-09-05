#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify MC questions into Book 1-5 / Sections 1-27 using an LLM.

Pipeline:
  1. Reuse / refresh OCR from output/ PNGs (tesseract cache under classified/ocr_cache)
  2. Call an OpenAI-compatible chat API one question (or small batch) at a time
  3. Write classified/<book>/<section>/ PNG copies plus:
       classified/mc_classification.csv
       classified/mc_classification.json
       classified/uncertain.csv
       classified/summary.json
  4. Optionally rebuild per-section combined.pdf (easiest -> hardest) + answer.pdf

Env:
  LLM_API_KEY   (or OPENAI_API_KEY / TOGETHER_API_KEY)
  LLM_BASE_URL  (default https://api.together.xyz/v1)
  LLM_MODEL     (default meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo)

You can also apply a precomputed JSON of LLM decisions:
  python scripts/classify_mc_llm.py --from-json classified/llm_classifications.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
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

OPTION_SPLIT = re.compile(r"(?:^|\n)\s*([A-D])\s*[.．、)]\s*", re.M)
LEADING_NUM = re.compile(r"^\s*\*?\s*\d{1,2}\s*[.．、)]\s*")
JSON_RE = re.compile(r"\{.*\}", re.S)

TAXONOMY_TEXT = """
HKDSE Physics MC taxonomy (section number -> name). Pick the BEST primary section.
Use a second section ONLY if the question is genuinely cross-topic (rare).

Book 1 Heat and Gases:
  1 Temperature and Heat Transfer (conduction/convection/radiation as heat transfer, thermometers, thermal equilibrium - NOT ideal-gas p-T laws)
  2 Heat Capacity (c, C, specific heat, mixing calorimetry without phase change)
  3 Change of State (melting/boiling/evaporation/latent heat/heating curves with plateaus)
  4 Gas Law and Kinetic Theory (ideal gas, pV=nRT, p-T / p-V / V-T graphs, molecular KE, pressure from molecules, internal energy of a body/system as a tested concept)

Book 2 Force and Motion:
  5 Motion (kinematics graphs, free fall without projectile framing)
  6 Force (Newton laws, friction as force, F=ma, free-body basics on a single body/particle - NOT multi-body systems or moments)
  7 More about Forces (systems of bodies, force resolution/addition in plane, moments/torque, CG, equilibrium of rigid body, connected particles/pulleys)
  8 Work, Energy and Power (W=Fs, KE/PE mechanical, power, efficiency of machines)
  9 Momentum (collisions, impulse, conservation of momentum)
  10 Projectile Motion
  11 Uniform Circular Motion (centripetal)
  12 Gravitation (g, orbits, Newton's law of gravitation)

Book 3A Wave Motion:
  13 Wave Motion (travelling/longitudinal/transverse waves, v=fλ, particle motion on a wave)
  14 Reflection, Refraction and Diffraction (of waves; diffraction grating for light waves)
  15 Interference and Stationary Wave (Young, beats, standing waves on string/air column)
  16 Light and Sound (EM spectrum properties, sound, ultrasound, Doppler of sound; NOT ray optics)

Book 3B Ray Optics:
  17 Reflection of Light (mirrors, periscope)
  18 Refraction of Light (Snell, total internal reflection, apparent depth)
  19 Lenses (thin lens formula, images)

Book 4 Electricity and Magnetism:
  20 Electrostatics (charge, Coulomb, field/potential of static charges)
  21 Circuit and Power (Ohm, series/parallel, electrical power/energy of circuits)
  22 AC and Domestic Electricity (mains, plugs, fuses, kWh, household)
  23 Electromagnetism (motor effect, magnetic field of currents, force on charges in B)
  24 Electromagnetic Induction (Faraday, Lenz, generators, transformers)

Book 5 Radioactivity and Nuclear Energy:
  25 Radiation and Radioactivity (alpha/beta/gamma nature, absorption, detectors, decay equations qualitatively)
  26 Rate of Decay and Uses of Radionuclides (half-life, activity, dating, tracers)
  27 Nuclear Energy (fission/fusion, binding energy, mass defect)
""".strip()

SYSTEM_PROMPT = f"""You are classifying HKDSE Physics multiple-choice questions into curriculum sections.
{TAXONOMY_TEXT}

Rules:
- Choose exactly one primary section number.
- Add at most one extra section, and only if the question truly requires both topics.
- Do NOT put ideal-gas / p-T / kinetic-theory questions in section 1 just because temperature is mentioned.
- Put single-body Newton/friction/resultant-force questions in section 6. Reserve section 7 for systems of bodies, moments/torque, rigid-body equilibrium, or connected particles.
- If the question relates to internal energy, or molecular/potential energy of gas particles, always include section 4 (Gas Law and Kinetic Theory) as primary or extra section - even when the stem also involves heat transfer, heat capacity, or change of state.
- Do NOT put travelling-wave / slinky / particle-on-a-wave questions in radioactivity.
- Ignore OCR garbage; classify from the meaningful physics content.
- Reply with JSON only: {{"sections":[<primary>, ...], "reason":"<one short sentence>"}}
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--years", nargs="*", default=None)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--skip-ocr", action="store_true")
    p.add_argument("--from-json", type=Path, default=None,
                   help="Apply precomputed LLM decisions instead of calling the API")
    p.add_argument("--limit", type=int, default=None, help="Classify only first N (debug)")
    p.add_argument("--sleep", type=float, default=0.15)
    p.add_argument("--rebuild-pdfs", action="store_true",
                   help="Also run combine_section_pdfs.py after writing folders")
    return p.parse_args()


def ensure_tree() -> None:
    CLASSIFIED.mkdir(parents=True, exist_ok=True)
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    for _n, book, folder, _name in SECTIONS:
        (CLASSIFIED / book / folder).mkdir(parents=True, exist_ok=True)


def year_key(name: str) -> tuple:
    if name in YEAR_ORDER:
        return (0, YEAR_ORDER.index(name))
    return (1, name)


def collect_jobs(years: list[str] | None) -> list[tuple[str, str, int]]:
    jobs: list[tuple[str, str, int]] = []
    year_dirs = sorted(
        [p for p in OUTPUT.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: year_key(p.name),
    )
    for year_dir in year_dirs:
        if years and year_dir.name not in years:
            continue
        for png in sorted(year_dir.glob("q*.png"), key=lambda p: int(p.stem[1:])):
            jobs.append((year_dir.name, str(png), int(png.stem[1:])))
    return jobs


def clean_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_question(ocr_text: str) -> tuple[str, dict[str, str]]:
    text = clean_text(ocr_text)
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


def _ocr_one(args: tuple[str, str, int]) -> dict:
    year, png_path, number = args
    cache = OCR_CACHE / str(year) / f"q{number}.txt"
    text = ocr_png(Path(png_path), cache)
    statement, options = parse_question(text)
    return {
        "Year": int(year) if str(year).isdigit() else year,
        "Question": number,
        "Question statement": statement,
        "Option": options,
        "PNG": f"output/{year}/q{number}.png",
        "OCR": text,
    }


def record_text(record: dict, limit: int = 900) -> str:
    stmt = (record.get("Question statement") or "").strip()
    opts = record.get("Option") or {}
    opt_lines = [f"{k}. {(v or '').strip()}" for k, v in opts.items() if (v or "").strip()]
    blob = stmt
    if opt_lines:
        blob += "\n" + "\n".join(opt_lines)
    return blob[:limit]


def llm_config() -> tuple[str, str, str]:
    key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("TOGETHER_API_KEY")
        or ""
    )
    base = os.environ.get("LLM_BASE_URL", "https://api.together.xyz/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo")
    return key, base, model


def chat_json(system: str, user: str, *, retries: int = 3) -> dict:
    key, base, model = llm_config()
    if not key:
        raise SystemExit("Set LLM_API_KEY / OPENAI_API_KEY / TOGETHER_API_KEY")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 250,
    }
    data = json.dumps(payload).encode()
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            match = JSON_RE.search(content)
            if not match:
                raise ValueError(f"No JSON in model reply: {content[:200]}")
            return json.loads(match.group(0))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"LLM call failed after {retries} retries: {last_err}")


def normalize_sections(raw: object) -> list[int]:
    if not isinstance(raw, list) or not raw:
        return []
    out: list[int] = []
    for item in raw:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 27 and n not in out:
            out.append(n)
        if len(out) >= 2:
            break
    return out


def classify_one_llm(record: dict) -> dict:
    user = (
        f"Year: {record['Year']}  Question: {record['Question']}\n"
        f"OCR text:\n{record_text(record)}\n\n"
        'Return JSON: {"sections":[primary, optional_second], "reason":"..."}'
    )
    result = chat_json(SYSTEM_PROMPT, user)
    sections = normalize_sections(result.get("sections"))
    if not sections:
        sections = [5]
        uncertain = True
        reason = "LLM returned no valid section; fallback Motion"
    else:
        uncertain = False
        reason = str(result.get("reason") or "").strip()
    return {
        "Year": record["Year"],
        "Question": record["Question"],
        "sections": sections,
        "reason": reason,
        "uncertain": uncertain,
        "PNG": record["PNG"],
        "StatementPreview": re.sub(r"\s+", " ", record_text(record, 160)),
    }


def dest_name(year: object, question: int) -> str:
    return f"{year}_q{question}.png"


def row_key(row: dict) -> tuple[str, int]:
    return (str(row["Year"]), int(row["Question"]))


def merge_by_year_question(existing: list[dict], updates: list[dict]) -> list[dict]:
    by_key = {row_key(row): row for row in existing}
    for row in updates:
        by_key[row_key(row)] = row
    return sorted(
        by_key.values(),
        key=lambda row: (year_key(str(row["Year"])), int(row["Question"])),
    )


def apply_classifications(records: list[dict], decisions: list[dict]) -> None:
    ensure_tree()
    by_key = {(str(d["Year"]), int(d["Question"])): d for d in decisions}
    # Clear previous PNG copies
    for _n, book, folder, _name in SECTIONS:
        for path in (CLASSIFIED / book / folder).glob("*_q*.png"):
            path.unlink()

    rows: list[dict] = []
    uncertain_rows: list[dict] = []
    buckets: dict[int, int] = defaultdict(int)
    n_one = n_multi = 0

    for record in records:
        key = (str(record["Year"]), int(record["Question"]))
        decision = by_key.get(key)
        if decision is None:
            decision = {
                "Year": record["Year"],
                "Question": record["Question"],
                "sections": [5],
                "reason": "missing LLM decision",
                "uncertain": True,
                "PNG": record["PNG"],
                "StatementPreview": re.sub(r"\s+", " ", record_text(record, 160)),
            }
        sections = normalize_sections(decision.get("sections")) or [5]
        if len(sections) == 1:
            n_one += 1
        else:
            n_multi += 1
        for sec in sections:
            book, folder, _name = SECTION_BY_NUM[sec]
            buckets[sec] += 1
            src = ROOT / record["PNG"]
            if src.exists():
                shutil.copy2(src, CLASSIFIED / book / folder / dest_name(record["Year"], int(record["Question"])))

        primary = sections[0]
        pbook, _pfolder, pname = SECTION_BY_NUM[primary]
        row = {
            "Year": record["Year"],
            "Question": record["Question"],
            "PrimarySection": primary,
            "PrimaryName": pname,
            "PrimaryBook": BOOK_NAMES[pbook],
            "AllSections": ";".join(str(s) for s in sections),
            "AllSectionNames": "; ".join(SECTION_BY_NUM[s][2] for s in sections),
            "Reason": decision.get("reason") or "",
            "Uncertain": "yes" if decision.get("uncertain") else "no",
            "PNG": record["PNG"],
            "StatementPreview": decision.get("StatementPreview")
            or re.sub(r"\s+", " ", record_text(record, 160)),
        }
        rows.append(row)
        if row["Uncertain"] == "yes":
            uncertain_rows.append(row)

    def sort_key(r: dict) -> tuple:
        y = r["Year"]
        return (year_key(str(y)), int(r["Question"]))

    rows.sort(key=sort_key)
    uncertain_rows.sort(key=sort_key)

    fieldnames = [
        "Year", "Question", "PrimarySection", "PrimaryName", "PrimaryBook",
        "AllSections", "AllSectionNames", "Reason", "Uncertain", "PNG", "StatementPreview",
    ]
    with (CLASSIFIED / "uncertain.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(uncertain_rows)
    (CLASSIFIED / "mc_classification.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    )
    with (CLASSIFIED / "mc_classification.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        f"S{n:02d} {name}": buckets[n]
        for n, _book, _folder, name in SECTIONS
    }
    (CLASSIFIED / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print("Questions:", len(rows))
    print("  one section:  ", n_one)
    print("  multi-section:", n_multi)
    print("  uncertain:    ", len(uncertain_rows))
    for n, _b, _f, name in SECTIONS:
        print(f"S{n:02d} {name}: {buckets[n]}")


def main() -> None:
    args = parse_args()
    ensure_tree()
    ocr_json = CLASSIFIED / "mc_ocr.json"
    ocr_full_json = CLASSIFIED / "mc_ocr_full.json"
    partial_run = bool(args.years or args.limit)

    if args.skip_ocr and ocr_json.exists():
        full_records = json.loads(ocr_json.read_text())
        print(f"Loaded {len(full_records)} OCR records")
    else:
        jobs = collect_jobs(args.years)
        print(f"OCR {len(jobs)} questions...")
        new_records = []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_ocr_one, job): job for job in jobs}
            done = 0
            for fut in as_completed(futs):
                new_records.append(fut.result())
                done += 1
                if done % 25 == 0 or done == len(jobs):
                    print(f"  OCR {done}/{len(jobs)}", flush=True)
        new_records.sort(key=lambda r: (year_key(str(r["Year"])), int(r["Question"])))
        slim_new = [{k: v for k, v in r.items() if k != "OCR"} for r in new_records]
        if partial_run and ocr_json.exists():
            full_records = merge_by_year_question(
                json.loads(ocr_json.read_text()),
                slim_new,
            )
            if ocr_full_json.exists():
                full_with_ocr = merge_by_year_question(
                    json.loads(ocr_full_json.read_text()),
                    new_records,
                )
            else:
                full_with_ocr = new_records
        else:
            full_records = slim_new
            full_with_ocr = new_records
        ocr_json.write_text(json.dumps(full_records, indent=2, ensure_ascii=False) + "\n")
        ocr_full_json.write_text(
            json.dumps(full_with_ocr, indent=2, ensure_ascii=False) + "\n"
        )

    work_records = full_records
    if args.years:
        want = set(args.years)
        work_records = [r for r in work_records if str(r["Year"]) in want]
    if args.limit:
        work_records = work_records[: args.limit]

    decisions_path = CLASSIFIED / "llm_classifications.json"
    existing_decisions: list[dict] = []
    if decisions_path.exists():
        existing_decisions = json.loads(decisions_path.read_text())

    if args.from_json:
        new_decisions = json.loads(args.from_json.read_text())
        print(f"Loaded {len(new_decisions)} LLM decisions from {args.from_json}")
    else:
        print(f"LLM-classifying {len(work_records)} questions...")
        new_decisions = []
        for i, record in enumerate(work_records, 1):
            decision = classify_one_llm(record)
            new_decisions.append(decision)
            if i % 10 == 0 or i == len(work_records):
                print(f"  LLM {i}/{len(work_records)}", flush=True)
            if args.sleep:
                time.sleep(args.sleep)
            if i % 25 == 0:
                checkpoint = merge_by_year_question(existing_decisions, new_decisions)
                decisions_path.write_text(
                    json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n"
                )

    decisions = merge_by_year_question(existing_decisions, new_decisions)
    decisions_path.write_text(json.dumps(decisions, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {decisions_path} ({len(decisions)} decisions)")

    apply_classifications(full_records, decisions)

    if args.rebuild_pdfs:
        subprocess.run(
            [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "combine_section_pdfs.py")],
            check=True,
        )


if __name__ == "__main__":
    main()
