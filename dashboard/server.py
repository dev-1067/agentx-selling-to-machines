"""SAUDA control room: a local dashboard for the seller agent (Python standard library only).

    python dashboard/server.py            then open http://localhost:8020

Endpoints (JSON):
  GET  /api/summary            latest benchmark results (results/benchmark_*.json)
  GET  /api/transcripts        negotiations from the latest official run (filter: buyer, outcome)
  GET  /api/transcript?id=     one negotiation, with SAUDA's per-round decision trace
  POST /api/negotiate          play one negotiation live: {"buyer": "manipulator", "seed": 7}
  POST /api/classify           classify a buyer message: {"text": "..."}
  GET  /api/classifier         training curve and test results of the tactic classifier
  POST /api/benchmark          run a quick benchmark: {"suite": "official" | "gym", "n": 60}
  GET  /api/catalogue, /api/policy, /api/agent-card, /api/rezolve
"""
import argparse
import json
import mimetypes
import os
import random
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym")]

import buyers as starter  # noqa: E402
from evaluate import run as eval_run, score_one, summarise  # noqa: E402
from gym_buyers import BUYERS as GYM  # noqa: E402
from harness import floor_price, load_class, load_policy, load_store, run_negotiation  # noqa: E402
from sauda_seller import Seller, classify_tactic  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "results"
STORE = load_store(ROOT / "starter_kit" / "store.csv")
POLICY = load_policy(ROOT / "starter_kit" / "policy.json")
LOCK = threading.Lock()

try:
    from llm_buyers import BUYERS as LLM
except Exception:
    LLM = {}

PERSONAS = {
    "cooperative": ("Cooperative", "starter"), "hard_bargainer": ("Hard bargainer", "starter"),
    "manipulator": ("Manipulator", "starter"), "impatient": ("Impatient", "gym"), "splitter": ("Meets in the middle", "gym"),
    "fixed_budget": ("Fixed budget", "gym"), "quick_accept": ("Quick acceptor", "gym"), "noisy": ("Noisy", "gym"),
    "messy": ("Messy messages", "gym"), "late_surge": ("Late surge", "gym"), "smooth_manipulator": ("Smooth manipulator", "gym"),
}
PERSONAS.update({k: (k.replace("llm_", "LLM ").replace("_", " ").title(), "llm") for k in LLM})


def all_buyers():
    out = dict(GYM)
    out.update(LLM)
    return out


def ollama_up():
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1) as r:
            return bool(json.loads(r.read()).get("models"))
    except Exception:
        return False


def latest(pattern):
    files = sorted(RESULTS.glob(pattern), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def load_transcripts():
    path = RESULTS / "official_transcripts.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def scenario_basket(sc):
    rows = []
    for it in sc["items"]:
        r = STORE[it["sku"]]
        rows.append({"sku": it["sku"], "name": r["name"], "qty": it["qty"], "stock": r["stock"], "list": r["price"],
                     "floor": floor_price(r, POLICY), "category": r["category"]})
    return rows


def play(buyer_key, seed):
    buyers = all_buyers()
    if buyer_key not in buyers:
        raise ValueError(f"unknown buyer {buyer_key}")
    if buyer_key in LLM and not ollama_up():
        raise ValueError("The local LLM (Ollama) is not running, so LLM buyers are unavailable.")
    sc = starter.make_scenarios(STORE, POLICY, 1, seed, {buyer_key: buyers[buyer_key]})[0]
    seller = Seller(STORE, POLICY)
    buyer = buyers[buyer_key](sc, STORE, random.Random(sc["seed"]))
    t0 = time.perf_counter()
    result = run_negotiation(seller, buyer, STORE, POLICY, sc["id"])
    elapsed = time.perf_counter() - t0
    score = score_one(result, sc, STORE, POLICY)
    for m in result["transcript"]:
        if m["from"] == "buyer":
            label, conf, src = classify_tactic(m.get("text"))
            m["tactic"], m["tactic_confidence"] = label, round(conf, 3)
    listed = sum(STORE[i["sku"]]["price"] * min(i["qty"], STORE[i["sku"]]["stock"]) for i in sc["items"])
    return {"scenario": {k: sc[k] for k in ("id", "buyer", "deadline", "stubborn")},
            "private_limit": round(sc["reservation"], 4), "basket": scenario_basket(sc), "listed": listed,
            "transcript": result["transcript"], "trace": seller.trace, "ended": result["ended"],
            "deal": result["deal"], "value": round(score["value"], 4), "score": round(100 * score["score"], 1),
            "violations": result["violations"], "elapsed_ms": round(1000 * elapsed, 1)}


def benchmark(suite, n, seed):
    buyers = dict(starter.BUYERS) if suite == "official" else {k: v for k, v in GYM.items() if k not in starter.BUYERS}
    scenarios = starter.make_scenarios(STORE, POLICY, n, seed, buyers)
    rows, _, times = eval_run(Seller, scenarios, buyers, STORE, POLICY)
    base_cls = load_class(str(ROOT / "starter_kit" / "example_seller.py"), "Seller") if os.name != "nt" else None
    if base_cls is None:   # the kit's loader splits Windows paths on ':'; load the baseline directly
        import importlib.util
        spec = importlib.util.spec_from_file_location("example_seller", ROOT / "starter_kit" / "example_seller.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        base_cls = mod.Seller
    base_rows = eval_run(base_cls, scenarios, buyers, STORE, POLICY)[0]
    s, b = summarise(rows), summarise(base_rows)
    per = []
    for name in buyers:
        mine = [r for r in rows if r["buyer"] == name]
        theirs = [r for r in base_rows if r["buyer"] == name]
        if mine:
            t, u = summarise(mine), summarise(theirs)
            per.append({"buyer": name, "label": PERSONAS.get(name, (name,))[0], "n": t["n"], "score": round(t["score"], 1),
                        "baseline": round(u["score"], 1), "deal_rate": round(t["deal_rate"], 3),
                        "margin": round(t["margin_kept"], 3), "deadlocks": t["deadlocks"]})
    ms = sorted(times)
    return {"suite": suite, "n": n, "seed": seed, "score": round(s["score"], 1), "baseline": round(b["score"], 1),
            "deal_rate": round(s["deal_rate"], 3), "feasible_deal_rate": round(s["feasible_deal_rate"], 3),
            "margin_kept": round(s["margin_kept"], 3), "policy": s["policy"], "truth": s["truth"],
            "deadlocks": s["deadlocks"], "latency_ms": round(1000 * statistics.mean(ms), 3),
            "latency_p95_ms": round(1000 * ms[int(0.95 * (len(ms) - 1))], 3), "per_buyer": per}


class Handler(BaseHTTPRequestHandler):
    server_version = "SAUDA/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("application/json", "text/")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/summary":
                return self._send(200, {"official": latest("benchmark_official.json"), "gym": latest("benchmark_gym.json"),
                                        "llm": latest("benchmark_llm.json"), "personas": PERSONAS,
                                        "llm_available": ollama_up()})
            if url.path == "/api/transcripts":
                rows = load_transcripts()
                if q.get("buyer"):
                    rows = [r for r in rows if r["buyer"] == q["buyer"]]
                counts = {"all": len(rows)}
                for key in ("deal", "seller_walked", "buyer_walked", "deadlock"):
                    counts[key] = sum(r["ended"] == key for r in rows)
                if q.get("outcome") and q["outcome"] != "all":
                    rows = [r for r in rows if r["ended"] == q["outcome"]]
                slim = [{k: r[k] for k in ("id", "buyer", "ended", "rounds", "value", "score", "deal", "feasible",
                                         "policy_violations", "truth_violations")} for r in rows]
                return self._send(200, {"rows": slim, "counts": counts})
            if url.path == "/api/transcript":
                for r in load_transcripts():
                    if r["id"] == q.get("id"):
                        return self._send(200, r)
                return self._send(404, {"error": "not found"})
            if url.path == "/api/classifier":
                p = RESULTS / "classifier_report.json"
                return self._send(200, json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})
            if url.path == "/api/catalogue":
                rows = [{**{k: r[k] for k in ("sku", "name", "category", "brand", "price", "cost", "stock")},
                         "floor": floor_price(r, POLICY)} for r in STORE.values()]
                return self._send(200, rows)
            if url.path == "/api/policy":
                return self._send(200, POLICY)
            if url.path == "/api/agent-card":
                return self._send(200, json.loads((ROOT / "docs" / "agent_card.json").read_text(encoding="utf-8")))
            if url.path == "/api/rezolve":
                key = bool(os.environ.get("REZOLVE_API_KEY"))
                return self._send(200, {"configured": key, "capabilities": [
                    {"name": "TraceWare commerce claim verification", "role": "Verifies every price and stock claim against recorded tool actions", "status": "connected" if key else "awaiting credentials"},
                    {"name": "Managed knowledge retrieval", "role": "Holds the catalogue and policy the seller quotes from", "status": "connected" if key else "awaiting credentials"},
                    {"name": "Agent builder and managed runtime", "role": "Hosts the seller as a managed text agent", "status": "planned"}]})
            path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            f = (STATIC / path).resolve()
            if STATIC.resolve() in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html")
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        url = urlparse(self.path)
        body = self._json()
        try:
            if url.path == "/api/negotiate":
                seed = int(body.get("seed", random.randrange(1, 10 ** 6)))
                with LOCK:
                    return self._send(200, play(str(body.get("buyer", "manipulator")), seed))
            if url.path == "/api/classify":
                text = str(body.get("text", ""))[:1000]
                label, conf, src = classify_tactic(text)
                return self._send(200, {"label": label, "confidence": round(conf, 4), "source": src})
            if url.path == "/api/benchmark":
                suite = body.get("suite", "official")
                n = max(12, min(int(body.get("n", 60)), 300))
                with LOCK:
                    res = benchmark("official" if suite == "official" else "gym", n, int(body.get("seed", 0)))
                return self._send(200, res)
            return self._send(404, {"error": "unknown endpoint"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"SAUDA control room on http://localhost:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
