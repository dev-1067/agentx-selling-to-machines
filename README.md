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
| Extra buyer gym (8 scripted styles, held-out seed) | 240 | **49.9** | 48.5 | 0 | 0 | 2 |
| Local LLM buyers (qwen2.5:3b, 4 personas, 4 seeds) | 96 | **57.3** | 56.9 | 0 | 0 | 5 |

Honest reading: SAUDA beats the starter baseline on the scripted buyers and is level with it (slightly
ahead) against LLM buyers that write their own messages, while making **zero policy violations and zero
untrue claims in every suite**. Against LLM buyers the gap closed from about 10 points behind to level
once SAUDA learned to handle buyers that repeat one number (see the strategy below); the LLM sample is
small and noisy, so treat it as "on par", not as a win.

### Robustness against the manipulator (scorecard: "results on the manipulator bot")

| Manipulative buyer | Negotiations | SAUDA | Baseline | Deal rate | Policy | Untrue claims | Deadlocks |
|---|---|---|---|---|---|---|---|
| Starter-kit manipulator (official harness) | 70 | **38.8** | 35.5 | 70% | 0 | 0 | 0 |
| Smooth manipulator (our gym: polite fake quotes, fake authority, injections) | 30 | **44.8** | 43.4 | 70% | 0 | 0 | 0 |
| LLM trickster (local LLM inventing quotes, budgets, fake agreements, fake manager) | 24 | 54.0 | 56.3 | 71% | 0 | 0 | 3 |

On the official run the classifier flagged 633 manipulative buyer messages and PolicyGuard refused 196 bids that
carried slipped-in out-of-stock lines, over-stock quantities or ₹1 add-ons; none of them moved a price, and none
of SAUDA's 1,463 messages made an untrue claim.

The **tactic classifier** scores **97.5%** on 200 fresh hand-written messages that were
frozen before the final training data was written (never used for training or model selection),
97.7% on the hand-written dev set used to choose its settings, and 100% on the organisers' own
manipulator lines. It only chooses wording; it never changes a price.

**Robustness against the manipulator bot** (scored separately): 38.8 vs the baseline's 35.5,
with 0 policy violations, 0 untrue claims and 0 deadlocks (the baseline deadlocks 15 times out of 70). On the
official run SAUDA flagged 633 manipulative messages and refused 196 rigged bids.

Reproduce: `python tools/run_benchmarks.py` (official + gym) and
`python tools/llm_eval.py --n 24 --seeds 4242 777` (LLM buyers, needs Ollama).

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
| Truth gate | `agent/sauda_guard.py`, `agent/rezolve_adapter.py` | Checks every sentence against the official truthfulness rules and a ledger of numbers in play; anything that would fail is replaced by a plain sentence. Can also use an external verifier such as Rezolve TraceWare when configured. |
| Tactic classifier | `agent/tactic_model.py` (trained by `model/`) | Names the buyer's tactic (fake competitor quote, prompt injection, false agreement, fake authority, stock claim, threat, budget claim) to choose calm wording and log it. It never changes a price. |

### The strategy in one paragraph

A buyer's **opening bid** tells SAUDA which situation it is in. A *near-limit opener* (opening
bid already near the policy floor) is close to what it will pay, so SAUDA answers a little above
the bid and closes quickly. A *long haggle* (opening bid far below the floor) is a buyer that
concedes slowly; SAUDA holds its ask while the buyer keeps moving, fits the buyer's concession
curve to its real bids, and in the last round asks for the limit that curve predicts (never more
than its previous ask). Its ask also glides toward the floor by round 8, so a patient buyer is never
left facing a list-price wall. **Repeated bids** get special handling, because LLM buyers often name
one number and repeat it: on the first repeat SAUDA steps most of the way toward it (a buyer bluffing
about its budget usually accepts that), and if the number is repeated again SAUDA takes the bid, or,
when the bid's total covers the floor but one line is below its own floor, offers exactly that total
spread so every line stays compliant. A compliant bid is accepted as soon as it is at least what SAUDA
would ask next. In the last round SAUDA always either closes or walks away, so a negotiation never
deadlocks unless the buyer ignores a valid final offer.

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

Web app (landing page, accounts and the control room):

```bash
pip install -r requirements-dev.txt      # only for the browser tests and model training
python dashboard/server.py               # http://localhost:8020
```

Open http://localhost:8020, then **Create account** (or **Try the live demo** for the shared demo
account). Accounts live in a local SQLite file (`data/sauda.db`); passwords are stored only as salted
PBKDF2-SHA256 hashes and sessions are random tokens in an HttpOnly cookie. Every arena run and
classifier check is saved to the signed-in store's **My activity** page.

Tests:

```bash
python -m pytest -q tests                # unit + end-to-end checks against the official rules
python tests/e2e_dashboard.py            # browser test: landing, sign-up/sign-in, every control-room button
python tools/llm_eval.py --n 24 --seeds 4242 777   # SAUDA vs baseline against local LLM buyers (Ollama)
```

Reproduce everything reported here:

```bash
python tools/run_benchmarks.py --llm 24  # official, gym and LLM-buyer results -> results/
python model/make_dataset.py && python model/make_llm_data.py --split llm_train --per-class 60 \
  && python model/make_v2_data.py && python model/make_fresh_test_v2.py && python model/train_classifier.py
python gym/tune.py --trials 200          # strategy parameter search (train/held-out seeds)
```

## Repository

```
agent/          the seller (sauda_seller.py), guards, trained tactic model, optional external-verifier adapter
starter_kit/    the official kit, unmodified
gym/            extra buyer bots (8 scripted styles, 4 fast LLM-like stand-ins, 4 local-LLM buyers) and the tuner
model/          classifier data generation and training
dashboard/      the web app: landing page, accounts (SQLite) and control room (standard-library server + vanilla JS)
tools/          benchmark runner and the parallel LLM-buyer evaluation
tests/          pytest suite and the browser test
results/        benchmark outputs, transcripts, classifier report, tuning logs
docs/           abstract, architecture note, agent card, screenshots
```

## Optional integration: Rezolve Labs

The problem statement updated on 23 Nov 2026 no longer requires Rezolve Labs, and SAUDA does not need
any external service: everything above runs locally. The truth gate keeps an optional adapter
(`agent/rezolve_adapter.py`) that sends each outgoing claim, with the catalogue lookups behind it as
evidence, to Rezolve TraceWare when `REZOLVE_API_KEY` and `REZOLVE_TRACEWARE_URL` are set. It is off by
default and fails closed: a network problem can never let an unchecked claim through.

## Credits

Starter kit, store data and scoring: Techfest IIT Bombay and Rezolve AI. Local LLM for test
buyers and classifier data: Qwen2.5-3B via Ollama. Libraries: scikit-learn (training only),
Playwright (tests). All buyer and store data is synthetic.
