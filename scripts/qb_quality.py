#!/usr/bin/env python3
"""qb-audit: quality checks + Lavish review board for the qb pipeline.

Gate (plan P1, all exact):
  - 199 PDFs: every real DOCX has qb-pdf/<bank>/<stem>.pdf
  - 3,712 unique items total, 1,881 in scope (Books 2,4,5), per-bank counts per plan §3.2
  - 100% crops (every item has qb-pdf/crops/<id>.png)
  - key-status table matches plan §3.2: per bank, `present` equals the withKey column,
    QB_202's 64 MC are `from-pdf`, QB_503's 38 MC are `missing`
  - converter render check: LibreOffice vs the 59 Quartz PDFs with DOCX twins
    (page count ±1), and every Symbol-font glyph of each DOCX appears in its
    PDF text layer with no Symbol PUA code points left

Outputs:
  qb-pdf/quality.json
  .lavish/qb-review/index.html  (local only, gitignored; 5% random crops per bank)

Also prints a human-readable gate report to stdout.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from qb_convert import EXPECTED_REAL_DOCX  # noqa: E402
from qb_items import find_qb_root, normalize_symbol_xml  # noqa: E402

QB_PDF = ROOT / "qb-pdf"
ITEMS_DIR = QB_PDF / "items"
CROPS_DIR = QB_PDF / "crops"
LAVISH_OUT = ROOT / ".lavish" / "qb-review"

EXPECTED_TOTAL_ITEMS = 3712
EXPECTED_IN_SCOPE = 1881
# Plan §3.2 in-scope census: bank -> (items, withKey from DOCX)
EXPECTED_BANKS = {
    "QB_201": (59, 59), "QB_202": (109, 45), "QB_203": (81, 81), "QB_204": (113, 113),
    "QB_205": (61, 61), "QB_206": (96, 96), "QB_207": (125, 125), "QB_208": (84, 84),
    "QB_209": (77, 77), "QB_210": (68, 68),
    "QB_401": (131, 131), "QB_402": (105, 105), "QB_403": (61, 61), "QB_404": (107, 107),
    "QB_405": (67, 67), "QB_406": (89, 89), "QB_407": (121, 121), "QB_408": (81, 81),
    "QB_501": (70, 70), "QB_502": (109, 109), "QB_503": (67, 29),
}
IN_SCOPE_BANKS = set(EXPECTED_BANKS)
# QB_202's 64 MC keys live only in the Quartz PDF text layer
EXPECTED_FROM_PDF = {"QB_202": 64}
MAX_PAGE_DELTA = 1


def load_index() -> dict | None:
    p = ITEMS_DIR / "index.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def real_docx(qb_root: Path | None) -> list[Path]:
    if qb_root is None:
        return []
    return [p for p in sorted(qb_root.rglob("*.docx")) if not p.name.startswith("~$")]


def lo_pdf(docx: Path) -> Path:
    return QB_PDF / docx.parent.name / (docx.stem + ".pdf")


def check_pdfs(qb_root: Path | None) -> dict:
    docx = real_docx(qb_root)
    missing = [f"{d.parent.name}/{d.stem}" for d in docx if not lo_pdf(d).is_file()]
    converted = len(docx) - len(missing)
    return {
        "real_docx": len(docx),
        "converted_pdfs": converted,
        "expected": EXPECTED_REAL_DOCX,
        "missing": missing[:20],
        "ok": len(docx) == EXPECTED_REAL_DOCX and converted == EXPECTED_REAL_DOCX,
    }


def check_items() -> dict:
    index = load_index()
    if not index:
        return {"ok": False, "error": "items/index.json missing (run qb_items first)"}
    items = index.get("items", [])
    total = len(items)
    in_scope = [e for e in items if e.get("bank") in IN_SCOPE_BANKS]
    by_bank: dict[str, int] = Counter(e["bank"] for e in items)
    bank_failures = [
        {"bank": bank, "expected": expected, "actual": by_bank.get(bank, 0)}
        for bank, (expected, _) in EXPECTED_BANKS.items()
        if by_bank.get(bank, 0) != expected
    ]
    total_ok = total == EXPECTED_TOTAL_ITEMS
    in_scope_ok = len(in_scope) == EXPECTED_IN_SCOPE
    return {
        "total": total,
        "expected_total": EXPECTED_TOTAL_ITEMS,
        "total_ok": total_ok,
        "in_scope": len(in_scope),
        "expected_in_scope": EXPECTED_IN_SCOPE,
        "in_scope_ok": in_scope_ok,
        "by_bank": dict(by_bank),
        "by_type_in_scope": dict(Counter(e.get("type", "?") for e in in_scope)),
        "bank_failures": bank_failures,
        "ok": total_ok and in_scope_ok and not bank_failures,
    }


def check_crops() -> dict:
    index = load_index()
    if not index:
        return {"ok": False, "error": "no index"}
    items = index.get("items", [])
    missing = [e["id"] for e in items if not (CROPS_DIR / f"{e['id']}.png").is_file()]
    total = len(items)
    present = total - len(missing)
    return {
        "total": total,
        "present": present,
        "missing": len(missing),
        "missing_ids": missing[:20],
        "pct": round(present / total * 100, 2) if total else 0,
        "ok": total > 0 and not missing,
    }


def check_key_status() -> dict:
    index = load_index()
    if not index:
        return {"ok": False, "error": "no index"}
    by_bank_items: dict[str, list[dict]] = defaultdict(list)
    for e in index.get("items", []):
        by_bank_items[e["bank"]].append(e)

    results = []
    for bank, (items_n, with_key) in sorted(EXPECTED_BANKS.items()):
        entries = by_bank_items.get(bank, [])
        from_pdf = EXPECTED_FROM_PDF.get(bank, 0)
        expected = {"present": with_key, "from-pdf": from_pdf, "missing": items_n - with_key - from_pdf}
        actual = Counter(e.get("status") for e in entries)
        actual_d = {k: actual.get(k, 0) for k in ("present", "from-pdf", "missing", "derived")}
        non_mc_unkeyed = sum(1 for e in entries if e.get("status") != "present" and e.get("type") != "mc")
        ok = all(actual_d[k] == v for k, v in expected.items()) and actual_d["derived"] == 0 and non_mc_unkeyed == 0
        results.append({"bank": bank, "expected": expected, "actual": actual_d, "non_mc_unkeyed": non_mc_unkeyed, "ok": ok})
    return {"checks": results, "ok": all(r["ok"] for r in results)}


def render_reference(docx: Path) -> tuple[set[str], set[str]]:
    """(Symbol glyphs the PDF must show, PUA chars allowed from non-Symbol w:sym fonts)."""
    with zipfile.ZipFile(str(docx)) as z:
        xml = z.read("word/document.xml").decode()
    allowed_pua = set()
    for tag in re.findall(r"<w:sym\b[^>]*/>", xml):
        font = re.search(r'w:font="([^"]*)"', tag)
        char = re.search(r'w:char="([^"]*)"', tag)
        if font and char and font.group(1) != "Symbol":
            allowed_pua.add(chr(int(char.group(1), 16) | 0xF000))
    return set(normalize_symbol_xml(xml)[1]), allowed_pua


def pdf_text(pdf: Path) -> tuple[int, str]:
    import pymupdf  # type: ignore

    doc = pymupdf.open(str(pdf))
    try:
        return len(doc), "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def check_render(qb_root: Path | None) -> dict:
    """LibreOffice PDFs vs Quartz twins (page count) and vs DOCX Symbol glyphs (text layer)."""
    docx = real_docx(qb_root)
    page_checks: list[dict] = []
    glyph_failures: list[dict] = []
    checked = 0
    for d in docx:
        lo = lo_pdf(d)
        if not lo.is_file():
            glyph_failures.append({"file": f"{d.parent.name}/{d.name}", "error": "pdf missing"})
            continue
        lo_pages, text = pdf_text(lo)
        checked += 1
        glyphs, allowed_pua = render_reference(d)
        missing = sorted(g for g in glyphs if g not in text)
        pua = sorted({c for c in text if 0xE000 <= ord(c) <= 0xF8FF} - allowed_pua)
        if missing or pua:
            glyph_failures.append({"file": f"{d.parent.name}/{d.name}", "missing_glyphs": missing, "pua": [f"U+{ord(c):04X}" for c in pua]})
        quartz = d.with_suffix(".pdf")
        if quartz.is_file():
            q_pages, _ = pdf_text(quartz)
            page_checks.append({"stem": f"{d.parent.name}/{d.stem}", "quartz_pages": q_pages, "lo_pages": lo_pages, "delta": abs(q_pages - lo_pages)})

    mismatched = [c for c in page_checks if c["delta"] > MAX_PAGE_DELTA]
    return {
        "compared": len(page_checks),
        "mismatched": len(mismatched),
        "mismatched_stems": mismatched[:10],
        "glyph_checked": checked,
        "glyph_failures": len(glyph_failures),
        "glyph_failure_files": glyph_failures[:10],
        "sample": random.sample(page_checks, min(5, len(page_checks))),
        "ok": bool(docx) and not glyph_failures and bool(page_checks) and not mismatched,
        "note": f"page count ±{MAX_PAGE_DELTA} vs Quartz twins; every DOCX Symbol glyph present in the PDF text layer, no Symbol PUA",
    }


def run_checks(qb_root: Path | None) -> dict:
    return {
        "pdfs": check_pdfs(qb_root),
        "items": check_items(),
        "crops": check_crops(),
        "keys": check_key_status(),
        "render": check_render(qb_root),
    }


def build_lavish(index: dict | None, checks: dict) -> Path | None:
    if not index:
        return None
    items = index.get("items", [])
    by_bank: dict[str, list[dict]] = defaultdict(list)
    for e in items:
        by_bank[e["bank"]].append(e)

    LAVISH_OUT.mkdir(parents=True, exist_ok=True)
    img_dir = LAVISH_OUT / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    # Copy 5% random crops per bank for review
    rng = random.Random(42)
    for bank, entries in sorted(by_bank.items()):
        if bank not in IN_SCOPE_BANKS:
            continue
        k = max(1, len(entries) // 20)  # 5%
        sample = rng.sample(entries, min(k, len(entries)))
        for e in sample:
            src = CROPS_DIR / f"{e['id']}.png"
            if src.is_file():
                dest = img_dir / f"{e['id']}.png"
                if not dest.exists():
                    shutil.copy2(src, dest)

    # Build index.html
    html_parts = []
    html_parts.append("""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QB Review Board</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem}
figure{margin:0;border:1px solid #ddd;border-radius:8px;overflow:hidden}
figure img{width:100%;display:block}
figcaption{padding:.4rem .6rem;font-size:.85em;background:#fafafa}
.badge{display:inline-block;padding:2px 6px;border-radius:4px;font-size:.75em;color:#fff}
.badge-mc{background:#2a7} .badge-sq{background:#36a} .badge-lq{background:#a63} .badge-rq{background:#a3a}
table{border-collapse:collapse;width:100%} th,td{border:1px solid #ddd;padding:6px 8px;text-align:left} th{background:#f5f5f5}
.fail{color:#b00} .pass{color:#090}
</style></head><body>
<h1>QB Pipeline Review — local Lavish board</h1>
<p><em>This board is gitignored. QB crops are copyrighted and must not appear in public PRs.</em></p>
""")

    gate_ok = all(v["ok"] for v in checks.values())
    html_parts.append(f"<h2>Gate {'<span class=pass>PASS</span>' if gate_ok else '<span class=fail>FAIL</span>'}</h2>")
    html_parts.append("<table><tr><th>Check</th><th>Result</th></tr>")
    for name, data in checks.items():
        ok = data.get("ok")
        icon = "✓" if ok else "✗"
        cls = "pass" if ok else "fail"
        html_parts.append(f"<tr><td>{name}</td><td class={cls}>{icon} {json.dumps(data)[:400]}</td></tr>")
    html_parts.append("</table>")

    # Per-bank stats
    html_parts.append("<h2>Per-bank counts (in-scope)</h2><table><tr><th>Bank</th><th>Items</th><th>MC/SQ/LQ/RQ</th><th>With key</th></tr>")
    if index:
        for bank in sorted(IN_SCOPE_BANKS):
            bank_json = ITEMS_DIR / f"{bank}.json"
            if bank_json.is_file():
                data = json.loads(bank_json.read_text())
                items_b = data.get("items", [])
                tc = Counter(it.get("type") for it in items_b)
                wk = sum(1 for it in items_b if it.get("answer", {}).get("status") in ("present", "from-pdf"))
                html_parts.append(f"<tr><td>{bank}</td><td>{len(items_b)}</td><td>{tc.get('mc',0)}/{tc.get('sq',0)}/{tc.get('lq',0)}/{tc.get('rq',0)}</td><td>{wk}</td></tr>")
    html_parts.append("</table>")

    # Random crops grid
    html_parts.append("<h2>Sample crops (5% per bank, 5 items each max shown)</h2><div class=grid>")
    for bank in sorted(IN_SCOPE_BANKS):
        bank_json = ITEMS_DIR / f"{bank}.json"
        if not bank_json.is_file():
            continue
        data = json.loads(bank_json.read_text())
        shown = 0
        for it in data.get("items", []):
            if shown >= 3:
                break
            code = it["id"]
            img_path = img_dir / f"{code}.png"
            if not img_path.is_file():
                continue
            typ = it.get("type", "?")
            status = it.get("answer", {}).get("status", "?")
            html_parts.append(
                f'<figure><img src="img/{code}.png" loading="lazy" alt="{code}">'
                f'<figcaption><span class="badge badge-{typ}">{typ}</span> {code} {bank} {status}<br>{it.get("stem",{}).get("text","")[:120]}</figcaption></figure>'
            )
            shown += 1
    html_parts.append("</div></body></html>")

    out = LAVISH_OUT / "index.html"
    out.write_text("\n".join(html_parts), encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any gate fails")
    parser.add_argument("--qb-root", default=None)
    parser.add_argument("--output", default=str(QB_PDF / "quality.json"))
    args = parser.parse_args()

    qb_root = find_qb_root(args.qb_root)
    print("QB quality audit")
    print("=" * 60)
    print(f"QB root: {qb_root}")

    checks = run_checks(qb_root)
    pdfs, items, crops, keys, render = (checks[k] for k in ("pdfs", "items", "crops", "keys", "render"))

    def mark(ok: bool | None) -> str:
        return "✓" if ok else "✗"

    print(f"\n[pdfs] {pdfs['converted_pdfs']}/{pdfs['real_docx']} real DOCX converted, expected {EXPECTED_REAL_DOCX} {mark(pdfs['ok'])}")
    for m in pdfs["missing"][:5]:
        print(f"  missing: {m}")

    print(f"\n[items] total={items.get('total', '?')} expected={EXPECTED_TOTAL_ITEMS} {mark(items.get('total_ok'))}")
    print(f"        in_scope={items.get('in_scope', '?')} expected={EXPECTED_IN_SCOPE} {mark(items.get('in_scope_ok'))}")
    for f in items.get("bank_failures", []):
        print(f"  FAIL {f['bank']}: expected {f['expected']} got {f['actual']}")
    if items.get("by_type_in_scope"):
        print(f"        types in scope: {items['by_type_in_scope']}")

    print(f"\n[crops] {crops.get('present', '?')}/{crops.get('total', '?')} ({crops.get('pct', '?')}%) {mark(crops['ok'])}")
    if crops.get("missing"):
        print(f"  missing: {crops['missing_ids'][:5]} ... ({crops['missing']} total)")

    print(f"\n[keys] {mark(keys['ok'])}")
    for chk in keys.get("checks", []):
        print(f"  {mark(chk['ok'])} {chk['bank']}: actual {chk['actual']} expected {chk['expected']}")

    print(f"\n[render] page count: {render['compared']} Quartz twins, {render['mismatched']} beyond ±{MAX_PAGE_DELTA}; "
          f"glyphs: {render['glyph_checked']} PDFs, {render['glyph_failures']} failing {mark(render['ok'])}")
    for mm in render["mismatched_stems"][:5]:
        print(f"  page mismatch: {mm}")
    for gf in render["glyph_failure_files"][:5]:
        print(f"  glyph failure: {gf}")

    all_ok = all(v["ok"] for v in checks.values())
    print("\n" + "=" * 60)
    summary = (
        f"{pdfs['converted_pdfs']} PDFs, {items.get('total', 0)} items, {items.get('in_scope', 0)} in-scope, "
        f"{crops.get('pct', 0)}% crops, key-status {'matches' if keys['ok'] else 'differs from'} §3.2, "
        f"render check {'passing' if render['ok'] else 'failing'}"
    )
    print(f"GATE {'PASS' if all_ok else 'FAIL'}: {summary}")

    results = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **checks, "gate": {"ok": all_ok}}
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {out_path}")

    try:
        lavish = build_lavish(load_index(), checks)
        if lavish:
            print(f"Lavish board: {lavish}")
    except Exception as e:
        print(f"Lavish board failed: {e}")

    if args.strict and not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
