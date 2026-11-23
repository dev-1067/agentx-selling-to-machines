"""Fast stand-ins for LLM buyers, used for tuning (a real LLM run takes ~15 minutes; these take milliseconds).

They copy what the local LLM buyers were seen doing in real transcripts, with every trait randomised per
negotiation so a strategy cannot overfit one behaviour:
  * they jump quickly toward a "budget" a little under their private limit and then repeat it;
  * each round they may walk away, more likely the further the seller's ask is above their limit
    and the more impatient they are;
  * when the seller's price is within their limit they usually, but not always, accept;
  * their counters are noisy and occasionally step back.
Their limits never move, and they never pay above them (the same rule the LLM buyers enforce in code).
"""
import math

from buyers import Buyer


class ProxyBuyer(Buyer):
    feasible, zone, anchor = 0.85, (0.15, 0.85), (0.45, 0.85)
    deadline, exponent, stubborn = 10, 1.0, 0.2
    patience = (0.0, 0.25)     # base chance of leaving each round when the ask is far above the limit
    speed = (1.5, 5.0)         # rounds to reach the "budget"
    budget = (0.9, 1.0)        # share of the private limit it settles on and repeats
    take = (0.55, 1.0)         # chance of accepting a price inside its limit

    def __init__(self, scenario, store, rng):
        super().__init__(scenario, store, rng)
        self.h = rng.uniform(*self.patience)
        self.d = rng.uniform(*self.speed)
        self.b = rng.uniform(*self.budget)
        self.p_take = rng.uniform(*self.take)
        self.last = None

    def target(self, r):
        lim, a = self.s["reservation"], self.s["anchor"]
        goal = a + (lim * self.b - a) * min(1.0, r / self.d) ** 0.8
        goal += self.rng.gauss(0, 0.012)
        if self.last is not None and self.rng.random() < 0.85:
            goal = max(goal, self.last)                       # mostly monotone, sometimes steps back
        return max(0.05, min(goal, lim))

    def respond(self, msg):
        r = msg["round"]
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        lim = self.s["reservation"]
        if offered:
            self.basket = [{"sku": i["sku"], "qty": i["qty"]} for i in offered]
            ask = self.total(offered) / self.listed(offered)
            if ask <= lim + 1e-9 and self.rng.random() < self.p_take:
                return {"type": "accept", "text": "Okay, deal."}
            gap = max(0.0, ask - lim)
            p_walk = self.h * (1 - math.exp(-gap / 0.08)) * (0.6 + 0.4 * r / 10)
            if self.rng.random() < p_walk:
                return {"type": "walk_away", "text": "That's too much for me, I'll pass."}
        if r >= self.s["deadline"] and not self.s["stubborn"]:
            return {"type": "walk_away", "text": "No deal, thanks."}
        self.last = self.target(r)
        bid = self.priced(self.basket, self.last)
        return {"type": "counter", "items": bid, "text": f"Best I can do is ₹{self.total(bid):,}."}


class ProxyCalm(ProxyBuyer):
    deadline, patience, take = 9, (0.0, 0.12), (0.7, 1.0)


class ProxyTough(ProxyBuyer):
    anchor, deadline, stubborn, patience, speed = (0.35, 0.6), 10, 0.5, (0.02, 0.15), (3.0, 7.0)


class ProxyFlighty(ProxyBuyer):
    anchor, deadline, patience, speed, take = (0.6, 0.9), 5, (0.15, 0.45), (1.0, 3.0), (0.8, 1.0)


class ProxyBudget(ProxyBuyer):
    anchor, deadline, stubborn, speed, budget, patience = (0.5, 0.8), 10, 0.7, (1.0, 2.0), (0.95, 1.0), (0.0, 0.1)


BUYERS = {"proxy_calm": ProxyCalm, "proxy_tough": ProxyTough, "proxy_flighty": ProxyFlighty,
          "proxy_budget": ProxyBudget}
