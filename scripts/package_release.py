#!/usr/bin/env python3
"""Collect a finished ./pipeline build into release assets + short notes.

Used by .github/workflows/pipeline-release.yml after a full build:
  dist/mc-combined.pdf, dist/lq-combined.pdf   all years, one A4 PDF each
  dist/mc-per-year.zip, dist/lq-per-year.zip   <year>.pdf per paper
  dist/mc-sections.zip   <book>/<section>/{combined,answer}.pdf
  dist/lq-sections.zip   <book>/<section>/{combined,answers,performance}.pdf
  dist/answer_keys.json, dist/candidate_performance.json, dist/quality_audit.json
  dist/stage_timings.json (when --timings is given)
  <notes>   markdown release notes: commit, branch, stage timings, assets

Exits non-zero if any expected build output is missing, so a partial build
cannot be published as if it were complete.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECON = ROOT / "tests" / "reconstructed"
SECTIONS = ROOT / "tests" / "sections"

SECTION_PDFS = {
    "mc": ("combined.pdf", "answer.pdf"),
    "lq": ("combined.pdf", "answers.pdf", "performance.pdf"),
}


def _require(path: Path) -> Path:
    if not path.is_file():
        raise SystemExit(f"Missing build output: {path.relative_to(ROOT)}")
    return path


def _zip(dest: Path, members: list[tuple[Path, str]]) -> None:
    if not members:
        raise SystemExit(f"No files to package into {dest.name}")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in members:
            zf.write(src, arcname)


def per_year_members(kind: str) -> list[tuple[Path, str]]:
    return [
        (pdf, f"{kind}-{pdf.parent.name}.pdf")
        for pdf in sorted((RECON / kind).glob("*/combined.pdf"))
    ]


def section_members(kind: str) -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []
    for section in sorted(p for p in (SECTIONS / kind).glob("*/*") if p.is_dir()):
        rel = section.relative_to(SECTIONS / kind)
        for name in SECTION_PDFS[kind]:
            pdf = section / name
            if pdf.is_file():
                members.append((pdf, f"{kind}-sections/{rel}/{name}"))
    return members


def build(out: Path, timings: Path | None) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for kind in ("mc", "lq"):
        shutil.copy2(_require(RECON / kind / "combined.pdf"), out / f"{kind}-combined.pdf")
        _zip(out / f"{kind}-per-year.zip", per_year_members(kind))
        _zip(out / f"{kind}-sections.zip", section_members(kind))
    for src in (
        SECTIONS / "mc" / "answer_keys.json",
        SECTIONS / "lq" / "candidate_performance.json",
        SECTIONS / "quality_audit.json",
    ):
        shutil.copy2(_require(src), out / src.name)
    if timings is not None:
        shutil.copy2(_require(timings), out / "stage_timings.json")
    return sorted(p.name for p in out.iterdir())


def notes(assets: list[str], out: Path, *, commit: str, branch: str, run_url: str) -> str:
    lines = [f"Commit: `{commit}`", f"Branch: `{branch}`"]
    if run_url:
        lines.append(f"Run: {run_url}")
    timings_path = out / "stage_timings.json"
    if timings_path.is_file():
        timings = json.loads(timings_path.read_text(encoding="utf-8"))
        total = sum(timings.values())
        lines += ["", "| stage | seconds |", "|---|---:|"]
        lines += [f"| {stage} | {seconds:.0f} |" for stage, seconds in timings.items()]
        lines.append(f"| **total** | **{total:.0f}** |")
    lines += ["", "Assets:"]
    for name in assets:
        size_mb = (out / name).stat().st_size / 1_000_000
        lines.append(f"- `{name}` ({size_mb:.1f} MB)")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "dist")
    p.add_argument("--timings", type=Path, default=None)
    p.add_argument("--notes", type=Path, required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--run-url", default="")
    args = p.parse_args()
    assets = build(args.out, args.timings)
    args.notes.write_text(
        notes(assets, args.out, commit=args.commit, branch=args.branch, run_url=args.run_url),
        encoding="utf-8",
    )
    print(f"Packaged {len(assets)} assets into {args.out}")
    for name in assets:
        print(f"  {name}")


if __name__ == "__main__":
    main()
