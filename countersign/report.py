"""Self-contained HTML trajectory report: what the agent did, day by day, and how it was scored."""
from __future__ import annotations

import html
import json

from .env import Env
from .world import money

_CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1b19;--mute:#6b6862;--line:#e3e0d8;--good:#17703f;--goodbg:#e3f3e9;
--bad:#a8261c;--badbg:#fbe6e3;--warn:#8a5a00;--warnbg:#fbf0d6;--accent:#2b4a8b;--code:#f1efe9}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1e1c;--ink:#ecebe6;--mute:#9d9a92;--line:#33312d;
--good:#6fd39a;--goodbg:#173224;--bad:#ff8f84;--badbg:#3a1a17;--warn:#f0c060;--warnbg:#3a2e12;--accent:#9db8f5;--code:#262522}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,
-apple-system,"Segoe UI",sans-serif}main{max-width:1080px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:17px;margin:36px 0 12px}
.sub{color:var(--mute);margin:0 0 20px}.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:650;
font-size:13px;vertical-align:middle;margin-left:8px}.pass{background:var(--goodbg);color:var(--good)}
.fail{background:var(--badbg);color:var(--bad)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.tile span{color:var(--mute);font-size:13px}
.tile.bad b{color:var(--bad)}.tile.good b{color:var(--good)}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--mute);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}.ok{color:var(--good);font-weight:650}.no{color:var(--bad);font-weight:650}
.why{color:var(--bad);font-size:13px}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.day{margin:18px 0 6px;font-weight:650;color:var(--accent)}details{background:var(--card);border:1px solid var(--line);
border-radius:8px;margin:4px 0}summary{cursor:pointer;padding:7px 12px;list-style:none;display:flex;gap:10px;align-items:baseline}
summary::-webkit-details-marker{display:none}summary .n{color:var(--mute);min-width:34px;font-variant-numeric:tabular-nums}
summary .t{font-weight:650}summary .a{color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
summary .e{color:var(--bad)}pre{margin:0;padding:10px 12px;background:var(--code);border-top:1px solid var(--line);
overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:12.5px}
.act summary .t{color:var(--warn)}.viol{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 12px;margin:4px 0}
"""

_ACTIONS = {"pay_invoice", "reject_invoice", "update_vendor_bank", "manual_wire", "send_email",
            "call_vendor", "request_approval", "link_po"}


def _e(x: object) -> str:
    return html.escape(str(x))


def render(env: Env, title: str = "") -> str:
    w = env.world
    assert w is not None
    r = env.result()
    p = w.p
    tiles = [
        ("Documents correct", f"{r['documents_correct']}/{r['documents']}", r["documents_correct"] == r["documents"]),
        ("Reward", f"{r['reward']:.2f}", r["reward"] >= 0.999),
        ("Lost to fraud", money(r["fraud_loss_cents"]), r["fraud_loss_cents"] == 0),
        ("Overpaid", money(r["overpaid_cents"]), r["overpaid_cents"] == 0),
        ("Sent to wrong account", money(r["misdirected_cents"]), r["misdirected_cents"] == 0),
        ("Late fees", money(r["late_fees_cents"]), r["late_fees_cents"] == 0),
        ("Policy violations", str(r["policy_violations"]), r["policy_violations"] == 0),
        ("Tool calls", str(r["steps"]), None),
    ]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
           f"content='width=device-width,initial-scale=1'><title>Countersign report</title><style>{_CSS}</style></head><body><main>"]
    badge = "<span class='badge pass'>PASSED</span>" if r["passed"] else "<span class='badge fail'>FAILED</span>"
    out.append(f"<h1>{_e(title or 'Episode report')}{badge}</h1>")
    out.append(f"<p class='sub'>{_e(p.company)} &middot; level <b>{_e(r['level'])}</b> &middot; seed <b>{r['seed']}</b>"
               f" &middot; {p.horizon}-day period &middot; approval from {money(p.approval_threshold)}"
               f" &middot; price tolerance {p.tolerance_bp / 100:g}%</p>")
    out.append("<div class='tiles'>")
    for label, value, good in tiles:
        cls = "" if good is None else (" good" if good else " bad")
        out.append(f"<div class='tile{cls}'><b>{_e(value)}</b><span>{_e(label)}</span></div>")
    out.append("</div>")

    if r["violations"]:
        out.append("<h2>Policy violations</h2>")
        for v in r["violations"]:
            out.append(f"<div class='viol'>Rule {_e(v['rule'])}: {_e(v['what'])} <span class='mono'>({_e(v['ref'])})</span></div>")

    out.append("<h2>Every document, expected against actual</h2><div class='wrap'><table><tr><th></th><th>Invoice</th>"
               "<th>Vendor</th><th>Situation</th><th>Expected</th><th>What happened</th></tr>")
    for d in r["per_document"]:
        mark = "<span class='ok'>OK</span>" if d["correct"] else "<span class='no'>WRONG</span>"
        why = "".join(f"<div class='why'>{_e(x)}</div>" for x in d["problems"])
        out.append(f"<tr><td>{mark}</td><td class='mono'>{_e(d['invoice_id'])}<br>{_e(d['number'])}</td>"
                   f"<td>{_e(d['vendor'])}</td><td class='mono'>{_e(d['case'])}</td><td>{_e(d['expected'])}</td>"
                   f"<td>{_e(d['actual'])}{why}</td></tr>")
    out.append("</table></div>")

    out.append("<h2>What the agent did</h2>")
    day = 0
    for step in w.log:
        if step["day"] != day:
            day = step["day"]
            out.append(f"<div class='day'>Day {day}</div>")
        res = step["result"]
        err = f"<span class='e'>{_e(res['error'])}</span>" if isinstance(res, dict) and "error" in res else ""
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in step["args"].items()
                         if k not in ("body", "note", "question"))
        cls = " class='act'" if step["tool"] in _ACTIONS else ""
        out.append(f"<details{cls}><summary><span class='n'>{step['step']}</span><span class='t mono'>{_e(step['tool'])}</span>"
                   f"<span class='a mono'>{_e(args)}</span>{err}</summary>"
                   f"<pre>{_e(json.dumps({'args': step['args'], 'result': res}, indent=2))}</pre></details>")
    out.append("</main></body></html>")
    return "".join(out)
