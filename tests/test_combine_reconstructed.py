"""tests/reconstructed/{mc,lq}/combined.pdf join every year's paper in syllabus order."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _year_pdf(path: Path, label: str, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    try:
        for index in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((40, 60), f"{label} page {index}", fontsize=12)
        doc.save(path)
    finally:
        doc.close()


class TestCombineReconstructed(unittest.TestCase):
    def test_joins_years_in_order(self) -> None:
        combine = load_module("combine_reconstructed", SCRIPTS / "combine_reconstructed.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mc = root / "tests" / "reconstructed" / "mc"
            _year_pdf(mc / "pp" / "combined.pdf", "pp", 1)
            _year_pdf(mc / "2013" / "combined.pdf", "2013", 2)
            _year_pdf(mc / "2012" / "combined.pdf", "2012", 3)
            (mc / "2014").mkdir()  # year without a per-year PDF -> skipped
            lq = root / "tests" / "reconstructed" / "lq"
            _year_pdf(lq / "2012" / "combined.pdf", "lq2012", 4)
            with (
                mock.patch.object(combine, "ROOT", root),
                mock.patch.object(combine, "RECONSTRUCTED", root / "tests" / "reconstructed"),
                mock.patch.object(combine, "parse_args", return_value=mock.Mock(kind=["mc", "lq"])),
            ):
                combine.main()
            document = fitz.open(mc / "combined.pdf")
            try:
                self.assertEqual(len(document), 6)
                texts = [page.get_text() for page in document]
                self.assertIn("2012 page 0", texts[0])
                self.assertIn("2013 page 0", texts[3])
                self.assertIn("pp page 0", texts[5])
            finally:
                document.close()
            self.assertEqual(len(fitz.open(lq / "combined.pdf")), 4)

    def test_lq_crops_stage_rebuilds_top_level_combined_even_when_years_are_ready(self) -> None:
        pipe = load_module("paper2db_pipeline", ROOT / "pipeline")
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_run(script_name: str, *args: str) -> None:
            calls.append((script_name, args))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "paper" / "lq").mkdir(parents=True)
            (tmp_path / "paper" / "lq" / "2099p1b.pdf").write_bytes(b"%PDF-1.4")
            with (
                mock.patch.object(pipe, "ROOT", tmp_path),
                mock.patch.object(pipe, "lq_crops_ready", return_value=True),
                mock.patch.object(pipe, "run_script", side_effect=fake_run),
            ):
                pipe.stage_lq_crops(["2099"], force=False, yes=True)
        self.assertEqual(calls, [("combine_reconstructed.py", ("--kind", "lq"))])
