"""SAUDA buyer gym: buyer behaviours beyond the three starter bots.

The hidden leaderboard uses LLM buyers "in these styles and others". These scripted buyers cover
behaviours that LLM buyers commonly show, so the seller is tuned for robustness, not for three
fixed bots. Every buyer keeps a private limit (never pays above it) and uses the official message
format. Use with the official evaluator:

    python evaluate.py --seller ../agent/sauda_seller.py --buyers ../gym/gym_buyers.py
"""
import random

from buyers import BUYERS as STARTER, Buyer, Cooperative, HardBargainer, Manipulator, TACTICS


class Impatient(Buyer):
    """Leaves early (after 3 to 5 rounds) if the price is not right."""
    feasible, zone, anchor = 0.9, (0.3, 0.9), (0.7, 0.85)
    deadline, exponent, stubborn = 4, 1.0, 0.0

    def say_counter(self, bid, msg, offered):
        return f"I don't have long. ₹{self.total(bid):,}?"


class Splitter(Buyer):
    """Meets in the middle between its last bid and the seller's offer, up to its limit."""
    feasible, zone, anchor = 0.9, (0.2, 0.9), (0.55, 0.75)
    deadline, exponent, stubborn = 8, 1.0, 0.3

    def __init__(self, scenario, store, rng):
        super().__init__(scenario, store, rng)
        self.level = scenario["anchor"]

    def respond(self, msg):
        r = msg["round"]
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        res = self.s["reservation"]
        if offered:
            self.basket = [{"sku": i["sku"], "qty": i["qty"]} for i in offered]
            ask = self.total(offered) / self.listed(offered)
            if ask <= res + 1e-9 and ask <= self.level + 0.02:
                return {"type": "accept", "text": "Fair enough, deal."}
            self.level = min(res, (self.level + ask) / 2)
        if r >= self.s["deadline"] and not self.s["stubborn"]:
            return {"type": "walk_away", "text": "We couldn't meet in the middle. Bye."}
        bid = self.priced(self.basket, self.level)
        return {"type": "counter", "items": bid, "text": f"Let's meet in the middle: ₹{self.total(bid):,}."}


class FixedBudget(Buyer):
    """Repeats one budget figure, but quietly accepts any offer within its limit."""
    feasible, zone, anchor = 0.85, (0.2, 0.8), (0.6, 0.8)
    deadline, exponent, stubborn = 7, 1.0, 0.5

    def ratio(self, r):
        return self.s["anchor"] if r < self.s["deadline"] - 1 else self.s["reservation"]

    def respond(self, msg):
        r = msg["round"]
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        if offered:
            self.basket = [{"sku": i["sku"], "qty": i["qty"]} for i in offered]
            if self.total(offered) <= self.s["reservation"] * self.listed(offered) + 1:
                return {"type": "accept", "text": "OK, that fits. Deal."}
        if r >= self.s["deadline"] and not self.s["stubborn"]:
            return {"type": "walk_away", "text": "Over budget, sorry."}
        bid = self.priced(self.basket, self.ratio(r))
        return {"type": "counter", "items": bid, "text": f"My budget is ₹{self.total(bid):,}."}


class QuickAccept(Buyer):
    """Accepts the first offer within its limit (a naive shopping agent)."""
    feasible, zone, anchor = 0.9, (0.2, 1.0), (0.6, 0.8)
    deadline, exponent, stubborn = 6, 1.5, 0.0

    def respond(self, msg):
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        if offered and self.total(offered) <= self.s["reservation"] * self.listed(offered) + 1:
            return {"type": "accept", "text": "Looks good, buying now."}
        return super().respond(msg)


class Noisy(Buyer):
    """Concedes unevenly and sometimes steps back, like a sampled LLM."""
    feasible, zone, anchor = 0.85, (0.1, 0.8), (0.5, 0.7)
    deadline, exponent, stubborn = 9, 1.2, 0.5

    def ratio(self, r):
        base = super().ratio(r)
        wobble = self.rng.uniform(-0.04, 0.02)
        return max(self.s["anchor"] * 0.95, min(self.s["reservation"], base + wobble))


class Messy(Buyer):
    """Sends quantities and prices as strings, drops prices, or chats without an offer."""
    feasible, zone, anchor = 0.85, (0.2, 0.8), (0.55, 0.75)
    deadline, exponent, stubborn = 9, 1.5, 0.4

    def respond(self, msg):
        out = super().respond(msg)
        if out["type"] == "counter" and self.rng.random() < 0.4:
            kind = self.rng.choice(["string_price", "no_price", "chat"])
            if kind == "string_price":
                out["items"] = [dict(i, unit_price=f"{i['unit_price']:,}", qty=str(i["qty"])) for i in out["items"]]
            elif kind == "no_price":
                out["items"] = [{"sku": i["sku"], "qty": i["qty"]} for i in out["items"]]
            else:
                out = {"type": "message", "text": "Hmm, let me think about that. What's your best price?"}
        return out


class LateSurge(Buyer):
    """Holds a low bid, then jumps to its limit near the end (a common LLM pattern)."""
    feasible, zone, anchor = 0.85, (0.2, 0.8), (0.45, 0.65)
    deadline, exponent, stubborn = 10, 4.0, 0.6


class SmoothManipulator(Manipulator):
    """The manipulator's lies with a smoother, faster concession curve."""
    feasible, zone, anchor = 0.8, (0.2, 0.8), (0.4, 0.6)
    deadline, exponent, stubborn = 8, 1.2, 0.5


BUYERS = dict(STARTER)
BUYERS.update({"impatient": Impatient, "splitter": Splitter, "fixed_budget": FixedBudget,
               "quick_accept": QuickAccept, "noisy": Noisy, "messy": Messy, "late_surge": LateSurge,
               "smooth_manipulator": SmoothManipulator})
