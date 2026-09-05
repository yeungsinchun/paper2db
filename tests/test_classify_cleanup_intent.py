"""Behavioral checks for classify-branch cleanup intent."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


class TestGitignorePages(unittest.TestCase):
    """gitignore must ignore LQ page-render caches (git is the consumer)."""

    def test_pages_paths_are_ignored(self) -> None:
        probe = "output/lq/2099/pages/page01.png"
        result = subprocess.run(
            ["git", "check-ignore", "-v", probe],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("output/**/pages/", result.stdout)

    def test_question_crops_and_combined_not_ignored(self) -> None:
        for path in (
            "output/lq/2024/q1.png",
            "output/lq/2024/combined.pdf",
            "output/lq/2026/q1.png",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "-v", path],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"{path} should not be gitignored: {result.stdout}",
            )


class TestClassificationSplitArtifacts(unittest.TestCase):
    """Shipped public classification contracts after the rename split."""

    def test_ambiguous_top_level_names_absent(self) -> None:
        self.assertFalse((ROOT / "classified" / "classification.csv").exists())
        self.assertFalse((ROOT / "classified" / "classification.json").exists())

    def test_mc_classification_list_contract(self) -> None:
        path = ROOT / "classified" / "mc_classification.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 100)
        required = {
            "Year",
            "Question",
            "PrimarySection",
            "PrimaryName",
            "Reason",
            "PNG",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        with (ROOT / "classified" / "mc_classification.csv").open(encoding="utf-8") as fh:
            csv_rows = list(csv.DictReader(fh))
        self.assertEqual(len(csv_rows), len(rows))

    def test_lq_classification_list_contract(self) -> None:
        path = ROOT / "classified" / "lq_classification.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 50)
        required = {
            "Year",
            "Question",
            "PrimarySection",
            "PrimaryName",
            "PNG",
            "AnswerPNG",
        }
        self.assertTrue(required.issubset(rows[0].keys()))
        nested = ROOT / "classified" / "lq" / "classification.csv"
        self.assertTrue(nested.is_file())
        with nested.open(encoding="utf-8") as fh:
            nested_rows = list(csv.DictReader(fh))
        self.assertEqual(len(nested_rows), len(rows))


class TestPaperRenameAndNewYears(unittest.TestCase):
    def test_2023_renamed_and_2024_2026_present(self) -> None:
        self.assertFalse((ROOT / "paper" / "lq" / "2023q1b.pdf").exists())
        for name in ("2023p1b.pdf", "2024p1b.pdf", "2025p1b.pdf", "2026p1b.pdf"):
            path = ROOT / "paper" / "lq" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 1000)


class TestMcLlmWriterOutputs(unittest.TestCase):
    """classify_mc_llm.apply_classifications writes only mc_* split names."""

    def test_apply_classifications_writes_mc_names_as_list(self) -> None:
        import classify_mc_llm as m

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in m.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            records = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "statement",
                }
            ]
            decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [2],
                    "reason": "heat capacity",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "preview",
                }
            ]
            with mock.patch.object(m, "CLASSIFIED", classified):
                m.apply_classifications(records, decisions)

            names = {p.name for p in classified.iterdir() if p.is_file()}
            self.assertIn("mc_classification.json", names)
            self.assertIn("mc_classification.csv", names)
            self.assertNotIn("classification.json", names)
            self.assertNotIn("classification.csv", names)
            payload = json.loads((classified / "mc_classification.json").read_text())
            self.assertIsInstance(payload, list)
            self.assertEqual(payload[0]["PrimarySection"], 2)
            self.assertEqual(payload[0]["Reason"], "heat capacity")


class TestMcLlmPartialYearsMerge(unittest.TestCase):
    """Partial --years runs must merge decisions, not S5-fill other years."""

    def test_merge_keeps_prior_decisions_on_partial_apply(self) -> None:
        import classify_mc_llm as m

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in m.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            full_records = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "gas law",
                    "Option": {},
                },
                {
                    "Year": "2013",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "lenses",
                    "Option": {},
                },
            ]
            existing_decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [4],
                    "reason": "gas",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "gas",
                },
                {
                    "Year": "2013",
                    "Question": 1,
                    "sections": [19],
                    "reason": "lenses",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "lenses",
                },
            ]
            new_decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [2],
                    "reason": "heat capacity",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "heat",
                }
            ]
            merged = m.merge_by_year_question(existing_decisions, new_decisions)
            with mock.patch.object(m, "CLASSIFIED", classified):
                m.apply_classifications(full_records, merged)

            rows = json.loads((classified / "mc_classification.json").read_text())
            by_year = {str(r["Year"]): r for r in rows}
            self.assertEqual(by_year["2012"]["PrimarySection"], 2)
            self.assertEqual(by_year["2012"]["Reason"], "heat capacity")
            self.assertEqual(by_year["2013"]["PrimarySection"], 19)
            self.assertEqual(by_year["2013"]["Reason"], "lenses")
            self.assertNotEqual(by_year["2013"]["Reason"], "missing LLM decision")

    def test_incomplete_decisions_abort_without_s5_stubs(self) -> None:
        """Full apply with gaps must abort, not invent Section 5 stubs."""
        import classify_mc_llm as m

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in m.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            full_records = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "gas",
                    "Option": {},
                },
                {
                    "Year": "2013",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "lenses",
                    "Option": {},
                },
            ]
            partial_decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [2],
                    "reason": "heat",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "heat",
                }
            ]
            with mock.patch.object(m, "CLASSIFIED", classified):
                with self.assertRaises(SystemExit) as ctx:
                    m.apply_classifications(full_records, partial_decisions)
            self.assertIn("refusing to invent Section 5 stubs", str(ctx.exception))
            self.assertFalse((classified / "mc_classification.json").exists())

    def test_partial_touched_keys_merges_existing_classification(self) -> None:
        """--years path merges only touched rows into shipped mc_classification.*."""
        import classify_mc_llm as m

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in m.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            book19, folder19, _ = m.SECTION_BY_NUM[19]
            kept_png = classified / book19 / folder19 / "2013_q1.png"
            kept_png.write_bytes(b"KEEP-2013")
            book4, folder4, _ = m.SECTION_BY_NUM[4]
            old_2012 = classified / book4 / folder4 / "2012_q1.png"
            old_2012.write_bytes(b"OLD-2012")

            existing_rows = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PrimarySection": 4,
                    "PrimaryName": "Gases",
                    "PrimaryBook": "Heat and Gases",
                    "AllSections": "4",
                    "AllSectionNames": "Gases",
                    "Reason": "old-gas",
                    "Uncertain": "no",
                    "PNG": "missing.png",
                    "StatementPreview": "gas",
                },
                {
                    "Year": "2013",
                    "Question": 1,
                    "PrimarySection": 19,
                    "PrimaryName": "Lenses",
                    "PrimaryBook": "Wave Motion",
                    "AllSections": "19",
                    "AllSectionNames": "Lenses",
                    "Reason": "old-lenses",
                    "Uncertain": "no",
                    "PNG": "missing.png",
                    "StatementPreview": "lenses",
                },
            ]
            (classified / "mc_classification.json").write_text(
                json.dumps(existing_rows, indent=2) + "\n", encoding="utf-8"
            )

            work_records = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PNG": "missing.png",
                    "Question statement": "heat capacity",
                    "Option": {},
                }
            ]
            new_decisions = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "sections": [2],
                    "reason": "heat capacity",
                    "uncertain": False,
                    "PNG": "missing.png",
                    "StatementPreview": "heat",
                }
            ]
            with mock.patch.object(m, "CLASSIFIED", classified):
                m.apply_classifications(
                    work_records,
                    new_decisions,
                    touched_keys={("2012", 1)},
                )

            rows = json.loads((classified / "mc_classification.json").read_text())
            self.assertEqual(len(rows), 2)
            by_year = {str(r["Year"]): r for r in rows}
            self.assertEqual(by_year["2012"]["PrimarySection"], 2)
            self.assertEqual(by_year["2012"]["Reason"], "heat capacity")
            self.assertEqual(by_year["2013"]["PrimarySection"], 19)
            self.assertEqual(by_year["2013"]["Reason"], "old-lenses")
            self.assertNotIn("missing LLM decision", by_year["2013"]["Reason"])
            self.assertTrue(kept_png.is_file())
            self.assertEqual(kept_png.read_bytes(), b"KEEP-2013")
            self.assertFalse(old_2012.exists())


class TestMcSectionsYearsMerge(unittest.TestCase):
    """classify_mc_sections --years must merge into existing mc_classification.*."""

    def test_years_skip_ocr_preserves_other_year_rows_and_pngs(self) -> None:
        import classify_mc_sections as sec

        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified"
            classified.mkdir()
            for _n, book, folder, _name in sec.SECTIONS:
                (classified / book / folder).mkdir(parents=True, exist_ok=True)

            book19, folder19, _ = sec.SECTION_BY_NUM[19]
            kept_png = classified / book19 / folder19 / "2013_q1.png"
            kept_png.write_bytes(b"KEEP-2013-SECTIONS")

            existing_rows = [
                {
                    "Year": "2012",
                    "Question": 1,
                    "PrimarySection": 5,
                    "PrimaryName": "Motion",
                    "PrimaryBook": "Force and Motion",
                    "AllSections": "5",
                    "AllSectionNames": "Motion",
                    "Scores": "1",
                    "Uncertain": "yes",
                    "BadCrop": "no",
                    "PNG": "missing.png",
                    "StatementPreview": "old-2012",
                },
                {
                    "Year": "2013",
                    "Question": 1,
                    "PrimarySection": 19,
                    "PrimaryName": "Lenses",
                    "PrimaryBook": "Ray Optics",
                    "AllSections": "19",
                    "AllSectionNames": "Lenses",
                    "Scores": "3",
                    "Uncertain": "no",
                    "BadCrop": "no",
                    "PNG": "missing.png",
                    "StatementPreview": "old-2013",
                },
            ]
            (classified / "mc_classification.json").write_text(
                json.dumps(existing_rows, indent=2) + "\n", encoding="utf-8"
            )
            (classified / "mc_ocr.json").write_text(
                json.dumps(
                    [
                        {
                            "Year": "2012",
                            "Question": 1,
                            "PNG": "missing.png",
                            "Question statement": "temperature heat transfer",
                            "Option": {},
                        },
                        {
                            "Year": "2013",
                            "Question": 1,
                            "PNG": "missing.png",
                            "Question statement": "lenses focal length",
                            "Option": {},
                        },
                    ],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(sec, "CLASSIFIED", classified),
                mock.patch(
                    "argparse.ArgumentParser.parse_args",
                    return_value=argparse.Namespace(years=["2012"], workers=1, skip_ocr=True),
                ),
                mock.patch.object(sec, "classify_multi", return_value=[(1, 4)]),
            ):
                sec.main()

            rows = json.loads((classified / "mc_classification.json").read_text())
            self.assertEqual(len(rows), 2)
            by_year = {str(r["Year"]): r for r in rows}
            self.assertEqual(by_year["2012"]["PrimarySection"], 1)
            self.assertEqual(by_year["2013"]["PrimarySection"], 19)
            self.assertEqual(by_year["2013"]["StatementPreview"], "old-2013")
            self.assertTrue(kept_png.is_file())
            self.assertEqual(kept_png.read_bytes(), b"KEEP-2013-SECTIONS")


class TestLqLlmAbortOnPartialFailure(unittest.TestCase):
    """LLM failures must not clear-and-replace nested LQ outputs."""

    def test_write_outputs_not_called_when_one_fails(self) -> None:
        import classify_lq_llm as lq

        records = [
            {
                "Year": "2012",
                "Question": 1,
                "Statement": "ok",
                "PNG": "output/lq/2012/q1.png",
                "AnswerPNG": "output/lq/2012/ans/q1.png",
            },
            {
                "Year": "2012",
                "Question": 2,
                "Statement": "bad",
                "PNG": "output/lq/2012/q2.png",
                "AnswerPNG": "output/lq/2012/ans/q2.png",
            },
        ]

        def fake_classify(rec: dict) -> dict:
            if int(rec["Question"]) == 2:
                raise ValueError("boom")
            return {"sections": [5], "reason": "ok"}

        with (
            mock.patch.object(lq, "collect_jobs", return_value=[
                ("2012", Path("x"), 1),
                ("2012", Path("y"), 2),
            ]),
            mock.patch.object(lq, "_ocr_one", side_effect=records),
            mock.patch.object(lq, "classify_one", side_effect=fake_classify),
            mock.patch.object(lq, "llm_config", return_value=("k", "http://x", "m")),
            mock.patch.object(lq, "write_outputs") as write_outputs,
            mock.patch.object(
                lq,
                "parse_args",
                return_value=argparse.Namespace(
                    years=None, workers=1, from_json=None, limit=None, sleep=0
                ),
            ),
        ):
            with self.assertRaises(SystemExit) as ctx:
                lq.main()
        self.assertIn("Aborting write", str(ctx.exception))
        write_outputs.assert_not_called()


class TestLqKeywordsYearsMerge(unittest.TestCase):
    """--years must merge into existing split LQ outputs."""

    def test_years_filter_preserves_other_year_rows_and_pngs(self) -> None:
        import classify_lq_keywords as kw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_lq = root / "output" / "lq"
            classified_lq = root / "classified" / "lq"
            classified = root / "classified"
            classified.mkdir()
            classified_lq.mkdir(parents=True)

            from PIL import Image

            for year, qn, primary in (("2013", 1, 5), ("2024", 1, 8)):
                year_dir = output_lq / year
                year_dir.mkdir(parents=True)
                png = year_dir / f"q{qn}.png"
                Image.new("RGB", (20, 20), "white").save(png)
                book, folder, _name = kw.SECTION_BY_NUM[primary]
                dest_dir = classified_lq / book / folder
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{year}-q{qn}.png"
                dest.write_bytes(b"OLD-" + year.encode())

            existing_rows = [
                {
                    "Year": "2013",
                    "Question": "1",
                    "Primary": "5",
                    "AllSections": "5",
                    "Reason": "old-2013",
                    "PNG": "output/lq/2013/q1.png",
                    "AnswerPNG": "output/lq/2013/ans/q1.png",
                },
                {
                    "Year": "2024",
                    "Question": "1",
                    "Primary": "8",
                    "AllSections": "8",
                    "Reason": "old-2024",
                    "PNG": "output/lq/2024/q1.png",
                    "AnswerPNG": "output/lq/2024/ans/q1.png",
                },
            ]
            with (classified_lq / "classification.csv").open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=kw.NESTED_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(existing_rows)
            (classified_lq / "llm_classifications.json").write_text(
                json.dumps(
                    {
                        "2013-q1": {"sections": [5], "reason": "old-2013"},
                        "2024-q1": {"sections": [8], "reason": "old-2024"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            detailed = kw.build_detailed_rows(
                [
                    {**r, "Primary": int(r["Primary"]), "Question": int(r["Question"])}
                    for r in existing_rows
                ],
                {},
            )
            (classified / "lq_classification.json").write_text(
                json.dumps(detailed, indent=2) + "\n", encoding="utf-8"
            )

            with (
                mock.patch.object(kw, "ROOT", root),
                mock.patch.object(kw, "OUTPUT_LQ", output_lq),
                mock.patch.object(kw, "CLASSIFIED_LQ", classified_lq),
                mock.patch.object(kw, "OCR_CACHE", classified_lq / "ocr_cache"),
                mock.patch.object(
                    kw,
                    "parse_args",
                    return_value=argparse.Namespace(years=["2024"]),
                ),
                mock.patch.object(kw, "ocr_png", return_value="kinetic energy work done"),
                mock.patch.object(kw, "classify_text", return_value=([8], "new-2024")),
            ):
                kw.main()

            with (classified_lq / "classification.csv").open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            by_year = {r["Year"]: r for r in rows}
            self.assertEqual(by_year["2013"]["Reason"], "old-2013")
            self.assertEqual(by_year["2024"]["Reason"], "new-2024")

            top = json.loads((classified / "lq_classification.json").read_text())
            self.assertEqual(len(top), 2)

            book5, folder5, _ = kw.SECTION_BY_NUM[5]
            kept = classified_lq / book5 / folder5 / "2013-q1.png"
            self.assertTrue(kept.is_file())
            self.assertEqual(kept.read_bytes(), b"OLD-2013")


class TestLqPerformanceYearsMerge(unittest.TestCase):
    """extract_lq_performance --years must merge into existing JSON."""

    def test_years_filter_preserves_other_year_notes(self) -> None:
        import extract_lq_performance as perf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_dir = root / "paper" / "performance"
            out = root / "classified" / "lq" / "candidate_performance.json"
            perf_dir.mkdir(parents=True)
            out.parent.mkdir(parents=True)

            def write_year_md(year: str, note: str) -> None:
                (perf_dir / f"{year} performance.md").write_text(
                    "\n".join(
                        [
                            "## Paper 1",
                            "### Section B",
                            "#### Question 1",
                            f"**Performance in General:** {note}",
                            "",
                            "## Paper 2",
                            "### Section A",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            write_year_md("2013", "kept-other-year")
            write_year_md("2024", "updated-2024")
            out.write_text(
                json.dumps(
                    {
                        "2013": {"1": "old-2013"},
                        "2024": {"1": "old-2024"},
                        "2025": {"1": "untouched-source-missing"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(perf, "PERF_DIR", perf_dir),
                mock.patch.object(perf, "OUT", out),
                mock.patch.object(
                    sys,
                    "argv",
                    ["extract_lq_performance.py", "--years", "2024"],
                ),
            ):
                perf.main()

            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"2013", "2024", "2025"})
            self.assertEqual(data["2013"]["1"], "old-2013")
            self.assertEqual(data["2025"]["1"], "untouched-source-missing")
            self.assertEqual(data["2024"]["1"], "updated-2024")


class TestPreprocessPreservesCrops(unittest.TestCase):
    """pages-only preprocess must not delete existing question crops."""

    def test_crop_questions_flag_false_keeps_q_png(self) -> None:
        import fitz
        from PIL import Image

        import preprocess_lq as pl

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "exam.pdf"
            doc = fitz.open()
            doc.new_page(width=200, height=300)
            doc.save(source)
            doc.close()

            output_dir = tmp_path / "out"
            output_dir.mkdir()
            sentinel = b"SENTINEL-CROP-BYTES-DO-NOT-DELETE"
            crop_path = output_dir / "q1.png"
            crop_path.write_bytes(sentinel)

            pages = [Image.new("RGB", (120, 240), "white")]

            with (
                mock.patch.object(pl, "load_page_pngs", return_value=pages),
                mock.patch.object(
                    pl, "find_question_starts", return_value=[(1, 0, 12.0)]
                ),
                mock.patch.object(pl, "combine_pngs_to_pdf"),
                mock.patch.object(pl, "doc_has_jpeg_scans", return_value=False),
            ):
                count = pl.process_one(
                    source,
                    output_dir,
                    cover_pages=0,
                    scale=2.0,
                    max_questions=10,
                    crop_questions_flag=False,
                )

            self.assertEqual(count, 1)
            self.assertTrue(crop_path.is_file())
            self.assertEqual(crop_path.read_bytes(), sentinel)
            self.assertTrue((output_dir / "starts.json").is_file())


if __name__ == "__main__":
    unittest.main()
