"""scripts/package_release.py: release assets from a finished build."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_release as pr  # noqa: E402


def _pdf(path: Path, pages: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=595, height=842)
    doc.save(path)
    doc.close()


class TestPackageRelease(unittest.TestCase):
    def _tree(self, root: Path) -> None:
        recon, sections = root / "tests" / "reconstructed", root / "tests" / "sections"
        for kind in ("mc", "lq"):
            _pdf(recon / kind / "combined.pdf", pages=3)
            _pdf(recon / kind / "2014" / "combined.pdf")
        _pdf(sections / "mc" / "01_Heat" / "1_Temperature" / "combined.pdf")
        _pdf(sections / "mc" / "01_Heat" / "1_Temperature" / "answer.pdf")
        for name in ("combined.pdf", "answers.pdf", "performance.pdf"):
            _pdf(sections / "lq" / "01_Heat" / "1_Temperature" / name)
        for rel in ("mc/answer_keys.json", "lq/candidate_performance.json", "quality_audit.json"):
            (sections / rel).write_text("{}", encoding="utf-8")

    def _patched(self, root: Path):
        return mock.patch.multiple(
            pr,
            ROOT=root,
            RECON=root / "tests" / "reconstructed",
            SECTIONS=root / "tests" / "sections",
        )

    def test_build_packages_assets_and_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            timings = root / "timings.json"
            timings.write_text(json.dumps({"mc-anchors": 60.0, "keys": 30.0}))
            out = root / "dist"
            with self._patched(root):
                assets = pr.build(out, timings)
                notes = pr.notes(assets, out, commit="abc", branch="b", run_url="")
            self.assertIn("mc-sections.zip", assets)
            self.assertIn("preview-mc-combined-p2.png", assets)
            with zipfile.ZipFile(out / "lq-sections.zip") as zf:
                self.assertIn(
                    "lq-sections/01_Heat/1_Temperature/performance.pdf", zf.namelist()
                )
            with zipfile.ZipFile(out / "mc-per-year.zip") as zf:
                self.assertEqual(zf.namelist(), ["mc-2014.pdf"])
            self.assertIn("| **total** | **90** |", notes)
            self.assertIn("`quality_audit.json`", notes)

    def test_missing_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            (root / "tests" / "sections" / "quality_audit.json").unlink()
            with self._patched(root), self.assertRaises(SystemExit) as raised:
                pr.build(root / "dist", None)
            self.assertIn("quality_audit.json", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
