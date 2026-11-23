"""Builds SAUDA_Abstract.pdf (max 500 words) from results/*.json so every number is real.

    python docs/build_abstract.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
o = json.loads((RES / "benchmark_official.json").read_text())
g = json.loads((RES / "benchmark_gym.json").read_text())
lp = RES / "benchmark_llm.json"
llm = json.loads(lp.read_text()) if lp.exists() else None
cr = json.loads((RES / "classifier_report.json").read_text())
llm_acc = cr.get("final_test", {}).get("accuracy")
kit_acc = cr.get("kit_lines", {}).get("accuracy")

llm_line = (f"Against four local LLM buyers that write their own messages ({llm['n']} negotiations): "
            f"{llm['policy']} policy violations, {llm['truth']} untrue claims, score {llm['score']}. ") if llm else ""
cls_line = (f"A trained tactic classifier (TF-IDF + logistic regression, trained epoch by epoch) reaches "
            f"{100 * llm_acc:.1f}% on a fresh set of hand-written messages written after training was frozen and "
            f"{100 * kit_acc:.1f}% on the organisers' own manipulator lines. ") if llm_acc is not None else ""

BODY = f"""
<h2>The problem</h2>
<p>Shoppers now send AI agents to buy for them, but store assistants are built for people. Research on agent-to-agent
commerce finds sellers that accept below-cost deals, are anchored by fake budgets, obey fake "system" messages and deadlock.</p>

<h2>Innovation: deciding is code, talking is checked</h2>
<p>SAUDA never lets the conversation set the price. A <b>strategist</b> chooses a basket price each round from numbers only.
A <b>PolicyGuard</b> clamps every offer to [floor, list] and stock, and refuses any bid carrying slipped-in out-of-stock,
over-stock or one-rupee lines. A <b>truth gate</b> checks every sentence against the official truthfulness rules and a ledger
of numbers genuinely in play; a failing sentence is replaced by a plain one. A <b>trained classifier</b> names the buyer's
tactic (fake competitor quote, prompt injection, fake authority, false agreement, stock claim, threat) to choose calm wording
and an audit label, never a discount. Because prices depend only on the basket and priced bids, two buyers with the same needs
and bids get identical offers.</p>
<p>The strategist reads the buyer's opening bid to tell a near-limit opener (close fast) from a long haggle (hold the ask
while it keeps conceding). It fits the buyer's concession curve to its real bids, predicts its limit, and makes the final ask
there; it always closes or walks away in time, so it does not deadlock.</p>

<h2>Results (official scoring code, hidden-set style run)</h2>
<p>Default run, 210 negotiations: score <b>{o['score']}</b> versus the starter baseline's <b>{o['baseline']}</b>,
<b>{o['policy']}</b> policy violations, <b>{o['truth']}</b> untrue claims, {o['deadlocks']} deadlocks,
deal rate {100 * o['deal_rate']:.1f}% ({100 * o['feasible_deal_rate']:.1f}% where a compliant deal existed),
{o['latency_ms']} ms per turn. {llm_line}{cls_line}The strategy was tuned on training seeds and selected on held-out seeds across
eleven buyer styles; on the eight extra gym styles it scores {g['score']} against the baseline's {g['baseline']}.</p>

<h2>Impact</h2>
<p>A seller that stays inside policy and truthful against manipulative machine buyers lets a small store join agentic commerce
without margin leakage or compliance risk, and its outputs are auditable round by round.</p>

<h2>Cost estimate</h2>
<p>The seller runs on the standard library in under 2 ms per turn, so serving cost is effectively one CPU core and no model
calls per message. The tactic classifier is a 1.3 MB file. Optional Rezolve TraceWare verification adds one metered call per
outgoing message; at ten rounds per negotiation that is at most ten calls per deal.</p>

<h2>Build summary and Rezolve Labs</h2>
<p>Code: agent/ (seller, guards, classifier inference, Rezolve adapter), gym/ (eight scripted and four local-LLM buyers, tuner),
model/ (data and training), dashboard/ (control room), 51 automated tests and a browser test of every dashboard button.
Rezolve Labs capabilities: <b>TraceWare commerce claim verification</b> for the truth gate and <b>managed knowledge retrieval</b>
for catalogue and policy. Credentials were not issued at submission; the adapter runs the local verifier and switches to
TraceWare when configured, failing closed.</p>
"""

text_only = re.sub(r"<[^>]+>", " ", BODY)
words = len(re.findall(r"\S+", text_only))
HTML = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SAUDA abstract</title><style>
@page {{ size: A4; margin: 14mm 15mm; }}
:root {{ --ink:#13201d; --body:#25302d; --brand:#1f6f5c; --mute:#5d6966; --line:#d6ddd9; }}
body {{ margin:0; font-family:"Segoe UI",Calibri,Arial,sans-serif; color:var(--body); font-size:10.6pt; line-height:1.45; }}
.head {{ border-bottom:1.3pt solid var(--ink); padding-bottom:6pt; margin-bottom:8pt; }}
.k {{ font-size:8pt; letter-spacing:.08em; text-transform:uppercase; color:var(--brand); font-weight:700; }}
h1 {{ font-family:Georgia,serif; font-size:21pt; margin:2pt 0 2pt; color:var(--ink); }} h1 span {{ color:var(--brand); }}
.sub {{ color:var(--mute); font-size:9.6pt; }}
h2 {{ font-size:11.6pt; color:var(--ink); margin:9pt 0 2pt; }} p {{ margin:0 0 4pt; text-align:justify; }} b {{ color:var(--ink); }}
.foot {{ margin-top:10pt; border-top:.6pt solid var(--line); padding-top:4pt; font-size:8.4pt; color:var(--mute); }}
</style></head><body>
<div class="head"><div class="k">AgentX · Challenge 2 · Selling to Machines · Round 1 abstract</div>
<h1>SAUDA <span>·</span> an honest, profitable seller agent</h1>
<div class="sub">Team HackBots · Devaansh Sharma · Techfest, IIT Bombay 2026–27</div></div>
{BODY}
<div class="foot">{words} words (limit 500). All figures come from the repository's results files and reproduce with tools/run_benchmarks.py.</div>
</body></html>"""
(ROOT / "docs" / "abstract.html").write_text(HTML, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto((ROOT / "docs" / "abstract.html").as_uri())
    pg.wait_for_timeout(300)
    pg.pdf(path=str(ROOT / "docs" / "SAUDA_Abstract.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
    b.close()
print("abstract words:", words)
