"""Behavioral checks for the ./pipeline entry point."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_pipeline():
    path = ROOT / "pipeline"
    loader = importlib.machinery.SourceFileLoader("paper2db_pipeline", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPipelineCli(unittest.TestCase):
    def test_list_stages_prints_all(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "pipeline"), "--list-stages"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(lines, list(load_pipeline().STAGES))

    def test_unknown_only_stage_exits(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "pipeline"), "--only", "not-a-stage"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown stage", result.stderr + result.stdout)


class TestPipelineHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipe = load_pipeline()

    def test_paper_year_label(self) -> None:
        self.assertEqual(self.pipe.paper_year_label("2019p1a"), "2019")
        self.assertEqual(self.pipe.paper_year_label("ppp1b"), "pp")
        self.assertEqual(self.pipe.paper_year_label("sapp1a"), "sap")

    def test_discover_mc_papers_respects_years(self) -> None:
        papers = self.pipe.discover_mc_papers(["2025"])
        self.assertTrue(papers)
        self.assertEqual({label for _, label in papers}, {"2025"})
        self.assertTrue(all(path.is_file() for path, _ in papers))

    def test_select_stages_from_until(self) -> None:
        args = mock.Mock(
            only=None,
            from_stage="keys",
            until="classify-lq",
            skip_lavish=False,
        )
        self.assertEqual(
            self.pipe.select_stages(args),
            ["keys", "classify-mc", "classify-lq"],
        )

    def test_keys_stage_copies_legacy_when_missing(self) -> None:
        pipe = self.pipe
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legacy_dir = tmp_path / "processed" / "MC"
            legacy_dir.mkdir(parents=True)
            legacy = legacy_dir / "answer_keys.json"
            legacy.write_text(
                json.dumps({"2012": {"1": {"Correct Option": "A"}}}) + "\n",
                encoding="utf-8",
            )
            dest = tmp_path / "classified" / "mc" / "answer_keys.json"
            with mock.patch.object(pipe, "ROOT", tmp_path):
                pipe.stage_keys(force=False)
                self.assertTrue(dest.is_file())
                payload = json.loads(dest.read_text(encoding="utf-8"))
                self.assertEqual(payload["2012"]["1"]["Correct Option"], "A")
                mtime = dest.stat().st_mtime
                pipe.stage_keys(force=False)
                self.assertEqual(dest.stat().st_mtime, mtime)


class TestAnswerKeyDefaults(unittest.TestCase):
    def test_extract_answer_keys_defaults_to_paper_ans(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import extract_answer_keys as eak

        with mock.patch.object(sys, "argv", ["extract_answer_keys.py"]):
            args = eak.parse_args()
        self.assertEqual(args.answers, ROOT / "paper" / "ans")
        self.assertEqual(args.output, ROOT / "classified" / "mc" / "answer_keys.json")

    def test_combine_section_pdfs_prefers_classified_keys(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import combine_section_pdfs as csp

        self.assertEqual(
            csp.DEFAULT_KEYS.resolve(),
            (ROOT / "classified" / "mc" / "answer_keys.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
