"""Behavioral checks for the ./pipeline entry point."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
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

    def test_lq_crops_ready_rejects_y_crop_and_missing_pages(self) -> None:
        pipe = self.pipe
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            year_dir = tmp_path / "output" / "lq" / "2099"
            pages = year_dir / "pages"
            pages.mkdir(parents=True)
            Image.new("RGB", (100, 200), (255, 255, 255)).save(pages / "page000.png")
            Image.new("RGB", (100, 200), (255, 255, 255)).save(pages / "page001.png")
            (year_dir / "starts.json").write_text(
                json.dumps(
                    {
                        "pages": 2,
                        "questions": [{"q": 1, "page_from": 0, "page_to": 1}],
                    }
                ),
                encoding="utf-8",
            )
            Image.new("RGB", (100, 80), (255, 255, 255)).save(year_dir / "q1.png")
            with mock.patch.object(pipe, "ROOT", tmp_path):
                self.assertFalse(pipe.lq_crops_ready("2099"))
            Image.new("RGB", (100, 400), (255, 255, 255)).save(year_dir / "q1.png")
            with mock.patch.object(pipe, "ROOT", tmp_path):
                self.assertTrue(pipe.lq_crops_ready("2099"))
            shutil.rmtree(pages)
            with mock.patch.object(pipe, "ROOT", tmp_path):
                self.assertFalse(pipe.lq_crops_ready("2099"))

    def test_lq_pages_keeps_existing_starts_json(self) -> None:
        pipe = self.pipe
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paper = tmp_path / "paper" / "lq"
            paper.mkdir(parents=True)
            (paper / "2099p1b.pdf").write_bytes(b"%PDF-1.4")
            year_dir = tmp_path / "output" / "lq" / "2099"
            year_dir.mkdir(parents=True)
            starts = year_dir / "starts.json"
            original = '{"questions":[],"pages":1}\n'
            starts.write_text(original, encoding="utf-8")
            calls: list[tuple[str, tuple[str, ...]]] = []

            def fake_run(script_name: str, *args: str) -> None:
                calls.append((script_name, args))

            def fake_segment(*_args: str) -> None:
                raise AssertionError("must not re-detect starts")

            with mock.patch.object(pipe, "ROOT", tmp_path):
                with mock.patch.object(pipe, "run_script", side_effect=fake_run):
                    with mock.patch.object(pipe, "run_segment", side_effect=fake_segment):
                        pipe.stage_lq_pages(["2099"], force=False)
            self.assertEqual(starts.read_text(encoding="utf-8"), original)
            self.assertEqual(calls[0][0], "crop_lq_from_pages.py")
            self.assertIn("--pages-only", calls[0][1])


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

        with mock.patch.object(sys, "argv", ["combine_section_pdfs.py"]):
            args = csp.parse_args()
        self.assertEqual(
            args.keys.resolve(),
            (ROOT / "classified" / "mc" / "answer_keys.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
