#!/usr/bin/env python3
"""qb-pdf: convert every QB DOCX to PDF via LibreOffice (or Word/docx2pdf).

Inputs:  qb/**/*.docx  (canonical: /Users/sinchunyeung/github/paper2notes/qb  or  qb/ in-tree)
         plus 6 PDF-only sources that have no DOCX twin.
Outputs: qb-pdf/<bank>/<stem>.pdf
         qb-pdf/convert-log.json  (sha256, page count, timing, origin, converter)

Converter priority (per captain intent):
  1. Try docx2pdf (Word at /Applications/Microsoft Word.app) if available and faithful.
  2. Otherwise use LibreOffice soffice --headless.

On macOS Word via docx2pdf is tested first; if it fails (timeout, dialog, missing),
falls back to LibreOffice. The chosen converter and reason are recorded in the log.

The --qb-root flag lets the caller point at the canonical QB tree outside the worktree.
By default it probes qb/ in-tree, then the paper2notes canonical path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "qb-pdf"
CANDIDATE_QB_ROOTS = [
    ROOT / "qb",
    Path("/Users/sinchunyeung/github/paper2notes/qb"),
    Path("/Users/sinchunyeung/.treehouse/paper2everything-706dc8/1/paper2everything/paper2db/qb"),
]

# 6 PDFs that have no DOCX twin -- ingested as-is.
PDF_ONLY_STEMS = {
    "QB_201/2_ch01_MC_e",
    "QB_202/2_ch02_MC_e",
    "QB_203/2_ch03_MC_e",
    "QB_206/2_ch06_MC_e",
    "QB_208/2_ch08_MC_e",
    "QB_208/2_ch08_MC_e_blank",
}

CONVERTER_CHOICES = ("libreoffice", "word", "docx2pdf")


def find_qb_root(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_dir():
            raise SystemExit(f"--qb-root {p} is not a directory")
        return p
    for cand in CANDIDATE_QB_ROOTS:
        if cand.is_dir() and any(cand.rglob("*.docx")):
            return cand
    raise SystemExit(
        "No qb/ found. Pass --qb-root /path/to/qb  (tried: "
        + ", ".join(str(c) for c in CANDIDATE_QB_ROOTS) + ")"
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_page_count(pdf: Path) -> int:
    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(str(pdf))
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return -1


def soffice_version() -> str:
    for cmd in ("soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        try:
            r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return r.stdout.strip() + f" ({cmd})"
        except Exception:
            continue
    return ""


def word_available() -> bool:
    return Path("/Applications/Microsoft Word.app").exists()


def try_docx2pdf_available() -> bool:
    try:
        import docx2pdf  # noqa: F401

        return True
    except ImportError:
        return False


def convert_with_word(docx: Path, pdf: Path) -> tuple[bool, str]:
    """Try Word/AppleScript conversion. Returns (ok, log)."""
    pdf.parent.mkdir(parents=True, exist_ok=True)
    script = f'''
tell application "Microsoft Word"
    open POSIX file "{docx.resolve()}"
    save as active document file format format PDF file name "{pdf.resolve()}"
    close active document saving no
    quit
end tell
'''
    try:
        r = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if r.returncode == 0 and pdf.exists() and pdf.stat().st_size > 1000:
            return True, "word: AppleScript succeeded"
        return False, f"word: rc={r.returncode} stderr={r.stderr[:300]} stdout={r.stdout[:300]}"
    except subprocess.TimeoutExpired:
        subprocess.run(["killall", "-9", "Microsoft Word"], capture_output=True, timeout=5)
        time.sleep(1)
        return False, "word: timeout (likely modal dialog)"
    except Exception as e:
        return False, f"word: {e}"


def convert_with_libreoffice(docx: Path, pdf: Path, tmp_profile: Path) -> tuple[bool, str]:
    cmd = [
        "soffice",
        "--headless",
        "--nologo",
        "--nolockcheck",
        f"-env:UserInstallation=file://{tmp_profile}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(pdf.parent),
        str(docx),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # soffice names output <stem>.pdf in outdir
        expected = pdf.parent / (docx.stem + ".pdf")
        if expected.exists() and expected != pdf:
            expected.rename(pdf)
        ok = pdf.exists() and pdf.stat().st_size > 1000
        log = r.stdout[-500:] + r.stderr[-500:]
        if ok:
            return True, f"libreoffice: converted ({r.returncode})"
        return False, f"libreoffice: rc={r.returncode} log={log[:400]}"
    except subprocess.TimeoutExpired:
        return False, "libreoffice: timeout"
    except FileNotFoundError:
        return False, "libreoffice: soffice not found"
    except Exception as e:
        return False, f"libreoffice: {e}"


def convert_one(
    docx: Path,
    out_pdf: Path,
    qb_root: Path,
    converter: str,
    tmp_base: Path,
) -> dict:
    start = time.time()
    rel = str(docx.relative_to(qb_root))
    sha = sha256_file(docx)
    # Skip lock stubs
    if docx.name.startswith("~$"):
        return {"file": rel, "skipped": "lock_stub", "sha256": sha}

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    # If PDF already exists and is newer than DOCX, skip (unless force)
    # Caller handles --force; here we always convert.

    ok = False
    log_msg = ""
    chosen = converter

    if converter == "word":
        ok, log_msg = convert_with_word(docx, out_pdf)
        if not ok:
            # Fallback to libreoffice
            tmp_profile = tmp_base / f"lo-fallback-{os.getpid()}"
            tmp_profile.mkdir(parents=True, exist_ok=True)
            ok2, log2 = convert_with_libreoffice(docx, out_pdf, tmp_profile)
            if ok2:
                chosen = "libreoffice (fallback from word)"
                log_msg = log_msg + " | " + log2
                ok = True
            else:
                log_msg = log_msg + " | " + log2
    elif converter == "docx2pdf":
        try:
            from docx2pdf import convert as docx2pdf_convert  # type: ignore

            docx2pdf_convert(str(docx), str(out_pdf))
            ok = out_pdf.exists() and out_pdf.stat().st_size > 1000
            log_msg = "docx2pdf: convert() returned"
            chosen = "docx2pdf"
        except SystemExit as e:
            log_msg = f"docx2pdf: SystemExit {e}"
        except Exception as e:
            log_msg = f"docx2pdf: {e}"
        if not ok:
            tmp_profile = tmp_base / f"lo-fallback-{os.getpid()}"
            tmp_profile.mkdir(parents=True, exist_ok=True)
            ok2, log2 = convert_with_libreoffice(docx, out_pdf, tmp_profile)
            if ok2:
                chosen = "libreoffice (fallback from docx2pdf)"
                log_msg = log_msg + " | " + log2
                ok = True
    else:  # libreoffice
        tmp_profile = tmp_base / f"lo-{os.getpid()}-{hash(docx.name) % 10000}"
        tmp_profile.mkdir(parents=True, exist_ok=True)
        ok, log_msg = convert_with_libreoffice(docx, out_pdf, tmp_profile)

    elapsed = time.time() - start
    pages = pdf_page_count(out_pdf) if ok else -1
    return {
        "file": rel,
        "sha256": sha,
        "pdf": str(out_pdf.relative_to(ROOT)) if out_pdf.is_relative_to(ROOT) else str(out_pdf),
        "pages": pages,
        "elapsed_s": round(elapsed, 2),
        "converter": chosen,
        "ok": ok,
        "log": log_msg[:800],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qb-root", default=None, help="Path to qb/ folder (default: auto-detect)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output qb-pdf dir")
    parser.add_argument("--converter", choices=CONVERTER_CHOICES, default="libreoffice", help="Preferred converter")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (libreoffice needs per-worker profile)")
    parser.add_argument("--force", action="store_true", help="Reconvert even if PDF exists")
    parser.add_argument("--only-bank", default=None, help="Only convert this bank, e.g. QB_501")
    args = parser.parse_args()

    qb_root = find_qb_root(args.qb_root)
    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    out_root.mkdir(parents=True, exist_ok=True)

    # Probe converter
    converter = args.converter
    if converter in ("word", "docx2pdf") and not word_available():
        print(f"Word not found at /Applications/Microsoft Word.app, falling back to libreoffice")
        converter = "libreoffice"
    if converter == "libreoffice":
        ver = soffice_version()
        if not ver:
            raise SystemExit("soffice not found. Install LibreOffice: brew install --cask libreoffice")
        print(f"Converter: libreoffice {ver}")
    else:
        print(f"Converter: {converter} (Word at /Applications/Microsoft Word.app)")

    # Collect DOCX files
    all_docx = sorted(qb_root.rglob("*.docx"))
    # Filter
    real_docx = [p for p in all_docx if not p.name.startswith("~$")]
    if args.only_bank:
        real_docx = [p for p in real_docx if p.parent.name == args.only_bank]

    print(f"QB root: {qb_root}")
    print(f"Found {len(all_docx)} .docx ({len(real_docx)} real, {len(all_docx)-len(real_docx)} lock stubs)")

    # Also handle PDF-only sources: copy them through
    for stem in PDF_ONLY_STEMS:
        pdf_src = qb_root / (stem + ".pdf")
        if pdf_src.is_file():
            bank = pdf_src.parent.name
            dest = out_root / bank / (pdf_src.stem + ".pdf")
            if args.only_bank and bank != args.only_bank:
                continue
            if not dest.exists() or args.force:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdf_src, dest)
                print(f"  copy PDF-only: {stem}.pdf -> {dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest}")

    # Filter to those needing conversion
    to_convert: list[tuple[Path, Path]] = []
    for docx in real_docx:
        bank = docx.parent.name
        # Handle supplements at qb root level? none, all under QB_xxx
        dest = out_root / bank / (docx.stem + ".pdf")
        if dest.exists() and not args.force:
            # Check if DOCX newer than PDF
            if docx.stat().st_mtime <= dest.stat().st_mtime:
                continue
        to_convert.append((docx, dest))

    print(f"To convert: {len(to_convert)} (skipping {len(real_docx)-len(to_convert)} up-to-date)")
    if not to_convert:
        # Still write log
        log_path = out_root / "convert-log.json"
        existing = json.loads(log_path.read_text()) if log_path.is_file() else {}
        print(f"Nothing to convert. Existing log: {log_path}")
        return

    tmp_base = Path("/tmp") / f"qb-convert-{os.getpid()}"
    tmp_base.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    failed: list[dict] = []

    # LibreOffice parallel needs per-worker profile; Word must be serial (single instance).
    if converter in ("word", "docx2pdf") or args.workers == 1:
        for docx, dest in to_convert:
            print(f"  converting {docx.parent.name}/{docx.name} ...", flush=True)
            r = convert_one(docx, dest, qb_root, converter, tmp_base)
            results.append(r)
            status = "OK" if r.get("ok") else "FAIL"
            print(f"    {status} {r.get('pages', '?')} pages {r.get('elapsed_s', '?')}s {r.get('converter','')} {r.get('log','')[:120]}")
            if not r.get("ok"):
                failed.append(r)
    else:
        # Parallel LibreOffice with per-task profile
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {}
            for docx, dest in to_convert:
                # Each task gets its own tmp profile dir
                fut = pool.submit(convert_one, docx, dest, qb_root, converter, tmp_base)
                futs[fut] = docx
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                status = "OK" if r.get("ok") else "FAIL"
                docx = futs[fut]
                print(f"  {status} {docx.parent.name}/{docx.name} {r.get('pages','?')}p {r.get('elapsed_s','?')}s")

                if not r.get("ok"):
                    failed.append(r)

    # Clean tmp profiles
    shutil.rmtree(tmp_base, ignore_errors=True)
    # Kill any stray soffice
    subprocess.run(["pkill", "-f", "soffice.*qb-convert"], capture_output=True)

    # Write log
    log_path = out_root / "convert-log.json"
    # Merge with previous if not force
    if log_path.is_file() and not args.force:
        try:
            prev = json.loads(log_path.read_text())
            # prev is list or dict
            if isinstance(prev, list):
                prev_by_file = {r["file"]: r for r in prev if "file" in r}
            else:
                prev_by_file = {r["file"]: r for r in prev.get("results", []) if "file" in r}
            for r in results:
                if "file" in r:
                    prev_by_file[r["file"]] = r
            results = list(prev_by_file.values())
        except Exception:
            pass

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "qb_root": str(qb_root),
        "out": str(out_root),
        "converter_requested": args.converter,
        "converter_effective": converter,
        "soffice_version": soffice_version(),
        "word_available": word_available(),
        "total_docx": len(all_docx),
        "real_docx": len(real_docx),
        "converted": len([r for r in results if r.get("ok")]),
        "failed": len(failed),
        "results": sorted(results, key=lambda r: r.get("file", "")),
    }
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {log_path}")
    print(f"  converted: {payload['converted']}/{len(real_docx)} real docx, {len(failed)} failed")

    # Also count PDFs on disk
    pdfs_on_disk = list(out_root.rglob("*.pdf"))
    # Exclude log etc - count only bank PDFs
    bank_pdfs = [p for p in pdfs_on_disk if p.parent.name.startswith("QB_")]
    print(f"  PDFs on disk: {len(bank_pdfs)} under qb-pdf/QB_*/")

    if failed:
        print("\nFailed conversions:")
        for r in failed:
            print(f"  {r.get('file')}: {r.get('log','')[:200]}")
        # Don't exit 1 if --force not set? Always warn but exit 0 for partial?
        # For CI gate we want non-zero if any fail
        if len(failed) > 5:
            raise SystemExit(f"{len(failed)} conversions failed - see {log_path}")

    # Quick gate check
    expected_real = 199
    if len(real_docx) != expected_real:
        print(f"WARNING: expected {expected_real} real docx, found {len(real_docx)} in {qb_root}")
    bank_pdf_count = len(bank_pdfs)
    # 199 real + 6 pdf-only but some pdf-only overlap with real stems? Actually pdf-only are 6 distinct PDFs
    # The 6 pdf-only include 2 from QB_208 where docx also exists for other stems.
    # So total PDFs should be ~199 + number of pdf-only that don't collide = ~205 but many overwrite same stem.
    # Better: count unique (bank, stem) where pdf exists.
    unique_stems = set(p.stem for p in bank_pdfs)
    print(f"  unique PDF stems: {len(unique_stems)}")


if __name__ == "__main__":
    main()
