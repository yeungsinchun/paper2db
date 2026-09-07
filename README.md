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

That one command walks every stage from `paper/` PDFs to `classified/` section folders and review PDFs. It pauses at a few review gates (MC anchors, optional LQ crops, uncertain classifications). Pass `--yes` to print the same paths without waiting for Enter.

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

## Layout

| Path | Role |
|------|------|
| `paper/mc/` | Paper 1A PDFs (`2012p1a.pdf`, `ppp1a.pdf`, ...) |
| `paper/lq/` | Paper 1B PDFs |
| `paper/ans/` | Marking schemes (`2012ans.pdf`, ...) |
| `paper/performance/` | Candidate-performance notes (markdown; optional, not a pipeline stage) |
| `output/<year>/` | MC question PNGs + `combined.pdf` |
| `output/lq/<year>/` | LQ pages, `qN.png`, `ans/qN.png`, review PDFs |
| `classified/mc/` | Section folders, CSVs, `answer_keys.json`, section PDFs |
| `classified/lq/` | Same for long questions (+ optional `candidate_performance.json`) |
| `scripts/` | Stage implementations (called by `./pipeline`) |
| `segment` | Low-level single-PDF tool (prefer `./pipeline`) |
| `.lavish/pipeline-review/` | Step-by-step HTML evidence for captain review |
| `.lavish/classified-review/` | MC section bank HTML |
| `.lavish/lq-classified-review/` | LQ section bank HTML |
| `classified/quality_audit.json` | Measured crop/classification failure rates |

## Stages

1. **mc-anchors** - blue dots on each MC paper; **you must review** `output/<year>-intermediate/anchor.pdf`
2. **mc-split** - crop clean `qN.png` into `output/<year>/`
3. **lq-pages** - export LQ pages + `starts.json`
4. **lq-crops** - full-question crops + `questions.pdf`
5. **lq-answers** - marking-scheme answer crops under `ans/`
6. **keys** - MC keys + correct-% → `classified/mc/answer_keys.json`
7. **classify-mc** - 27 syllabus sections (LLM if keyed, else keywords)
8. **classify-lq** - same sections for LQ (LLM if keyed, else keywords)
9. **section-pdfs** - per-section `combined.pdf` / `questions.pdf` (+ answer PDFs)
10. **lavish** - quality audit + HTML reviews under `.lavish/` (pipeline walkthrough, MC banks, LQ banks)

Candidate-performance extraction stays available as `python scripts/extract_lq_performance.py` when needed; it is not part of `./pipeline`.

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
3. **LQ crops** - skim `output/lq/<year>/questions.pdf` if a year looks truncated.
4. Trust the section review PDFs under `classified/*/.../combined.pdf` (or `questions.pdf`) rather than browsing PNG lists.
5. Skim `.lavish/pipeline-review/` for the measured rates before accepting a new year.

## Low-level tools

`./segment` and `scripts/*.py` remain available for single-paper debugging. Day-to-day use should be `./pipeline` only.
