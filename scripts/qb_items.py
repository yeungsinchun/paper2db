#!/usr/bin/env python3
"""qb-items: parse QB DOCX XML into item JSON + per-item PNG crops.

Inputs:
  qb/**/*.docx  (or --qb-root)
  qb-pdf/<bank>/<stem>.pdf  (from qb_convert)
  qb-pdf/<bank>/<stem>.pdf.txt + tsv  (from qb_ocr, optional -- used for ocr field)

Outputs:
  qb-pdf/items/<bank>.json   {bank, generated_at, tool_versions, items: [...]}
  qb-pdf/items/index.json    flat id -> bank/type/marks/key-status
  qb-pdf/crops/<id>.png      stem crop
  qb-pdf/crops/<id>.ans.png  answer crop (if present)

Parsing:
  Document XML is walked in document order.  <w:t> runs give text,
  <w:sym> gives Symbol/Wingdings glyphs mapped to Unicode,
  <o:OLEObject> / <w:object> gives [eq:N] placeholders,
  <m:oMath> gives OMML equations (counted, replaced with [eq:N]),
  figures are detected via <wp:inline>/<wp:anchor> or word/media refs.

  Tags are HTML-escaped in the XML: &lt;code=PHY...&gt; so we unescape
  the joined w:t before regex.  But w:sym glyphs are NOT in w:t, so
  we must interleave them in XML order rather than joining w:t blindly.

Answer precedence when merging variants by code:
  _ans / _answer / _yes_ans DOCX  >  plain _e DOCX with ans blocks  >  PDF-only text layer (QB_202 MC)  >  missing
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QB = ROOT / "qb"
DEFAULT_PDF = ROOT / "qb-pdf"
CANDIDATE_QB_ROOTS = [
    ROOT / "qb",
    Path("/Users/sinchunyeung/github/paper2notes/qb"),
    Path("/Users/sinchunyeung/.treehouse/paper2everything-706dc8/1/paper2everything/paper2db/qb"),
]

# Symbol font mapping (Adobe Symbol encoding, subset)
SYMBOL_MAP = {
    'F020': ' ', 'F021': '!', 'F022': '\u2200', 'F023': '#', 'F024': '\u2203',
    'F025': '%', 'F026': '&', 'F027': '\u220B', 'F028': '(', 'F029': ')',
    'F02A': '\u2217', 'F02B': '+', 'F02C': ',', 'F02D': '-', 'F02E': '.', 'F02F': '/',
    'F030': '0', 'F031': '1', 'F032': '2', 'F033': '3', 'F034': '4',
    'F035': '5', 'F036': '6', 'F037': '7', 'F038': '8', 'F039': '9',
    'F03A': ':', 'F03B': ';', 'F03C': '<', 'F03D': '=', 'F03E': '>', 'F03F': '?',
    'F040': '\u2245', 'F041': '\u0391', 'F042': '\u0392', 'F043': '\u03A7', 'F044': '\u0394',
    'F045': '\u0395', 'F046': '\u03A6', 'F047': '\u0393', 'F048': '\u0397', 'F049': '\u0399',
    'F04A': '\u03D1', 'F04B': '\u039A', 'F04C': '\u039B', 'F04D': '\u039C', 'F04E': '\u039D',
    'F04F': '\u039F', 'F050': '\u03A0', 'F051': '\u0398', 'F052': '\u03A1', 'F053': '\u03A3',
    'F054': '\u03A4', 'F055': '\u03A5', 'F056': '\u03C2', 'F057': '\u03A9', 'F058': '\u039E',
    'F059': '\u03A8', 'F05A': '\u03A6', 'F05B': '[', 'F05C': '\u2234', 'F05D': ']', 'F05E': '\u22A5',
    'F05F': '_', 'F060': '\uF8E5', 'F061': '\u03B1', 'F062': '\u03B2', 'F063': '\u03C7',
    'F064': '\u03B4', 'F065': '\u03B5', 'F066': '\u03C6', 'F067': '\u03B3', 'F068': '\u03B7',
    'F069': '\u03B9', 'F06A': '\u03D5', 'F06B': '\u03BA', 'F06C': '\u03BB', 'F06D': '\u03BC',
    'F06E': '\u03BD', 'F06F': '\u03BF', 'F070': '\u03C0', 'F071': '\u03B8', 'F072': '\u03C1',
    'F073': '\u03C3', 'F074': '\u03C4', 'F075': '\u03C5', 'F076': '\u03D6', 'F077': '\u03C9',
    'F078': '\u03BE', 'F079': '\u03C8', 'F07A': '\u03B6', 'F07B': '{', 'F07C': '|',
    'F07D': '}', 'F07E': '\u223C', 'F0A0': '\u20AC',
    'F0A1': '\u03D2', 'F0A2': '\u2032', 'F0A3': '\u2264', 'F0A4': '\u2044', 'F0A5': '\u221E',
    'F0A6': '\u0192', 'F0A7': '\u2663', 'F0A8': '\u2666', 'F0A9': '\u2665', 'F0AA': '\u2660',
    'F0AB': '\u2194', 'F0AC': '\u2190', 'F0AD': '\u2191', 'F0AE': '\u2192', 'F0AF': '\u2193',
    'F0B0': '\u00B0', 'F0B1': '\u00B1', 'F0B2': '\u2033', 'F0B3': '\u2265', 'F0B4': '\u00D7',
    'F0B5': '\u221D', 'F0B6': '\u2202', 'F0B7': '\u2022', 'F0B8': '\u00F7', 'F0B9': '\u2260',
    'F0BA': '\u2261', 'F0BB': '\u2248', 'F0BC': '\u2026', 'F0BD': '\u2502', 'F0BE': '\u2500',
    'F0BF': '\u21B5', 'F0C0': '\u2135', 'F0C1': '\u2111', 'F0C2': '\u211C', 'F0C3': '\u2118',
    'F0C4': '\u2297', 'F0C5': '\u2295', 'F0C6': '\u2205', 'F0C7': '\u2229', 'F0C8': '\u222A',
    'F0C9': '\u2283', 'F0CA': '\u2287', 'F0CB': '\u2284', 'F0CC': '\u2282', 'F0CD': '\u2283',
    'F0CE': '\u2208', 'F0CF': '\u2209', 'F0D0': '\u2220', 'F0D1': '\u2207', 'F0D2': '\u00AE',
    'F0D3': '\u00A9', 'F0D4': '\u2122', 'F0D5': '\u220F', 'F0D6': '\u221A', 'F0D7': '\u00B7',
    'F0D8': '\u00AC', 'F0D9': '\u2227', 'F0DA': '\u2228', 'F0DB': '\u21D4', 'F0DC': '\u21D0',
    'F0DD': '\u21D1', 'F0DE': '\u21D2', 'F0DF': '\u21D3', 'F0E0': '\u25CA', 'F0E1': '\u2329',
    'F0E2': '\u00AE', 'F0E3': '\u00A9', 'F0E4': '\u2122', 'F0E5': '\u2211',
}
WINGDINGS_MAP = {
    'F021': '\u2702', 'F02D': '-', 'F0AB': '\u2192', 'F0AC': '\u2190', 'F0AD': '\u2191',
    'F0AE': '\u2192', 'F0AF': '\u2193', 'F09F': '\u25A0', 'F0A7': '\u25B2', 'F0A8': '\u25BC',
    'F0A9': '\u25C6', 'F0B7': '\u2022',
}


def find_qb_root(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_dir():
            raise SystemExit(f"--qb-root {p} not a directory")
        return p
    for cand in CANDIDATE_QB_ROOTS:
        if cand.is_dir() and any(cand.rglob("*.docx")):
            return cand
    return DEFAULT_QB


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool_versions() -> dict:
    versions = {}
    try:
        import pymupdf  # type: ignore

        versions["pymupdf"] = pymupdf.__version__
    except Exception:
        versions["pymupdf"] = "unknown"
    try:
        r = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=5)
        versions["tesseract"] = r.stdout.splitlines()[0] if r.returncode == 0 else "unknown"
    except Exception:
        versions["tesseract"] = "unknown"
    try:
        r = subprocess.run(["soffice", "--version"], capture_output=True, text=True, timeout=5)
        versions["soffice"] = r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        versions["soffice"] = "unknown"
    return versions


def answer_priority(filename: str) -> int:
    """Higher = more trusted. _ans/_answer/_yes_ans > plain."""
    name = filename.lower()
    if "_yes_ans" in name or "_answer" in name:
        return 3
    if "_ans" in name:
        return 2
    if "copy" in name:
        return 0
    return 1


def extract_docx_text_and_equations(docx_path: Path) -> tuple[str, int, int, int, bool]:
    """Walk document.xml in order, interleaving w:t and w:sym, counting OLE/OMML.

    Returns (text, eq_count, sym_count, image_count, has_figure)
    where text has w:sym mapped to Unicode and [eq:N] for each OLE/OMML.
    """
    z = zipfile.ZipFile(str(docx_path))
    try:
        xml = z.read("word/document.xml").decode()
    except KeyError:
        return ("", 0, 0, 0, False)

    media = [f for f in z.namelist() if f.startswith("word/media/")]
    has_figure = len(media) > 0 or ("<wp:inline" in xml) or ("<wp:anchor" in xml)

    # Count equations
    ole_count = xml.count("o:OLEObject") + xml.count("OLEObject")
    # More precise: count actual OLE objects
    ole_count = len(re.findall(r'<o:OLEObject\b', xml)) + len(re.findall(r'<w:object\b', xml))
    omml_count = len(re.findall(r'<m:oMath\b', xml))
    eq_total = ole_count + omml_count

    # We need to walk XML in order.  Use regex to find tokens in sequence:
    #  - <w:t[^>]*>([^<]*)</w:t>  -> text run
    #  - <w:sym[^>]*>  -> symbol
    #  - <w:object\b or <o:OLEObject\b  -> equation
    #  - <m:oMath\b  -> OMML equation
    # Process by scanning XML sequentially.

    # Combined pattern that captures all relevant tokens in order
    token_pat = re.compile(
        r'<w:t[^>]*>([^<]*)</w:t>'
        r'|<w:sym[^>]*w:font="([^"]*)"[^>]*w:char="([^"]*)"[^>]*/>'
        r'|<w:sym[^>]*w:char="([^"]*)"[^>]*w:font="([^"]*)"[^>]*/>'
        r'|<w:object\b[^>]*>'
        r'|<o:OLEObject\b[^>]*>'
        r'|<m:oMath\b[^>]*>',
        re.DOTALL,
    )

    parts: list[str] = []
    eq_idx = 0
    sym_count = 0

    for m in token_pat.finditer(xml):
        if m.group(1) is not None:
            # w:t
            parts.append(m.group(1))
        elif m.group(2) is not None:
            # w:sym font then char
            font, char = m.group(2), m.group(3)
            sym_count += 1
            if font == "Symbol":
                parts.append(SYMBOL_MAP.get(char, f"[sym:{char}]"))
            elif font == "Wingdings":
                parts.append(WINGDINGS_MAP.get(char, SYMBOL_MAP.get(char, f"[wing:{char}]")))
            else:
                parts.append(SYMBOL_MAP.get(char, f"[{font}:{char}]"))
        elif m.group(4) is not None:
            # w:sym char then font
            char, font = m.group(4), m.group(5)
            sym_count += 1
            if font == "Symbol":
                parts.append(SYMBOL_MAP.get(char, f"[sym:{char}]"))
            elif font == "Wingdings":
                parts.append(WINGDINGS_MAP.get(char, SYMBOL_MAP.get(char, f"[wing:{char}]")))
            else:
                parts.append(SYMBOL_MAP.get(char, f"[{font}:{char}]"))
        else:
            # object / OLE / oMath -> equation placeholder
            eq_idx += 1
            parts.append(f"[eq:{eq_idx}]")

    raw = "".join(parts)
    # Unescape HTML entities (tags use &lt; &gt;)
    text = html.unescape(raw)
    image_count = len(media)
    return text, eq_total, sym_count, image_count, has_figure


# Regex for tag grammar after unescaping: <code=PHY1...><lvl=...><part=...><type=...><mark=...><bk=...><ch=...><content>
# Some QB files have typos: extra spaces or missing '<' (e.g. "<type=lq> <mark=10>" or "lvl=easy>")
TAG_RE = re.compile(
    r"<code=(PHY1\w+)>\s*<?lvl=(\w+)>\s*<?part=(\w+)>\s*<?type=(\w+)>\s*<?mark=(\d+)>\s*<?bk=(\w+)>\s*<?ch=(\w+)>\s*<?content>"
)
# Answer markers: -- ans --  or  -- ans –  (en dash) and  -- ans end --
ANS_START_PAT = re.compile(r"--\s*ans\s*[-\u2013]+\s*", re.IGNORECASE)
ANS_END_PAT = re.compile(r"--\s*ans\s*end\s*--", re.IGNORECASE)
END_PAT = re.compile(r"<end>", re.IGNORECASE)


def parse_items_from_text(
    text: str,
    docx_path: Path,
    sha256: str,
    qb_root: Path,
    pdf_root: Path,
) -> list[dict]:
    """Split text by <code= tags into items, parse each item's fields."""
    items: list[dict] = []
    # Split on <code= while keeping it
    # Find all code tag positions
    tag_positions = [m.start() for m in TAG_RE.finditer(text)]
    if not tag_positions:
        return items

    for idx, pos in enumerate(tag_positions):
        next_pos = tag_positions[idx + 1] if idx + 1 < len(tag_positions) else len(text)
        block = text[pos:next_pos]

        m = TAG_RE.search(block)
        if not m:
            continue
        code, lvl, part, typ, mark, bk, ch = m.groups()
        # bk and ch are numeric but keep as string
        book = bk.lstrip("0") or "0"
        chapter = ch

        # Content is between <content> and -- ans  or <end>
        content_start = m.end()
        # Find ans start
        ans_m = ANS_START_PAT.search(block)
        end_m = END_PAT.search(block)

        if ans_m:
            stem_raw = block[content_start : ans_m.start()]
            # Answer block is between ans start and ans end
            ans_end_m = ANS_END_PAT.search(block, ans_m.end())
            if ans_end_m:
                ans_raw = block[ans_m.end() : ans_end_m.start()]
                has_ans_block = True
                # Trim ans_raw for key/worked
                ans_block_present = len(ans_raw.strip()) > 0
            else:
                ans_raw = block[ans_m.end() :]
                has_ans_block = True
                ans_block_present = len(ans_raw.strip()) > 0
                # Look for <end> after
                if end_m:
                    ans_raw = block[ans_m.end() : end_m.start()]
        else:
            # No ans marker
            if end_m:
                stem_raw = block[content_start : end_m.start()]
            else:
                stem_raw = block[content_start:]
            ans_raw = ""
            has_ans_block = False
            ans_block_present = False

        # Clean up stray « glyph
        stem_raw = stem_raw.replace("\u00ab", "").strip()
        ans_raw = ans_raw.replace("\u00ab", "").strip()

        # Parse options for MC: A ... B ... C ... D ... (tab or space separated)
        options: list[dict] = []
        subparts: list[dict] = []

        if typ == "mc":
            # Try to split stem into question stem + options
            # Options are labeled A, B, C, D with tab or newline
            # We keep stem as full text; options extracted separately for display
            # Pattern: A<text> B<text> C<text> D<text>  (with tabs/newlines)
            # Use a simple split on standalone A-D at line/tab boundaries
            opt_pat = re.compile(r"(?:^|[\t\n])\s*([A-D])\s*[.\u3001]?\s*")
            # Find option labels
            opt_matches = list(opt_pat.finditer("\n" + stem_raw))
            if len(opt_matches) >= 4:
                # Reconstruct stem without options
                stem_text = stem_raw[: opt_matches[0].start()].strip() if opt_matches else stem_raw.strip()
                # Extract options
                for oi, om in enumerate(opt_matches):
                    label = om.group(1)
                    start = om.end()
                    end = opt_matches[oi + 1].start() if oi + 1 < len(opt_matches) else len("\n" + stem_raw)
                    # Map back to stem_raw offset (we prepended \n)
                    raw_start = start - 1
                    raw_end = end - 1
                    opt_text = stem_raw[raw_start:raw_end].strip()
                    # Remove leading label
                    opt_text = re.sub(r"^[A-D]\s*[.\u3001]?\s*", "", opt_text).strip()
                    if label in ("A", "B", "C", "D"):
                        options.append({"label": label, "text": opt_text})
                # If we got 4 options, stem is question-only
                if len(options) == 4:
                    stem_text_clean = stem_text
                else:
                    stem_text_clean = stem_raw.strip()
                    options = []
            else:
                stem_text_clean = stem_raw.strip()
        else:
            stem_text_clean = stem_raw.strip()
            # Parse subparts for SQ/LQ/RQ: look for (a), (b)(i), (3 marks) etc.
            # Extract marks per subpart
            sub_pat = re.compile(r"\(([a-z])(?:\s*\((i+)\))?\)\s*(.*?)\s*\((\d+)\s*marks?\)", re.IGNORECASE | re.DOTALL)
            for sm in sub_pat.finditer(stem_raw):
                label_main, sub_idx, sub_text, marks = sm.groups()
                label = f"{label_main}" + (f"({sub_idx})" if sub_idx else "")
                sub_text = sub_text.strip()[:500]
                subparts.append({"label": label, "text": sub_text, "marks": int(marks)})

        # Parse answer
        answer_key: str | None = None
        marking: list[dict] = []
        worked = ""
        if has_ans_block and ans_block_present:
            worked = ans_raw.strip()
            # For MC, first char is key (A-D)
            if typ == "mc":
                # Key is first A-D letter, possibly with newline/tab
                km = re.search(r"^\s*([A-D])\b", ans_raw)
                if km:
                    answer_key = km.group(1)
                    # Worked is remainder
                    worked = ans_raw[km.end() :].strip()
                else:
                    # Some files have key without clear Answer separation
                    answer_key = None
            # For SQ/LQ/RQ, parse marking scheme rows: look for 1A, 1M, 2A etc
            if typ in ("sq", "lq", "rq"):
                # Each marking point is like " ... 1A" at end of line
                for line in ans_raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Look for trailing mark code
                    mm = re.search(r"(\d+)\s*([AM])\s*$", line)
                    if mm:
                        code_marks = mm.group(0)
                        point_text = line[: mm.start()].strip()
                        # Extract part label if present like "(a)(i)" or "a(i)"
                        part_m = re.match(r"\s*\(?([a-z])(?:\s*\((i+)\))?\)?", point_text, re.IGNORECASE)
                        part_label = part_m.group(0).strip() if part_m else "a"
                        marking.append({"part": part_label, "point": point_text[:300], "code": code_marks})
                if not marking:
                    # Keep worked as full answer
                    marking = []

        # Build sources entry
        rel_file = str(docx_path.relative_to(qb_root)) if docx_path.is_relative_to(qb_root) else str(docx_path)
        bank = docx_path.parent.name
        pdf_path = pdf_root / bank / (docx_path.stem + ".pdf")
        rel_pdf = str(pdf_path.relative_to(ROOT)) if pdf_path.is_relative_to(ROOT) else str(pdf_path)

        items.append(
            {
                "_code": code,
                "_lvl": lvl,
                "_part": part,
                "_type": typ,
                "_marks": int(mark),
                "_bk": book,
                "_ch": chapter,
                "_stem_raw": stem_raw,
                "_stem_clean": stem_text_clean if typ == "mc" else stem_raw.strip(),
                "_ans_raw": ans_raw,
                "_has_ans_block": has_ans_block,
                "_ans_present": ans_block_present,
                "_answer_key": answer_key,
                "_marking": marking,
                "_worked": worked[:2000],
                "_options": options,
                "_subparts": subparts,
                "_docx": docx_path,
                "_sha256": sha256,
                "_rel_file": rel_file,
                "_rel_pdf": rel_pdf,
                "_ans_priority": answer_priority(docx_path.name),
            }
        )

    return items


def parse_docx(docx_path: Path, qb_root: Path, pdf_root: Path) -> list[dict]:
    sha = sha256_file(docx_path)
    text, eq_count, sym_count, img_count, has_figure = extract_docx_text_and_equations(docx_path)
    raw_items = parse_items_from_text(text, docx_path, sha, qb_root, pdf_root)
    for it in raw_items:
        it["_eq_count"] = eq_count  # will be deduped later, but keep per-item estimate
        it["_sym_count"] = sym_count
        it["_img_count"] = img_count
        it["_has_figure"] = has_figure
        it["_docx_text"] = text  # for debugging
    return raw_items


def load_pdf_only_keys(qb_root: Path, pdf_root: Path) -> dict[str, str]:
    """Extract keys from the 6 PDF-only sources via text layer (for QB_202 MC etc).

    Uses PyMuPDF to read text; looks for answer keys near codes.
    For QB_202 MC blank docx has no keys; the PDF has worked answers.
    We parse the PDF text for keys as fallback.
    """
    keys: dict[str, str] = {}
    # Only the 6 known PDF-only files
    pdf_only_candidates = [
        qb_root / "QB_201/2_ch01_MC_e.pdf",
        qb_root / "QB_202/2_ch02_MC_e.pdf",
        qb_root / "QB_203/2_ch03_MC_e.pdf",
        qb_root / "QB_206/2_ch06_MC_e.pdf",
        qb_root / "QB_208/2_ch08_MC_e.pdf",
        qb_root / "QB_208/2_ch08_MC_e_blank.pdf",
    ]
    # Also check qb-pdf copies
    for pdf_path in pdf_only_candidates:
        if not pdf_path.is_file():
            alt = pdf_root / pdf_path.parent.name / pdf_path.name
            if alt.is_file():
                pdf_path = alt
            else:
                continue
        try:
            import pymupdf  # type: ignore

            doc = pymupdf.open(str(pdf_path))
            full_text = ""
            for page in doc:
                full_text += page.get_text() + "\n"
            doc.close()
            # Look for pattern: <code=PHY...> then ans key nearby
            # In PDF, structure is similar: code tag, content, then answer
            # Keys appear as single letter A-D after ans marker
            # We reuse TAG_RE but on PDF text (which has <code= decoded correctly)
            tag_positions = [m.start() for m in TAG_RE.finditer(full_text)]
            for idx, pos in enumerate(tag_positions):
                next_pos = tag_positions[idx + 1] if idx + 1 < len(tag_positions) else len(full_text)
                block = full_text[pos:next_pos]
                m = TAG_RE.search(block)
                if not m:
                    continue
                code = m.group(1)
                if code in keys:
                    continue
                ans_m = ANS_START_PAT.search(block)
                if ans_m:
                    ans_end_m = ANS_END_PAT.search(block, ans_m.end())
                    ans_raw = block[ans_m.end() : ans_end_m.start()] if ans_end_m else block[ans_m.end() :]
                    km = re.search(r"^\s*([A-D])\b", ans_raw.strip())
                    if km:
                        keys[code] = km.group(1)
        except Exception as e:
            print(f"  PDF-only key extraction failed for {pdf_path}: {e}", file=sys.stderr)
    return keys


def build_crop(pdf_path: Path, code: str, crop_dir: Path, next_code: str | None = None) -> tuple[Path | None, Path | None, dict]:
    """Crop a PDF to the item's stem and answer regions using text search.

    Uses PyMuPDF to find <code=...> anchors.  Crops from this code's y to
    next code's y (or page end).  Stem crop stops at -- ans marker.
    Returns (stem_png, ans_png, info).
    """
    info: dict = {"pages": [], "bbox_pt": None, "warnings": []}
    if not pdf_path.is_file():
        return None, None, info

    try:
        import pymupdf  # type: ignore

        doc = pymupdf.open(str(pdf_path))
    except Exception as e:
        info["warnings"].append(f"open failed: {e}")
        return None, None, info

    # Find anchor for this code and next code
    anchor_rect: tuple[float, float, float, float] | None = None
    anchor_page: int = 0
    next_rect_page: int | None = None
    next_rect: tuple[float, float, float, float] | None = None
    ans_rect: tuple[float, float, float, float] | None = None
    ans_page: int | None = None

    search = f"<code={code}>"
    # Search all pages
    for pno, page in enumerate(doc):
        rects = page.search_for(search)
        if rects:
            anchor_rect = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1)
            anchor_page = pno
            break
    if anchor_rect is None:
        # Try without <code= wrapper (some PDFs render differently)
        for pno, page in enumerate(doc):
            rects = page.search_for(code)
            if rects:
                anchor_rect = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1)
                anchor_page = pno
                break

    if anchor_rect is None:
        doc.close()
        info["warnings"].append("anchor not found")
        return None, None, info

    # Find ans marker on or after anchor page
    for pno in range(anchor_page, len(doc)):
        page = doc[pno]
        # Look for -- ans or en dash variant
        for needle in ("-- ans", "\u2013 ans"):
            rects = page.search_for(needle)
            if rects:
                # Take first rect that is below anchor if same page, else first
                for r in rects:
                    if pno == anchor_page and r.y0 < anchor_rect[1]:
                        continue
                    ans_rect = (r.x0, r.y0, r.x1, r.y1)
                    ans_page = pno
                    break
                if ans_rect:
                    break
        if ans_rect:
            break

    # Find next code anchor
    if next_code:
        search_next = f"<code={next_code}>"
        for pno in range(anchor_page, len(doc)):
            page = doc[pno]
            rects = page.search_for(search_next)
            if rects:
                # Must be after anchor
                for r in rects:
                    if pno == anchor_page and r.y0 <= anchor_rect[1]:
                        continue
                    next_rect = (r.x0, r.y0, r.x1, r.y1)
                    next_rect_page = pno
                    break
                if next_rect:
                    break

    # Determine crop rectangles
    # We crop at 2x scale for readability (144 dpi equivalent)
    try:
        from PIL import Image
        import io

        zoom = 2.0  # 144 dpi
        mat = pymupdf.Matrix(zoom, zoom)

        # Stem: anchor to ans_rect or next_rect or page bottom
        stem_end_page: int
        stem_end_y: float
        if ans_rect is not None:
            stem_end_page = ans_page  # type: ignore
            stem_end_y = ans_rect[1] - 2  # just above ans marker
        elif next_rect is not None:
            stem_end_page = next_rect_page  # type: ignore
            stem_end_y = next_rect[1] - 2
        else:
            stem_end_page = len(doc) - 1
            # Use page height
            stem_end_y = float(doc[stem_end_page].rect.height)

        # Render stem pages: anchor_page .. stem_end_page
        # For each page, clip to [anchor_y, end_y] and stack vertically
        stem_pixmaps = []
        for pno in range(anchor_page, stem_end_page + 1):
            page = doc[pno]
            rect = page.rect
            if pno == anchor_page:
                clip_y0 = anchor_rect[1] - 4  # slightly above anchor
            else:
                clip_y0 = 0
            if pno == stem_end_page:
                clip_y1 = min(stem_end_y + 4, rect.height)
            else:
                clip_y1 = rect.height
            if clip_y1 <= clip_y0:
                continue
            clip = pymupdf.Rect(0, clip_y0, rect.width, clip_y1)
            pix = page.get_pixmap(matrix=mat, clip=clip)
            stem_pixmaps.append(pix)

        if stem_pixmaps:
            # Stack vertically
            total_h = sum(p.height for p in stem_pixmaps)
            max_w = max(p.width for p in stem_pixmaps)
            # Composite via PIL
            from PIL import Image as PILImage

            combined = PILImage.new("RGB", (max_w, total_h), (255, 255, 255))
            y_off = 0
            for pix in stem_pixmaps:
                img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                combined.paste(img, (0, y_off))
                y_off += pix.height
            stem_png = crop_dir / f"{code}.png"
            stem_png.parent.mkdir(parents=True, exist_ok=True)
            combined.save(stem_png, "PNG")
            stem_result: Path | None = stem_png
            info["pages"] = list(range(anchor_page + 1, stem_end_page + 2))
            info["bbox_pt"] = [anchor_rect[0], anchor_rect[1], anchor_rect[2], anchor_rect[3]]
            if stem_end_page != anchor_page:
                info["warnings"].append("crop_multi_page")
        else:
            stem_result = None

        # Answer crop: ans_rect to next_rect or end
        ans_result: Path | None = None
        if ans_rect is not None:
            ans_end_page = next_rect_page if next_rect is not None else len(doc) - 1
            ans_end_y = next_rect[1] - 2 if next_rect is not None else float(doc[ans_end_page].rect.height) if ans_end_page is not None else 0
            ans_pixmaps = []
            for pno in range(ans_page, (ans_end_page + 1) if ans_end_page is not None else ans_page + 1):  # type: ignore
                page = doc[pno]
                rect = page.rect
                if pno == ans_page:
                    clip_y0 = ans_rect[1] - 2  # type: ignore
                else:
                    clip_y0 = 0
                if ans_end_page is not None and pno == ans_end_page:
                    clip_y1 = min(ans_end_y + 4, rect.height)  # type: ignore
                else:
                    clip_y1 = rect.height
                if clip_y1 <= clip_y0:
                    continue
                clip = pymupdf.Rect(0, clip_y0, rect.width, clip_y1)
                pix = page.get_pixmap(matrix=mat, clip=clip)
                ans_pixmaps.append(pix)
            if ans_pixmaps:
                total_h = sum(p.height for p in ans_pixmaps)
                max_w = max(p.width for p in ans_pixmaps)
                combined_a = PILImage.new("RGB", (max_w, total_h), (255, 255, 255))
                y_off = 0
                for pix in ans_pixmaps:
                    img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    combined_a.paste(img, (0, y_off))
                    y_off += pix.height
                ans_png = crop_dir / f"{code}.ans.png"
                combined_a.save(ans_png, "PNG")
                ans_result = ans_png

        doc.close()
        return stem_result, ans_result, info

    except Exception as e:
        doc.close()
        info["warnings"].append(f"render failed: {e}")
        return None, None, info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qb-root", default=None)
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF))
    parser.add_argument("--out", default=str(DEFAULT_PDF / "items"))
    parser.add_argument("--crop-dir", default=str(DEFAULT_PDF / "crops"))
    parser.add_argument("--only-bank", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    qb_root = find_qb_root(args.qb_root)
    pdf_root = Path(args.pdf_root)
    if not pdf_root.is_absolute():
        pdf_root = ROOT / pdf_root
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    crop_dir = Path(args.crop_dir)
    if not crop_dir.is_absolute():
        crop_dir = ROOT / crop_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)

    # Collect DOCX files
    all_docx = sorted(qb_root.rglob("*.docx"))
    real_docx = [p for p in all_docx if not p.name.startswith("~$")]
    if args.only_bank:
        real_docx = [p for p in real_docx if p.parent.name == args.only_bank]

    print(f"QB root: {qb_root}  ({len(real_docx)} real docx)")
    print(f"PDF root: {pdf_root}")
    print(f"Out: {out_dir}  Crops: {crop_dir}")

    # Parse all DOCX, collecting items by code
    by_code: dict[str, list[dict]] = defaultdict(list)
    all_banks: set[str] = set()
    for docx in real_docx:
        bank = docx.parent.name
        all_banks.add(bank)
        try:
            items = parse_docx(docx, qb_root, pdf_root)
            for it in items:
                by_code[it["_code"]].append(it)
        except Exception as e:
            print(f"  parse failed {docx}: {e}", file=sys.stderr)

    print(f"Unique codes (all banks): {len(by_code)}")

    # Load PDF-only keys for fallback
    pdf_only_keys = load_pdf_only_keys(qb_root, pdf_root)
    if pdf_only_keys:
        print(f"  PDF-only keys: {len(pdf_only_keys)} from quartz PDFs")

    # Merge by code: pick best variant
    merged: dict[str, dict] = {}
    for code, variants in by_code.items():
        # Sort by answer priority desc, then by stem length desc (more complete)
        variants_sorted = sorted(variants, key=lambda v: (v["_ans_priority"], len(v["_stem_clean"])), reverse=True)
        best = variants_sorted[0]
        # Detect variant conflict: same code, differing stems
        warnings: list[str] = []
        if len(variants) > 1:
            stems = set(v["_stem_clean"][:200] for v in variants)
            if len(stems) > 1:
                warnings.append("variant_conflict")
        # Resolve answer: prefer variant with key, else fallback
        answer_source = None
        answer_status = "missing"
        answer_key = None
        worked = ""
        marking: list[dict] = []
        answer_warnings: list[str] = []
        # Find first variant with answer key
        for v in variants_sorted:
            if v["_answer_key"] is not None or v["_has_ans_block"]:
                answer_key = v["_answer_key"]
                worked = v["_worked"]
                marking = v["_marking"]
                answer_source = v["_rel_file"]
                answer_status = "present"
                break
        if answer_status == "missing" and code in pdf_only_keys:
            answer_key = pdf_only_keys[code]
            answer_status = "from-pdf"
            # Find the PDF file that had it
            answer_source = "qb-pdf PDF-only (quartz)"
            answer_warnings.append("from_pdf_key")
        if answer_key is None and answer_status in ("present", "from-pdf"):
            # Has ans block but no single-letter key (SQ/LQ/RQ)
            pass
        if answer_status == "missing":
            answer_warnings.append("key_missing")

        merged[code] = {
            "_best": best,
            "_variants": variants,
            "_warnings": warnings + answer_warnings,
            "_answer_key": answer_key,
            "_answer_status": answer_status,
            "_answer_source": answer_source,
            "_worked": worked,
            "_marking": marking,
        }

    # Group by bank for output (bank derived from code: PHY1 B CC T ...  book= B)
    # But codes encode book: PHY1<book><ch><type><...>
    # We also track original bank folders. Use the best variant's bank.
    by_bank: dict[str, list[str]] = defaultdict(list)
    for code, data in merged.items():
        bank = data["_best"]["_docx"].parent.name
        by_bank[bank].append(code)

    # For cropping we need ordered codes per PDF to find "next" anchor.
    # Order items as they appear in the PDF (by code search order), but we don't
    # have PDF order easily.  Use docx appearance order per bank instead.
    # Build per-bank ordered list from parsing sequence.
    bank_order: dict[str, list[str]] = defaultdict(list)
    for docx in real_docx:
        try:
            items = parse_docx(docx, qb_root, pdf_root)
            for it in items:
                code = it["_code"]
                bank = docx.parent.name
                if code not in bank_order[bank]:
                    bank_order[bank].append(code)
        except Exception:
            pass

    # Now build final JSON items per bank
    versions = tool_versions()
    # Load OCR text for stem.ocr field if available
    in_scope_banks = {f"QB_{i}" for i in list(range(201, 211)) + list(range(401, 409)) + list(range(501, 504))}

    total_items = len(merged)
    in_scope_items = sum(1 for code in merged if any(code.startswith(f"PHY1{int(b.split('_')[1]):02d}") for b in in_scope_banks))
    # Better: check book field
    in_scope_count = 0
    for code, data in merged.items():
        bk = data["_best"]["_bk"]
        ch = data["_best"]["_ch"]
        # Map to bank: QB_<book><ch> -- but book can be multiple digits? For QB_503, book=5 ch=03
        try:
            bank_guess = f"QB_{int(bk):d}{ch}"
            # For 2-digit book+ch combos: QB_201 etc
            # Actually bank is like QB_501 for book 5 ch 01
            bank_guess2 = f"QB_{bk}{ch}" if len(bk) == 1 else f"QB_{bk}{ch}"
            # Simpler: use actual bank from best variant
            actual_bank = data["_best"]["_docx"].parent.name
            if actual_bank in in_scope_banks:
                in_scope_count += 1
        except Exception:
            pass

    print(f"Total unique: {total_items}  In-scope: {in_scope_count}")

    # Build per-bank JSON + crops
    all_index: list[dict] = []
    crop_ok = 0
    crop_fail = 0

    for bank in sorted(by_bank.keys()):
        if args.only_bank and bank != args.only_bank:
            continue
        codes = sorted(by_bank[bank])  # sorted by code string approximates seq order
        # But prefer document order if available
        if bank in bank_order:
            ordered = [c for c in bank_order[bank] if c in codes]
            # Append any missing codes sorted
            remaining = sorted(set(codes) - set(ordered))
            codes = ordered + remaining

        json_items: list[dict] = []
        for idx, code in enumerate(codes):
            data = merged[code]
            best = data["_best"]
            next_code = codes[idx + 1] if idx + 1 < len(codes) else None

            # Determine fields
            bk_ch = best["_bk"]
            # book from variant _bk (already stripped leading zeros)
            book = bk_ch
            chapter = best["_ch"]
            seq = int(code[-2:])  # last 2 digits
            typ = best["_type"]  # mc/sq/lq/rq mapping: type digit 1-4
            # Map type from tag: already mc/sq/lq/rq string
            level = best["_lvl"] if best["_lvl"] in ("easy", "avg", "dif") else "avg"
            part = best["_part"] if best["_part"] in ("core", "ext") else "core"
            marks = int(best["_marks"])
            stem_text = best["_stem_clean"][:8000]
            # Count equations in stem (approx via [eq:N] placeholders)
            eq_count = stem_text.count("[eq:")
            has_figure = bool(best["_has_figure"])
            equation_only = eq_count > 0 and len(stem_text.replace("[eq:", "").strip()) < 20

            # Options / subparts
            options = best["_options"]
            subparts = best["_subparts"]

            answer_status = data["_answer_status"]
            answer_key = data["_answer_key"]
            worked = data["_worked"]
            marking = data["_marking"]
            answer_source = data["_answer_source"]

            warnings = list(data["_warnings"])
            if has_figure:
                pass
            if eq_count >= 3:
                warnings.append("equation_heavy")

            # OCR text: try to read from qb-pdf/<bank>/<stem>.pdf.txt cropped region?
            # For now, read the full PDF OCR and slice around code (cheap).
            ocr_text = ""
            # Try to load OCR from the bank's main PDF's ocr
            bank_pdfs = list((pdf_root / bank).glob("*.pdf"))
            # Prefer the pdf matching best's docx stem
            best_pdf = pdf_root / bank / (best["_docx"].stem + ".pdf")
            if best_pdf.is_file():
                ocr_path = best_pdf.with_suffix(".pdf.txt")
                if ocr_path.is_file():
                    full_ocr = ocr_path.read_text(encoding="utf-8", errors="ignore")
                    # Slice around code
                    idx_ocr = full_ocr.find(code)
                    if idx_ocr >= 0:
                        ocr_text = full_ocr[max(0, idx_ocr - 500) : idx_ocr + 3000].strip()[:3000]
                    else:
                        ocr_text = full_ocr[:3000]

            # Crop
            pdf_for_crop = pdf_root / bank / (best["_docx"].stem + ".pdf")
            # If that stem PDF missing, try any pdf in bank
            if not pdf_for_crop.is_file() and bank_pdfs:
                pdf_for_crop = bank_pdfs[0]

            stem_png: Path | None = None
            ans_png: Path | None = None
            crop_info: dict = {"pages": [], "bbox_pt": None, "warnings": []}
            # Check existing crops unless force
            existing_stem = crop_dir / f"{code}.png"
            existing_ans = crop_dir / f"{code}.ans.png"
            if existing_stem.is_file() and not args.force:
                stem_png = existing_stem
                # Still try to get crop_info from log? Skip rendering
                crop_info["pages"] = []
            else:
                stem_png, ans_png, crop_info = build_crop(pdf_for_crop, code, crop_dir, next_code)
                if stem_png is not None:
                    crop_ok += 1
                else:
                    crop_fail += 1
                if ans_png is None and answer_status != "missing":
                    # Answer crop may be separate; not fatal
                    pass

            if crop_info.get("warnings"):
                for w in crop_info["warnings"]:
                    if w not in warnings:
                        warnings.append(w)

            stem_images = [f"crops/{code}.png"] if (crop_dir / f"{code}.png").is_file() else []
            ans_images = [f"crops/{code}.ans.png"] if (crop_dir / f"{code}.ans.png").is_file() else []

            # Sources: include all variants that contributed
            sources = []
            for v in data["_variants"]:
                pdf_p = pdf_root / v["_docx"].parent.name / (v["_docx"].stem + ".pdf")
                rel_pdf = str(pdf_p.relative_to(ROOT)) if pdf_p.is_relative_to(ROOT) else str(pdf_p)
                sources.append(
                    {
                        "file": v["_rel_file"],
                        "sha256": v["_sha256"],
                        "pdf": rel_pdf,
                        "pages": crop_info.get("pages", []),
                        "bbox_pt": crop_info.get("bbox_pt"),
                        "origin": "libreoffice" if pdf_p.is_file() else "quartz-pdf",
                    }
                )
            # Deduplicate sources by file
            seen_files = set()
            uniq_sources = []
            for s in sources:
                if s["file"] not in seen_files:
                    uniq_sources.append(s)
                    seen_files.add(s["file"])

            # Fix origin for PDF-only banks
            if pdf_for_crop.name in ("2_ch02_MC_e.pdf",) and bank == "QB_202":
                for s in uniq_sources:
                    if "quartz" in s["pdf"]:
                        s["origin"] = "quartz-pdf"

            item = {
                "schema": "paper2db.qb-item.v1",
                "id": code,
                "bank": bank,
                "book": book,
                "chapter": chapter,
                "seq": seq,
                "type": typ,
                "level": level,
                "part": part,
                "marks": marks,
                "stem": {
                    "text": stem_text,
                    "ocr": ocr_text[:3000],
                    "equations": eq_count,
                    "has_figure": has_figure,
                    "equation_only": equation_only,
                },
                "options": options,
                "subparts": subparts,
                "answer": {
                    "status": answer_status,
                    "key": answer_key,
                    "worked": worked,
                    "marking": marking,
                    "source": answer_source,
                },
                "images": {"stem": stem_images, "answer": ans_images},
                "sources": uniq_sources,
                "warnings": warnings,
            }
            json_items.append(item)

            # Index entry
            all_index.append(
                {
                    "id": code,
                    "bank": bank,
                    "book": book,
                    "chapter": chapter,
                    "type": typ,
                    "level": level,
                    "part": part,
                    "marks": marks,
                    "status": answer_status,
                    "has_crop": bool(stem_images),
                }
            )

        # Write bank JSON
        bank_out = out_dir / f"{bank}.json"
        payload = {
            "bank": bank,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_versions": versions,
            "count": len(json_items),
            "items": json_items,
        }
        bank_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"  {bank}: {len(json_items)} items -> {bank_out.relative_to(ROOT)}")

    # Write index
    index_path = out_dir / "index.json"
    index_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(all_index),
        "tool_versions": versions,
        "items": sorted(all_index, key=lambda x: x["id"]),
    }
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {index_path}  total={len(all_index)}")

    # Summary for gate
    in_scope_index = [e for e in all_index if e["bank"] in in_scope_banks]
    with_crop = [e for e in all_index if e["has_crop"]]
    print(f"Crops: {crop_ok} new, {crop_fail} failed, {len(with_crop)}/{len(all_index)} with crop on disk")
    print(f"In-scope banks: {sorted(in_scope_banks)} -> {len(in_scope_index)} items")
    # Count by status
    status_counts = Counter(e["status"] for e in all_index)
    print(f"Answer status: {dict(status_counts)}")
    # Per in-scope bank breakdown
    for bank in sorted(in_scope_banks):
        entries = [e for e in in_scope_index if e["bank"] == bank]
        if entries:
            sc = Counter(e["status"] for e in entries)
            print(f"  {bank}: {len(entries)} items status={dict(sc)}")


if __name__ == "__main__":
    main()
