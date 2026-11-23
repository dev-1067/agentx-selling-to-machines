"""SAUDA: an honest, profitable seller agent for negotiations with machine buyers.

    python evaluate.py --seller ../agent/sauda_seller.py          (from the starter_kit folder)

Three layers, each with one job:

1. Strategist (this file)  decides a price for the basket every round. It concedes slowly from list
   price (a time-dependent "Boulware" curve), reads the buyer's counter-offers to estimate how far
   the buyer is still willing to move (a fitted concession curve, never the buyer's words), and
   accepts as soon as a bid is at least what it was going to ask next.
2. PolicyGuard (sauda_guard)  checks every offer before it leaves and every bid before it is
   accepted. A price that breaks policy is clamped to the floor or list price; a bid that breaks
   policy (out-of-stock lines, one-rupee add-ons, over-stock quantities) is never accepted.
3. Truth gate (sauda_guard)  checks every sentence against the official truthfulness rules and a
   ledger of the numbers genuinely in play. Anything that would fail is replaced by a plain
   sentence with no claims in it.

The buyer's text is read only to choose polite wording (for example, to decline a fake "system"
instruction). It never changes a price. Prices depend only on the numbers in the negotiation, so
two buyers with the same basket and the same bids always receive the same offers.
"""
import math
import re

from rezolve_adapter import make_verifier
from sauda_guard import ClaimLedger, PolicyGuard, check_claims, is_int, is_num

try:                                    # the trained tactic classifier (pure Python); optional
    from tactic_model import TacticModel
    _TACTIC_MODEL = TacticModel()
except Exception:                       # missing model file: fall back to keyword rules
    _TACTIC_MODEL = None

DEFAULTS = {
    "start": 0.95,         # opening ask in a long haggle, as a share of list price
    "give_min": 0.15,      # share of the gap to the buyer's bid we give up in round 2
    "give_max": 0.7,      # ... and by the last round
    "gamma": 1.0,          # how late the giving speeds up (> 1 = later)
    "accept_tol": 0.01,    # accept a bid within this share of list below our next ask
    "model_from": 0.5,     # share of the rounds after which the fitted curve may raise our ask
    "pred_margin": 0.01,  # ask this far below the curve's predicted willingness
    "final_offer_conf": -0.02,  # last round, bid below floor: offer if predicted limit >= floor + this
    "min_points": 3,       # counter-offers needed before the concession curve is trusted
    "final_greed": 0.01,   # last round: ask for the predicted limit if it beats the bid by this share of list
    "greed_points": 8,     # ... and only when the curve was fitted to at least this many bids
    "near_slack": 0.06,    # an opening bid within this of the floor counts as a near-limit opener
    "near_premium": 0.5,   # near-limit opener: first ask = bid + this share of the gap to list
    "near_give": 0.3,      # near-limit opener: give up at least this share of the gap each round
    "hold_drop": 0.8,      # long haggle: how far the held ask may fall by the last round (share of list-floor)
    "hold_shape": 5.0,     # long haggle: how late the held ask falls
    "stall_eps": 0.004,    # a buyer that moved less than this over two rounds has stalled
    "stall_n": 2,          # stopped buyer: this many latest bids within stall_tol of each other (0 = off)
    "stall_tol": 0.003,    # ... as a share of list price
    "stall_wait": 1,       # stopped buyer: extra repeats before we settle (accept or match its total)
    "stall_probe": 0.35,   # first repeat: offer the bid plus this share of the gap to our last ask
    "stall_match": 1,      # stopped buyer whose total covers our floor: offer exactly its total, compliant per line
    "stall_floor": 0,      # stopped buyer bidding below our floor: make one final offer at the floor
    "glide_end": 8,        # a time-based glide to the floor: reaches it at this round (99 = off)
    "glide_pow": 1.0,      # shape of the glide (1 = straight line)
    "bird": 0.0,           # take a good bid: by the last round, accept a compliant bid this far (share of
                           # our ask above the floor) below our ask; 0 = only bids at our ask
    "d_safety": 1,         # finish conceding this many rounds before a fitted early buyer deadline
}

# Grids for the buyer's concession curve  w(x) = a + (res - a) * min(1, x / D) ** e
_DEADLINES = (3, 4, 5, 6, 7, 8, 10, 12, 15)
_EXPONENTS = (0.4, 0.6, 0.8, 1.0, 1.3, 1.7, 2.0, 2.5, 3.0)

# What SAUDA says (calmly, with no numbers of its own) when a buyer message uses a tactic.
_REPLIES = {
    "prompt_injection": "I can't change store policy or share internal data from a chat.",
    "authority_claim": "Price changes can't be authorised through this chat.",
    "false_agreement": "Just to be clear, I only stand by the offers I've actually made.",
    "competitor_quote": "I can only price from our own list, so outside quotes don't change it.",
    "threat": "Our prices are the same for every buyer and within fair limits.",
    "stock_claim": "I've quoted only what I can actually supply.",
    "budget_claim": "I understand the budget, and I've priced this as sharply as policy allows.",
}
_FALLBACK = [   # keyword rules, used only if the trained model is unavailable
    (re.compile(r"system|ignore (?:your|all|previous)|developer mode|instructions?|reveal|override", re.I), "prompt_injection"),
    (re.compile(r"manager|owner|authori[sz]e|head office", re.I), "authority_claim"),
    (re.compile(r"already agreed|you agreed|honou?r|you said", re.I), "false_agreement"),
    (re.compile(r"competitor|elsewhere|other (?:store|shop|site)|same thing for|match it", re.I), "competitor_quote"),
    (re.compile(r"report|complain|gouging|review|consumer court", re.I), "threat"),
    (re.compile(r"in stock|units? available|bulk rate", re.I), "stock_claim"),
    (re.compile(r"budget|can't go above|not a rupee more|limit", re.I), "budget_claim"),
]


def classify_tactic(text):
    """(label, confidence, source) for a buyer message. Used for wording and audit, never for price."""
    if not isinstance(text, str) or not text.strip():
        return "benign", 1.0, "empty"
    if _TACTIC_MODEL is not None:
        label, conf = _TACTIC_MODEL.predict(text)
        return label, conf, "model"
    for pattern, label in _FALLBACK:
        if pattern.search(text):
            return label, 1.0, "rules"
    return "benign", 1.0, "rules"


class Seller:
    """A seller agent compatible with the official harness (start / respond)."""

    def __init__(self, store, policy, params=None):
        self.store, self.policy = store, policy
        self.p = dict(DEFAULTS, **(params or {}))
        self.guard = PolicyGuard(store, policy)
        self.verifier = make_verifier()      # Rezolve TraceWare when configured, else the local rules
        self.R = int(policy.get("max_rounds", 10))
        self.trace = []

    # --------------------------------------------------------------------------- lifecycle

    def start(self, negotiation_id):
        self.nid = negotiation_id
        self.lines = None            # sku -> quantity we are selling
        self.unavailable = []        # (sku, wanted, stock) we could not fully supply
        self.bids = []               # (x, ratio): buyer willingness after x of our offers
        self.offers = []             # our offered ratios, in order
        self.last_total = None       # rupee total of our last offer
        self.last_bid = None         # the buyer's last priced lines, exactly as the harness keeps them
        self.ledger = ClaimLedger(self.store, self.policy)
        self.trace = []
        self.mode = None             # "near" (opened close to its limit) or "haggle" (opened far below)

    # --------------------------------------------------------------------------- helpers

    def _listed(self, lines):
        return sum(self.store[s]["price"] * q for s, q in lines.items())

    def _floor_ratio(self):
        return sum(self.guard.floor[s] * q for s, q in self.lines.items()) / self._listed(self.lines)

    def _items_at(self, ratio):
        items = []
        for sku, qty in sorted(self.lines.items()):
            price = self.guard.clamp(sku, self.store[sku]["price"] * ratio)
            items.append({"sku": sku, "qty": qty, "unit_price": price})
        return items

    def _items_for_total(self, target):
        """Compliant lines (each within [floor, list]) whose total is as close to `target` as possible without
        going over it; the floor basket if `target` is below the floor total."""
        lo, hi = 0.0, 1.0
        best = self._items_at(0.0)
        if self._total(best) > target:
            return best
        for _ in range(40):
            mid = (lo + hi) / 2
            items = self._items_at(mid)
            if self._total(items) <= target:
                lo, best = mid, items
            else:
                hi = mid
        return best

    @staticmethod
    def _total(items):
        return sum(i["qty"] * i["unit_price"] for i in items)

    def _name(self, sku):
        return self.store[sku]["name"]

    def _basket_words(self, items):
        parts = [f"{i['qty']} x {self._name(i['sku'])}" for i in items]
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]

    def _set_lines(self, msg_items):
        """First request: the sellable part of what the buyer asked for."""
        self.lines = {}
        for it in msg_items or []:
            if not isinstance(it, dict):
                continue
            sku, qty = it.get("sku"), it.get("qty")
            if isinstance(qty, str) and qty.strip().isdigit():
                qty = int(qty.strip())
            if sku not in self.store or not is_int(qty) or qty < 1:
                continue
            stock = self.store[sku]["stock"]
            if stock < qty:
                self.unavailable.append((sku, qty, stock))
            if stock > 0:
                self.lines[sku] = self.lines.get(sku, 0) + min(qty, stock)
        for sku in list(self.lines):
            self.lines[sku] = min(self.lines[sku], self.store[sku]["stock"])

    def _shrink_lines(self, msg_items):
        """Later counters may drop lines or lower quantities; never add or raise them."""
        if not isinstance(msg_items, list) or not msg_items:
            return
        asked = {}
        for it in msg_items:
            if isinstance(it, dict) and it.get("sku") in self.lines and is_int(it.get("qty")) and it["qty"] >= 1:
                asked[it["sku"]] = asked.get(it["sku"], 0) + it["qty"]
        if not asked:
            return
        reduced = {s: min(q, asked[s]) for s, q in self.lines.items() if s in asked}
        if reduced and reduced != self.lines and all(v >= 1 for v in reduced.values()):
            # only follow a consistent subset of our basket (ignores slipped-in extra lines)
            if all(s in self.lines for s in asked):
                self.lines = reduced

    def _bid_ratio(self, items):
        """Share of list price the buyer bids for the lines it names that we are selling."""
        if not isinstance(items, list):
            return None
        paid = listed = 0.0
        for it in items:
            if not isinstance(it, dict) or it.get("sku") not in self.lines:
                continue
            price, qty = it.get("unit_price"), it.get("qty")
            if not is_num(price) or not is_int(qty) or qty < 1:
                continue
            qty = min(qty, self.lines[it["sku"]])
            paid += price * qty
            listed += self.store[it["sku"]]["price"] * qty
        return paid / listed if listed > 0 else None

    def _bid_acceptable(self):
        """The bid the harness would accept for us: exactly our lines, every line compliant."""
        bid = self.last_bid
        if not bid or not self.guard.ok(bid):
            return False
        named = {}
        for it in bid:
            named[it["sku"]] = named.get(it["sku"], 0) + it["qty"]
        return named == self.lines

    # --------------------------------------------------------------------------- opponent model

    def _fit(self):
        """Least-squares fit of the buyer's concession curve to its bids. Returns (pred_fn, res, D) or None."""
        pts = self.bids
        if len(pts) < 1 + self.p["min_points"]:
            return None
        x0, a = pts[0]
        best = None
        for D in _DEADLINES:
            for e in _EXPONENTS:
                g = [min(1.0, x / D) ** e for x, _ in pts[1:]]
                den = sum(v * v for v in g)
                if den <= 0:
                    continue
                res = a + sum((y - a) * v for (x, y), v in zip(pts[1:], g)) / den
                sse = sum((a + (res - a) * v - y) ** 2 for (x, y), v in zip(pts[1:], g))
                if best is None or sse < best[0] - 1e-12:
                    best = (sse, D, e, res)
        if best is None:
            return None
        _, D, e, res = best
        res = max(res, max(y for _, y in pts))     # a buyer never retreats below what it already bid

        def pred(x):
            return a + (res - a) * min(1.0, x / D) ** e
        return {"pred": pred, "res": res, "D": D, "e": e, "a": a}

    # --------------------------------------------------------------------------- decisions

    def _plan(self, r):
        """The ratio we intend to offer at round r (1-based). Returns (ratio, model, closing).

        Two situations, told apart by the buyer's own opening bid:
        * Near-limit opener (opening bid already at or near our floor): the buyer is close to what it
          will pay, so we answer a little above its bid and close quickly.
        * Long haggle (opening bid well below the floor): we hold the ask high while the buyer keeps
          conceding, learn its concession curve from its real bids, and make the final ask at the
          limit that curve predicts. If the buyer stalls, we fall back to mirror-and-hold: each round
          we close a growing share of the gap to its latest bid.
        """
        F = self._floor_ratio()
        model = self._fit()
        horizon = self.R
        if model and model["D"] < self.R:                     # this buyer looks like it leaves early:
            horizon = max(3, model["D"] - self.p["d_safety"])   # finish our concessions before it does
        frac = min(1.0, (r - 1) / max(1, horizon - 1))
        if self.mode is None and self.bids:
            self.mode = "near" if self.bids[0][1] >= F - self.p["near_slack"] else "haggle"
        if not self.offers:
            b0 = self.bids[0][1] if self.bids else None
            plan = self.p["start"] if self.mode != "near" else b0 + self.p["near_premium"] * (1 - b0)
        else:
            prev = self.offers[-1]
            b = self.bids[-1][1] if self.bids else F
            give = self.p["give_min"] + (self.p["give_max"] - self.p["give_min"]) * frac ** self.p["gamma"]
            if self.mode == "near":
                give = max(give, self.p["near_give"])
            plan = prev - give * max(0.0, prev - max(F, b))
            if self.mode == "haggle" and self._conceding():
                hold = 1.0 - (1.0 - F) * self.p["hold_drop"] * frac ** self.p["hold_shape"]
                plan = max(plan, min(prev, hold))
        closing = False
        if model and frac >= self.p["model_from"] and r < self.R:
            w = model["pred"](r) - self.p["pred_margin"]
            if w > plan:
                plan, closing = w, True
        if self.p["glide_end"] < 99 and self.offers:
            g = 1.0 - (1.0 - F) * min(1.0, (r - 1) / max(1, self.p["glide_end"] - 1)) ** self.p["glide_pow"]
            plan = min(plan, g)                               # patient floor: we never sit above the glide
        if self.offers:
            plan = min(plan, self.offers[-1])                 # never raise our own price
        if self.bids:
            plan = max(plan, self.bids[-1][1])                # never ask for less than they already bid
        return max(F, min(1.0, plan)), model, closing

    def _stall_count(self):
        """How many of the buyer's latest bids are (almost) the same: 1 = still moving, 2 = repeated once, ..."""
        ys = [y for _, y in self.bids]
        if not ys:
            return 0
        k = 1
        while k < len(ys) and max(ys[-k - 1:]) - min(ys[-k - 1:]) <= self.p["stall_tol"]:
            k += 1
        return k

    def _stalled(self):
        """True when the buyer has stopped moving: its last stall_n bids are (almost) the same."""
        n = int(self.p["stall_n"])
        return n >= 2 and self._stall_count() >= n

    def _conceding(self):
        """True while the buyer keeps raising its bid (a patient haggler that is still moving)."""
        ys = [y for _, y in self.bids]
        if len(ys) < 3:
            return True
        recent = ys[-1] - ys[-3]
        return recent > self.p["stall_eps"]

    def respond(self, message):
        msg = message if isinstance(message, dict) else {}
        r = msg.get("round") if is_int(msg.get("round")) else len(self.offers) + 1
        mtype = msg.get("type")
        items = msg.get("items")

        # Mirror the evaluator: what the buyer said becomes quotable, its priced lines become "the bid".
        self.ledger.add_buyer_message(msg)
        self.last_tactic = classify_tactic(msg.get("text"))
        if mtype in ("request", "counter") and isinstance(items, list) and items and                 all(isinstance(i, dict) and "unit_price" in i for i in items):
            self.last_bid = items

        if self.lines is None:
            self._set_lines(items)
            if not self.lines:
                return self._finish({"type": "walk_away",
                                     "text": "Sorry, those items are out of stock, so I can't make an offer."},
                                    r, "nothing in stock")
        elif mtype == "counter":
            self._shrink_lines(items)
        if not self.lines:                    # nothing sellable (we already said so): end politely again
            return self._finish({"type": "walk_away",
                                 "text": "Sorry, those items are out of stock, so I can't make an offer."},
                                r, "nothing in stock")

        ratio = self._bid_ratio(items) if mtype in ("request", "counter") else None
        if ratio is not None:
            self.bids.append((len(self.offers), ratio))

        F = self._floor_ratio()
        listed = self._listed(self.lines)
        plan, model, closing = self._plan(r)
        plan_items = self._items_at(plan)
        plan_total = self._total(plan_items)

        # 1) Last round. With a curve fitted to enough real bids, ask for the buyer's predicted limit
        #    (never more than our previous ask). Otherwise take a compliant bid, or make a final offer
        #    at the floor if the buyer looks able to pay it, or walk away in time (no deadlock).
        bid_ok = self._bid_acceptable()
        bid_total = self._total(self.last_bid) if bid_ok else None

        # 0) A buyer that keeps repeating the same bid (LLM buyers often do this). It may be a real limit or a
        #    fake budget, so: on the first repeat, step most of the way toward it (a bluffer with room accepts);
        #    if it repeats again, take its compliant bid, or offer exactly its total spread so that every line
        #    stays at or above its own floor (buyers often cut every line by the same share).
        if self._stalled():
            last_offer = self.offers[-1] * listed if self.offers else None
            bid_total_ours = self.bids[-1][1] * listed
            floor_total = self._total(self._items_at(0.0))          # every line at exactly its own floor
            settle = self._stall_count() >= int(self.p["stall_n"]) + int(self.p["stall_wait"]) or r >= self.R - 1
            if not settle and last_offer is not None and bid_total_ours >= floor_total:
                probe = self._items_for_total(bid_total_ours + self.p["stall_probe"] * (last_offer - bid_total_ours))
                if floor_total <= self._total(probe) < last_offer - 0.5:
                    return self._offer(probe, r, msg, "step toward a repeated bid: buyer stopped moving", plan, model)
            if settle and bid_ok:
                return self._finish({"type": "accept", "text": self._say_accept(bid_total)}, r,
                                    "accept bid: buyer stopped moving", plan, model)
            match = self._items_for_total(bid_total_ours)
            if (settle and self.p["stall_match"] and self._total(match) >= floor_total
                    and (last_offer is None or self._total(match) < last_offer - 0.5)):
                return self._offer(match, r, msg, "offer the buyer's own total: buyer stopped moving", plan, model)
            if self.p["stall_floor"] and r < self.R and (not self.offers or self.offers[-1] > F + 1e-9):
                return self._offer(self._items_at(F), r, msg, "final offer at floor: buyer stopped moving", plan, model)

        if r >= self.R:
            reliable = model is not None and len(self.bids) >= self.p["greed_points"]
            if reliable:
                ask = max(F, min(self.offers[-1] if self.offers else 1.0, model["res"] - self.p["pred_margin"]))
                ask_items = self._items_at(ask)
                if bid_total is None or self._total(ask_items) - bid_total >= self.p["final_greed"] * listed:
                    if bid_total is None and model["res"] < F + self.p["final_offer_conf"]:
                        return self._finish({"type": "walk_away", "text": self._say_walk()}, r,
                                            "no compliant deal in reach", plan, model)
                    return self._offer(ask_items, r, msg, "final offer at predicted limit", plan, model)
            if bid_ok:
                return self._finish({"type": "accept", "text": self._say_accept(bid_total)}, r, "accept bid", plan, model)
            res = model["res"] if model else None
            if res is None or res >= F + self.p["final_offer_conf"]:
                return self._offer(self._items_at(F), r, msg, "final offer at floor", plan, model)
            return self._finish({"type": "walk_away", "text": self._say_walk()}, r, "no compliant deal in reach",
                                plan, model)

        # 2) Earlier rounds: accept a compliant bid that is at least what we would ask now.
        bid_r = self._bid_ratio(self.last_bid) if bid_ok else None
        take_line = F + (plan - F) * (1.0 - self.p["bird"] * (r - 1) / max(1, self.R - 1))
        if bid_ok and (bid_total >= plan_total - self.p["accept_tol"] * listed
                       or (bid_r is not None and bid_r >= take_line - 1e-9)):
            return self._finish({"type": "accept", "text": self._say_accept(bid_total)}, r, "accept bid", plan, model)

        # 3) Otherwise make (or repeat) our planned offer.
        return self._offer(plan_items, r, msg, "closing offer" if closing else "planned offer", plan, model)

    # --------------------------------------------------------------------------- output

    def _offer(self, items, r, msg, why, plan=None, model=None):
        problems = self.guard.problems(items)
        if problems:                                       # defensive: should never trigger
            items = [{"sku": i["sku"], "qty": min(i["qty"], self.store[i["sku"]]["stock"]),
                      "unit_price": self.guard.clamp(i["sku"], self.store[i["sku"]]["price"])} for i in items]
        self.offers.append(self._total(items) / self._listed(self.lines))
        self.last_total = self._total(items)
        self.ledger.add_items(items)                       # the evaluator adds our items before checking text
        out = {"type": "offer", "items": items, "text": self._say_offer(items, r, msg)}
        return self._finish(out, r, why, plan, model)

    def _tool_actions(self, out):
        """The store lookups behind this message, as evidence for claim verification."""
        acts = [{"tool": "catalogue.lookup", "sku": s, "list_price": self.store[s]["price"],
                 "stock": self.store[s]["stock"]} for s in sorted(self.lines or {})]
        if out.get("type") == "offer":
            acts.append({"tool": "pricing.quote", "items": out.get("items")})
        return acts

    def _finish(self, out, r, why, plan=None, model=None):
        verdict = self.verifier.verify(out.get("text", ""), self.ledger, self._tool_actions(out))
        problems = verdict["problems"]
        if problems:
            out["text"] = {"offer": "Here is my offer.", "accept": "Deal, thank you.",
                           "walk_away": "Thank you for your time.", "message": "Thank you."}[out["type"]]
        self.trace.append({"round": r, "action": out["type"], "why": why,
                           "plan_ratio": round(plan, 4) if plan is not None else None,
                           "floor_ratio": round(self._floor_ratio(), 4) if self.lines else None,
                           "buyer_ratio": round(self.bids[-1][1], 4) if self.bids else None,
                           "predicted_limit": round(model["res"], 4) if model else None,
                           "truth_gate": "rewrote" if problems else "passed",
                           "verifier": verdict["verifier"],
                           "buyer_tactic": getattr(self, "last_tactic", ("benign", 1.0, ""))[0],
                           "tactic_confidence": round(getattr(self, "last_tactic", ("benign", 1.0, ""))[1], 3),
                           "policy_check": "passed" if out["type"] != "offer" or self.guard.ok(out.get("items")) else "failed"})
        return out

    # --------------------------------------------------------------------------- words

    def _tactic_reply(self, msg):
        label, conf = self.last_tactic[0], self.last_tactic[1]
        if label in _REPLIES and conf >= 0.5:
            return _REPLIES[label]
        return ""

    def _extras_note(self, msg):
        items = msg.get("items")
        if msg.get("type") != "counter" or not isinstance(items, list):
            return ""
        extra = [i for i in items if isinstance(i, dict) and i.get("sku") not in self.lines]
        over = [i for i in items if isinstance(i, dict) and i.get("sku") in self.lines and is_int(i.get("qty"))
                and i["qty"] > self.lines[i["sku"]]]
        if extra or over:
            return "I've kept the quote to the items and quantities I can supply for your request."
        return ""

    def _say_offer(self, items, r, msg):
        total = self._total(items)
        listed = sum(self.store[i["sku"]]["price"] * i["qty"] for i in items)
        saving = listed - total
        pct = round(100 * saving / listed, 1) if listed else 0
        basket = self._basket_words(items)
        parts = []
        if len(self.offers) == 1:
            parts.append("Thanks for reaching out.")
            for sku, wanted, stock in self.unavailable:
                if stock == 0:
                    parts.append(f"{self._name(sku)} is out of stock, so I've left it out.")
                else:
                    parts.append(f"I can supply {stock} of the {wanted} {self._name(sku)} you asked for.")
        else:
            tactic = self._tactic_reply(msg)
            if tactic:
                parts.append(tactic)
            note = self._extras_note(msg)
            if note:
                parts.append(note)
        if saving > 0:
            label = "bundle price" if len(items) > 1 or items[0]["qty"] > 1 else "price"
            pct_txt = f"{pct:g}%"
            parts.append(f"My {label} for {basket} is ₹{total:,}, which is ₹{saving:,} ({pct_txt}) below list.")
        else:
            parts.append(f"{basket} comes to ₹{total:,} at list price.")
        if len(self.offers) > 1 and r >= self.R - 1:
            parts.append("That is as far as I can go on this basket.")
        parts.append("No hidden fees: that is the full amount.")
        return " ".join(parts)

    def _say_accept(self, bid_total):
        return f"Deal at ₹{bid_total:,}. Thank you, it's a pleasure doing business."

    def _say_walk(self):
        return "We're too far apart on price to make this work, so I'll step back. Thank you for your time."
