"""Produce SAUDA's benchmark results and transcripts (the files the dashboard and README report).

    python tools/run_benchmarks.py                 official + gym
    python tools/run_benchmarks.py --llm 24        also 24 negotiations against local LLM buyers

Writes results/benchmark_official.json, benchmark_gym.json, benchmark_llm.json and
results/official_transcripts.jsonl (every official negotiation, with SAUDA's decision trace).
The official numbers use the starter kit's own harness and scoring (evaluate.score_one).
"""
import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym")]

import buyers as starter  # noqa: E402
from evaluate import score_one, summarise  # noqa: E402
from gym_buyers import BUYERS as GYM  # noqa: E402
from harness import check_items, load_policy, load_store, run_negotiation  # noqa: E402
from sauda_seller import Seller, classify_tactic  # noqa: E402

STORE, POLICY = load_store(ROOT / "starter_kit" / "store.csv"), load_policy(ROOT / "starter_kit" / "policy.json")
RES = ROOT / "results"
LABELS = {"cooperative": "Cooperative", "hard_bargainer": "Hard bargainer", "manipulator": "Manipulator",
          "impatient": "Impatient", "splitter": "Meets in the middle", "fixed_budget": "Fixed budget",
          "quick_accept": "Quick acceptor", "noisy": "Noisy", "messy": "Messy messages", "late_surge": "Late surge",
          "smooth_manipulator": "Smooth manipulator", "llm_friendly": "LLM friendly", "llm_haggler": "LLM haggler",
          "llm_trickster": "LLM trickster", "llm_impatient": "LLM impatient"}


def baseline_cls():
    import importlib.util
    spec = importlib.util.spec_from_file_location("example_seller", ROOT / "starter_kit" / "example_seller.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Seller


def play_all(seller_cls, scenarios, buyer_classes, keep=False):
    rows, times, extra = [], [], {"offers_checked": 0, "texts_checked": 0, "tactics_detected": 0, "bad_bids_refused": 0}
    seller = seller_cls(STORE, POLICY)
    respond = seller.respond

    def timed(msg):
        t0 = time.perf_counter()
        try:
            return respond(msg)
        finally:
            times.append(time.perf_counter() - t0)
    seller.respond = timed
    for sc in scenarios:
        buyer = buyer_classes[sc["buyer"]](sc, STORE, random.Random(sc["seed"]))
        res = run_negotiation(seller, buyer, STORE, POLICY, sc["id"])
        row = score_one(res, sc, STORE, POLICY)
        if keep:
            for m in res["transcript"]:
                if m["from"] == "buyer":
                    label, conf, _ = classify_tactic(m.get("text"))
                    m["tactic"], m["tactic_confidence"] = label, round(conf, 3)
                    extra["tactics_detected"] += label != "benign"
                    items = m.get("items")
                    if m.get("type") == "counter" and isinstance(items, list):
                        kinds = {v["kind"] for v in check_items(items, STORE, POLICY)}
                        tricks = {"policy:out_of_stock", "policy:unknown_sku", "policy:malformed"}
                        one_rupee = any(isinstance(i, dict) and isinstance(i.get("unit_price"), (int, float))
                                        and i["unit_price"] <= 1 for i in items)
                        if kinds & tricks or one_rupee:
                            extra["bad_bids_refused"] += 1
                else:
                    extra["texts_checked"] += 1
                    extra["offers_checked"] += m.get("type") == "offer"
            row = dict(row, transcript=res["transcript"], trace=list(getattr(seller, "trace", [])))
        rows.append(row)
    return rows, times, extra


def report(name, scenarios, buyer_classes, n, seed, keep=False):
    rows, times, extra = play_all(Seller, scenarios, buyer_classes, keep)
    base = play_all(baseline_cls(), scenarios, buyer_classes)[0]
    s = summarise(rows)
    b = summarise(base) if base else None
    per = []
    for k in buyer_classes:
        mine = [r for r in rows if r["buyer"] == k]
        if not mine:
            continue
        t = summarise(mine)
        u = summarise([r for r in base if r["buyer"] == k]) if base else None
        per.append({"buyer": k, "label": LABELS.get(k, k), "n": t["n"], "score": round(t["score"], 1),
                    "baseline": round(u["score"], 1) if u else None, "deal_rate": round(t["deal_rate"], 3),
                    "margin": round(t["margin_kept"], 3), "deadlocks": t["deadlocks"],
                    "policy": t["policy"], "truth": t["truth"]})
    ms = sorted(times)
    out = {"suite": name, "n": n, "seed": seed, "score": round(s["score"], 1), "baseline": round(b["score"], 1) if b else None,
           "deal_rate": round(s["deal_rate"], 3), "feasible_deal_rate": round(s["feasible_deal_rate"], 3),
           "margin_kept": round(s["margin_kept"], 3), "policy": s["policy"], "truth": s["truth"], "kinds": s["kinds"],
           "deadlocks": s["deadlocks"], "seller_walked": s["seller_walked"], "buyer_walked": s["buyer_walked"],
           "latency_ms": round(1000 * statistics.mean(ms), 3), "latency_p95_ms": round(1000 * ms[int(0.95 * (len(ms) - 1))], 3),
           "per_buyer": per, **extra}
    (RES / f"benchmark_{name}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{name:9} score {out['score']:6.1f}   baseline {out['baseline']}   policy {out['policy']}  truth {out['truth']}  "
          f"deadlocks {out['deadlocks']}  deal rate {out['deal_rate']}  latency {out['latency_ms']} ms")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", type=int, default=0, help="negotiations against local LLM buyers (needs Ollama)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only-llm", action="store_true", help="skip the official and gym suites")
    args = ap.parse_args()
    RES.mkdir(exist_ok=True)
    if args.only_llm:
        from llm_buyers import BUYERS as LLM
        report("llm", starter.make_scenarios(STORE, POLICY, args.llm, 4242, LLM), LLM, args.llm, 4242)
        return
    sc = starter.make_scenarios(STORE, POLICY, 210, args.seed, starter.BUYERS)
    rows = report("official", sc, starter.BUYERS, 210, args.seed, keep=True)
    with open(RES / "official_transcripts.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    gym = {k: v for k, v in GYM.items() if k not in starter.BUYERS}
    report("gym", starter.make_scenarios(STORE, POLICY, 240, 909, gym), gym, 240, 909)
    if args.llm:
        from llm_buyers import BUYERS as LLM
        report("llm", starter.make_scenarios(STORE, POLICY, args.llm, 4242, LLM), LLM, args.llm, 4242)


if __name__ == "__main__":
    main()
