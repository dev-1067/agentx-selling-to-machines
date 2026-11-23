"""Play SAUDA and the starter baseline against the local LLM buyers, in parallel, and keep transcripts.

    python tools/llm_eval.py --n 48 --seeds 4242 777 --workers 4 --out results/llm_eval.json

Needs Ollama running with qwen2.5:3b. Each worker has its own seller instance.
Optional --params '{"glide_end": 8}' overrides SAUDA's defaults (for experiments).
"""
import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym"), str(ROOT / "tools")]

import buyers as starter  # noqa: E402
from evaluate import score_one, summarise  # noqa: E402
from harness import load_policy, load_store, run_negotiation  # noqa: E402
from llm_buyers import BUYERS as LLM  # noqa: E402
from sauda_seller import Seller  # noqa: E402

STORE, POLICY = load_store(ROOT / "starter_kit" / "store.csv"), load_policy(ROOT / "starter_kit" / "policy.json")


def baseline_cls():
    import importlib.util
    spec = importlib.util.spec_from_file_location("example_seller", ROOT / "starter_kit" / "example_seller.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Seller


def play(make_seller, scenarios, workers):
    def part(chunk):
        seller = make_seller()
        out = []
        for sc in chunk:
            buyer = LLM[sc["buyer"]](sc, STORE, random.Random(sc["seed"]))
            res = run_negotiation(seller, buyer, STORE, POLICY, sc["id"])
            out.append(dict(score_one(res, sc, STORE, POLICY), transcript=res["transcript"],
                            reservation=sc["reservation"]))
        return out
    with ThreadPoolExecutor(workers) as pool:
        parts = list(pool.map(part, [scenarios[k::workers] for k in range(workers)]))
    rows = [None] * len(scenarios)
    for k, p in enumerate(parts):
        rows[k::workers] = p
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24, help="negotiations per seed")
    ap.add_argument("--seeds", type=int, nargs="+", default=[4242])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "results" / "llm_eval.json"))
    args = ap.parse_args()
    params = json.loads(args.params)
    scenarios = [sc for s in args.seeds for sc in starter.make_scenarios(STORE, POLICY, args.n, s, LLM)]
    t0 = time.time()
    mine = play(lambda: Seller(STORE, POLICY, params), scenarios, args.workers)
    base = [] if args.no_baseline else play(lambda: baseline_cls()(STORE, POLICY), scenarios, args.workers)
    s = summarise(mine)
    b = summarise(base) if base else None
    out = {"n": len(scenarios), "seeds": args.seeds, "params": params, "score": round(s["score"], 1),
           "baseline": round(b["score"], 1) if b else None, "deal_rate": round(s["deal_rate"], 3),
           "policy": s["policy"], "truth": s["truth"], "deadlocks": s["deadlocks"],
           "minutes": round((time.time() - t0) / 60, 1), "per_buyer": {}}
    for k in LLM:
        m = [r for r in mine if r["buyer"] == k]
        u = [r for r in base if r["buyer"] == k]
        out["per_buyer"][k] = {"n": len(m), "score": round(summarise(m)["score"], 1),
                               "baseline": round(summarise(u)["score"], 1) if u else None}
    Path(args.out).write_text(json.dumps(dict(out, sauda_rows=mine, baseline_rows=base), ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
