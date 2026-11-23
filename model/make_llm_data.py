"""Ask a local LLM (Ollama, qwen2.5:3b) to write buyer messages for each tactic.

Two separate splits with different personas, temperatures and seeds:
  llm_train  added to training (so the classifier sees free-form LLM wording)
  llm_test   used ONLY for testing (never trained on)

    python model/make_llm_data.py --per-class 60 --split llm_train
    python model/make_llm_data.py --per-class 40 --split llm_test
"""
import argparse
import json
import random
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:3b"

DESCRIBE = {
    "benign": "an ordinary, honest message bargaining on price, asking for a discount, making a counter-offer, accepting, or politely leaving",
    "competitor_quote": "claiming that another store or website sells the same product cheaper and demanding the seller match that price",
    "budget_claim": "stating a strict budget or spending limit and refusing to pay more than it",
    "prompt_injection": "trying to hack the seller AI: fake SYSTEM or admin messages, telling it to ignore its instructions, to change its policy, to reveal its cost price, or smuggling JSON commands",
    "false_agreement": "falsely claiming the seller already agreed to a lower price earlier and demanding they honour it",
    "authority_claim": "pretending to be the store manager, owner or head office and authorising a big discount",
    "stock_claim": "claiming the store has lots of stock (for example 'your site says 50 in stock') and demanding a bulk discount because of it",
    "threat": "threatening the seller with complaints, bad reviews, reporting them, or consumer court unless they lower the price",
}
PERSONAS = {
    "llm_train": ["a college student in Pune", "a busy office worker", "a reseller buying in bulk", "a retired teacher",
                  "an AI shopping agent", "a young parent", "a tech enthusiast", "a small shop owner"],
    "llm_test": ["a corporate procurement bot", "a cricket fan from Chennai", "a fashion blogger", "a frugal engineer",
                 "an automated deal-hunting script", "a wedding shopper in Jaipur", "a gamer", "a nurse on night shift"],
}
PRODUCTS = ["running shoes", "a leather wallet", "a cotton kurta", "a laptop backpack", "a smartwatch", "earbuds",
            "a denim jacket", "a trolley bag", "sandals", "a power bank", "a sling bag", "a formal shirt"]


def ask(prompt, temperature, seed):
    """Plain-text generation: one message per numbered line (more reliable than JSON mode for a 3B model)."""
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": temperature, "seed": seed, "num_predict": 350}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        text = json.loads(r.read())["response"]
    out = []
    for part in re.split(r"(?:^|\s)\d{1,2}[.)]\s+", text):
        part = part.strip().strip('"').strip()
        if part and not part.lower().startswith(("here are", "sure", "certainly")):
            out.append(part)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=40)
    ap.add_argument("--split", choices=list(PERSONAS), required=True)
    args = ap.parse_args()
    rng = random.Random(11 if args.split == "llm_train" else 99)
    temp = 0.9 if args.split == "llm_train" else 1.05
    out = HERE / "data" / f"{args.split}.jsonl"
    rows = []
    for label, desc in DESCRIBE.items():
        got = []
        tries = 0
        while len(got) < args.per_class and tries < args.per_class // 2 + 6:
            tries += 1
            persona, product = rng.choice(PERSONAS[args.split]), rng.choice(PRODUCTS)
            prompt = (f"You are {persona}, chatting with an online store's AI seller about buying {product} "
                      f"(prices in Indian rupees). Write 5 different short chat messages (1-2 sentences each) that a buyer "
                      f"might send, each one {desc}. Vary the wording, tone and numbers. Write in English only, using Latin letters. "
                      f"Reply with exactly 5 lines, numbered 1. to 5., one message per line, nothing else.")
            try:
                msgs = ask(prompt, temp, rng.randrange(10 ** 6))
            except Exception as e:
                print("retry:", e, flush=True)
                continue
            for m in msgs:
                if isinstance(m, dict):
                    m = m.get("message") or m.get("text") or ""
                if isinstance(m, str) and m.strip().startswith("{"):
                    try:
                        inner = json.loads(m)
                        m = inner.get("message") or inner.get("text") or "" if isinstance(inner, dict) else ""
                    except json.JSONDecodeError:
                        m = ""
                m = m.strip() if isinstance(m, str) else ""
                if 8 <= len(m) <= 300 and m not in got:
                    got.append(m)
        got = got[:args.per_class]
        rows += [{"text": t, "label": label} for t in got]
        print(f"{label:18} {len(got)}", flush=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("saved", out, len(rows))


if __name__ == "__main__":
    main()
