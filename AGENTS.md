# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Day-to-day entry is `./pipeline` (stages via `--list-stages`, `--from`, `--only`, `--years`, `--force`, `--yes`). Prefer it over calling `scripts/*.py` or `./segment` directly.
- Quality bar and failure definitions: `python scripts/quality_audit.py` → `classified/quality_audit.json`. Captain walkthrough: `.lavish/pipeline-review/` (LQ Step C uses multi-page fit previews so whole `page_from`..`page_to` stacks are visible). PR screenshot pack: `.lavish/pr-evidence/`. MC/LQ banks: `.lavish/classified-review/` and `.lavish/lq-classified-review/`.
- Generated trees (`reconstructed/`, `classified/`, `.lavish/`) are gitignored: regenerate with `./pipeline` (see README "Layout"), never commit them; PRs carry scripts, overrides, tests, docs, and visual evidence.
- Human gates that matter: review `reconstructed/mc/<year>-intermediate/anchor.pdf` before MC split; skim `classified/mc/uncertain.csv` after classify. Hard MC pages live in `scripts/overrides_YYYY.json` (each counts toward the ≤5% manual-tuning budget). LQ uses whole exam pages only (`page_from`..`page_to`) - no within-page crop. Trailing HKDSE data/formulae sheets and blank "do not write" inserts are excluded (`scripts/formula_sheet.py`). `scripts/crop_lq_from_pages.py` exports missing pages from `paper/lq` without rewriting `starts.json`, then always rewrites `qN.png` so stale y-crops cannot linger. Answer crops under `ans/` stay cropped.
- Regenerate anchor evidence with `./pipeline --only mc-anchors --years YYYY --force --yes` (`reconstructed/mc/<year>-intermediate/`).
- LQ Book 5 classification is resolved by `classify_book5()` in `scripts/classify_lq_keywords.py` (every tested section is listed); `tests/test_lq_book5_sections.py` pins the per-chapter problem list from OCR fixtures under `tests/fixtures/lq_ocr/`. Section `questions.pdf` include non-primary listings, so a stale PDF means the section-pdfs stage was not re-run.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
