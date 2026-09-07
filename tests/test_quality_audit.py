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
    def test_report_counts_overrides_toward_five_percent(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "quality_audit.py"),
                "--output",
                str(ROOT / "classified" / "quality_audit.json"),
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
        overrides = summary["overrides_historical"]["total_questions"]
        self.assertGreaterEqual(overrides, 1)
        self.assertGreaterEqual(summary["mc"]["failure_count"], overrides)
        self.assertIn("override_tuned", summary["failure_definitions"]["counted"])
        self.assertNotIn(
            "override_baked_in", summary["failure_definitions"]["not_counted"]
        )
        self.assertNotIn(
            "override_tuned", summary["failure_definitions"]["not_counted"]
        )
        # Current repo has 72 MC overrides (~12.6%), so the bar fails honestly.
        self.assertFalse(summary["passes_5pct_bar"])
        self.assertGreater(summary["mc"]["questions"], 100)
        self.assertGreater(summary["lq"]["questions"], 50)

    def test_summarize_counts_override_items(self) -> None:
        audit = load_module("quality_audit", ROOT / "scripts" / "quality_audit.py")
        summary = audit.summarize(
            {
                "failures": [],
                "crop_count": 100,
            },
            {
                "failures": [],
                "crop_count": 50,
            },
            {
                "uncertain": [],
                "missing_crop": [],
                "missing_classified": [],
                "book_order_inversions": [],
                "row_count": 100,
            },
            {
                "missing_crop": [],
                "missing_classified": [],
                "missing_answer_png": [],
                "row_count": 50,
            },
            {
                "total_questions": 10,
                "by_year": {"2099": 10},
                "items": [{"year": "2099", "q": str(i)} for i in range(1, 11)],
            },
        )
        self.assertEqual(summary["mc"]["failure_count"], 10)
        self.assertEqual(summary["mc"]["manual_tuning_rate"], 0.1)
        self.assertFalse(summary["passes_5pct_bar"])
        self.assertTrue(
            all(item["kind"] == "override_tuned" for item in summary["mc"]["failures"])
        )

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

    def test_pipeline_review_preserves_img_when_sources_missing(self) -> None:
        lavish = load_module(
            "build_pipeline_lavish_review",
            ROOT / "scripts" / "build_pipeline_lavish_review.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / ".lavish" / "pipeline-review"
            img = out / "img"
            img.mkdir(parents=True)
            marker = img / "anchor-2024-page01.png"
            marker.write_bytes(b"kept-anchor")
            audit = {
                "summary": {
                    "mc": {
                        "manual_tuning_rate": 0.1,
                        "failure_count": 10,
                        "questions": 100,
                    },
                    "lq": {
                        "manual_tuning_rate": 0.0,
                        "failure_count": 0,
                        "questions": 50,
                        "missing_answer_png_count": 0,
                    },
                    "combined": {
                        "manual_tuning_rate": 0.0667,
                        "failure_count": 10,
                        "questions": 150,
                    },
                    "overrides_historical": {"total_questions": 10, "by_year": {"2099": 10}},
                    "passes_5pct_bar": False,
                    "failure_definitions": {
                        "counted": ["override_tuned"],
                        "not_counted": ["missing_answer_png"],
                    },
                }
            }
            with mock.patch.object(lavish, "ROOT", tmp_path):
                with mock.patch.object(lavish, "OUT", out):
                    with mock.patch.object(lavish, "IMG", img):
                        with mock.patch.object(
                            lavish, "AUDIT_JSON", tmp_path / "classified" / "quality_audit.json"
                        ):
                            with mock.patch.object(
                                lavish, "ensure_audit", return_value=audit
                            ):
                                lavish.main()
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), b"kept-anchor")
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("FAIL (above 5%)", html)
            self.assertIn("img/anchor-2024-page01.png", html)


if __name__ == "__main__":
    unittest.main()
