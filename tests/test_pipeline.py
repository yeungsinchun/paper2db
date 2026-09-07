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
        self.assertNotIn("performance", lines)

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

    def test_keys_stage_uses_paper_ans_only(self) -> None:
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
            (tmp_path / "paper" / "ans").mkdir(parents=True)
            dest = tmp_path / "classified" / "mc" / "answer_keys.json"
            calls: list[tuple[str, tuple[str, ...]]] = []

            def fake_run(script_name: str, *args: str) -> None:
                calls.append((script_name, args))

            with mock.patch.object(pipe, "ROOT", tmp_path):
                with mock.patch.object(pipe, "run_script", side_effect=fake_run):
                    pipe.stage_keys(force=False)
            self.assertEqual(calls[0][0], "extract_answer_keys.py")
            self.assertIn(str(tmp_path / "paper" / "ans"), calls[0][1])
            self.assertFalse(dest.is_file())

    def test_keys_stage_fails_without_paper_ans(self) -> None:
        pipe = self.pipe
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(pipe, "ROOT", tmp_path):
                with self.assertRaises(SystemExit) as raised:
                    pipe.stage_keys(force=True)
            self.assertIn("paper/ans", str(raised.exception))

    def test_force_mc_split_fails_when_intermediates_missing(self) -> None:
        pipe = self.pipe
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paper = tmp_path / "paper" / "mc"
            paper.mkdir(parents=True)
            (paper / "2099p1a.pdf").write_bytes(b"%PDF-1.4")
            year_dir = tmp_path / "output" / "2099"
            year_dir.mkdir(parents=True)
            from PIL import Image

            for index in range(1, 31):
                Image.new("RGB", (400, 120), (255, 255, 255)).save(
                    year_dir / f"q{index}.png"
                )
            (year_dir / "combined.pdf").write_bytes(b"%PDF-1.4")
            with mock.patch.object(pipe, "ROOT", tmp_path):
                with self.assertRaises(SystemExit) as raised:
                    pipe.stage_mc_split(["2099"], force=True)
            self.assertIn("Run mc-anchors first", str(raised.exception))

    def test_classify_lq_uses_llm_when_keyed(self) -> None:
        pipe = self.pipe
        calls: list[str] = []

        def fake_run(script_name: str, *args: str) -> None:
            calls.append(script_name)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(pipe, "ROOT", tmp_path):
                with mock.patch.object(pipe, "has_llm_key", return_value=True):
                    with mock.patch.object(pipe, "run_script", side_effect=fake_run):
                        with mock.patch.dict("os.environ", {}, clear=False):
                            pipe.stage_classify_lq(None, force=True)
        self.assertEqual(calls, ["classify_lq_llm.py"])


class TestAnswerKeyDefaults(unittest.TestCase):
    def test_extract_answer_keys_defaults_to_paper_ans(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import extract_answer_keys as eak

        with mock.patch.object(sys, "argv", ["extract_answer_keys.py"]):
            args = eak.parse_args()
        self.assertEqual(args.answers, ROOT / "paper" / "ans")
        self.assertEqual(args.output, ROOT / "classified" / "mc" / "answer_keys.json")

    def test_combine_section_pdfs_uses_classified_keys(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import combine_section_pdfs as csp

        self.assertEqual(
            csp.DEFAULT_KEYS.resolve(),
            (ROOT / "classified" / "mc" / "answer_keys.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
