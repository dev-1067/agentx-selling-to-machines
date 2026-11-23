"""Tune SAUDA's strategy parameters across the starter bots and the buyer gym.

    python gym/tune.py --trials 120

Scores every candidate on a training set of seeds, then reports the best few on held-out seeds,
so the chosen parameters are not fitted to one sample of negotiations.
"""
import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym")]

import buyers as starter  # noqa: E402
from evaluate import score_one  # noqa: E402
from gym_buyers import BUYERS as GYM  # noqa: E402
from harness import load_policy, load_store, run_negotiation  # noqa: E402
from sauda_seller import DEFAULTS, Seller  # noqa: E402

STORE, POLICY = load_store(ROOT / "starter_kit" / "store.csv"), load_policy(ROOT / "starter_kit" / "policy.json")


def evaluate(params, seeds, n=120, buyers=GYM):
    by = {}
    for seed in seeds:
        scenarios = starter.make_scenarios(STORE, POLICY, n, seed, buyers)
        seller = Seller(STORE, POLICY, params)
        for sc in scenarios:
            buyer = buyers[sc["buyer"]](sc, STORE, random.Random(sc["seed"]))
            res = run_negotiation(seller, buyer, STORE, POLICY, sc["id"])
            by.setdefault(sc["buyer"], []).append(score_one(res, sc, STORE, POLICY)["score"])
    per = {k: 100 * statistics.mean(v) for k, v in by.items()}
    starter_avg = statistics.mean(per[k] for k in starter.BUYERS)
    gym_avg = statistics.mean(per[k] for k in per if k not in starter.BUYERS)
    return 0.5 * starter_avg + 0.5 * gym_avg, per


SPACE = {
    "give_min": [0.15, 0.25, 0.35],
    "give_max": [0.35, 0.5, 0.7],
    "gamma": [0.7, 1.0],
    "accept_tol": [0.01, 0.02],
    "model_from": [0.5, 0.7],
    "pred_margin": [0.005, 0.01, 0.03],
    "final_offer_conf": [-0.05, -0.02],
    "min_points": [3, 4],
    "final_greed": [0.0, 0.01],
    "greed_points": [6, 8],
    "near_slack": [-1.0, 0.0, 0.03, 0.06],
    "near_premium": [0.2, 0.35, 0.5],
    "near_give": [0.3, 0.5, 0.7],
    "hold_drop": [0.3, 0.5, 0.8, 1.0],
    "hold_shape": [1.5, 3.0, 5.0],
    "stall_eps": [0.0, 0.004, 0.01, 0.03, 9.0],
    "d_safety": [0, 1, 2],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--out", default=str(ROOT / "results" / "tuning.json"))
    args = ap.parse_args()
    rng = random.Random(7)
    train, held = [101, 202, 606], [303, 404, 505]
    tried = [dict(DEFAULTS)]
    for _ in range(args.trials):
        tried.append({k: rng.choice(v) for k, v in SPACE.items()})
    scored = []
    for i, p in enumerate(tried):
        s, _ = evaluate(p, train)
        scored.append((s, p))
        if i % 10 == 0:
            best = max(scored, key=lambda x: x[0])
            print(f"{i:4d}  best train {best[0]:.2f}  {json.dumps(best[1])}", flush=True)
    scored.sort(key=lambda x: -x[0])
    report = []
    for s, p in scored[:6]:
        h, per = evaluate(p, held)
        report.append({"train": round(s, 2), "held_out": round(h, 2), "params": p,
                       "per_buyer": {k: round(v, 1) for k, v in per.items()}})
        print(f"train {s:.2f}  held-out {h:.2f}  {json.dumps(p)}")
    Path(args.out).write_text(json.dumps(report, indent=2))
    print("saved", args.out)


if __name__ == "__main__":
    main()
