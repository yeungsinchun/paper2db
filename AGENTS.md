# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Day-to-day entry is `./pipeline` (stages via `--list-stages`, `--from`, `--only`, `--years`, `--force`, `--yes`). Prefer it over calling `scripts/*.py` or `./segment` directly.
- Quality bar and failure definitions: `python scripts/quality_audit.py` → `classified/quality_audit.json`. Captain walkthrough: `.lavish/pipeline-review/`. MC/LQ banks: `.lavish/classified-review/` and `.lavish/lq-classified-review/`.
- Human gates that matter: review `output/<year>-intermediate/anchor.pdf` before MC split; skim `classified/mc/uncertain.csv` after classify. Hard MC pages live in `scripts/overrides_YYYY.json`.
- Intermediate dirs (`output/*-intermediate/`, page PNGs) are gitignored; regenerate with `./pipeline --only mc-anchors --years YYYY --force --yes` when you need anchor evidence.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
