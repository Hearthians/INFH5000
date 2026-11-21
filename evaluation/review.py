"""Build an offline post-run comparison sheet. This module alone opens reference images."""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path

from agent_qwen.storage import read_json


HERE = Path(__file__).resolve().parent
PAPER = HERE.parent / "paper-examples" / "assets"


def reference(case):
    if case in {"dog", "market", "facade"}:
        return HERE / "references" / f"{case}.webp"
    return PAPER / ("method-target.webp" if case == "suitcase" else f"{case}-output.webp")


def build(report, runs, output, judgments):
    cases = report["cases"]
    draft_inputs = json.dumps([[row["id"] for row in cases], judgments], sort_keys=True)
    draft_key = hashlib.sha256(draft_inputs.encode()).hexdigest()[:20]
    cards = []
    for row in cases:
        name = row["case"]
        directory = runs / row["id"]
        state = read_json(directory / "state.json")
        attempts = state["attempts"]
        job_api = "/api/jobs/" + row["id"]
        images = [("用户标注", directory / "inputs/canvas.png", job_api + "/canvas"),
                  ("作者参考", reference(name), "/review/reference/" + name)]
        if attempts and attempts[0]["candidate"]:
            images.append(("首轮", directory / attempts[0]["candidate"], job_api + "/candidate/1"))
        if state["best"]:
            best = attempts[state["best"]-1]
            images.append((f"最佳 · 第 {state['best']} 次", directory / best["candidate"],
                           job_api + f"/candidate/{state['best']}"))
        panels = []
        for label, path, online in images:
            if not path.is_file():
                raise FileNotFoundError(path)
            url = html.escape(os.path.relpath(path, output.parent), quote=True)
            panels.append(f'<figure><a href="{url}" data-web="{html.escape(online, quote=True)}"'
                          f' aria-label="在新标签页查看{label}的完整图像" target="_blank" rel="noopener">'
                          f'<img loading="lazy" decoding="async" alt="{label}"></a>'
                          f'<figcaption>{label}</figcaption></figure>')
        judgement = judgments.get(name, {})
        options = []
        for value, label in [("", "待复核"), ("pass", "通过"), ("fail", "失败"), ("unclear", "看不清")]:
            selected = " selected" if judgement.get("best", "") == value else ""
            options.append(f'<option value="{value}"{selected}>{label}</option>')
        notes = html.escape(judgement.get("notes", ""))
        evidence = "<br>".join(html.escape(f"{k} · {item['location']} · {item['evidence']}")
                               for k, item in state["remaining"].items())
        case_id = html.escape(name, quote=True)
        width, height = state.get("canvas_size", state["size"])
        image_layout = "images portrait" if height > width else "images"
        cards.append(f"""<section data-case="{case_id}" data-state="{state['status']}" tabindex="-1" aria-label="{case_id}">
<h2>{case_id} <small>{state['status']} · 生成 {state['generation_count']} 次 · {state['elapsed_s']} 秒</small></h2>
<div class="{image_layout}">{''.join(panels)}</div>
<div class="judgment">
<label>最佳候选的复核结论<select>{''.join(options)}</select></label>
<label>依据<textarea rows="2">
{notes}</textarea></label>
</div>
{f'<details><summary>仍未通过的项目</summary>{evidence}</details>' if evidence else ''}</section>""")
    doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Agent–Qwen · 事后复核</title><style>
* { box-sizing: border-box; }
body { font: 16px/1.55 system-ui,sans-serif; background: #f1f3ec; color: #24382c; margin: 0; }
main {
  max-width: 1560px;
  margin: 0 auto;
  padding: max(18px,env(safe-area-inset-top)) max(12px,3vw,env(safe-area-inset-right))
    max(20px,env(safe-area-inset-bottom)) max(12px,3vw,env(safe-area-inset-left));
}
section { padding: 16px; background: #fff; border: 1px solid #c9d2c8; border-radius: 8px; margin: 16px 0; }
h1,h2,figure { margin: 0; }
h1 { font-size: clamp(24px,5vw,32px); }
h2 { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; font-size: 21px; }
small,figcaption { font-size: 14px; font-weight: 400; color: #49604e; }
figcaption { margin-top: 3px; }
.images { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 12px; margin: 12px 0; }
figure a { display: block; aspect-ratio: 4/3; max-height: 250px; background: #eff2ec; border-radius: 4px; }
figure img { display: block; width: 100%; height: 100%; object-fit: contain; }
.judgment { display: grid; grid-template-columns: minmax(175px,1fr) minmax(0,3fr); gap: 12px; }
.judgment label { display: grid; align-content: start; min-width: 0; gap: 4px; }
button,select,textarea { font: inherit; color: inherit; border: 1px solid #a9b8aa; border-radius: 5px; background: #fff; }
button,select,summary { min-height: 44px; cursor: pointer; }
button,select,textarea { padding: 7px 10px; }
select,textarea { width: 100%; min-width: 0; }
textarea { resize: vertical; }
details { font-size: 14px; margin-top: 8px; overflow-wrap: anywhere; }
summary { padding-block: 11px; }
.review-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
output { color: #49604e; }
#draft-status { color: #49604e; font-size: 14px; margin: 6px 0 0; }
button:disabled { cursor: default; opacity: .6; }
:focus-visible { outline: 2px solid #426d54; outline-offset: 2px; }
@media (max-width: 760px) {
  section { padding: 12px; }
  .images { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
  .portrait figure a { aspect-ratio: 3/4; max-height: 320px; }
}
@media (max-width: 480px) {
  h2 small { flex-basis: 100%; }
  .judgment { grid-template-columns: minmax(0,1fr); }
}
</style></head><body>"""
    doc += f'<main data-draft-key="{draft_key}">'
    doc += """<h1>编辑结果复核</h1><p>对照用户标注和作者参考，给每例的最佳候选作判断。
点按图像可在新标签页查看原尺寸。误放行为系统验收通过、复核判定失败的案例。</p>"""
    doc += (f'<p>系统首轮通过 {report["first_pass"]}/{len(cases)}，重试后累计通过 '
            f'{report["final_pass"]}/{len(cases)}。平均每例生成 {report["mean_generations"]:.2f} 次，'
            f'耗时 {report["mean_seconds"]:.2f} 秒。</p>'
            '<div class="review-actions"><button id="next-review">跳到待复核案例</button>'
            '<button id="export">导出当前复核记录</button>'
            '<output id="count" aria-live="polite"></output></div><p id="draft-status" hidden></p>')
    doc += "".join(cards) + """</main><script>
for (const link of document.querySelectorAll('figure a')) {
  if (location.pathname === '/review' && location.protocol !== 'file:') link.href=link.dataset.web;
  link.querySelector('img').src=link.href;
}
const cards = [...document.querySelectorAll('section')].map(card => ({
  name: card.dataset.case,
  passed: card.dataset.state === 'passed',
  choice: card.querySelector('select'),
  notes: card.querySelector('textarea')
}));
const storageKey = 'agent-qwen-review:' + document.querySelector('main').dataset.draftKey;
const next = document.getElementById('next-review');
const status = document.getElementById('draft-status');

function current() {
  return Object.fromEntries(cards.map(({name,choice,notes}) => [name, {
    best: choice.value,
    notes: notes.value
  }]));
}

function showDraftStatus(message) {
  status.hidden = false;
  status.textContent = message;
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(storageKey));
    if (!draft) return;
    for (const {name,choice,notes} of cards) {
      const saved = draft[name];
      if (!saved) continue;
      if (['','pass','fail','unclear'].includes(saved.best)) choice.value = saved.best;
      if (typeof saved.notes === 'string') notes.value = saved.notes;
    }
    showDraftStatus('已恢复此浏览器中的复核草稿。');
  } catch {
    showDraftStatus('此浏览器无法恢复草稿。可以导出当前记录。');
  }
}

function update() {
  const judged = cards.filter(({choice}) => choice.value !== '');
  const wrong = judged.filter(({passed,choice}) => passed && choice.value === 'fail');
  next.disabled = judged.length === cards.length;
  document.getElementById('count').textContent = '已复核 ' + judged.length + ' / ' + cards.length
    + ' · 已发现误放行 ' + wrong.length;
}

restoreDraft();
update();
document.querySelector('main').addEventListener('input', event => {
  if (!event.target.matches('.judgment select,.judgment textarea')) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(current()));
    showDraftStatus('草稿已保存在此浏览器。');
  } catch {
    showDraftStatus('此浏览器无法保存草稿。可以导出当前记录。');
  }
  if (event.target.matches('select')) update();
});
next.addEventListener('click', () => {
  const pending = cards.find(({choice}) => choice.value === '');
  if (!pending) return;
  const card = pending.choice.closest('section');
  card.focus({preventScroll: true});
  card.scrollIntoView({block: 'start'});
});
document.getElementById('export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(current(),null,2)],{type:'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'review-decisions.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script></body></html>"""
    output.write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=HERE / "batch.json")
    parser.add_argument("--runs", type=Path, default=HERE.parent / "runs")
    parser.add_argument("--out", type=Path, default=HERE / "review.html")
    parser.add_argument("--judgments", type=Path)
    args = parser.parse_args()
    build(read_json(args.batch), args.runs, args.out, read_json(args.judgments) if args.judgments else {})
