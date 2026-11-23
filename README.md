# SAUDA: an honest, profitable seller agent for machine buyers

**AgentX: The Agentic Commerce Challenge · Techfest IIT Bombay 2026–27 · Challenge 2: Selling to Machines**
Team HackBots (Devaansh Sharma)

SAUDA (Hindi for "deal") is a store's selling agent that negotiates price and bundles with AI
buyer agents over the AgentX JSON message format. It closes as many profitable deals as it can,
**never breaks store policy, never says anything the store data does not support, and never
deadlocks**. Prices come from code; the conversation can never talk the price down.

## Results

| Suite | Negotiations | SAUDA | Starter baseline | Policy violations | Untrue claims | Deadlocks |
|---|---|---|---|---|---|---|
| Official harness (`starter_kit/evaluate.py`, seed 0) | 210 | **51.0** | 48.5 | 0 | 0 | 1 |
| Extra buyer gym (8 styles, held-out seed) | 240 | **50.9** | 48.5 | 0 | 0 | 2 |
| Local LLM buyers (qwen2.5:3b, 4 styles) | 24 | **42.6** | 53.8 | 0 | 0 | 0 |

Honest reading: SAUDA beats the baseline on the scripted buyers and stays at zero violations everywhere,
including against LLM buyers. Against the local LLM buyers it is safe but still earns less than the baseline
(small, noisy sample), because those buyers anchor near their limit and leave early. The tactic classifier
scores 85.7% on fresh hand-written messages and 100.0% on the organisers' own manipulator lines.
Reproduce: `python tools/run_benchmarks.py` (add `--llm 24` with Ollama running).


## Why it is built this way

Research on agent-to-agent commerce keeps finding the same failures: LLM sellers sell below
cost and get talked into discounts (Anthropic, *Project Vend*), agents are redirected by prompt
injection and authority claims (Microsoft, *Magentic Marketplace*), and a seller whose price
floor is written only in its prompt concedes past that floor under sustained pressure, while a
seller whose floor is enforced in code never does (*Computation Is Not Authorization*, NeurIPS
2026). SAUDA therefore separates **deciding** from **talking**:

| Layer | File | Job |
|---|---|---|
| Strategist | `agent/sauda_seller.py` | Chooses a basket price each round from numbers only: the buyer's priced bids, the floor and the round. |
| Opponent model | `agent/sauda_seller.py` (`_fit`) | Fits the buyer's concession curve to its real bids and predicts the most it will pay. |
| PolicyGuard | `agent/sauda_guard.py` | Checks every offer before it is sent and every bid before it is accepted (margin, discount, list cap, stock, well-formed lines). |
| Truth gate | `agent/sauda_guard.py`, `agent/rezolve_adapter.py` | Checks every sentence against the official truthfulness rules and a ledger of numbers in play; anything that would fail is replaced by a plain sentence. Uses Rezolve TraceWare when configured. |
| Tactic classifier | `agent/tactic_model.py` (trained by `model/`) | Names the buyer's tactic (fake competitor quote, prompt injection, false agreement, fake authority, stock claim, threat, budget claim) to choose calm wording and log it. It never changes a price. |

### The strategy in one paragraph

A buyer's **opening bid** tells SAUDA which situation it is in. A *near-limit opener* (opening
bid already near the policy floor) is close to what it will pay, so SAUDA answers a little above
the bid and closes quickly. A *long haggle* (opening bid far below the floor) is a buyer that
concedes slowly; SAUDA holds its ask while the buyer keeps moving, fits the buyer's concession
curve to its real bids, and in the last round asks for the limit that curve predicts (never more
than its previous ask). If the buyer stalls, SAUDA mirrors it, closing a growing share of the gap
each round. A compliant bid is accepted as soon as it is at least what SAUDA would ask next. In
the last round SAUDA always either closes or walks away, so a negotiation never deadlocks unless
the buyer ignores a valid final offer.

### Honesty and fairness by construction

* **Policy:** offers are clamped to `[floor, list]` per product and to stock; bids with slipped-in
  out-of-stock lines, over-stock quantities, unknown SKUs or one-rupee add-ons are never accepted.
* **Truthfulness:** the seller only mentions its own offer total, list total, saving and discount
  (all "in play"), never stock counts unless exact, never pressure words, never fees except
  "no hidden fees".
* **Manipulation:** fake competitor quotes, budgets, "SYSTEM" instructions, "store manager"
  authority and "you already agreed" claims change the wording of the reply, never the price.
* **Fairness:** prices depend only on the basket and the priced bids, so two buyers with the same
  needs and the same bids get identical offers, whatever they write (tested in `tests/`).
* **Agent card:** `docs/agent_card.json` states what SAUDA can and cannot negotiate.

## Run it

Python 3.9+. The seller itself uses the standard library only, like the starter kit.

```bash
cd starter_kit
python evaluate.py --seller ../agent/sauda_seller.py                    # official scoring, default run
python evaluate.py --seller ../agent/sauda_seller.py --log ../results/official_transcripts_eval.jsonl
python harness.py  --seller ../agent/sauda_seller.py --buyer manipulator --seed 3   # watch one negotiation
python evaluate.py --seller ../agent/sauda_seller.py --buyers ../gym/gym_buyers.py  # 8 extra buyer styles
python evaluate.py --seller ../agent/sauda_seller.py --buyers ../gym/llm_buyers.py --n 24   # LLM buyers (needs Ollama)
```

On Windows, pass the seller as a relative path (as above): the starter kit's loader splits
`path:ClassName` on `:`, which breaks absolute `C:\...` paths.

Control room (dashboard):

```bash
pip install -r requirements-dev.txt      # only for the dashboard tests and model training
python dashboard/server.py               # http://localhost:8020
```

Tests:

```bash
python -m pytest -q tests                # unit + end-to-end checks against the official rules
python tests/e2e_dashboard.py            # browser test of every page and button (server running)
```

Reproduce everything reported here:

```bash
python tools/run_benchmarks.py --llm 24  # official, gym and LLM-buyer results -> results/
python model/make_dataset.py && python model/make_llm_data.py --split llm_train --per-class 60 \
  && python model/make_llm_data.py --split llm_test --per-class 40 && python model/train_classifier.py
python gym/tune.py --trials 200          # strategy parameter search (train/held-out seeds)
```

## Repository

```
agent/          the seller (sauda_seller.py), guards, trained tactic model, Rezolve adapter
starter_kit/    the official kit, unmodified
gym/            extra buyer bots (8 scripted styles + 4 local-LLM buyers) and the tuner
model/          classifier data generation and training
dashboard/      the control room (standard-library server + vanilla JS)
tools/          benchmark runner
tests/          pytest suite and the browser test
results/        benchmark outputs, transcripts, classifier report, tuning logs
docs/           abstract, architecture note, agent card, screenshots
```

## Rezolve Labs

The competition requires at least one Rezolve Labs capability. SAUDA uses **TraceWare commerce
claim verification** for its truth gate (every outgoing sentence, with the catalogue lookups and
quote behind it as evidence) and is designed to hold its catalogue and policy in **Managed
knowledge retrieval**. Rezolve Labs credentials had not been issued when this was submitted, so
the adapter (`agent/rezolve_adapter.py`) runs the local verifier, which re-implements the official
rules, and switches to TraceWare when `REZOLVE_API_KEY` and `REZOLVE_TRACEWARE_URL` are set. It
fails closed: a network problem can never let an unchecked claim through.

## Credits

Starter kit, store data and scoring: Techfest IIT Bombay and Rezolve AI. Local LLM for test
buyers and classifier data: Qwen2.5-3B via Ollama. Libraries: scikit-learn (training only),
Playwright (tests). All buyer and store data is synthetic.
