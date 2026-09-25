# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Day-to-day entry is `./pipeline` (stages via `--list-stages`, `--from`, `--only`, `--years`, `--force`, `--yes`). Prefer it over calling `scripts/*.py` or `./segment.py` directly.
- `metadata/{mc,lq}/llm_classifications.json` are the only tracked classification outputs (paid, nondeterministic LLM calls); everything else the pipeline can produce - section PNGs/CSVs/PDFs, `answer_keys.json`, `candidate_performance.json`, `quality_audit.json`, OCR caches - lives under generated `tests/sections/{mc,lq}/` and is gitignored. Hand-tuned inputs that stay tracked: `scripts/overrides_YYYY.json` (MC anchor overrides) and `scripts/answer_key_overrides.json` (MC answer-key OCR patches).
- Quality bar and failure definitions: `python scripts/quality_audit.py` → `tests/sections/quality_audit.json`. Captain walkthrough: `.lavish/pipeline-review/` (LQ Step C uses multi-page fit previews so whole `page_from`..`page_to` stacks are visible). PR screenshot pack: `.lavish/pr-evidence/`. MC/LQ banks: `.lavish/classified-review/` and `.lavish/lq-classified-review/`.
- Generated artifacts under `intermediate/`, `tests/reconstructed/`, `tests/sections/`, and `.lavish/` are gitignored: regenerate with `./pipeline`; README "Regenerating" lists the tracked inputs. Never commit generated crops, PDFs, or review HTML.
- Tests: `.venv/bin/python -m unittest discover -s tests` runs in seconds with no build (README "Tests"); generated-bank checks skip until `./pipeline` has run. A full rebuild is long, so for gate evidence build one track/year rather than everything.
- Human gates that matter: review `intermediate/mc/<year>/anchor.pdf` before MC split; skim `tests/sections/mc/uncertain.csv` after classify. Hard MC pages live in `scripts/overrides_YYYY.json` (each counts toward the ≤5% manual-tuning budget). LQ uses whole exam pages only (`page_from`..`page_to`) - no within-page crop. Trailing HKDSE data/formulae sheets and blank "do not write" inserts are excluded (`scripts/formula_sheet.py`). `scripts/crop_lq_from_pages.py` exports missing pages from `paper/lq` without rewriting `starts.json`, then always rewrites `qN.png` so stale y-crops cannot linger. Answer crops under `ans/` stay cropped.
- Regenerate anchor evidence with `./pipeline --only mc-anchors --years YYYY --force --yes` (`intermediate/mc/<year>/`).
- LQ Book 5 listings go through `apply_book5_listings()` in `scripts/classify_lq_keywords.py` after either classify-lq backend (keyword or LLM); `tests/test_lq_book5_sections.py` pins the per-chapter problem list from OCR fixtures under `tests/fixtures/lq_ocr/`. Section `combined.pdf` (MC and LQ alike) include non-primary listings, so a stale PDF means the section-pdfs stage was not re-run.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
