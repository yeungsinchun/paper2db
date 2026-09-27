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
  <o:OLEObject> and <m:oMath> give [eq:N] placeholders,
  <w:drawing> / <w:pict> give [fig:N] placeholders (per-item has_figure),
  paragraph ends / <w:br/> give newlines and <w:tab/> gives tabs.

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
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
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
    'F05F': '_', 'F060': '\u203E', 'F061': '\u03B1', 'F062': '\u03B2', 'F063': '\u03C7',
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


def find_qb_root(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_dir():
            raise SystemExit(f"--qb-root {p} not a directory")
        return p
    for cand in CANDIDATE_QB_ROOTS:
        if cand.is_dir() and any(cand.rglob("*.docx")):
            return cand
    return None


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


def symbol_key(char: str) -> str:
    """Normalise a w:char value ("61" or "F061") to a SYMBOL_MAP key."""
    return f"{int(char, 16) | 0xF000:04X}"


def sym_text(font: str, char: str) -> str:
    key = symbol_key(char)
    if font == "Symbol":
        return SYMBOL_MAP.get(key, f"[sym:{char}]")
    if font == "Wingdings":
        return WINGDINGS_MAP.get(key, SYMBOL_MAP.get(key, f"[wing:{char}]"))
    return SYMBOL_MAP.get(key, f"[{font}:{char}]")


SYM_RE = re.compile(r"<w:sym\b[^>]*/>")
RUN_RE = re.compile(r"<w:r\b[^>]*>.*?</w:r>", re.DOTALL)
RFONTS_RE = re.compile(r"<w:rFonts\b[^>]*/>")
T_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")
SYMBOL_FONT_ATTR_RE = re.compile(r'(w:(?:ascii|hAnsi)=")Symbol(")')
REPLACEMENT_FONT = "Times New Roman"


def symbol_run_char(c: str) -> str | None:
    n = ord(c)
    if 0xF000 <= n <= 0xF0FF:
        return SYMBOL_MAP.get(f"{n:04X}")
    return None


def normalize_symbol_xml(xml: str) -> tuple[str, list[str]]:
    """Rewrite Symbol-font glyphs in document.xml as Unicode text.

    Returns (new_xml, glyphs) where glyphs lists every non-ASCII character
    produced, in document order -- the reference the render check expects to
    find in the PDF text layer.
    """
    glyphs: list[str] = []

    def sym(m: re.Match) -> str:
        tag = m.group(0)
        font = re.search(r'w:font="([^"]*)"', tag)
        char = re.search(r'w:char="([^"]*)"', tag)
        if not (font and char and font.group(1) == "Symbol"):
            return tag
        u = SYMBOL_MAP.get(symbol_key(char.group(1)))
        if u is None:
            return tag
        if not u.isascii():
            glyphs.append(u)
        return f'<w:t xml:space="preserve">{escape(u)}</w:t>'

    def run(m: re.Match) -> str:
        r = m.group(0)
        fonts = RFONTS_RE.search(r)
        if not (fonts and SYMBOL_FONT_ATTR_RE.search(fonts.group(0))):
            return r

        mapped = []
        unmapped = []

        def text(tm: re.Match) -> str:
            out = []
            for c in html.unescape(tm.group(2)):
                u = symbol_run_char(c)
                if u is None:
                    unmapped.append(c)
                else:
                    mapped.append(u)
                out.append(c if u is None else u)
            return tm.group(1) + escape("".join(out)) + tm.group(3)

        r = T_RE.sub(text, r)
        if not mapped:
            return m.group(0)
        glyphs.extend(u for u in mapped if not u.isascii())
        if unmapped:
            return r
        new_fonts = SYMBOL_FONT_ATTR_RE.sub(rf"\g<1>{REPLACEMENT_FONT}\g<2>", fonts.group(0))
        return r.replace(fonts.group(0), new_fonts, 1)

    xml = SYM_RE.sub(sym, xml)
    xml = RUN_RE.sub(run, xml)
    return xml, glyphs


# Table cell / row separators emitted by the XML walker so the marking-scheme
# parser can see the two-column layout; stripped from every output text field.
CELL = "\x1f"
ROW = "\x1e"

TOKEN_RE = re.compile(
    r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>"
    r"|(<w:sym\b[^>]*/>)"
    r"|(<o:OLEObject\b|<m:oMath\b)"
    r"|(<w:drawing\b|<w:pict\b)"
    r"|(</w:p>|<w:br\b[^>]*/>|<w:cr\s*/>)"
    r"|(<w:tab\s*/>)"
    r"|(</w:tc>)"
    r"|(</w:tr>)"
)
SYM_FONT_RE = re.compile(r'w:font="([^"]*)"')
SYM_CHAR_RE = re.compile(r'w:char="([^"]*)"')


def extract_docx_text_and_equations(docx_path: Path) -> str:
    """Walk document.xml in order, interleaving w:t and w:sym.

    Paragraph ends and <w:br/> become "\n", <w:tab/> becomes "\t", table cell
    and row ends become CELL / ROW, each OLE/OMML equation becomes [eq:N] and
    each drawing/picture becomes [fig:N].
    """
    with zipfile.ZipFile(str(docx_path)) as z:
        try:
            xml = z.read("word/document.xml").decode()
        except KeyError:
            return ""
    xml = normalize_symbol_xml(xml)[0]

    parts: list[str] = []
    eq_idx = 0
    fig_idx = 0
    for m in TOKEN_RE.finditer(xml):
        if m.group(1) is not None:
            parts.append(m.group(1))
        elif m.group(2):
            font = SYM_FONT_RE.search(m.group(2))
            char = SYM_CHAR_RE.search(m.group(2))
            if font and char:
                parts.append(sym_text(font.group(1), char.group(1)))
        elif m.group(3):
            eq_idx += 1
            parts.append(f"[eq:{eq_idx}]")
        elif m.group(4):
            fig_idx += 1
            parts.append(f"[fig:{fig_idx}]")
        elif m.group(5):
            parts.append("\n")
        elif m.group(6):
            parts.append("\t")
        elif m.group(7):
            parts.append(CELL)
        else:
            parts.append(ROW)

    # Tags are stored HTML-escaped (&lt;code=...&gt;)
    return html.unescape("".join(parts))


# Regex for tag grammar after unescaping: <code=PHY1...><lvl=...><part=...><type=...><mark=...><bk=...><ch=...><content>
# Some QB files have typos: extra spaces or missing '<' (e.g. "<type=lq> <mark=10>" or "lvl=easy>")
TAG_RE = re.compile(
    r"<\s*code=(PHY1\w+)>\s*<?lvl=(\w+)>\s*<?part=(\w+)>\s*<?type=(\w+)>\s*<?mark=(\d+)>\s*<?bk=(\w+)>\s*<?ch=(\w+)>\s*<?content>"
)
# Answer markers: -- ans --  or  -- ans –  (en dash) and  -- ans end --
ANS_START_PAT = re.compile(r"--\s*ans\s*[-–]+\s*", re.IGNORECASE)
ANS_END_PAT = re.compile(r"--\s*ans\s*end\s*--", re.IGNORECASE)
END_PAT = re.compile(r"<end>", re.IGNORECASE)
MC_KEY_RE = re.compile(r"\s*([A-D])\b")
PDF_CODE_RE = re.compile(r"<\s*code=(PHY1\w+)>")
PDF_KEY_RE = re.compile(r"(?m)^[ \t]*([A-D])[ \t]*$")
OPTION_RE = re.compile(r"(?m)(?:^|(?<=\t))[ \t]*([A-D])(?![A-Za-z])[ \t]*[.、]?[ \t]*")
SUBPART_RE = re.compile(r"\((?:([a-h])\)\s*\()?([a-h]|[ivx]{1,4})\)\s*(.*?)\s*\((\d+)\s*marks?\)", re.DOTALL)
MARK_TAIL_RE = re.compile(r"(?:\d+\s*[AM]\s*)+$")
MARK_CODE_RE = re.compile(r"(\d+)\s*([AM])")
PART_LABEL_RE = re.compile(r"\s*(?:\(([a-h])\))?\s*(?:\(([ivx]{1,4})\))?\s*")


def clean_text(s: str) -> str:
    return s.replace(CELL, "").replace(ROW, "").replace("«", "").strip()


def parse_options(stem: str) -> tuple[str, list[dict]]:
    """Split an MC stem into (question, options) using the last A-B-C-D label run."""
    matches = list(OPTION_RE.finditer(stem))
    labels = [m.group(1) for m in matches]
    for i in range(len(matches) - 4, -1, -1):
        if labels[i : i + 4] == ["A", "B", "C", "D"]:
            run = matches[i : i + 4]
            options = []
            for j, om in enumerate(run):
                end = run[j + 1].start() if j + 1 < len(run) else len(stem)
                options.append({"label": om.group(1), "text": stem[om.end() : end].strip()})
            return stem[: run[0].start()].strip(), options
    return stem.strip(), []


def parse_subparts(stem: str) -> list[dict]:
    subparts: list[dict] = []
    main = ""
    for sm in SUBPART_RE.finditer(stem):
        outer, label_part, sub_text, marks = sm.groups()
        if outer:
            main, label = outer, f"{outer}({label_part})"
        elif re.fullmatch(r"[a-h]", label_part):
            main, label = label_part, label_part
        else:
            label = f"{main}({label_part})"
        subparts.append({"label": label, "text": sub_text.strip()[:500], "marks": int(marks)})
    return subparts


def parse_marking(ans_raw: str) -> list[dict]:
    """Marking-scheme rows: table rows (point cells | mark cell) or plain lines ending in 1A/1M."""
    rows: list[list[str]] = []
    for chunk in ans_raw.split(ROW):
        if CELL in chunk:
            rows.append(chunk.split(CELL))
        else:
            rows.extend([line] for line in chunk.splitlines())

    marking: list[dict] = []
    main = ""
    label = ""
    for cells in rows:
        cells = [clean_text(c) for c in cells]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        tail = MARK_TAIL_RE.search(cells[-1])
        point = " ".join(c for c in cells[:-1] + [cells[-1][: tail.start()] if tail else cells[-1]] if c.strip())
        lm = PART_LABEL_RE.match(point)
        if lm.group(1):
            main = label = lm.group(1)
        if lm.group(2):
            label = f"{main}({lm.group(2)})"
        if not tail:
            continue
        codes = " ".join(n + t for n, t in MARK_CODE_RE.findall(tail.group(0)))
        marking.append({"part": label, "point": point[lm.end() :].strip()[:300], "code": codes})
    return marking


def parse_items_from_text(
    text: str,
    docx_path: Path,
    sha256: str,
    qb_root: Path,
) -> list[dict]:
    """Split text by <code= tags into items, parse each item's fields."""
    items: list[dict] = []
    tag_positions = [m.start() for m in TAG_RE.finditer(text)]
    rel_file = str(docx_path.relative_to(qb_root)) if docx_path.is_relative_to(qb_root) else str(docx_path)

    for idx, pos in enumerate(tag_positions):
        next_pos = tag_positions[idx + 1] if idx + 1 < len(tag_positions) else len(text)
        block = text[pos:next_pos]

        m = TAG_RE.match(block)
        if not m:
            continue
        code, lvl, part, typ, mark, bk, ch = m.groups()
        book = bk.lstrip("0") or "0"

        content_start = m.end()
        ans_m = ANS_START_PAT.search(block, content_start)
        end_m = END_PAT.search(block, content_start)
        has_ans_block = ans_m is not None
        if ans_m:
            stem_raw = block[content_start : ans_m.start()]
            ans_end_m = ANS_END_PAT.search(block, ans_m.end())
            if ans_end_m:
                ans_raw = block[ans_m.end() : ans_end_m.start()]
            elif end_m and end_m.start() > ans_m.end():
                ans_raw = block[ans_m.end() : end_m.start()]
            else:
                ans_raw = block[ans_m.end() :]
        else:
            stem_raw = block[content_start : end_m.start()] if end_m else block[content_start:]
            ans_raw = ""

        marking = parse_marking(ans_raw) if typ in ("sq", "lq", "rq") else []
        stem_raw = clean_text(stem_raw)
        ans_text = clean_text(ans_raw)

        options: list[dict] = []
        subparts: list[dict] = []
        if typ == "mc":
            stem_clean, options = parse_options(stem_raw)
        else:
            stem_clean = stem_raw
            subparts = parse_subparts(stem_raw)

        answer_key: str | None = None
        worked = ans_text
        if typ == "mc":
            km = MC_KEY_RE.match(ans_text)
            if km:
                answer_key = km.group(1)
                worked = ans_text[km.end() :].strip()

        items.append(
            {
                "_code": code,
                "_lvl": lvl,
                "_part": part,
                "_type": typ,
                "_marks": int(mark),
                "_bk": book,
                "_ch": ch,
                "_stem_raw": stem_raw,
                "_stem_clean": stem_clean,
                "_has_figure": "[fig:" in stem_raw,
                "_has_ans_block": has_ans_block,
                "_ans_present": bool(ans_text),
                "_answer_key": answer_key,
                "_marking": marking,
                "_worked": worked[:2000],
                "_options": options,
                "_subparts": subparts,
                "_docx": docx_path,
                "_sha256": sha256,
                "_rel_file": rel_file,
                "_ans_priority": answer_priority(docx_path.name),
            }
        )

    return items


def parse_docx(docx_path: Path, qb_root: Path) -> list[dict]:
    text = extract_docx_text_and_equations(docx_path)
    return parse_items_from_text(text, docx_path, sha256_file(docx_path), qb_root)


def is_keyed(item: dict) -> bool:
    """An item variant carries a usable answer: an MC letter, or a non-empty ans block."""
    if item["_type"] == "mc":
        return item["_answer_key"] is not None
    return item["_ans_present"]


def load_pdf_only_keys(qb_root: Path, pdf_root: Path) -> dict[str, tuple[str, str]]:
    """Extract MC keys from the 6 PDF-only sources via their text layer.

    Returns code -> (key, source pdf).  QB_202 MC keys exist only here.
    """
    keys: dict[str, tuple[str, str]] = {}
    pdf_only_candidates = [
        qb_root / "QB_201/2_ch01_MC_e.pdf",
        qb_root / "QB_202/2_ch02_MC_e.pdf",
        qb_root / "QB_203/2_ch03_MC_e.pdf",
        qb_root / "QB_206/2_ch06_MC_e.pdf",
        qb_root / "QB_208/2_ch08_MC_e.pdf",
        qb_root / "QB_208/2_ch08_MC_e_blank.pdf",
    ]
    for pdf_path in pdf_only_candidates:
        if not pdf_path.is_file():
            alt = pdf_root / pdf_path.parent.name / pdf_path.name
            if alt.is_file():
                pdf_path = alt
            else:
                continue
        rel = f"{pdf_path.parent.name}/{pdf_path.name}"
        try:
            import pymupdf  # type: ignore

            doc = pymupdf.open(str(pdf_path))
            full_text = "\n".join(page.get_text() for page in doc)
            doc.close()
            codes = list(PDF_CODE_RE.finditer(full_text))
            for idx, m in enumerate(codes):
                next_pos = codes[idx + 1].start() if idx + 1 < len(codes) else len(full_text)
                block = full_text[m.end() : next_pos]
                if m.group(1) in keys:
                    continue
                ans_m = ANS_START_PAT.search(block)
                if ans_m:
                    ans_end_m = ANS_END_PAT.search(block, ans_m.end())
                    ans_raw = block[ans_m.end() : ans_end_m.start()] if ans_end_m else block[ans_m.end() :]
                    km = PDF_KEY_RE.search(ans_raw)
                    if km:
                        keys[m.group(1)] = (km.group(1), rel)
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

    # Find next code anchor (next item in this PDF's own order)
    if next_code:
        search_next = f"<code={next_code}>"
        for pno in range(anchor_page, len(doc)):
            for r in doc[pno].search_for(search_next):
                if pno == anchor_page and r.y0 <= anchor_rect[1]:
                    continue
                next_rect = (r.x0, r.y0, r.x1, r.y1)
                next_rect_page = pno
                break
            if next_rect:
                break
    next_pos = (next_rect_page, next_rect[1]) if next_rect is not None else None

    # Find this item's ans marker: after the anchor and before the next item
    for pno in range(anchor_page, len(doc) if next_pos is None else next_pos[0] + 1):
        hits = [r for needle in ("-- ans", "\u2013 ans") for r in doc[pno].search_for(needle)]
        for r in sorted(hits, key=lambda r: r.y0):
            if pno == anchor_page and r.y0 < anchor_rect[1]:
                continue
            if next_pos is not None and (pno, r.y0) >= next_pos:
                break
            ans_rect = (r.x0, r.y0, r.x1, r.y1)
            ans_page = pno
            break
        if ans_rect:
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


def ocr_slice(full_ocr: str, code: str, next_code: str | None) -> str:
    """OCR text from this item's code up to its ans marker or the next item's code."""
    start = full_ocr.find(code)
    if start < 0:
        return ""
    end = len(full_ocr)
    if next_code:
        n = full_ocr.find(next_code, start + len(code))
        if n >= 0:
            end = n
    ans = re.search(r"--\s*ans", full_ocr[start:end], re.IGNORECASE)
    if ans:
        end = start + ans.start()
    return full_ocr[start:end].strip()


def resolve_answer(variants_sorted: list[dict], pdf_only_keys: dict[str, tuple[str, str]], code: str) -> dict:
    """Answer precedence: keyed DOCX variant (by priority) > PDF-only text layer > missing."""
    for v in variants_sorted:
        if is_keyed(v):
            return {
                "status": "present",
                "key": v["_answer_key"],
                "worked": v["_worked"],
                "marking": v["_marking"],
                "source": v["_rel_file"],
                "warnings": [],
            }
    if code in pdf_only_keys:
        key, source = pdf_only_keys[code]
        return {"status": "from-pdf", "key": key, "worked": "", "marking": [], "source": source, "warnings": ["from_pdf_key"]}
    return {"status": "missing", "key": None, "worked": "", "marking": [], "source": None, "warnings": ["key_missing"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qb-root", default=None)
    parser.add_argument("--pdf-root", default=str(DEFAULT_PDF))
    parser.add_argument("--out", default=str(DEFAULT_PDF / "items"))
    parser.add_argument("--crop-dir", default=str(DEFAULT_PDF / "crops"))
    parser.add_argument("--only-bank", default=None)
    args = parser.parse_args()

    qb_root = find_qb_root(args.qb_root)
    if qb_root is None:
        raise SystemExit("No qb/ found. Pass --qb-root /path/to/qb  (tried: " + ", ".join(str(c) for c in CANDIDATE_QB_ROOTS) + ")")
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

    all_docx = sorted(qb_root.rglob("*.docx"))
    real_docx = [p for p in all_docx if not p.name.startswith("~$")]
    if args.only_bank:
        real_docx = [p for p in real_docx if p.parent.name == args.only_bank]

    print(f"QB root: {qb_root}  ({len(real_docx)} real docx)")
    print(f"PDF root: {pdf_root}")
    print(f"Out: {out_dir}  Crops: {crop_dir}")

    # Parse all DOCX once, collecting items by code and each DOCX's code order
    by_code: dict[str, list[dict]] = defaultdict(list)
    docx_order: dict[Path, list[str]] = {}
    bank_order: dict[str, list[str]] = defaultdict(list)
    for docx in real_docx:
        try:
            items = parse_docx(docx, qb_root)
        except Exception as e:
            print(f"  parse failed {docx}: {e}", file=sys.stderr)
            continue
        docx_order[docx] = [it["_code"] for it in items]
        for it in items:
            by_code[it["_code"]].append(it)
            if it["_code"] not in bank_order[docx.parent.name]:
                bank_order[docx.parent.name].append(it["_code"])

    print(f"Unique codes (all banks): {len(by_code)}")

    pdf_only_keys = load_pdf_only_keys(qb_root, pdf_root)
    if pdf_only_keys:
        print(f"  PDF-only keys: {len(pdf_only_keys)} from quartz PDFs")

    # Merge by code: pick best variant (answer priority, then longest stem)
    merged: dict[str, dict] = {}
    for code, variants in by_code.items():
        variants_sorted = sorted(variants, key=lambda v: (v["_ans_priority"], len(v["_stem_clean"])), reverse=True)
        warnings: list[str] = []
        if len({v["_stem_clean"][:200] for v in variants}) > 1:
            warnings.append("variant_conflict")
        answer = resolve_answer(variants_sorted, pdf_only_keys, code)
        merged[code] = {
            "_best": variants_sorted[0],
            "_variants": variants,
            "_warnings": warnings + answer.pop("warnings"),
            "_answer": answer,
        }

    by_bank: dict[str, list[str]] = defaultdict(list)
    for code, data in merged.items():
        by_bank[data["_best"]["_docx"].parent.name].append(code)

    versions = tool_versions()
    in_scope_banks = {f"QB_{i}" for i in list(range(201, 211)) + list(range(401, 409)) + list(range(501, 504))}
    in_scope_count = sum(len(codes) for bank, codes in by_bank.items() if bank in in_scope_banks)
    print(f"Total unique: {len(merged)}  In-scope: {in_scope_count}")

    all_index: list[dict] = []
    crop_ok = 0
    crop_fail = 0
    ocr_cache: dict[Path, str] = {}

    for bank in sorted(by_bank.keys()):
        bank_codes = set(by_bank[bank])
        codes = [c for c in bank_order[bank] if c in bank_codes]

        json_items: list[dict] = []
        for code in codes:
            data = merged[code]
            best = data["_best"]
            answer = data["_answer"]
            order = docx_order[best["_docx"]]
            pos = order.index(code)
            next_code = order[pos + 1] if pos + 1 < len(order) else None

            typ = best["_type"]
            level = best["_lvl"] if best["_lvl"] in ("easy", "avg", "dif") else "avg"
            part = best["_part"] if best["_part"] in ("core", "ext") else "core"
            stem_text = best["_stem_clean"][:8000]
            eq_count = stem_text.count("[eq:")
            has_figure = bool(best["_has_figure"])
            equation_only = eq_count > 0 and len(stem_text.replace("[eq:", "").strip()) < 20

            warnings = list(data["_warnings"])
            if eq_count >= 3:
                warnings.append("equation_heavy")

            pdf_for_crop = pdf_root / bank / (best["_docx"].stem + ".pdf")
            ocr_text = ""
            ocr_path = pdf_for_crop.with_suffix(".pdf.txt")
            if ocr_path.is_file():
                if ocr_path not in ocr_cache:
                    ocr_cache[ocr_path] = ocr_path.read_text(encoding="utf-8", errors="ignore")
                ocr_text = ocr_slice(ocr_cache[ocr_path], code, next_code)

            for stale in (crop_dir / f"{code}.png", crop_dir / f"{code}.ans.png"):
                stale.unlink(missing_ok=True)
            stem_png, ans_png, crop_info = build_crop(pdf_for_crop, code, crop_dir, next_code)
            if stem_png is not None:
                crop_ok += 1
            else:
                crop_fail += 1
            for w in crop_info["warnings"]:
                if w not in warnings:
                    warnings.append(w)

            stem_images = [f"crops/{code}.png"] if stem_png is not None else []
            ans_images = [f"crops/{code}.ans.png"] if ans_png is not None else []

            sources = []
            seen_files: set[str] = set()
            for v in data["_variants"]:
                if v["_rel_file"] in seen_files:
                    continue
                seen_files.add(v["_rel_file"])
                pdf_p = pdf_root / v["_docx"].parent.name / (v["_docx"].stem + ".pdf")
                cropped = pdf_p == pdf_for_crop
                sources.append(
                    {
                        "file": v["_rel_file"],
                        "sha256": v["_sha256"],
                        "pdf": str(pdf_p.relative_to(ROOT)) if pdf_p.is_relative_to(ROOT) else str(pdf_p),
                        "pages": crop_info["pages"] if cropped else [],
                        "bbox_pt": crop_info["bbox_pt"] if cropped else None,
                        "origin": "libreoffice",
                    }
                )

            item = {
                "schema": "paper2db.qb-item.v1",
                "id": code,
                "bank": bank,
                "book": best["_bk"],
                "chapter": best["_ch"],
                "seq": int(code[-2:]),
                "type": typ,
                "level": level,
                "part": part,
                "marks": int(best["_marks"]),
                "stem": {
                    "text": stem_text,
                    "ocr": ocr_text[:3000],
                    "equations": eq_count,
                    "has_figure": has_figure,
                    "equation_only": equation_only,
                },
                "options": best["_options"],
                "subparts": best["_subparts"],
                "answer": answer,
                "images": {"stem": stem_images, "answer": ans_images},
                "sources": sources,
                "warnings": warnings,
            }
            json_items.append(item)

            all_index.append(
                {
                    "id": code,
                    "bank": bank,
                    "book": item["book"],
                    "chapter": item["chapter"],
                    "type": typ,
                    "level": level,
                    "part": part,
                    "marks": item["marks"],
                    "status": answer["status"],
                    "has_crop": bool(stem_images),
                }
            )

        bank_out = out_dir / f"{bank}.json"
        payload = {
            "bank": bank,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_versions": versions,
            "count": len(json_items),
            "items": json_items,
        }
        bank_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        print(f"  {bank}: {len(json_items)} items -> {bank_out}")

    index_path = out_dir / "index.json"
    index_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(all_index),
        "tool_versions": versions,
        "items": sorted(all_index, key=lambda x: x["id"]),
    }
    index_path.write_text(json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n")
    print(f"\nWrote {index_path}  total={len(all_index)}")

    in_scope_index = [e for e in all_index if e["bank"] in in_scope_banks]
    print(f"Crops: {crop_ok} ok, {crop_fail} failed")
    print(f"In-scope banks: {sorted(in_scope_banks)} -> {len(in_scope_index)} items")
    print(f"Answer status: {dict(Counter(e['status'] for e in all_index))}")
    for bank in sorted(in_scope_banks):
        entries = [e for e in in_scope_index if e["bank"] == bank]
        if entries:
            print(f"  {bank}: {len(entries)} items status={dict(Counter(e['status'] for e in entries))}")


if __name__ == "__main__":
    main()
