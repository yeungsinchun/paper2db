# paper2db

Turn HKDSE Physics past papers into per-question crops and curriculum-section banks.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# tesseract must be on PATH (OCR)

./pipeline
```

That one command walks every stage from `paper/` PDFs to `tests/reconstructed/` papers and `classified/` section folders with review PDFs. Generated artifacts in both trees are gitignored - regenerate them rather than committing them (`./pipeline --force --yes` rebuilds everything). The tracked inputs are listed below. It pauses at a few review gates (MC anchors, optional LQ crops, uncertain classifications). Pass `--yes` to print the same paths without waiting for Enter.

```bash
./pipeline --years 2025          # one year
./pipeline --from classify-mc    # resume mid-pipeline
./pipeline --only keys,section-pdfs
./pipeline --force --yes         # rebuild everything, no prompts
./pipeline --list-stages
```

Optional LLM for MC and LQ classification (better than keywords when available):

```bash
export LLM_API_KEY=...           # or OPENAI_API_KEY / TOGETHER_API_KEY
export LLM_BASE_URL=https://api.together.xyz/v1   # optional
export LLM_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo
./pipeline --from classify-mc --force
```

When no API key is set, both MC and LQ use the keyword classifiers.

## Regenerating `tests/reconstructed/` and `classified/`

Generated crops, section PDFs and `.lavish/` HTML are **not committed** (see `.gitignore`). Rebuild them from `paper/` with:

```bash
./pipeline --force --yes      # all years, all stages, no review prompts
./pipeline --years 2025 --force --yes   # one year
```

The only files under `tests/reconstructed/` and `classified/` that git tracks are durable inputs or replayable decisions: `tests/reconstructed/lq/<year>/starts.json` (LQ page ranges, preserved by normal `lq-pages` runs), `classified/*/llm_classifications.json` (LLM section decisions, editable and replayable with `--from-json`) and `classified/lq/candidate_performance.json` (from `scripts/extract_lq_performance.py`). Everything else is reproducible from `paper/` with `./pipeline`, so never `git add` crops, section PDFs or `.lavish/` HTML.

## Layout

| Path | Role |
|------|------|
| `paper/mc/` | Paper 1A PDFs (`2012p1a.pdf`, `ppp1a.pdf`, ...) |
| `paper/lq/` | Paper 1B PDFs |
| `paper/ans/` | Marking schemes (`2012ans.pdf`, ...) |
| `paper/performance/` | Candidate-performance notes (markdown; optional, not a pipeline stage) |
| `intermediate/mc/<year>/` | MC anchor review PDF (`anchor.pdf`), OCR meta and preview PNGs, ahead of the split |
| `tests/reconstructed/mc/` | `combined.pdf` (every year's MC paper) + `<year>/` MC question PNGs with a per-year `combined.pdf` |
| `tests/reconstructed/lq/` | `combined.pdf` (every year's LQ questions) + `<year>/` LQ pages, `qN.png`, `ans/qN.png`, per-year `combined.pdf` |
| `classified/mc/` | Section folders, CSVs, `answer_keys.json`, section PDFs |
| `classified/lq/` | Same for long questions (+ optional `candidate_performance.json`) |
| `scripts/` | Stage implementations (called by `./pipeline`) |
| `segment.py` | Low-level single-PDF tool (prefer `./pipeline`) |
| `.lavish/pipeline-review/` | Step-by-step HTML evidence for captain review |
| `.lavish/classified-review/` | MC section bank HTML |
| `.lavish/lq-classified-review/` | LQ section bank HTML |
| `classified/quality_audit.json` | Measured crop/classification failure rates |

## Stages

1. **mc-anchors** - blue dots on each MC paper; **you must review** `intermediate/mc/<year>/anchor.pdf`
2. **mc-split** - crop clean `qN.png` into `tests/reconstructed/mc/<year>/` + A4 `combined.pdf`; then joins every year into `tests/reconstructed/mc/combined.pdf`
3. **lq-pages** - export LQ pages + `starts.json`
4. **lq-crops** - whole exam page stack per question (`page_from`..`page_to`); A4 `combined.pdf` of those stacks from the source paper (no cover, no within-page crop; trailing data/formulae sheets excluded); then joins every year into `tests/reconstructed/lq/combined.pdf`
5. **lq-answers** - marking-scheme answer crops under `ans/`
6. **keys** - MC keys + correct-% → `classified/mc/answer_keys.json`
7. **classify-mc** - 27 syllabus sections (LLM if keyed, else keywords)
8. **classify-lq** - same sections for LQ (LLM if keyed, else keywords). Either backend then lists every Book 5 section a radioactivity LQ tests (e.g. 2014 Q10: ch26 activity + ch25 alpha handling; 2012 Q11 keeps 25+26+27), primary = latest section. Both backends OCR the whole page stack (cache keyed by PNG size under `classified/lq/ocr_cache/`)
9. **section-pdfs** - per-section A4 `combined.pdf` (+ LQ `answers.pdf` / `performance.pdf`); an LQ appears in every section it is listed under, not only its primary
10. **lavish** - quality audit + HTML reviews under `.lavish/` (pipeline walkthrough, MC banks, LQ banks)

Candidate-performance extraction stays available as `python scripts/extract_lq_performance.py` when needed; it is not part of `./pipeline`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests   # seconds; needs no build
```

The suite runs against fixtures (`tests/fixtures/lq_ocr/`, temp trees) and covers the Book 5 listing rule, the reconstructed layout joiner and the upright section-PDF packing. Tests that inspect generated banks (`classified/`, `tests/reconstructed/`) skip until `./pipeline` has built them. A full rebuild takes tens of minutes (page export and OCR dominate), so when evidence is needed build one track - e.g. `./pipeline --only lq-pages,lq-crops,lq-answers,classify-lq,keys,section-pdfs --yes` for the LQ banks (`section-pdfs` needs `keys`) - or one year with `--years`, rather than everything.

## Quality bar

Target: **≤5%** of questions need human manual tuning, including every entry in `scripts/overrides_YYYY.json`.

```bash
./pipeline --only lavish
# or:
python scripts/quality_audit.py --strict
```

`classified/quality_audit.json` counts as failures: missing crop, missing classified copy, uncertain flag, tiny crop, incomplete year folder, and override-tuned questions. It does **not** count missing LQ answer PNGs when no ans PDF exists, or tall LQ crops.

Captain review surface: `.lavish/pipeline-review/index.html` (step-by-step intermediates + finals). Full banks: `.lavish/classified-review/` (MC) and `.lavish/lq-classified-review/` (LQ).

## Quality checklist (minimal human work)

1. **Anchors** - every blue dot beside the question number with a clear gap (not on options or diagrams). Wrong anchors poison every later step. Use `scripts/overrides_YYYY.json` for hard pages (each counts toward the 5% budget).
2. **Uncertain MC** - skim `classified/mc/uncertain.csv` and spot-check a few section folders.
3. **LQ pages** - skim `tests/reconstructed/lq/<year>/combined.pdf` if a question's page range looks wrong (`starts.json`). The last question should stop before any trailing data/formulae sheet or blank "do not write" insert.
4. Trust the section review PDFs under `classified/*/.../combined.pdf` rather than browsing PNG lists. Those PDFs are portrait A4 with year and question labels.
5. Skim `.lavish/pipeline-review/` for the measured rates before accepting a new year.

## Low-level tools

`./segment.py` and `scripts/*.py` remain available for single-paper debugging. Day-to-day use should be `./pipeline` only.
