"""Experiment: two-mode strategy (near-limit openers vs long haggles). Prints official and gym scores."""
import importlib.util
import itertools
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym")]
import buyers as starter  # noqa: E402
from evaluate import run, summarise  # noqa: E402
from gym_buyers import BUYERS as GYM  # noqa: E402
from harness import load_policy, load_store  # noqa: E402
import sauda_seller  # noqa: E402

S, P = load_store(ROOT / "starter_kit" / "store.csv"), load_policy(ROOT / "starter_kit" / "policy.json")


class TwoMode(sauda_seller.Seller):
    X = {}

    def __init__(self, store, policy):
        super().__init__(store, policy, dict(json.load(open(ROOT / "results" / "tuning_v3.json"))[1]["params"]))
        self.x = dict(self.X)

    def start(self, nid):
        super().start(nid)
        self.mode = None

    def _plan(self, r):
        F = self._floor_ratio()
        model = self._fit()
        if self.mode is None and self.bids:
            self.mode = "A" if self.bids[0][1] >= F - self.x["a_slack"] else "B"
        if self.mode == "B":
            frac = (r - 1) / max(1, self.R - 1)
            hold = 1.0 - (1.0 - F) * self.x["b_drop"] * frac ** self.x["b_shape"]
            plan = hold
            if model and r >= self.R - self.x["b_model_rounds"]:
                w = model["pred"](r) - self.p["pred_margin"]
                plan = max(plan, w) if r < self.R else plan
            if self.offers:
                plan = min(plan, self.offers[-1])
            if self.bids:
                plan = max(plan, self.bids[-1][1])
            return max(F, min(1.0, plan)), model, False
        if self.mode == "A":
            b = self.bids[-1][1]
            plan = b + self.x["a_premium"] * (1 - b) if not self.offers else self.offers[-1] - self.x["a_give"] * (self.offers[-1] - b)
            if self.offers:
                plan = min(plan, self.offers[-1])
            return max(F, min(1.0, max(plan, b))), model, False
        return super()._plan(r)

    def respond(self, message):
        out = super().respond(message)
        return out


def score(cls, buyers, seeds, n):
    rows = []
    for sd in seeds:
        rows += run(cls, starter.make_scenarios(S, P, n, sd, buyers), buyers, S, P)[0]
    per = {b: round(summarise([r for r in rows if r["buyer"] == b])["score"], 1) for b in buyers}
    return round(summarise(rows)["score"], 1), per


if __name__ == "__main__":
    gym = {k: v for k, v in GYM.items() if k not in starter.BUYERS}
    grid = {"a_slack": [0.0, 0.03], "a_premium": [0.0, 0.3, 0.5], "a_give": [0.5, 0.8], "b_drop": [0.2, 0.5, 0.8],
            "b_shape": [1.0, 3.0], "b_model_rounds": [0, 2]}
    keys = list(grid)
    best = []
    rng = random.Random(1)
    combos = list(itertools.product(*grid.values()))
    rng.shuffle(combos)
    for combo in combos[:60]:
        TwoMode.X = dict(zip(keys, combo))
        o, per = score(TwoMode, starter.BUYERS, range(3), 210)
        best.append((o, TwoMode.X, per))
        print(o, TwoMode.X, per, flush=True)
    best.sort(key=lambda x: -x[0])
    print("TOP")
    for o, x, per in best[:5]:
        TwoMode.X = x
        g, gper = score(TwoMode, gym, [303, 404], 200)
        o5, _ = score(TwoMode, starter.BUYERS, range(5), 210)
        print(o5, g, x, gper)
