"""Tests for qb pipeline stage wiring, the converter's Symbol mapping and gate logic."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_docx(path: Path, body: str) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + body
        + "</w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("word/document.xml", xml)


SYMBOL_BODY = (
    '<w:p><w:r><w:t xml:space="preserve">decay </w:t></w:r>'
    '<w:r><w:sym w:font="Symbol" w:char="F061"/></w:r>'
    '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>\uf062</w:t></w:r>'
    '<w:r><w:sym w:font="Wingdings" w:char="F09F"/></w:r></w:p>'
)


class TestQbPipelineStages(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipe = load_module("paper2db_pipeline", ROOT / "pipeline")

    def test_select_qb_only(self) -> None:
        args = mock.Mock(only="qb-pdf,qb-items", from_stage=None, until=None, skip_lavish=False)
        self.assertEqual(self.pipe.select_stages(args), ["qb-pdf", "qb-items"])

    def test_qb_stages_skip_without_qb_sources(self) -> None:
        with mock.patch.object(self.pipe, "qb_source_root", return_value=None), mock.patch.object(self.pipe, "run_script") as run:
            self.pipe.stage_qb_pdf(force=True)
            self.pipe.stage_qb_ocr(force=True)
            self.pipe.stage_qb_items(force=True)
            self.pipe.stage_qb_audit()
        run.assert_not_called()

    def test_force_is_passed_through_and_audit_is_strict(self) -> None:
        with mock.patch.object(self.pipe, "qb_source_root", return_value=Path("/qb")), mock.patch.object(self.pipe, "run_script") as run:
            self.pipe.stage_qb_pdf(force=True)
            self.pipe.stage_qb_ocr(force=True)
            self.pipe.stage_qb_pdf(force=False)
            self.pipe.stage_qb_audit()
        calls = [c.args for c in run.call_args_list]
        self.assertEqual(calls[0], ("qb_convert.py", "--workers", "2", "--force"))
        self.assertEqual(calls[1], ("qb_ocr.py", "--workers", "4", "--force"))
        self.assertEqual(calls[2], ("qb_convert.py", "--workers", "2"))
        self.assertEqual(calls[3], ("qb_quality.py", "--strict"))


class TestQbConvertSymbols(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conv = load_module("qb_convert", ROOT / "scripts" / "qb_convert.py")
        cls.items = load_module("qb_items_for_convert", ROOT / "scripts" / "qb_items.py")

    def test_symbol_sym_and_runs_become_unicode_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.docx"
            dest = Path(tmp) / "out.docx"
            write_docx(src, SYMBOL_BODY)
            count = self.conv.write_normalized_docx(src, dest)
            self.assertEqual(count, 2)
            with zipfile.ZipFile(str(dest)) as z:
                xml = z.read("word/document.xml").decode()
            self.assertNotIn('w:font="Symbol"', xml)
            self.assertNotIn('w:ascii="Symbol"', xml)
            self.assertIn('w:font="Wingdings"', xml)
            self.assertEqual(self.items.extract_docx_text_and_equations(dest), "decay αβ■\n")
            self.assertEqual(self.items.extract_docx_text_and_equations(src), "decay αβ■\n")
            self.assertEqual(self.conv.docx_symbol_glyphs(src), ["α", "β"])


    def test_latin_text_is_never_remapped(self) -> None:
        cases = [
            '<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Symbol"/></w:rPr><w:t>a b</w:t></w:r>',
            '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>a b</w:t></w:r>',
            '<w:r><w:rPr><w:rFonts w:cs="Symbol"/></w:rPr><w:t>\uf061</w:t></w:r>',
        ]
        for run in cases:
            xml = f"<w:p>{run}</w:p>"
            self.assertEqual(self.items.normalize_symbol_xml(xml), (xml, []), run)

    def test_symbol_chars_follow_adobe_symbol_encoding(self) -> None:
        xml = (
            '<w:p><w:r><w:sym w:font="Symbol" w:char="F05A"/></w:r>'
            '<w:r><w:sym w:font="Symbol" w:char="F046"/></w:r>'
            '<w:r><w:sym w:font="Symbol" w:char="F0C9"/></w:r>'
            '<w:r><w:sym w:font="Symbol" w:char="F0CD"/></w:r></w:p>'
        )
        self.assertEqual(self.items.normalize_symbol_xml(xml)[1], ["\u0396", "\u03a6", "\u2283", "\u2286"])

    def test_mixed_symbol_run_maps_pua_and_keeps_font(self) -> None:
        xml = '<w:p><w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>\uf067 a</w:t></w:r></w:p>'
        new_xml, glyphs = self.items.normalize_symbol_xml(xml)
        self.assertEqual(glyphs, ["\u03b3"])
        self.assertIn("<w:t>\u03b3 a</w:t>", new_xml)
        self.assertIn('w:ascii="Symbol"', new_xml)


class TestQbQualityAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module("qb_quality", ROOT / "scripts" / "qb_quality.py")

    def plan_index(self) -> list[dict]:
        items = []
        for bank, (count, with_key) in self.mod.EXPECTED_BANKS.items():
            from_pdf = self.mod.EXPECTED_FROM_PDF.get(bank, 0)
            for i in range(count):
                if i < with_key:
                    typ, status = ("sq" if i % 2 else "mc"), "present"
                else:
                    typ, status = "mc", ("from-pdf" if i < with_key + from_pdf else "missing")
                items.append({"id": f"{bank}_{i}", "bank": bank, "type": typ, "status": status})
        for i in range(self.mod.EXPECTED_TOTAL_ITEMS - len(items)):
            items.append({"id": f"OUT_{i}", "bank": "QB_101", "type": "mc", "status": "present"})
        return items

    def run_checks(self, items: list[dict]) -> tuple[dict, dict, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            items_dir = Path(tmp) / "items"
            items_dir.mkdir()
            (items_dir / "index.json").write_text(json.dumps({"items": items, "total": len(items)}))
            crops_dir = Path(tmp) / "crops"
            crops_dir.mkdir()
            for e in items:
                (crops_dir / f"{e['id']}.png").write_bytes(b"\x89PNG")
            with mock.patch.object(self.mod, "ITEMS_DIR", items_dir), mock.patch.object(self.mod, "CROPS_DIR", crops_dir):
                return self.mod.check_items(), self.mod.check_key_status(), self.mod.check_crops()

    def test_plan_table_passes(self) -> None:
        items_chk, keys_chk, crops_chk = self.run_checks(self.plan_index())
        self.assertTrue(items_chk["ok"])
        self.assertTrue(keys_chk["ok"], keys_chk)
        self.assertTrue(crops_chk["ok"])

    def test_total_off_by_two_fails(self) -> None:
        items_chk, _, _ = self.run_checks(self.plan_index()[:-2])
        self.assertFalse(items_chk["ok"])
        self.assertFalse(items_chk["total_ok"])

    def test_qb202_without_pdf_recovery_fails(self) -> None:
        items = [dict(e, status="missing") if e["status"] == "from-pdf" else e for e in self.plan_index()]
        _, keys_chk, _ = self.run_checks(items)
        self.assertFalse(keys_chk["ok"])
        qb202 = next(c for c in keys_chk["checks"] if c["bank"] == "QB_202")
        self.assertEqual(qb202["actual"]["from-pdf"], 0)
        self.assertFalse(qb202["ok"])

    def test_extra_present_key_fails(self) -> None:
        items = self.plan_index()
        missing = next(e for e in items if e["status"] == "missing")
        missing["status"] = "present"
        _, keys_chk, _ = self.run_checks(items)
        self.assertFalse(keys_chk["ok"])

    def test_render_check_detects_missing_symbol_glyph(self) -> None:
        import pymupdf  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            qb_root = Path(tmp) / "qb"
            qb_pdf = Path(tmp) / "qb-pdf"
            docx = qb_root / "QB_501" / "5_ch01_MC_e.docx"
            write_docx(docx, SYMBOL_BODY)

            def write_pdf(path: Path, html: str) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                doc = pymupdf.open()
                doc.new_page().insert_htmlbox(pymupdf.Rect(50, 50, 500, 200), html)
                doc.save(str(path))
                doc.close()

            write_pdf(docx.with_suffix(".pdf"), "decay")
            lo = qb_pdf / "QB_501" / "5_ch01_MC_e.pdf"
            with mock.patch.object(self.mod, "QB_PDF", qb_pdf):
                write_pdf(lo, "decay αβ")
                good = self.mod.check_render(qb_root)
                write_pdf(lo, "decay ••")
                bad = self.mod.check_render(qb_root)
        self.assertTrue(good["ok"], good)
        self.assertEqual(good["compared"], 1)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["glyph_failure_files"][0]["missing_glyphs"], ["α", "β"])


if __name__ == "__main__":
    unittest.main()
