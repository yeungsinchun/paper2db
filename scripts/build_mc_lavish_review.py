#!/usr/bin/env python3
"""Rebuild MC Lavish review from classified/mc/classification.csv."""
from __future__ import annotations

import csv
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path

from classify_mc_llm import BOOK_NAMES, SECTIONS

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIED = ROOT / "classified" / "mc"
OUT = ROOT / ".lavish" / "classified-review"
IMG = OUT / "img"


def main() -> None:
    csv_path = CLASSIFIED / "classification.csv"
    if not csv_path.is_file():
        raise SystemExit(f"Missing {csv_path}")

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    OUT.mkdir(parents=True, exist_ok=True)
    if IMG.exists():
        shutil.rmtree(IMG)
    IMG.mkdir(parents=True)

    by_sec: dict[int, list[dict]] = defaultdict(list)
    bust = str(int(time.time()))
    for row in rows:
        primary = int(row["PrimarySection"])
        all_secs = [int(x) for x in row["AllSections"].split(";") if x]
        year, question = row["Year"], int(row["Question"])
        image_name = f"{year}_q{question}.png"
        dest = IMG / image_name
        source = ROOT / row["PNG"]
        if source.is_file():
            shutil.copy2(source, dest)
        preview = (row.get("StatementPreview") or "")[:220]
        item = {
            "year": year,
            "q": question,
            "primary": primary,
            "sections": ";".join(str(s) for s in all_secs),
            "is_primary": True,
            "preview": preview,
            "reason": row.get("Reason") or "",
            "uncertain": row.get("Uncertain", "no"),
            "img": f"img/{image_name}?v={bust}" if dest.is_file() else "",
        }
        for section in all_secs:
            copy = dict(item)
            copy["is_primary"] = section == primary
            by_sec[section].append(copy)

    sections = []
    for num, book, _folder, name in SECTIONS:
        items = by_sec.get(num, [])
        sections.append(
            {
                "num": num,
                "name": name,
                "book": BOOK_NAMES.get(book, book),
                "count": len(items),
                "items": items,
            }
        )

    (OUT / "data.json").write_text(
        json.dumps({"sections": sections}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_html(sections)
    print(f"Wrote {OUT}/index.html ({sum(s['count'] for s in sections)} placements)")


def write_html(sections: list[dict]) -> None:
    payload = {
        "sections": [
            {
                "num": section["num"],
                "name": section["name"],
                "book": section["book"],
                "count": section["count"],
                "items": [
                    {
                        "year": item["year"],
                        "q": item["q"],
                        "img": item["img"],
                        "sections": item["sections"],
                        "primary": item["primary"],
                        "is_primary": item["is_primary"],
                        "preview": item["preview"],
                        "reason": item["reason"],
                        "uncertain": item.get("uncertain") or "no",
                    }
                    for item in section["items"]
                ],
            }
            for section in sections
        ]
    }
    data_js = json.dumps(payload, ensure_ascii=False)
    sec_names = {str(section["num"]): section["name"] for section in sections}
    books: list[dict] = []
    for section in sections:
        if not books or books[-1]["name"] != section["book"]:
            books.append({"name": section["book"], "sections": []})
        books[-1]["sections"].append(section)

    nav = []
    for book in books:
        nav.append(f'<div class="nav-book">{book["name"]}</div>')
        for section in book["sections"]:
            nav.append(
                f'<button type="button" class="nav-sec" data-sec="{section["num"]}">'
                f'<span class="nav-sec-label"><span class="mono">S{section["num"]}</span> '
                f'{section["name"]}</span>'
                f'<span class="nav-count">{section["count"]}</span></button>'
            )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MC classified review</title>
  <style>
    :root {{
      --bg: #14121a; --panel: #1d1a24; --panel-2: #25212e; --line: rgba(255,255,255,0.10);
      --text: #f3eef8; --muted: #a89bb8; --accent: #d4a574; --accent-2: #8ec5c0;
      --shadow: 0 10px 30px rgba(0,0,0,0.28); --radius: 14px; --nav-w: 280px;
      --font: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: "Avenir Next", "Segoe UI", system-ui, sans-serif;
    }}
    *, *::before, *::after {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); }}
    img {{ max-width: 100%; height: auto; display: block; }}
    button, select, input {{ font: inherit; }}
    .app {{ display: grid; grid-template-columns: var(--nav-w) minmax(0, 1fr); min-height: 100vh; }}
    .sidebar {{
      position: sticky; top: 0; height: 100vh; overflow: auto;
      background: var(--panel); border-right: 1px solid var(--line); padding: 18px 12px 28px;
    }}
    .brand {{ padding: 4px 10px 16px; border-bottom: 1px solid var(--line); margin-bottom: 12px; }}
    .brand h1 {{ margin: 0; font-family: var(--font); font-size: 1.25rem; }}
    .brand p {{ margin: 6px 0 0; color: var(--muted); font-size: 0.8rem; }}
    .nav-book {{ margin: 14px 8px 6px; font-size: 0.68rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); font-weight: 700; }}
    .nav-sec {{
      width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px;
      text-align: left; background: transparent; color: var(--text); border: 0; border-radius: 10px;
      padding: 8px 10px; cursor: pointer; margin-bottom: 2px;
    }}
    .nav-sec:hover {{ background: rgba(255,255,255,0.05); }}
    .nav-sec.active {{ background: rgba(212,165,116,0.16); outline: 1px solid rgba(212,165,116,0.35); }}
    .nav-sec-label {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.86rem; }}
    .mono {{ font-family: var(--mono); font-weight: 700; }}
    .nav-count {{ flex: 0 0 auto; font-size: 0.72rem; color: var(--muted); background: rgba(255,255,255,0.06); border-radius: 999px; padding: 2px 7px; }}
    .main {{ min-width: 0; padding: 20px 24px 64px; }}
    .topbar {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: end; justify-content: space-between; margin-bottom: 18px; }}
    .topbar h2 {{ margin: 0; font-family: var(--font); font-size: clamp(1.5rem, 2.4vw, 2rem); }}
    .topbar .meta {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
    .help {{ background: rgba(142,197,192,0.10); border: 1px solid rgba(142,197,192,0.28); border-radius: var(--radius); padding: 12px 14px; color: #d7ecea; font-size: 0.9rem; margin-bottom: 18px; line-height: 1.45; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: 16px; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); min-width: 0; }}
    .card.uncertain {{ outline: 1px solid rgba(230,180,80,0.45); }}
    .card-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; padding: 12px 14px; background: var(--panel-2); border-bottom: 1px solid var(--line); }}
    .qid .year {{ margin: 0; font-size: 0.72rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }}
    .qid .qn {{ margin: 2px 0 0; font-family: var(--mono); font-size: 1.55rem; font-weight: 800; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }}
    .badge {{ font-size: 0.72rem; border-radius: 999px; padding: 3px 8px; background: rgba(255,255,255,0.07); color: var(--muted); border: 1px solid var(--line); }}
    .badge.primary {{ background: rgba(212,165,116,0.18); color: #f0d2ad; border-color: rgba(212,165,116,0.35); }}
    .badge.secondary {{ background: rgba(142,197,192,0.14); color: #bfe3df; border-color: rgba(142,197,192,0.35); }}
    .badge.warn {{ background: rgba(230,180,80,0.16); color: #f0d79a; border-color: rgba(230,180,80,0.35); }}
    .shot {{ background: #fff; border-bottom: 1px solid var(--line); }}
    .shot img {{ width: 100%; height: auto; display: block; }}
    .missing {{ padding: 24px; color: var(--muted); font-size: 0.9rem; }}
    .body {{ padding: 12px 14px 14px; display: grid; gap: 10px; }}
    .preview, .reason {{ margin: 0; font-size: 0.84rem; line-height: 1.45; color: var(--muted); overflow-wrap: anywhere; }}
    .reason strong {{ color: #d8cfe4; font-weight: 600; }}
    .form {{ display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid var(--line); }}
    .field {{ display: grid; gap: 4px; }}
    .field span {{ font-size: 0.74rem; color: var(--muted); }}
    .field select, .field input[type="text"] {{ width: 100%; background: var(--bg); color: var(--text); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .btn {{ border: 1px solid var(--line); background: var(--panel-2); color: var(--text); border-radius: 10px; padding: 8px 12px; cursor: pointer; }}
    .btn-save {{ background: rgba(212,165,116,0.22); border-color: rgba(212,165,116,0.45); }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: relative; height: auto; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>MC review</h1>
        <p>Empty question crops by syllabus section. Mark fixes below each card.</p>
      </div>
      {''.join(nav)}
    </aside>
    <main class="main">
      <div class="topbar">
        <div>
          <div class="meta" id="bookLabel"></div>
          <h2 id="secTitle">Pick a section</h2>
          <div class="meta" id="secCount"></div>
        </div>
      </div>
      <div class="help">Each card is the paper crop (stem + options, no answers). Uncertain rows are highlighted. Use the form to queue a section move.</div>
      <div class="grid" id="grid"></div>
    </main>
  </div>
  <script>
    const DATA = {data_js};
    const SEC_NAMES = {json.dumps(sec_names)};
    const SECTION_OPTS = DATA.sections.map(s =>
      '<option value="' + s.num + '">' + s.num + '. ' + s.name.replace(/</g,'&lt;') + '</option>'
    ).join('');
    const grid = document.getElementById('grid');
    const bookLabel = document.getElementById('bookLabel');
    const secTitle = document.getElementById('secTitle');
    const secCount = document.getElementById('secCount');

    function esc(s) {{
      return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    }}

    function badges(it) {{
      const all = String(it.sections).split(';').map(Number).filter(Boolean);
      const primary = Number(it.primary);
      let html = '<span class="badge primary">primary S' + primary + ' · ' + esc(SEC_NAMES[String(primary)] || '') + '</span>';
      for (const n of all.filter(x => x !== primary)) {{
        html += '<span class="badge secondary">also S' + n + '</span>';
      }}
      if (String(it.uncertain).toLowerCase() === 'yes') {{
        html += '<span class="badge warn">uncertain</span>';
      }}
      return html;
    }}

    function renderSection(num) {{
      const sec = DATA.sections.find(s => s.num === Number(num));
      if (!sec) return;
      document.querySelectorAll('.nav-sec').forEach(btn => {{
        btn.classList.toggle('active', Number(btn.dataset.sec) === sec.num);
      }});
      bookLabel.textContent = sec.book;
      secTitle.innerHTML = '<span class="mono">S' + sec.num + '</span> · ' + esc(sec.name);
      secCount.textContent = sec.count + ' questions';
      if (!sec.items.length) {{
        grid.innerHTML = '<p class="empty">No MC in this section yet.</p>';
        return;
      }}
      grid.innerHTML = sec.items.map(it => {{
        const qid = it.year + '-q' + it.q;
        const uncertain = String(it.uncertain).toLowerCase() === 'yes';
        const shot = it.img
          ? '<div class="shot"><img src="' + esc(it.img) + '" alt="crop" loading="lazy" /></div>'
          : '<div class="missing">Missing crop PNG</div>';
        return (
'<article class="card' + (uncertain ? ' uncertain' : '') + '" id="q-' + qid + '">' +
  '<div class="card-head">' +
    '<div class="qid"><p class="year">Year ' + esc(it.year) + '</p><p class="qn">Q' + it.q + '</p></div>' +
    '<div class="badges">' + badges(it) + '</div>' +
  '</div>' +
  shot +
  '<div class="body">' +
    '<p class="preview">' + esc(it.preview) + '</p>' +
    '<p class="reason"><strong>Reason:</strong> ' + (it.reason ? esc(it.reason) : '<span style="opacity:.6">None</span>') + '</p>' +
    '<form class="form" data-lavish-question="fix-' + qid + '" onsubmit="return window.__submitFix(event)">' +
      '<input type="hidden" name="year" value="' + esc(it.year) + '" />' +
      '<input type="hidden" name="question" value="' + it.q + '" />' +
      '<input type="hidden" name="current" value="' + sec.num + '" />' +
      '<label class="field"><span>Correct section</span><select name="target"><option value="">OK as classified</option>' + SECTION_OPTS + '</select></label>' +
      '<label class="field"><span>Note</span><input type="text" name="note" placeholder="crop / topic note" /></label>' +
      '<div class="actions"><button type="submit" class="btn btn-save">Queue fix</button></div>' +
    '</form>' +
  '</div>' +
'</article>'
        );
      }}).join('');
      history.replaceState(null, '', '#sec-' + sec.num);
    }}

    window.__submitFix = function(ev) {{
      ev.preventDefault();
      const form = ev.target;
      const year = form.year.value;
      const q = form.question.value;
      const current = form.current.value;
      const target = form.target.value;
      const note = (form.note.value || '').trim();
      const parts = ['MC fix: ' + year + ' Q' + q + ' (currently S' + current + ')'];
      if (target) parts.push('Move to S' + target + ' (' + (SEC_NAMES[target] || '') + ').');
      if (note) parts.push('Note: ' + note);
      if (!target && !note) parts.push('Marked OK.');
      if (!window.lavish || typeof window.lavish.queuePrompt !== 'function') {{
        alert(parts.join(' '));
        return false;
      }}
      window.lavish.queuePrompt(parts.join(' '), {{ tag: 'mc-fix', data: {{ year, q, current, target, note }} }});
      if (typeof window.lavish.sendQueuedPrompts === 'function') window.lavish.sendQueuedPrompts();
      form.reset();
      return false;
    }};

    document.querySelectorAll('.nav-sec').forEach(btn => {{
      btn.addEventListener('click', () => renderSection(btn.dataset.sec));
    }});
    const hash = (location.hash || '').replace('#sec-', '');
    const first = DATA.sections.find(s => s.count > 0);
    renderSection(hash || (first ? first.num : 1));
  </script>
</body>
</html>
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
