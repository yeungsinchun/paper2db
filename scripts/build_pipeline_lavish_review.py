#!/usr/bin/env python3
"""Build a step-by-step pipeline Lavish review (intermediates + finals + quality)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
OUT = ROOT / ".lavish" / "pipeline-review"
IMG = OUT / "img"
AUDIT_JSON = ROOT / "classified" / "quality_audit.json"


def python_executable() -> str:
    venv = ROOT / ".venv" / "bin" / "python3"
    return str(venv) if venv.is_file() else sys.executable


def ensure_audit() -> dict:
    subprocess.run(
        [python_executable(), str(SCRIPTS / "quality_audit.py"), "--output", str(AUDIT_JSON)],
        check=True,
        cwd=str(ROOT),
    )
    return json.loads(AUDIT_JSON.read_text(encoding="utf-8"))


def copy_sample(src: Path, dest_name: str) -> str | None:
    if not src.is_file():
        return None
    dest = IMG / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return f"img/{dest_name}"


def existing_sample(dest_name: str) -> str | None:
    if (IMG / dest_name).is_file():
        return f"img/{dest_name}"
    return None


def resolve_sample(src: Path, dest_name: str) -> str | None:
    return copy_sample(src, dest_name) or existing_sample(dest_name)


def collect_assets() -> dict:
    assets: dict[str, list[dict]] = {
        "anchors": [],
        "mc_crops": [],
        "lq_crops": [],
        "mc_classified": [],
        "lq_classified": [],
    }

    for page in range(1, 4):
        dest_name = f"anchor-2024-page{page:02d}.png"
        rel = resolve_sample(
            ROOT / "output" / "2024-intermediate" / f"page{page:02d}.png",
            dest_name,
        )
        if rel:
            assets["anchors"].append(
                {
                    "label": f"2024 MC anchor page {page}",
                    "path": rel,
                    "note": "Blue dots must sit beside question numbers with a clear gap.",
                }
            )

    for year, question in (("2012", 1), ("2012", 15), ("2024", 1), ("2024", 4), ("2025", 1)):
        dest_name = f"mc-{year}-q{question}.png"
        rel = resolve_sample(
            ROOT / "output" / year / f"q{question}.png",
            dest_name,
        )
        if rel:
            assets["mc_crops"].append(
                {
                    "label": f"MC {year} Q{question}",
                    "path": rel,
                    "note": "Empty stem + options; no answer bleed.",
                }
            )

    for year, question in (("2012", 1), ("2012", 2), ("2024", 1), ("2025", 1)):
        dest_name = f"lq-{year}-q{question}.png"
        rel = resolve_sample(
            ROOT / "output" / "lq" / year / f"q{question}.png",
            dest_name,
        )
        if rel:
            assets["lq_crops"].append(
                {
                    "label": f"LQ {year} Q{question}",
                    "path": rel,
                    "note": "Full long-question crop from the paper (may span pages).",
                }
            )

    mc_samples = [
        ROOT
        / "classified"
        / "mc"
        / "01_Heat_and_Gases"
        / "02_Heat_Capacity"
        / "2012_q1.png",
        ROOT
        / "classified"
        / "mc"
        / "02_Force_and_Motion"
        / "05_Motion"
        / "2024_q4.png",
        ROOT
        / "classified"
        / "mc"
        / "03A_Wave_Motion"
        / "13_Wave_Motion"
        / "2012_q15.png",
    ]
    for src in mc_samples:
        if not src.is_file():
            # Fall back to any PNG in that section folder.
            candidates = list(src.parent.glob("*.png"))
            src = candidates[0] if candidates else src
        dest_name = f"classified-mc-{src.name}"
        rel = resolve_sample(src, dest_name)
        if rel:
            assets["mc_classified"].append(
                {
                    "label": f"Bank: {src.parent.name} / {src.name}",
                    "path": rel,
                    "note": str(src.relative_to(ROOT)) if src.is_file() else dest_name,
                }
            )

    lq_samples = [
        ROOT
        / "classified"
        / "lq"
        / "01_Heat_and_Gases"
        / "03_Change_of_State"
        / "2012-q1.png",
        ROOT
        / "classified"
        / "lq"
        / "02_Force_and_Motion"
        / "09_Momentum"
        / "2012-q4.png",
    ]
    for src in lq_samples:
        if not src.is_file():
            candidates = list(src.parent.glob("*-q*.png"))
            candidates = [c for c in candidates if "-ans" not in c.name]
            src = candidates[0] if candidates else src
        dest_name = f"classified-lq-{src.name}"
        rel = resolve_sample(src, dest_name)
        if rel:
            assets["lq_classified"].append(
                {
                    "label": f"Bank: {src.parent.name} / {src.name}",
                    "path": rel,
                    "note": str(src.relative_to(ROOT)) if src.is_file() else dest_name,
                }
            )

    return assets


def pct(rate: float) -> str:
    return f"{100 * rate:.2f}%"


def write_html(audit: dict, assets: dict) -> None:
    summary = audit["summary"]
    mc = summary["mc"]
    lq = summary["lq"]
    combined = summary["combined"]
    overrides = summary["overrides_historical"]
    passes = summary["passes_5pct_bar"]

    stages = [
        ("1. MC anchors", "mc-anchors", "Blue dots on each Paper 1A PDF → output/<year>-intermediate/anchor.pdf"),
        ("2. MC split", "mc-split", "Crop empty qN.png + combined.pdf under output/<year>/"),
        ("3. LQ pages", "lq-pages", "Export pages + starts.json under output/lq/<year>/"),
        ("4. LQ crops", "lq-crops", "Full-question crops + questions.pdf"),
        ("5. LQ answers", "lq-answers", "Marking-scheme answer crops under ans/"),
        ("6. Keys", "keys", "MC answer keys → classified/mc/answer_keys.json"),
        ("7. Classify MC", "classify-mc", "27 syllabus sections (LLM if keyed, else keywords)"),
        ("8. Classify LQ", "classify-lq", "Same sections for long questions (LLM if keyed, else keywords)"),
        ("9. Section PDFs", "section-pdfs", "Per-section combined.pdf / questions.pdf"),
        ("10. Lavish", "lavish", "HTML reviews under .lavish/"),
    ]

    def gallery(items: list[dict]) -> str:
        if not items:
            return '<p class="muted">No sample assets found for this step.</p>'
        cards = []
        for item in items:
            cards.append(
                f"""
<article class="shot">
  <img src="{item['path']}" alt="{item['label']}" loading="lazy" />
  <div class="shot-cap">
    <strong>{item['label']}</strong>
    <span>{item['note']}</span>
  </div>
</article>"""
            )
        return '<div class="gallery">' + "".join(cards) + "</div>"

    stage_rows = "".join(
        f"<tr><td>{name}</td><td><code>{code}</code></td><td>{desc}</td></tr>"
        for name, code, desc in stages
    )

    fail_defs = summary["failure_definitions"]
    counted = "".join(f"<li><code>{item}</code></li>" for item in fail_defs["counted"])
    not_counted = "".join(
        f"<li><code>{item}</code></li>" for item in fail_defs["not_counted"]
    )

    override_rows = "".join(
        f"<tr><td>{year}</td><td>{count}</td></tr>"
        for year, count in sorted(overrides["by_year"].items())
    )

    status_class = "ok" if passes else "bad"
    status_text = "PASS (<=5% manual tuning)" if passes else "FAIL (above 5%)"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>paper2db pipeline review</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    :where(.rate-grid, .two-col, .gallery, .review-hero) > * {{ min-width: 0; }}
    p, h1, h2, h3, li, td, th, .shot-cap span {{ overflow-wrap: anywhere; }}
    img, svg {{ max-width: 100%; height: auto; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(1200px 600px at 10% -10%, rgba(212,165,116,0.18), transparent 60%),
        radial-gradient(900px 500px at 90% 0%, rgba(142,197,192,0.12), transparent 55%),
        #120f18;
      color: #f3eef8;
      font-family: "Avenir Next", "Segoe UI", system-ui, sans-serif;
    }}
    .wrap {{ max-width: 1120px; margin: 0 auto; padding: 28px 18px 80px; }}
    .review-hero {{
      display: grid; gap: 14px; margin-bottom: 28px;
      padding: 22px 22px 20px; border-radius: 18px;
      background: linear-gradient(160deg, rgba(29,26,36,0.96), rgba(20,18,26,0.92));
      border: 1px solid rgba(255,255,255,0.08);
    }}
    .review-hero h1 {{
      margin: 0; font-family: "Iowan Old Style", Palatino, Georgia, serif;
      font-size: clamp(1.8rem, 3vw, 2.6rem); letter-spacing: -0.02em;
      line-height: 1.15;
    }}
    .review-hero p {{ margin: 0; color: #b7acc6; line-height: 1.5; max-width: 62ch; }}
    .verdict-pill {{
      display: inline-flex; align-items: center; gap: 8px; width: fit-content;
      padding: 8px 12px; border-radius: 999px; font-weight: 700; font-size: 0.9rem;
    }}
    .verdict-pill.ok {{ background: rgba(125,207,154,0.16); color: #9ee0b4; border: 1px solid rgba(125,207,154,0.35); }}
    .verdict-pill.bad {{ background: rgba(232,139,139,0.16); color: #f0b0b0; border: 1px solid rgba(232,139,139,0.35); }}
    .rate-grid {{
      display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;
    }}
    @media (max-width: 800px) {{ .rate-grid {{ grid-template-columns: 1fr; }} }}
    .rate-card {{
      background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
      border-radius: 14px; padding: 14px 16px;
    }}
    .rate-card .k {{ color: #a89bb8; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; }}
    .rate-card .v {{ font-size: 1.6rem; font-weight: 800; margin-top: 4px; font-family: ui-monospace, Menlo, monospace; }}
    .rate-card .s {{ color: #a89bb8; font-size: 0.85rem; margin-top: 4px; }}
    section.block {{
      margin: 28px 0; padding: 20px; border-radius: 18px;
      background: rgba(29,26,36,0.88); border: 1px solid rgba(255,255,255,0.08);
    }}
    section.block h2 {{
      margin: 0 0 8px; font-family: "Iowan Old Style", Palatino, Georgia, serif;
      font-size: 1.45rem;
    }}
    section.block .lede {{ margin: 0 0 16px; color: #b7acc6; line-height: 1.45; }}
    .flow {{
      width: 100%; overflow-x: auto; padding: 8px 0 4px;
    }}
    .muted {{ color: #a89bb8; }}
    table.stages {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
    table.stages th, table.stages td {{
      text-align: left; padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.08);
      vertical-align: top;
    }}
    table.stages th {{ color: #d4a574; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; }}
    code {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.85em; color: #f0d2ad; }}
    .gallery {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr));
      gap: 14px;
    }}
    .shot {{
      background: #0f0d14; border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; overflow: hidden;
    }}
    .shot img {{ width: 100%; height: auto; display: block; background: #fff; }}
    .shot-cap {{ padding: 10px 12px; display: grid; gap: 4px; }}
    .shot-cap strong {{ font-size: 0.92rem; }}
    .shot-cap span {{ color: #a89bb8; font-size: 0.8rem; overflow-wrap: anywhere; }}
    .two-col {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
    }}
    @media (max-width: 800px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
    .listbox {{
      background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px; padding: 12px 14px;
    }}
    .listbox h3 {{ margin: 0 0 8px; font-size: 0.95rem; color: #d4a574; }}
    .listbox ul {{ margin: 0; padding-left: 1.1rem; color: #d8cfe4; }}
    .links a {{
      display: inline-flex; margin: 0 10px 10px 0; padding: 8px 12px; border-radius: 10px;
      background: rgba(212,165,116,0.16); border: 1px solid rgba(212,165,116,0.35);
      color: #f0d2ad; text-decoration: none; font-size: 0.9rem;
    }}
    .cmd {{
      background: #0b0910; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px;
      padding: 12px 14px; font-family: ui-monospace, Menlo, monospace; font-size: 0.85rem;
      color: #cfe8e4; overflow-x: auto; white-space: pre-wrap;
    }}
    .verdict-btn {{
      margin-top: 12px; border: 1px solid rgba(212,165,116,0.45);
      background: rgba(212,165,116,0.22); color: #f0d2ad;
      border-radius: 10px; padding: 8px 14px; cursor: pointer; font: inherit;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="review-hero">
      <div class="verdict-pill {status_class}">{status_text}</div>
      <h1>paper2db pipeline evidence</h1>
      <p>
        One command (<code>./pipeline</code>) turns HKDSE Physics PDFs into classified empty
        question crops for MC and long questions. This page walks the stages with intermediate
        artifacts, final banks, and the measured manual-tuning rate.
      </p>
      <div class="rate-grid">
        <div class="rate-card">
          <div class="k">MC manual tuning</div>
          <div class="v">{pct(mc['manual_tuning_rate'])}</div>
          <div class="s">{mc['failure_count']} / {mc['questions']} questions</div>
        </div>
        <div class="rate-card">
          <div class="k">LQ manual tuning</div>
          <div class="v">{pct(lq['manual_tuning_rate'])}</div>
          <div class="s">{lq['failure_count']} / {lq['questions']} questions</div>
        </div>
        <div class="rate-card">
          <div class="k">Combined</div>
          <div class="v">{pct(combined['manual_tuning_rate'])}</div>
          <div class="s">{combined['failure_count']} / {combined['questions']} questions</div>
        </div>
      </div>
    </header>

    <section class="block" id="flow">
      <h2>Pipeline flow</h2>
      <p class="lede">Day-to-day entry is <code>./pipeline</code>. Scripts under <code>scripts/</code> and <code>segment</code> are internals.</p>
      <div class="flow" aria-label="pipeline stages diagram">
        <svg viewBox="0 0 980 170" width="100%" role="img" id="pipeline-flow">
          <title>PDF papers flow through ten stages into classified crops and Lavish reviews</title>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#8ec5c0" />
            </marker>
          </defs>
          <rect x="8" y="40" width="120" height="54" rx="12" fill="#1d1a24" stroke="#d4a574" />
          <text x="68" y="72" text-anchor="middle" fill="#f3eef8" font-size="13">paper/*.pdf</text>
          <line x1="128" y1="67" x2="168" y2="67" stroke="#8ec5c0" stroke-width="2" marker-end="url(#arrow)" />
          <rect x="170" y="28" width="150" height="78" rx="12" fill="#25212e" stroke="rgba(255,255,255,0.15)" />
          <text x="245" y="58" text-anchor="middle" fill="#d4a574" font-size="12">anchors + split</text>
          <text x="245" y="80" text-anchor="middle" fill="#a89bb8" font-size="11">MC blue dots → crops</text>
          <line x1="320" y1="67" x2="360" y2="67" stroke="#8ec5c0" stroke-width="2" marker-end="url(#arrow)" />
          <rect x="362" y="28" width="150" height="78" rx="12" fill="#25212e" stroke="rgba(255,255,255,0.15)" />
          <text x="437" y="58" text-anchor="middle" fill="#d4a574" font-size="12">LQ pages/crops</text>
          <text x="437" y="80" text-anchor="middle" fill="#a89bb8" font-size="11">starts.json → qN.png</text>
          <line x1="512" y1="67" x2="552" y2="67" stroke="#8ec5c0" stroke-width="2" marker-end="url(#arrow)" />
          <rect x="554" y="28" width="150" height="78" rx="12" fill="#25212e" stroke="rgba(255,255,255,0.15)" />
          <text x="629" y="58" text-anchor="middle" fill="#d4a574" font-size="12">classify</text>
          <text x="629" y="80" text-anchor="middle" fill="#a89bb8" font-size="11">27 syllabus sections</text>
          <line x1="704" y1="67" x2="744" y2="67" stroke="#8ec5c0" stroke-width="2" marker-end="url(#arrow)" />
          <rect x="746" y="40" width="220" height="54" rx="12" fill="#1d1a24" stroke="#8ec5c0" />
          <text x="856" y="72" text-anchor="middle" fill="#f3eef8" font-size="13">classified/* + .lavish/</text>
          <text x="490" y="150" text-anchor="middle" fill="#a89bb8" font-size="12">Human gates: review anchor.pdf, then uncertain.csv (usually empty)</text>
        </svg>
      </div>
      <div style="overflow-x:auto">
        <table class="stages">
          <thead><tr><th>Step</th><th>Stage</th><th>Output</th></tr></thead>
          <tbody>{stage_rows}</tbody>
        </table>
      </div>
      <div class="cmd" style="margin-top:14px">./pipeline
./pipeline --years 2024 2025
./pipeline --from classify-mc
./pipeline --only lavish
python scripts/quality_audit.py --strict</div>
    </section>

    <section class="block" id="quality">
      <h2>What counts as a failure</h2>
      <p class="lede">These events count toward the &lt;=5% manual-tuning budget, including every question listed in <code>scripts/overrides_YYYY.json</code>.</p>
      <div class="two-col">
        <div class="listbox">
          <h3>Counted</h3>
          <ul>{counted}</ul>
        </div>
        <div class="listbox">
          <h3>Not counted</h3>
          <ul>{not_counted}</ul>
          <p class="muted" style="margin:10px 0 0;font-size:0.85rem">
            LQ missing answers: {lq['missing_answer_png_count']}
            (often no ans PDF for 2026/pp). Override-tuned questions
            (counted above): {overrides['total_questions']} across
            {len(overrides['by_year'])} years.
          </p>
        </div>
      </div>
      <div style="overflow-x:auto;margin-top:16px">
        <table class="stages">
          <thead><tr><th>Override year</th><th>Questions tuned (counted)</th></tr></thead>
          <tbody>{override_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="block" id="anchors">
      <h2>Step A - MC anchors (intermediate)</h2>
      <p class="lede">Sample from <code>output/2024-intermediate/</code> (regenerated for this review). Every blue dot must sit beside the question number - not on options, diagrams, or digits.</p>
      {gallery(assets['anchors'])}
    </section>

    <section class="block" id="mc-crops">
      <h2>Step B - MC empty crops (final per-question)</h2>
      <p class="lede">Clean screenshots cut from the paper: full stem + options, no neighboring bleed. Review PDF: <code>output/&lt;year&gt;/combined.pdf</code>.</p>
      {gallery(assets['mc_crops'])}
    </section>

    <section class="block" id="lq-crops">
      <h2>Step C - LQ crops (final per-question)</h2>
      <p class="lede">Long questions are taller and often multi-page. Review PDF: <code>output/lq/&lt;year&gt;/questions.pdf</code>.</p>
      {gallery(assets['lq_crops'])}
    </section>

    <section class="block" id="classified">
      <h2>Step D - Classified section banks</h2>
      <p class="lede">Crops copied into syllabus folders with section PDFs. Full interactive banks are linked below.</p>
      <h3 style="margin:0 0 10px;color:#d4a574;font-size:1rem">MC samples</h3>
      {gallery(assets['mc_classified'])}
      <h3 style="margin:18px 0 10px;color:#d4a574;font-size:1rem">LQ samples</h3>
      {gallery(assets['lq_classified'])}
    </section>

    <section class="block" id="reviews">
      <h2>Full review surfaces</h2>
      <p class="lede">Open these for exhaustive section-by-section inspection (crop + classification reason + fix form).</p>
      <div class="links">
        <a href="../classified-review/index.html">MC classified review</a>
        <a href="../lq-classified-review/index.html">LQ classified review</a>
      </div>
      <form data-lavish-question="captain-verdict" onsubmit="return window.__submitVerdict(event)" class="listbox" style="margin-top:12px">
        <h3>Captain verdict</h3>
        <p class="muted" style="margin:0 0 10px;font-size:0.9rem">Does this evidence clear the &lt;=5% bar for accepting the pipeline?</p>
        <label style="display:block;margin:6px 0"><input type="radio" name="verdict" value="accept" /> Accept - pipeline is good enough</label>
        <label style="display:block;margin:6px 0"><input type="radio" name="verdict" value="needs-work" /> Needs more work before accept</label>
        <label style="display:grid;gap:4px;margin-top:10px">
          <span class="muted" style="font-size:0.8rem">Note</span>
          <input name="note" type="text" placeholder="optional note" style="padding:8px 10px;border-radius:10px;border:1px solid rgba(255,255,255,0.12);background:#120f18;color:#f3eef8" />
        </label>
        <button type="submit" class="verdict-btn">Queue verdict</button>
      </form>
    </section>
  </div>
  <script>
    window.__submitVerdict = function(ev) {{
      ev.preventDefault();
      const form = ev.target;
      const verdict = new FormData(form).get('verdict');
      const note = (form.note.value || '').trim();
      if (!verdict) {{
        alert('Pick accept or needs-work');
        return false;
      }}
      const parts = ['Pipeline review verdict: ' + verdict];
      if (note) parts.push('Note: ' + note);
      if (!window.lavish || typeof window.lavish.queuePrompt !== 'function') {{
        alert(parts.join(' '));
        return false;
      }}
      window.lavish.queuePrompt(parts.join(' '), {{
        tag: 'pipeline-verdict',
        data: {{ verdict, note, mc_rate: {mc['manual_tuning_rate']}, lq_rate: {lq['manual_tuning_rate']}, combined_rate: {combined['manual_tuning_rate']} }}
      }});
      if (typeof window.lavish.sendQueuedPrompts === 'function') window.lavish.sendQueuedPrompts();
      return false;
    }};
  </script>
</body>
</html>
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "data.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "assets": assets,
                "audit_path": str(AUDIT_JSON.relative_to(ROOT)),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)
    audit = ensure_audit()
    assets = collect_assets()
    write_html(audit, assets)
    print(f"Wrote {OUT}/index.html")
    print(f"Quality: passes_5pct_bar={audit['summary']['passes_5pct_bar']}")


if __name__ == "__main__":
    main()
