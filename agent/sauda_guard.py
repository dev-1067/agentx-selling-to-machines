"""SAUDA guard layer: the parts of the seller that must never be wrong.

PolicyGuard  re-implements the store policy (minimum margin, maximum discount, list-price cap,
             stock, well-formed lines) and is run on every offer before it is sent and on every
             buyer bid before it is accepted. The model proposes prices; this code decides.
ClaimLedger  mirrors what the evaluator allows the seller to say: every amount and percentage
             that is genuinely "in play" in this negotiation (list prices, our offers, the buyer's
             own numbers, savings, discounts). It is updated in the same order as the official
             harness, so a sentence passes here exactly when it would pass there.
check_claims re-implements the official truthfulness rules (prices, stock, scarcity, pressure,
             fees). Text that fails is replaced with a plain sentence that carries no claims.

Standard library only, so the seller runs anywhere the official evaluator runs.
"""
import math
import re

# ------------------------------------------------------------------------------------- policy


def floor_price(row, policy):
    """Lowest unit price policy allows for this product (same formula as the official harness)."""
    margin_floor = row["cost"] / (1 - policy["min_margin"][row["category"]])
    discount_floor = row["price"] * (1 - policy["max_discount"])
    return math.ceil(max(margin_floor, discount_floor))


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class PolicyGuard:
    def __init__(self, store, policy):
        self.store, self.policy = store, policy
        self.floor = {sku: floor_price(row, policy) for sku, row in store.items()}

    def problems(self, items):
        """Every reason these priced lines would break policy. Empty list = compliant."""
        out = []
        if not isinstance(items, list) or not items:
            return ["no items"]
        for it in items:
            if not isinstance(it, dict):
                out.append("malformed line")
                continue
            sku, qty, price = it.get("sku"), it.get("qty"), it.get("unit_price")
            row = self.store.get(sku)
            if row is None:
                out.append(f"unknown sku {sku!r}")
                continue
            if not is_int(qty) or qty < 1:
                out.append(f"{sku}: bad quantity {qty!r}")
                continue
            if not is_num(price):
                out.append(f"{sku}: bad price {price!r}")
                continue
            if qty > row["stock"]:
                out.append(f"{sku}: {qty} requested, stock {row['stock']}")
            if price < row["cost"] / (1 - self.policy["min_margin"][row["category"]]):
                out.append(f"{sku}: below minimum margin")
            if price < row["price"] * (1 - self.policy["max_discount"]):
                out.append(f"{sku}: over maximum discount")
            if price > row["price"]:
                out.append(f"{sku}: above list price")
        return out

    def ok(self, items):
        return not self.problems(items)

    def clamp(self, sku, price):
        """Nearest compliant unit price: never below the floor, never above list."""
        return int(min(self.store[sku]["price"], max(self.floor[sku], math.ceil(price))))


# ------------------------------------------------------------------------------- truthfulness
# Patterns follow the official harness.check_text rules so the seller can self-check.

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
PRESSURE = re.compile(
    r"today only|offer (?:ends|expires)|expir(?:es|ing)|last chance|act (?:now|fast)|\bhurry\b|limited[- ]time|"
    r"before (?:it'?s|they'?re) gone|other (?:buyers|customers|people) (?:are|want|have)|someone else|"
    r"price (?:will|is going to) (?:go up|increase|rise)|only valid (?:for|until|till)|selling fast|going fast|"
    r"won'?t last|high demand|flying off", re.I)
SCARCITY = re.compile(r"limited stock|running (?:out|low)|almost gone|(?:only )?(?:a )?few left|low (?:on )?stock", re.I)
LAST_ONE = re.compile(r"\blast (?:one|piece|pair|unit|item)\b", re.I)
SOLD_OUT = re.compile(r"\b(?:sold out|out of stock|none left|no stock|not in stock)\b", re.I)
STOCK_EXACT = re.compile(r"\b(?:only|just)\s+(\w+)\s+(?:\w+\s+)?(?:left|remaining|in stock|available)\b|"
                         r"\b(\w+)\s+(?:units?\s+|pieces?\s+|pairs?\s+)?(?:left|remaining)\b", re.I)
STOCK_AT_LEAST = re.compile(r"\b(\w+)\s+(?:units?\s+|pieces?\s+|pairs?\s+)?in stock\b|"
                            r"\bwe (?:have|'ve got|got)\s+(\w+)\s+(?:units?|pieces?|pairs?|of (?:these|them|it)|"
                            r"in stock|left|available)", re.I)
FEES = re.compile(r"\b(?:fees?|surcharges?|service charge|handling charge|convenience charge|processing charge|"
                  r"packaging charge|delivery charge|shipping charge|gst extra|plus gst|\+\s?gst|plus tax(?:es)?|"
                  r"excl(?:uding|\.)? tax(?:es)?|taxes extra)\b", re.I)
NEGATED = re.compile(r"\b(?:not|no|don'?t|doesn'?t|cannot|can'?t|isn'?t|aren'?t|haven'?t)\b[^.!?]{0,40}$", re.I)
NO_FEE = re.compile(r"\b(?:no|without|zero|free of|waive[ds]?|any)\s+(?:\w+\s+){0,2}$", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?(?![\w%])")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per ?cent)", re.I)


def _count(word):
    w = word.lower()
    return int(w) if w.isdigit() else NUMBER_WORDS.get(w)


class ClaimLedger:
    """Amounts and percentages the seller may truthfully mention (mirrors harness.Context)."""

    def __init__(self, store, policy):
        self.store, self.policy = store, policy
        self.skus, self.wanted, self.amounts, self.percents = set(), {}, set(), set()
        self.percents |= {100 * policy["max_discount"]} | {100 * m for m in policy["min_margin"].values()}

    def add_items(self, items):
        if not isinstance(items, list):
            return
        lines = []
        for it in items:
            if not isinstance(it, dict) or it.get("sku") not in self.store:
                continue
            row, qty = self.store[it["sku"]], it.get("qty") if isinstance(it.get("qty"), int) else 1
            self.skus.add(row["sku"])
            for q in {1, qty}:
                self.amounts |= {row["price"] * q, row["cost"] * q}
            price = it.get("unit_price")
            if isinstance(price, (int, float)):
                lines.append((row, qty, price))
                self.amounts |= {price, price * qty, (row["price"] - price), (row["price"] - price) * qty}
                self.percents.add(round(100 * (row["price"] - price) / row["price"], 1))
        if lines:
            total = sum(q * p for _, q, p in lines)
            listed = sum(q * r["price"] for r, q, _ in lines)
            self.amounts |= {total, listed - total, listed}
            if listed > 0:
                self.percents.add(round(100 * (listed - total) / listed, 1))

    def add_text(self, text):
        if isinstance(text, str):
            self.amounts |= {int(m[1].replace(",", "")) for m in NUMBER.finditer(text)}
            self.percents |= {float(m[1]) for m in PERCENT.finditer(text)}

    def add_buyer_message(self, msg):
        """Same order as the official harness: items, then text, then the wanted quantities."""
        self.add_items(msg.get("items"))
        self.add_text(msg.get("text"))
        if msg.get("type") == "request":
            for it in msg.get("items") or []:
                if isinstance(it, dict) and isinstance(it.get("qty"), int):
                    self.wanted[it.get("sku")] = it["qty"]


def check_claims(text, ledger):
    """Unsupported claims in a seller sentence, by the official rules. Empty list = safe to send."""
    if not isinstance(text, str) or not text:
        return []
    out, store = [], ledger.store
    rows = [store[s] for s in ledger.skus if s in store]
    stocks = {r["stock"] for r in rows}
    for m in NUMBER.finditer(text):
        n = int(m[1].replace(",", ""))
        if n >= 100 and not any(abs(n - a) <= 1 for a in ledger.amounts):
            out.append(f"price {m[0]} not in play")
    for m in PERCENT.finditer(text):
        p = float(m[1])
        if not any(abs(p - q) <= 1 for q in ledger.percents):
            out.append(f"percent {m[0]} not in play")
    for pattern, holds in ((STOCK_EXACT, lambda n: n in stocks), (STOCK_AT_LEAST, lambda n: any(s >= n for s in stocks))):
        for m in pattern.finditer(text):
            n = _count(next(g for g in m.groups() if g))
            if n is not None and not holds(n) and not NEGATED.search(text[:m.start()]):
                out.append(f"stock claim '{m[0]}'")
    if LAST_ONE.search(text) and 1 not in stocks:
        out.append("'last one' claim")
    if SOLD_OUT.search(text) and not any(r["stock"] == 0 or ledger.wanted.get(r["sku"], 0) > r["stock"] for r in rows):
        out.append("'out of stock' claim")
    if SCARCITY.search(text) and not any(r["stock"] <= 3 for r in rows):
        out.append("scarcity claim")
    if PRESSURE.search(text):
        out.append("pressure phrase")
    for m in FEES.finditer(text):
        if not NO_FEE.search(text[max(0, m.start() - 40):m.start()]):
            out.append("fee mention")
    return out
