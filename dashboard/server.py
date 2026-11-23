"""SAUDA control room: a local web app for the seller agent (Python standard library + SQLite only).

    python dashboard/server.py            then open http://localhost:8020

Pages: /  landing page · /signup, /signin  accounts · /app  the control room (needs a session)

Account endpoints (JSON):
  POST /api/auth/signup {name, email, password, store}   POST /api/auth/login {email, password}
  POST /api/auth/demo   (signs in to the shared demo account)   POST /api/auth/logout   GET /api/auth/me
  POST /api/profile {name, store}    GET /api/activity    GET /api/run?id=    POST /api/run/delete {id}
Public: GET /api/public/stats, GET /api/public/demo (one fixed negotiation for the landing page)

Control-room endpoints (JSON, need a session):
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
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent"), str(ROOT / "gym")]

import buyers as starter  # noqa: E402
from evaluate import run as eval_run, score_one, summarise  # noqa: E402
from gym_buyers import BUYERS as GYM  # noqa: E402
from harness import floor_price, load_class, load_policy, load_store, run_negotiation  # noqa: E402
from sauda_seller import Seller, classify_tactic  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import DB, DEMO_EMAIL, AuthError  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "results"
STORE = load_store(ROOT / "starter_kit" / "store.csv")
POLICY = load_policy(ROOT / "starter_kit" / "policy.json")
LOCK = threading.Lock()
DBX = DB()
COOKIE = "sauda_session"
FAILS = {}                     # ip -> times of failed sign-ins in the last minute (a brute-force brake)
_DEMO_CACHE = {}
PUBLIC_API = {"/api/auth/signup", "/api/auth/login", "/api/auth/demo", "/api/auth/logout", "/api/auth/me",
              "/api/public/stats", "/api/public/demo"}

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


_OLLAMA = {"t": 0.0, "up": False}


def ollama_up():
    """Is a local LLM available? Cached for 15 seconds so pages stay fast."""
    import urllib.request
    if time.time() - _OLLAMA["t"] < 15:
        return _OLLAMA["up"]
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1) as r:
            up = bool(json.loads(r.read()).get("models"))
    except Exception:
        up = False
    _OLLAMA.update(t=time.time(), up=up)
    return up


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

    def _token(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[COOKIE].value if COOKIE in c else None

    def _user(self):
        if not hasattr(self, "_u"):
            self._u = DBX.session_user(self._token())
        return self._u

    def _cookie_reply(self, cookie, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _login(self, user_id, demo=False):
        token = DBX.new_session(user_id)
        return self._cookie_reply(f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={7 * 86400}",
                                  {"ok": True, "user": DBX.user(user_id), "demo": demo})

    def _redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _page(self, name):
        return self._send(200, (STATIC / name).read_bytes(), "text/html")

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
            if url.path in ("/", "/index.html"):
                return self._page("landing.html")
            if url.path in ("/signin", "/signup", "/login", "/register"):
                if self._user():
                    return self._redirect("/app")
                return self._page("auth.html")
            if url.path in ("/app", "/app/"):
                if not self._user():
                    return self._redirect("/signin?next=" + quote("/app"))
                return self._page("app.html")
            if url.path.startswith("/api/") and url.path not in PUBLIC_API and not self._user():
                return self._send(401, {"error": "Please sign in first."})
            if url.path == "/api/auth/me":
                u = self._user()
                return self._send(200, {"user": u, "demo": bool(u and u["email"] == DEMO_EMAIL)})
            if url.path == "/api/public/stats":
                o, g, l = latest("benchmark_official.json") or {}, latest("benchmark_gym.json") or {}, latest("benchmark_llm.json") or {}
                cr = RESULTS / "classifier_report.json"
                c = json.loads(cr.read_text(encoding="utf-8")) if cr.exists() else {}

                def pick(d, ks):
                    return {k: d.get(k) for k in ks}
                return self._send(200, {
                    "official": pick(o, ("score", "baseline", "policy", "truth", "deadlocks", "deal_rate", "feasible_deal_rate",
                                         "latency_ms", "n")),
                    "gym": pick(g, ("score", "baseline", "n", "policy", "truth")),
                    "llm": pick(l, ("score", "baseline", "n", "policy", "truth")),
                    "classifier": {"fresh": (c.get("fresh_test_v2") or {}).get("accuracy"),
                                   "fresh_n": (c.get("fresh_test_v2") or {}).get("n"),
                                   "kit": (c.get("kit_lines") or {}).get("accuracy")}})
            if url.path == "/api/public/demo":
                if "res" not in _DEMO_CACHE:
                    with LOCK:
                        _DEMO_CACHE["res"] = play("manipulator", 7)
                r = _DEMO_CACHE["res"]
                return self._send(200, {k: r[k] for k in ("basket", "listed", "transcript", "ended", "deal", "score")})
            if url.path == "/api/activity":
                u = self._user()
                return self._send(200, {"stats": DBX.stats(u["id"]), "runs": DBX.runs(u["id"]),
                                        "classifications": DBX.classifications(u["id"])})
            if url.path == "/api/run":
                r = DBX.run(self._user()["id"], int(q.get("id", 0) or 0))
                return self._send(200, r) if r else self._send(404, {"error": "not found"})
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
                    {"name": "TraceWare commerce claim verification", "role": "Verifies every price and stock claim against recorded tool actions", "status": "connected" if key else "optional · off"},
                    {"name": "Managed knowledge retrieval", "role": "Could hold the catalogue and policy the seller quotes from", "status": "optional · off"},
                    {"name": "Agent builder and managed runtime", "role": "Could host the seller as a managed text agent", "status": "optional · off"}]})
            path = url.path.lstrip("/")
            if path.endswith(".html") and path not in ("landing.html", "404.html"):
                return self._redirect("/app" if path == "app.html" else "/signin")
            f = (STATIC / path).resolve()
            if STATIC.resolve() in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            if url.path.startswith("/api/"):
                return self._send(404, {"error": "unknown endpoint"})
            data = (STATIC / "404.html").read_bytes()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        url = urlparse(self.path)
        body = self._json()
        ip = self.client_address[0]
        try:
            if url.path.startswith("/api/") and url.path not in PUBLIC_API and not self._user():
                return self._send(401, {"error": "Please sign in first."})
            if url.path in ("/api/auth/login", "/api/auth/signup"):
                now = time.time()
                FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 60]
                if len(FAILS[ip]) >= 8:
                    return self._send(429, {"error": "Too many attempts. Please wait a minute and try again."})
                try:
                    if url.path == "/api/auth/signup":
                        uid = DBX.create_user(body.get("name"), body.get("email"), str(body.get("password") or ""),
                                              body.get("store"))
                    else:
                        uid = DBX.verify(body.get("email"), str(body.get("password") or ""))
                except AuthError:
                    FAILS[ip].append(now)
                    raise
                return self._login(uid)
            if url.path == "/api/auth/demo":
                return self._login(DBX.demo_id(), demo=True)
            if url.path == "/api/auth/logout":
                DBX.end_session(self._token())
                return self._cookie_reply(f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0", {"ok": True})
            if url.path == "/api/profile":
                if self._user()["email"] == DEMO_EMAIL:
                    raise ValueError("The shared demo account can't be edited. Create your own account to save changes.")
                return self._send(200, {"user": DBX.update_profile(self._user()["id"], body.get("name"), body.get("store"))})
            if url.path == "/api/run/delete":
                n = DBX.delete_run(self._user()["id"], int(body.get("id", 0) or 0))
                return self._send(200 if n else 404, {"ok": bool(n)})
            if url.path == "/api/negotiate":
                seed = int(body.get("seed", random.randrange(1, 10 ** 6)))
                buyer = str(body.get("buyer", "manipulator"))
                with LOCK:
                    res = play(buyer, seed)
                res["run_id"] = DBX.save_run(self._user()["id"], res, buyer, seed)
                return self._send(200, res)
            if url.path == "/api/classify":
                text = str(body.get("text", ""))[:1000]
                if not text.strip():
                    raise ValueError("Type a buyer message first.")
                label, conf, src = classify_tactic(text)
                DBX.save_classification(self._user()["id"], text, label, round(conf, 4))
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
