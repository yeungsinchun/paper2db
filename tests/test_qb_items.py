"""Unit tests for qb_items parsing (synthetic DOCX fixture, no copyrighted content)."""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import qb_items

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def t(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


TAB = "<w:r><w:tab/></w:r>"


def tag(code: str, typ: str, mark: int) -> list[str]:
    return [
        para(t(f"&lt;code={code}&gt;")),
        para(t(f"&lt;lvl=easy&gt;&lt;part=core&gt;&lt;type={typ}&gt;&lt;mark={mark}&gt;&lt;bk=5&gt;&lt;ch=01&gt;&lt;content&gt;")),
    ]


def table(rows: list[list[list[str]]]) -> str:
    """rows -> cells -> paragraphs, laid out the way Word writes a marking scheme."""
    out = "<w:tbl>"
    for row in rows:
        out += "<w:tr>"
        for cell in row:
            out += "<w:tc>" + "".join(para(t(p)) if p else para() for p in cell) + "</w:tc>"
        out += "</w:tr>"
    return out + "</w:tbl>"


def write_docx(path: Path, body: list[str]) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {W_NS}><w:body>" + "".join(body) + "</w:body></w:document>"
    )
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("word/document.xml", document_xml)


def mc_block(code: str, key: str | None, *, figure: bool = False, equation: bool = False) -> list[str]:
    body = tag(code, "mc", 2)
    stem_runs = [TAB, t("Which radiation is most ionizing?")]
    if equation:
        stem_runs.append('<w:r><w:object><v:shape/><o:OLEObject ProgID="Equation.3"/></w:object></w:r>')
    body.append(para(*stem_runs))
    if figure:
        body.append(para('<w:r><w:drawing><wp:inline/></w:drawing></w:r>'))
    for label, text in [("A", "alpha"), ("B", "beta"), ("C", "gamma"), ("D", "X-ray")]:
        body.append(para(TAB, t(label), TAB, t(text)))
    ans_cell = ["-- ans –"] + ([key, "Alpha particles carry charge."] if key else []) + ["-- ans end --"]
    body.append(table([[ans_cell]]))
    body.append(para(t("&lt;end&gt;")))
    return body


def sq_block(code: str) -> list[str]:
    body = tag(code, "sq", 3)
    body.append(para(TAB, t("(a)"), TAB, t("State one property of alpha particles. (1 mark)")))
    body.append(para(TAB, t("(b)"), TAB, t("(i)"), TAB, t("Explain why they are deflected. (2 marks)")))
    body.append(para(t("-- ans --")))
    body.append(
        table(
            [
                [["Solutions"], ["Marks"]],
                [["(a) They carry positive charge."], ["1A"]],
                [["(b)"], [""]],
                [["(i) They are charged", "and massive."], ["1M", "1A"]],
            ]
        )
    )
    body.append(para(t("-- ans end --")))
    body.append(para(t("&lt;end&gt;")))
    return body


def make_docx_with_sym(path: Path, sym_char: str = "F061", sym_font: str = "Symbol") -> None:
    write_docx(path, [para(t("Test "), f'<w:r><w:sym w:font="{sym_font}" w:char="{sym_char}"/></w:r>', t(" radiation"))])


class TestQbItemsParse(unittest.TestCase):
    def parse(self, body: list[str], name: str = "5_ch01_e.docx") -> list[dict]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        tmp = Path(tmpdir.name)
        docx = tmp / name
        write_docx(docx, body)
        return qb_items.parse_docx(docx, tmp)

    def test_mc_options_key_and_worked_from_word_paragraphs(self) -> None:
        (item,) = self.parse(mc_block("PHY15011101", "A"))
        self.assertEqual(item["_code"], "PHY15011101")
        self.assertEqual(item["_answer_key"], "A")
        self.assertEqual(item["_worked"], "Alpha particles carry charge.")
        self.assertEqual(
            item["_options"],
            [{"label": "A", "text": "alpha"}, {"label": "B", "text": "beta"}, {"label": "C", "text": "gamma"}, {"label": "D", "text": "X-ray"}],
        )
        self.assertEqual(item["_stem_clean"], "Which radiation is most ionizing?")

    def test_marking_rows_and_subparts_from_table(self) -> None:
        (item,) = self.parse(sq_block("PHY15011201"))
        self.assertEqual(
            item["_marking"],
            [
                {"part": "a", "point": "They carry positive charge.", "code": "1A"},
                {"part": "b(i)", "point": "They are charged\nand massive.", "code": "1M 1A"},
            ],
        )
        self.assertEqual(
            [(s["label"], s["marks"]) for s in item["_subparts"]],
            [("a", 1), ("b(i)", 2)],
        )
        self.assertTrue(item["_ans_present"])
        self.assertNotIn(qb_items.CELL, item["_worked"])
        self.assertNotIn(qb_items.ROW, item["_worked"])

    def test_has_figure_and_equations_are_per_item(self) -> None:
        items = self.parse(mc_block("PHY15011101", "A", figure=True, equation=True) + mc_block("PHY15011102", "B"))
        self.assertEqual([it["_has_figure"] for it in items], [True, False])
        self.assertEqual(items[0]["_stem_clean"].count("[eq:"), 1)
        self.assertEqual(items[1]["_stem_clean"].count("[eq:"), 0)

    def test_code_tag_split_across_paragraphs(self) -> None:
        body = [para(t("&lt;")), para(t("code=PHY15011103&gt;"))] + mc_block("PHY15011101", "C")[1:]
        (item,) = self.parse(body)
        self.assertEqual(item["_code"], "PHY15011103")

    def test_missing_ans_block(self) -> None:
        body = tag("PHY15011199", "mc", 2) + [para(t("Question without answer")), para(t("&lt;end&gt;"))]
        (item,) = self.parse(body)
        self.assertFalse(item["_has_ans_block"])
        self.assertIsNone(item["_answer_key"])

    def test_sym_mapped_to_unicode(self) -> None:
        for char, expected in [("F061", "α"), ("F062", "β"), ("F067", "γ"), ("61", "α")]:
            with tempfile.TemporaryDirectory() as tmp:
                docx = Path(tmp) / "sym.docx"
                make_docx_with_sym(docx, char, "Symbol")
                self.assertEqual(qb_items.extract_docx_text_and_equations(docx), f"Test {expected} radiation\n")

    def test_answer_priority(self) -> None:
        self.assertGreater(qb_items.answer_priority("5_ch01_MC_e_ans.docx"), qb_items.answer_priority("5_ch01_MC_e.docx"))
        self.assertGreater(qb_items.answer_priority("file_answer.docx"), qb_items.answer_priority("file_ans.docx"))

    def test_schema_file_exists(self) -> None:
        schema = Path(__file__).resolve().parents[1] / "schemas" / "qb-item.v1.json"
        data = json.loads(schema.read_text())
        self.assertEqual(data["title"], "paper2db.qb-item.v1")


class TestResolveAnswer(unittest.TestCase):
    def variant(self, typ: str, key: str | None, present: bool, name: str) -> dict:
        return {
            "_type": typ,
            "_answer_key": key,
            "_ans_present": present,
            "_has_ans_block": True,
            "_worked": "w",
            "_marking": [],
            "_rel_file": name,
        }

    def test_mc_block_without_key_falls_through_to_keyed_variant(self) -> None:
        variants = [self.variant("mc", None, True, "x_ans.docx"), self.variant("mc", "B", True, "x.docx")]
        answer = qb_items.resolve_answer(variants, {}, "PHY1")
        self.assertEqual((answer["status"], answer["key"], answer["source"]), ("present", "B", "x.docx"))

    def test_mc_without_key_uses_pdf_then_missing(self) -> None:
        variants = [self.variant("mc", None, True, "x_ans.docx")]
        from_pdf = qb_items.resolve_answer(variants, {"PHY1": ("C", "QB_202/2_ch02_MC_e.pdf")}, "PHY1")
        self.assertEqual((from_pdf["status"], from_pdf["key"], from_pdf["warnings"]), ("from-pdf", "C", ["from_pdf_key"]))
        missing = qb_items.resolve_answer(variants, {}, "PHY1")
        self.assertEqual((missing["status"], missing["key"], missing["warnings"]), ("missing", None, ["key_missing"]))

    def test_empty_ans_block_is_not_present(self) -> None:
        answer = qb_items.resolve_answer([self.variant("lq", None, False, "x_ans.docx")], {}, "PHY1")
        self.assertEqual(answer["status"], "missing")


class TestOcrSlice(unittest.TestCase):
    def test_slice_stops_at_ans_and_is_empty_when_code_absent(self) -> None:
        ocr = "header <code=PHY1A> stem A -- ans -- B worked <code=PHY1B> next"
        self.assertEqual(qb_items.ocr_slice(ocr, "PHY1A", "PHY1B"), "PHY1A> stem A")
        self.assertEqual(qb_items.ocr_slice("no codes here", "PHY1A", None), "")


class TestSymbolCoverage(unittest.TestCase):
    def test_every_sym_code_in_corpus_is_mapped(self) -> None:
        # Every w:sym w:char and every PUA code point that appears as Symbol-font
        # text in the real QB corpus must have an entry in SYMBOL_MAP or WINGDINGS_MAP,
        # otherwise normalize_symbol_xml would leave PUA in the PDF and the glyph check fails.
        qb_root = None
        for cand in qb_items.CANDIDATE_QB_ROOTS:
            if cand.is_dir() and any(cand.rglob("*.docx")):
                qb_root = cand
                break
        if qb_root is None:
            self.skipTest("no QB corpus available (gitignored)")
        import re, zipfile
        unseen: dict[str, list[str]] = {}
        for docx in qb_root.rglob("*.docx"):
            if docx.name.startswith("~$"):
                continue
            try:
                xml = zipfile.ZipFile(str(docx)).read("word/document.xml").decode()
            except Exception:
                continue
            for m in re.finditer(r'<w:sym[^>]*w:char="([^"]*)"', xml):
                char = m.group(1)
                key = qb_items.symbol_key(char)
                if key not in qb_items.SYMBOL_MAP and key not in qb_items.WINGDINGS_MAP:
                    unseen.setdefault(key, []).append(f"{docx.parent.name}/{docx.name}")
            # Also check PUA in Symbol-font runs via the normalizer's glyph set
            _, glyphs = qb_items.normalize_symbol_xml(xml)
            # Any PUA that survived normalization and is in Symbol range is unmapped
            for c in re.findall(r'[\uF000-\uF0FF]', xml):
                key = f"{ord(c):04X}"
                if key not in qb_items.SYMBOL_MAP and key not in qb_items.WINGDINGS_MAP:
                    # Only count if the DOCX actually contains that PUA as text (not just in binary)
                    unseen.setdefault(key, []).append(f"{docx.parent.name}/{docx.name} PUA")
        if unseen:
            sample = ", ".join(f"{k} in {v[0]}" for k, v in sorted(unseen.items())[:5])
            self.fail(f"Unmapped Symbol codes in corpus: {sample} (and {len(unseen)-5} more) - add to SYMBOL_MAP/WINGDINGS_MAP")


if __name__ == "__main__":
    unittest.main()
