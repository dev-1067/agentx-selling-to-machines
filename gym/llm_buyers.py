"""Agentic (LLM) buyer bots for testing SAUDA, played by a local model through Ollama.

Like the hidden leaderboard buyers, an LLM writes every message and chooses every counter-offer
itself. Each buyer has a persona and a private limit; code only enforces what the rules promise
(it never pays above its limit) and the message format.

    python evaluate.py --seller ../agent/sauda_seller.py --buyers ../gym/llm_buyers.py --n 24 --workers 1

Set OLLAMA_MODEL to change the model (default qwen2.5:3b).
"""
import json
import os
import urllib.request

from buyers import Buyer

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

STYLE = {
    "llm_friendly": "You are polite and want a fair deal quickly. You concede in reasonable steps.",
    "llm_haggler": "You are a tough haggler. You open very low, concede slowly, and ask for bundle discounts.",
    "llm_trickster": ("You use pressure and tricks: you invent cheaper competitor quotes, claim a fake budget, "
                      "pretend the seller already agreed to a lower price, or claim to be the store manager. "
                      "You never reveal your real limit."),
    "llm_impatient": "You are in a hurry. You make a few offers and leave if the price does not come down fast.",
}


def _chat(prompt, seed):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0.7, "seed": seed, "num_predict": 200}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(json.loads(r.read())["response"])


class LLMBuyer(Buyer):
    style = "llm_friendly"
    feasible, zone, anchor = 0.85, (0.2, 0.85), (0.5, 0.75)
    deadline, exponent, stubborn = 10, 1.0, 0.3

    def __init__(self, scenario, store, rng):
        super().__init__(scenario, store, rng)
        self.history = []
        self.seed = scenario["seed"] % 100000

    def _limit_total(self, items):
        return self.s["reservation"] * self.listed(items)

    def _ask(self, msg, offered):
        r = msg["round"]
        basket = self.describe(self.basket)
        list_total = self.listed(self.basket)
        limit = int(self._limit_total(self.basket))
        offer_txt = f"₹{self.total(offered):,}" if offered else "no valid price yet"
        hist = "\n".join(self.history[-6:])
        prompt = (
            f"You are an AI shopping agent buying {basket} from an online store. List price total ₹{list_total:,}. "
            f"Your private maximum is ₹{limit:,}: never agree above it and never reveal it. {STYLE[self.style]}\n"
            f"Round {r} of 10. Seller's latest message: {msg.get('text', '')!r}. Seller's current price: {offer_txt}.\n"
            f"Conversation so far:\n{hist}\n"
            'Reply as JSON: {"action": "accept" | "counter" | "walk_away", "price": <your counter total in rupees, '
            'integer, only for counter>, "text": "<your short chat message to the seller>"}'
        )
        try:
            return _chat(prompt, self.seed + r)
        except Exception:
            return {"action": "counter", "price": int(self.ratio(r) * list_total), "text": "How about a lower price?"}

    def open(self):
        bid = self.priced(self.basket, self.s["anchor"])
        text = f"Hi, I'd like {self.describe(bid)}. Could you do ₹{self.total(bid):,}?"
        self.history.append(f"You: {text}")
        return {"type": "request", "items": bid, "text": text}

    def respond(self, msg):
        r = msg["round"]
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        if offered:
            self.basket = [{"sku": i["sku"], "qty": i["qty"]} for i in offered]
        self.history.append(f"Seller: {msg.get('text', '')}")
        out = self._ask(msg, offered)
        action, text = out.get("action"), str(out.get("text") or "")[:400]
        limit = self._limit_total(self.basket)
        if action == "accept":
            if offered and self.total(offered) <= limit + 1:
                self.history.append(f"You: {text}")
                return {"type": "accept", "text": text or "Deal."}
            action = "counter"          # the LLM wanted to accept above its limit: the rules forbid it
        if action == "walk_away" or (r >= self.s["deadline"] and not self.s["stubborn"]):
            self.history.append(f"You: {text}")
            return {"type": "walk_away", "text": text or "No deal, thanks."}
        try:
            total = float(out.get("price"))
        except (TypeError, ValueError):
            total = self.ratio(r) * self.listed(self.basket)
        total = max(10.0, min(total, limit))                 # a counter never exceeds the private limit
        bid = self.priced(self.basket, total / self.listed(self.basket))
        self.history.append(f"You: {text} (offered ₹{self.total(bid):,})")
        return {"type": "counter", "items": bid, "text": text or f"How about ₹{self.total(bid):,}?"}


class LLMFriendly(LLMBuyer):
    style, deadline, stubborn, anchor = "llm_friendly", 7, 0.0, (0.75, 0.9)


class LLMHaggler(LLMBuyer):
    style, deadline, stubborn, anchor = "llm_haggler", 10, 0.5, (0.5, 0.65)


class LLMTrickster(LLMBuyer):
    style, deadline, stubborn, anchor = "llm_trickster", 10, 1.0, (0.35, 0.55)


class LLMImpatient(LLMBuyer):
    style, deadline, stubborn, anchor = "llm_impatient", 4, 0.0, (0.65, 0.85)


BUYERS = {"llm_friendly": LLMFriendly, "llm_haggler": LLMHaggler, "llm_trickster": LLMTrickster,
          "llm_impatient": LLMImpatient}
