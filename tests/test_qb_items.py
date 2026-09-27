"""Unit tests for qb_items parsing (synthetic DOCX fixture, no copyrighted content)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import qb_items


def make_docx(path: Path, blocks: list[dict]) -> None:
    """Create a minimal DOCX with given item blocks.

    Each block: {code, lvl, part, type, mark, bk, ch, content, ans_key, worked}
    """
    # Minimal document.xml
    paragraphs = []
    for b in blocks:
        tag = f"&lt;code={b['code']}&gt;&lt;lvl={b['lvl']}&gt;&lt;part={b['part']}&gt;&lt;type={b['type']}&gt;&lt;mark={b['mark']}&gt;&lt;bk={b['bk']}&gt;&lt;ch={b['ch']}&gt;&lt;content&gt;"
        content = b["content"]
        ans = ""
        if b.get("ans_key") or b.get("worked"):
            ans = f"-- ans --\n{b.get('ans_key','')}\n{b.get('worked','')}\n-- ans end --"
        end = "&lt;end&gt;"
        full = tag + content + ans + end
        # Wrap in w:p/w:r/w:t (escape handled by joining w:t)
        paragraphs.append(f'<w:p><w:r><w:t>{full}</w:t></w:r></w:p>')

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    word_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )

    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", word_rels)
        z.writestr("word/document.xml", document_xml)


def make_docx_with_sym(path: Path, sym_char: str = "F061", sym_font: str = "Symbol") -> None:
    """Create a DOCX with a w:sym glyph between text runs."""
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        '<w:p><w:r><w:t>Test </w:t></w:r>'
        f'<w:r><w:sym w:font="{sym_font}" w:char="{sym_char}"/></w:r>'
        '<w:r><w:t> radiation</w:t></w:r>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    word_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", word_rels)
        z.writestr("word/document.xml", document_xml)


class TestQbItemsParse(unittest.TestCase):
    def test_two_items_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx = tmp_path / "test.docx"
            make_docx(
                docx,
                [
                    {
                        "code": "PHY15011101",
                        "lvl": "easy",
                        "part": "core",
                        "type": "mc",
                        "mark": "2",
                        "bk": "5",
                        "ch": "01",
                        "content": "Which statement is correct? A yes B no",
                        "ans_key": "A",
                        "worked": "Because ...",
                    },
                    {
                        "code": "PHY15011201",
                        "lvl": "avg",
                        "part": "core",
                        "type": "sq",
                        "mark": "3",
                        "bk": "5",
                        "ch": "01",
                        "content": "Explain alpha decay. (3 marks)",
                        "ans_key": "",
                        "worked": "Alpha particles 1A. Energy 1M.",
                    },
                ],
            )
            items = qb_items.parse_docx(docx, tmp_path, tmp_path / "qb-pdf")
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["_code"], "PHY15011101")
            self.assertEqual(items[0]["_type"], "mc")
            self.assertEqual(items[0]["_answer_key"], "A")
            self.assertEqual(items[1]["_code"], "PHY15011201")
            self.assertEqual(items[1]["_type"], "sq")

    def test_sym_mapped_to_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx = tmp_path / "sym.docx"
            make_docx_with_sym(docx, "F061", "Symbol")
            text, eq, sym, img, has_fig = qb_items.extract_docx_text_and_equations(docx)
            self.assertIn("\u03b1", text)
            self.assertEqual(sym, 1)

    def test_sym_beta_gamma(self) -> None:
        for char, expected in [("F061", "\u03b1"), ("F062", "\u03b2"), ("F067", "\u03b3")]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                docx = tmp_path / "sym.docx"
                make_docx_with_sym(docx, char, "Symbol")
                text, *_ = qb_items.extract_docx_text_and_equations(docx)
                self.assertIn(expected, text, f"Symbol {char} should map to {expected}")

    def test_answer_priority(self) -> None:
        self.assertGreater(qb_items.answer_priority("5_ch01_MC_e_ans.docx"), qb_items.answer_priority("5_ch01_MC_e.docx"))
        self.assertGreater(qb_items.answer_priority("file_answer.docx"), qb_items.answer_priority("file_yes_ans.docx") - 1)

    def test_tag_regex(self) -> None:
        text = "<code=PHY15011101><lvl=easy><part=core><type=mc><mark=2><bk=5><ch=01><content>hello"
        m = qb_items.TAG_RE.search(text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "PHY15011101")

    def test_missing_ans_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx = tmp_path / "no_ans.docx"
            make_docx(
                docx,
                [
                    {
                        "code": "PHY15011199",
                        "lvl": "easy",
                        "part": "core",
                        "type": "mc",
                        "mark": "2",
                        "bk": "5",
                        "ch": "01",
                        "content": "Question without answer",
                    }
                ],
            )
            items = qb_items.parse_docx(docx, tmp_path, tmp_path / "qb-pdf")
            self.assertEqual(len(items), 1)
            self.assertFalse(items[0]["_has_ans_block"])
            self.assertIsNone(items[0]["_answer_key"])

    def test_schema_file_exists(self) -> None:
        schema = Path(__file__).resolve().parents[1] / "schemas" / "qb-item.v1.json"
        self.assertTrue(schema.is_file(), "schemas/qb-item.v1.json must exist")
        data = json.loads(schema.read_text())
        self.assertEqual(data["title"], "paper2db.qb-item.v1")


if __name__ == "__main__":
    unittest.main()
