#!/usr/bin/env python3
"""qb-ocr: OCR every qb-pdf PDF via pdftoppm + tesseract --psm 1.

Inputs:  qb-pdf/<bank>/<stem>.pdf  (from qb_convert)
Outputs: qb-pdf/<bank>/<stem>.pdf.txt   (tesseract --psm 1 -l eng)
         qb-pdf/<bank>/<stem>.pages/pNNN.png  (pdftoppm -r 300)
         qb-pdf/<bank>/<stem>.tsv       (tesseract tsv for word boxes)

Uses OMP_THREAD_LIMIT=1 for tesseract (14x speedup per paper2db CI fix).
Runs in parallel over PDFs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IN = ROOT / "qb-pdf"


def ocr_one(pdf: Path, out_root: Path, dpi: int = 300) -> dict:
    bank = pdf.parent.name
    stem = pdf.stem
    # Output paths (flat alongside PDF, plus pages subdir)
    txt_path = pdf.with_suffix(".pdf.txt")
    tsv_path = pdf.with_suffix(".tsv")
    pages_dir = pdf.parent / f"{stem}.pages"

    # Skip if outputs newer than PDF and --force not set? Caller decides.
    start = time.time()

    # 1) Render pages to PNG via pdftoppm
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        # pdftoppm -r 300 -png <pdf> <tmp>/p
        cmd_ppm = ["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(tmp_p / "p")]
        r = subprocess.run(cmd_ppm, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return {"pdf": str(pdf), "ok": False, "error": f"pdftoppm: {r.stderr[:400]}"}

        pngs = sorted(tmp_p.glob("p-*.png"))
        # Also handle p-01.png naming
        if not pngs:
            pngs = sorted(tmp_p.glob("p*.png"))
        if not pngs:
            return {"pdf": str(pdf), "ok": False, "error": "no pngs from pdftoppm"}

        pages_dir.mkdir(parents=True, exist_ok=True)
        # Copy to pages_dir as p001.png etc
        for i, src in enumerate(pngs, start=1):
            dest = pages_dir / f"p{i:03d}.png"
            # Use hard copy to avoid re-encode
            if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
                import shutil

                shutil.copy2(src, dest)

        # 2) Tesseract OCR per page, concatenated
        env = os.environ.copy()
        env["OMP_THREAD_LIMIT"] = "1"
        full_text_parts: list[str] = []
        full_tsv_parts: list[str] = []
        for idx, src in enumerate(pngs, start=1):
            # Text
            cmd_ocr = ["tesseract", str(src), "stdout", "--psm", "1", "-l", "eng"]
            r2 = subprocess.run(cmd_ocr, capture_output=True, text=True, timeout=60, env=env)
            if r2.returncode != 0:
                full_text_parts.append(f"[OCR failed page {idx}: {r2.stderr[:200]}]")
            else:
                full_text_parts.append(r2.stdout)

            # TSV (for word boxes, used by crop stage)
            cmd_tsv = ["tesseract", str(src), "stdout", "--psm", "1", "-l", "eng", "tsv"]
            r3 = subprocess.run(cmd_tsv, capture_output=True, text=True, timeout=60, env=env)
            if r3.returncode == 0:
                # Prefix page number
                for line in r3.stdout.splitlines():
                    full_tsv_parts.append(f"{idx}\t{line}" if line else line)

        txt_path.write_text("\n\n".join(full_text_parts), encoding="utf-8")
        tsv_path.write_text("\n".join(full_tsv_parts), encoding="utf-8")

    elapsed = time.time() - start
    return {
        "pdf": str(pdf.relative_to(ROOT)) if pdf.is_relative_to(ROOT) else str(pdf),
        "bank": bank,
        "stem": stem,
        "pages": len(list(pages_dir.glob("p*.png"))),
        "txt": str(txt_path.relative_to(ROOT)) if txt_path.is_relative_to(ROOT) else str(txt_path),
        "elapsed_s": round(elapsed, 2),
        "ok": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_dir", default=str(DEFAULT_IN), help="qb-pdf dir")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only-bank", default=None)
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    if not in_dir.is_absolute():
        in_dir = ROOT / in_dir
    if not in_dir.is_dir():
        raise SystemExit(f"qb-pdf dir not found: {in_dir} (run qb_convert first)")

    pdfs = sorted(in_dir.rglob("*.pdf"))
    # Only bank PDFs (QB_xxx/*.pdf), not combined
    pdfs = [p for p in pdfs if p.parent.name.startswith("QB_")]
    if args.only_bank:
        pdfs = [p for p in pdfs if p.parent.name == args.only_bank]

    if not args.force:
        # Skip those where .pdf.txt newer than .pdf
        filtered = []
        for pdf in pdfs:
            txt = pdf.with_suffix(".pdf.txt")
            if txt.is_file() and txt.stat().st_mtime >= pdf.stat().st_mtime:
                continue
            filtered.append(pdf)
        print(f"Skipping {len(pdfs)-len(filtered)} up-to-date, OCR for {len(filtered)}")
        pdfs = filtered

    print(f"OCR {len(pdfs)} PDFs with {args.workers} workers (dpi={args.dpi}, psm=1, eng, OMP_THREAD_LIMIT=1)")
    if not pdfs:
        print("Nothing to OCR")
        return

    results: list[dict] = []
    failed: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(ocr_one, pdf, in_dir, args.dpi): pdf for pdf in pdfs}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            pdf = futs[fut]
            status = "OK" if r.get("ok") else "FAIL"
            print(f"  [{done}/{len(pdfs)}] {status} {pdf.parent.name}/{pdf.name} {r.get('pages','?')}p {r.get('elapsed_s','?')}s {r.get('error','')[:100]}")
            results.append(r)
            if not r.get("ok"):
                failed.append(r)

    # Write summary
    summary_path = in_dir / "ocr-log.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pdfs": len(pdfs),
        "ok": len([r for r in results if r.get("ok")]),
        "failed": len(failed),
        "results": results,
    }
    # Merge if not force
    if summary_path.is_file() and not args.force:
        try:
            prev = json.loads(summary_path.read_text())
            by_pdf = {r["pdf"]: r for r in prev.get("results", []) if "pdf" in r}
            for r in results:
                by_pdf[r["pdf"]] = r
            payload["results"] = sorted(by_pdf.values(), key=lambda r: r.get("pdf", ""))
            payload["ok"] = len([r for r in payload["results"] if r.get("ok")])
            payload["failed"] = len([r for r in payload["results"] if not r.get("ok")])
        except Exception:
            pass

    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {summary_path}  ok={payload['ok']} failed={payload['failed']}")

    if failed:
        for r in failed:
            print(f"  FAIL {r.get('pdf')}: {r.get('error','')[:200]}")
        raise SystemExit(f"{len(failed)} OCR failures")


if __name__ == "__main__":
    main()
