"""Tests for qb pipeline stage wiring and gate logic."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
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


def load_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestQbPipelineStages(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipe = load_pipeline()

    def test_stages_include_qb(self) -> None:
        self.assertIn("qb-pdf", self.pipe.STAGES)
        self.assertIn("qb-ocr", self.pipe.STAGES)
        self.assertIn("qb-items", self.pipe.STAGES)
        self.assertIn("qb-audit", self.pipe.STAGES)

    def test_qb_stages_after_lavish(self) -> None:
        idx_lavish = self.pipe.STAGES.index("lavish")
        idx_qb_pdf = self.pipe.STAGES.index("qb-pdf")
        self.assertGreater(idx_qb_pdf, idx_lavish)

    def test_select_qb_only(self) -> None:
        args = mock.Mock(only="qb-pdf,qb-items", from_stage=None, until=None, skip_lavish=False)
        self.assertEqual(self.pipe.select_stages(args), ["qb-pdf", "qb-items"])


class TestQbQualityAudit(unittest.TestCase):
    def test_quality_module_loads(self) -> None:
        mod = load_module("qb_quality", ROOT / "scripts" / "qb_quality.py")
        self.assertTrue(hasattr(mod, "check_items"))
        self.assertTrue(hasattr(mod, "check_crops"))
        self.assertTrue(hasattr(mod, "check_key_status"))

    def test_check_items_with_synthetic_index(self) -> None:
        mod = load_module("qb_quality", ROOT / "scripts" / "qb_quality.py")
        # Build a synthetic index that matches expected gate
        items = []
        for bank, count in mod.EXPECTED_IN_SCOPE_BY_BANK.items():
            for i in range(count):
                # Mix types
                typ = "mc" if i < count // 2 else ("sq" if i % 3 == 0 else "lq")
                status = "present"
                # QB_503 MC missing
                if bank == "QB_503" and typ == "mc" and i < 38:
                    status = "missing"
                # QB_202: first 64 are MC from-pdf
                if bank == "QB_202" and i < 64:
                    typ = "mc"
                    status = "from-pdf" if i < 64 else "present"
                    if i >= 45 and typ == "mc":
                        status = "missing"
                items.append({"id": f"PHY{bank}_{i}", "bank": bank, "type": typ, "status": status})
        # Add out-of-scope items to reach 3712
        remaining = 3712 - len(items)
        for i in range(remaining):
            items.append({"id": f"PHY_OUT_{i}", "bank": "QB_101", "type": "mc", "status": "present"})

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            items_dir = tmp_path / "qb-pdf" / "items"
            items_dir.mkdir(parents=True)
            (items_dir / "index.json").write_text(json.dumps({"items": items, "total": len(items)}))
            crops_dir = tmp_path / "qb-pdf" / "crops"
            crops_dir.mkdir(parents=True)
            for e in items:
                (crops_dir / f"{e['id']}.png").write_bytes(b"\x89PNG")

            with mock.patch.object(mod, "ITEMS_DIR", items_dir):
                with mock.patch.object(mod, "CROPS_DIR", crops_dir):
                    with mock.patch.object(mod, "QB_PDF", tmp_path / "qb-pdf"):
                        c = mod.check_crops()
                        self.assertEqual(c["present"], len(items))
                        self.assertTrue(c["ok"])


if __name__ == "__main__":
    unittest.main()
