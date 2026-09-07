"""Behavioral checks for quality_audit and lavish wiring."""
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


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestQualityAudit(unittest.TestCase):
    def test_report_passes_five_percent_on_repo_outputs(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "quality_audit.py"),
                "--output",
                str(ROOT / "classified" / "quality_audit.json"),
                "--strict",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(
            (ROOT / "classified" / "quality_audit.json").read_text(encoding="utf-8")
        )
        summary = report["summary"]
        self.assertTrue(summary["passes_5pct_bar"])
        self.assertEqual(summary["mc"]["failure_count"], 0)
        self.assertEqual(summary["lq"]["failure_count"], 0)
        self.assertGreater(summary["mc"]["questions"], 100)
        self.assertGreater(summary["lq"]["questions"], 50)

    def test_tiny_crop_is_counted_as_failure(self) -> None:
        audit = load_module("quality_audit", ROOT / "scripts" / "quality_audit.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            year_dir = tmp_path / "output" / "2099"
            year_dir.mkdir(parents=True)
            from PIL import Image

            Image.new("RGB", (40, 40), (255, 255, 255)).save(year_dir / "q1.png")
            # Pad to look like a year folder with q1 only - few_year_crops also fires.
            with mock.patch.object(audit, "ROOT", tmp_path):
                crops = audit.audit_mc_crops()
            kinds = {item["kind"] for item in crops["failures"]}
            self.assertIn("tiny_crop", kinds)
            self.assertIn("few_year_crops", kinds)


class TestPipelineLavishWiring(unittest.TestCase):
    def test_stage_lavish_runs_quality_and_three_builders(self) -> None:
        pipe = load_module("paper2db_pipeline", ROOT / "pipeline")
        calls: list[str] = []

        def fake_run(script_name: str, *args: str) -> None:
            calls.append(script_name)

        with mock.patch.object(pipe, "run_script", side_effect=fake_run):
            pipe.stage_lavish()
        self.assertEqual(
            calls,
            [
                "quality_audit.py",
                "build_mc_lavish_review.py",
                "build_lq_lavish_review.py",
                "build_pipeline_lavish_review.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
