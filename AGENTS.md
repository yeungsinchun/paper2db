# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Day-to-day entry is `./pipeline` (stages via `--list-stages`, `--from`, `--only`, `--years`, `--force`, `--yes`). Prefer it over calling `scripts/*.py` or `./segment` directly.
- Quality bar and failure definitions: `python scripts/quality_audit.py` → `classified/quality_audit.json`. Captain walkthrough: `.lavish/pipeline-review/` (LQ Step C uses multi-page fit previews so whole `page_from`..`page_to` stacks are visible). PR screenshot pack: `.lavish/pr-evidence/`. MC/LQ banks: `.lavish/classified-review/` and `.lavish/lq-classified-review/`.
- Human gates that matter: review `output/<year>-intermediate/anchor.pdf` before MC split; skim `classified/mc/uncertain.csv` after classify. Hard MC pages live in `scripts/overrides_YYYY.json` (each counts toward the ≤5% manual-tuning budget). LQ uses whole exam pages only (`page_from`..`page_to`) - no within-page crop. Trailing HKDSE data/formulae sheets and blank "do not write" inserts are excluded (`scripts/formula_sheet.py`). `scripts/crop_lq_from_pages.py` exports missing pages from `paper/lq` without rewriting `starts.json`, then always rewrites `qN.png` so stale y-crops cannot linger. Answer crops under `ans/` stay cropped.
- `output/`, `classified/` and `.lavish/` are gitignored build products; rebuild with `./pipeline --force --yes` (README "Regenerating output/ and classified/"). Never commit crops, section PDFs or review HTML. Only the hand-tuned inputs stay tracked: `output/lq/<year>/starts.json`, `classified/*/llm_classifications.json`, `classified/lq/candidate_performance.json`, `scripts/overrides_YYYY.json`. Anchor evidence: `./pipeline --only mc-anchors --years YYYY --force --yes`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
