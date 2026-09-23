"""--replay-classifications: tracked metadata JSON only, no LLM, no fallback."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_pipeline import load_pipeline

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class TestPipelineReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipe = load_pipeline()

    def _run_classify(self, tmp_path: Path, stage, **kwargs) -> list[tuple[str, ...]]:
        calls: list[tuple[str, ...]] = []
        perf = tmp_path / "tests" / "sections" / "lq" / "candidate_performance.json"
        perf.parent.mkdir(parents=True, exist_ok=True)
        perf.write_text("{}", encoding="utf-8")
        with (
            mock.patch.object(self.pipe, "ROOT", tmp_path),
            mock.patch.object(self.pipe, "has_llm_key", return_value=True),
            mock.patch.object(
                self.pipe, "run_script", side_effect=lambda *a: calls.append(a)
            ),
        ):
            stage(**kwargs)
        return calls

    def test_replay_uses_tracked_json_even_with_llm_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for kind in ("mc", "lq"):
                path = tmp_path / "metadata" / kind / "llm_classifications.json"
                path.parent.mkdir(parents=True)
                path.write_text("[]" if kind == "mc" else "{}", encoding="utf-8")
            mc = self._run_classify(
                tmp_path,
                self.pipe.stage_classify_mc,
                years=["2014"],
                force=True,
                yes=True,
                replay=True,
            )
            lq = self._run_classify(
                tmp_path, self.pipe.stage_classify_lq, years=None, force=True, replay=True
            )
        self.assertEqual(mc, [("classify_mc_llm.py", "--replay", "--years", "2014")])
        self.assertEqual(lq, [("classify_lq_llm.py", "--replay")])

    def test_replay_fails_loudly_when_tracked_json_missing(self) -> None:
        stages = {
            "mc": lambda: self.pipe.stage_classify_mc(None, force=True, yes=True, replay=True),
            "lq": lambda: self.pipe.stage_classify_lq(None, force=True, replay=True),
        }
        for kind, run_stage in stages.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                calls: list[tuple[str, ...]] = []
                with (
                    mock.patch.object(self.pipe, "ROOT", Path(tmp)),
                    mock.patch.object(self.pipe, "has_llm_key", return_value=False),
                    mock.patch.object(self.pipe, "ensure_lq_performance"),
                    mock.patch.object(
                        self.pipe, "run_script", side_effect=lambda *a: calls.append(a)
                    ),
                ):
                    with self.assertRaises(SystemExit) as raised:
                        run_stage()
                self.assertIn("llm_classifications.json", str(raised.exception))
                self.assertEqual(calls, [], "must not fall back to another classifier")

    def test_timings_json_creates_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tests" / "sections" / "stage_timings.json"
            argv = ["pipeline", "--only", "keys", "--yes", "--timings-json", str(out)]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(self.pipe, "stage_keys") as stage_keys,
            ):
                self.pipe.main()
            stage_keys.assert_called_once()
            self.assertEqual(list(json.loads(out.read_text(encoding="utf-8"))), ["keys"])


class TestMcReplay(unittest.TestCase):
    def test_missing_file_or_decision_exits(self) -> None:
        import classify_mc_llm as mc

        records = [{"Year": 2014, "Question": 1}, {"Year": 2014, "Question": 2}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm_classifications.json"
            with self.assertRaises(SystemExit) as raised:
                mc.load_replay_decisions(path, records)
            self.assertIn("missing tracked decisions", str(raised.exception))

            path.write_text(
                json.dumps([{"Year": 2014, "Question": 1, "sections": [5]}]),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as raised:
                mc.load_replay_decisions(path, records)
            self.assertIn("2014 Q2", str(raised.exception))

            records = records[:1]
            self.assertEqual(len(mc.load_replay_decisions(path, records)), 1)

    def test_invalid_replay_sections_exit_without_normalization(self) -> None:
        import classify_mc_llm as mc

        records = [{"Year": 2014, "Question": 1}]
        invalid_sections = ([], [0], [28], [5, 6, 7], [5, 5], ["5"], None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm_classifications.json"
            for sections in invalid_sections:
                with self.subTest(sections=sections):
                    path.write_text(
                        json.dumps(
                            [{"Year": 2014, "Question": 1, "sections": sections}]
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaises(SystemExit) as raised:
                        mc.load_replay_decisions(path, records)
                    self.assertIn("bad sections", str(raised.exception))

    def test_main_replay_never_calls_llm_or_rewrites_json(self) -> None:
        import classify_mc_llm as mc

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            classified = root / "tests" / "sections" / "mc"
            metadata = root / "metadata" / "mc"
            metadata.mkdir(parents=True)
            classified.mkdir(parents=True)
            decisions = metadata / "llm_classifications.json"
            original = json.dumps(
                [{"Year": 2014, "Question": 1, "sections": [9, 8], "reason": "kept"}]
            )
            decisions.write_text(original, encoding="utf-8")
            (classified / "mc_ocr.json").write_text(
                json.dumps(
                    [{"Year": 2014, "Question": 1, "PNG": "x/q1.png", "Statement": "s"}]
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                years=None,
                limit=None,
                skip_ocr=True,
                from_json=None,
                replay=True,
                workers=1,
                sleep=0,
                rebuild_pdfs=False,
            )
            with (
                mock.patch.object(mc, "ROOT", root),
                mock.patch.object(mc, "CLASSIFIED", classified),
                mock.patch.object(mc, "METADATA_MC", metadata),
                mock.patch.object(mc, "parse_args", return_value=args),
                mock.patch.object(mc, "classify_one_llm", side_effect=AssertionError("LLM")),
            ):
                mc.main()
            self.assertEqual(decisions.read_text(encoding="utf-8"), original)
            rows = json.loads((classified / "classification.json").read_text())
            self.assertEqual(rows[0]["AllSections"], "9;8")


class TestLqReplay(unittest.TestCase):
    def _run(self, decisions: dict | None) -> tuple[Path, Path]:
        import classify_lq_llm as lq

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        classified = root / "tests" / "sections" / "lq"
        metadata = root / "metadata" / "lq"
        metadata.mkdir(parents=True)
        decisions_path = metadata / "llm_classifications.json"
        if decisions is not None:
            decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        record = {
            "Year": "2014",
            "Question": 1,
            # Book 5 wording that finalize_sections would re-list: replay must not.
            "Statement": "radioactive decay half-life nuclear fission",
            "PNG": "tests/reconstructed/lq/2014/q1.png",
            "AnswerPNG": "tests/reconstructed/lq/2014/ans/q1.png",
        }
        args = argparse.Namespace(
            years=None, workers=1, from_json=None, limit=None, sleep=0, replay=True
        )
        with (
            mock.patch.object(lq, "ROOT", root),
            mock.patch.object(lq, "CLASSIFIED_LQ", classified),
            mock.patch.object(lq, "OCR_CACHE", classified / "ocr_cache"),
            mock.patch.object(lq, "METADATA_LQ", metadata),
            mock.patch.object(lq, "collect_jobs", return_value=[("2014", Path("x"), 1)]),
            mock.patch.object(lq, "_ocr_one", return_value=record),
            mock.patch.object(lq, "classify_one", side_effect=AssertionError("LLM")),
            mock.patch.object(lq, "parse_args", return_value=args),
        ):
            lq.main()
        return classified, decisions_path

    def test_replay_applies_verbatim_and_keeps_tracked_json(self) -> None:
        tracked = {"2014-q1": {"sections": [7, 2], "reason": "tracked reason"}}
        classified, decisions_path = self._run(tracked)
        self.assertEqual(json.loads(decisions_path.read_text()), tracked)
        with (classified / "classification.csv").open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["AllSections"], "7;2")
        self.assertEqual(rows[0]["Reason"], "tracked reason")
        top = json.loads((classified.parent / "lq_classification.json").read_text())
        self.assertEqual(top[0]["PrimarySection"], 7)

    def test_replay_exits_on_missing_file_or_decision(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self._run(None)
        self.assertIn("missing tracked decisions", str(raised.exception))
        with self.assertRaises(SystemExit) as raised:
            self._run({"2014-q2": {"sections": [7], "reason": ""}})
        self.assertIn("2014-q1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
