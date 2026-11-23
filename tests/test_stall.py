"""Tests for SAUDA's rules for a buyer that has stopped moving (it keeps repeating the same bid)."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "starter_kit"), str(ROOT / "agent")]

from buyers import Buyer  # noqa: E402
from evaluate import score_one  # noqa: E402
from harness import check_items, floor_price, load_policy, load_store, run_negotiation  # noqa: E402
from sauda_seller import Seller  # noqa: E402

STORE, POLICY = load_store(ROOT / "starter_kit" / "store.csv"), load_policy(ROOT / "starter_kit" / "policy.json")
SKUS = sorted(STORE)


class Repeater(Buyer):
    """Bids `bid_ratio` of list on every line, every round, and accepts any offer at or below `limit`."""
    def respond(self, msg):
        offered = self.usable(msg.get("items")) if msg.get("type") == "offer" else None
        if offered and self.total(offered) <= self.s["reservation"] * self.listed(offered) + 1:
            return {"type": "accept", "text": "Deal."}
        bid = self.priced(self.basket, self.s["anchor"])
        return {"type": "counter", "items": bid, "text": f"My budget is ₹{self.total(bid):,}, that's it."}


def scenario(skus, ratio, limit):
    return {"id": "t", "buyer": "repeater", "items": [{"sku": s, "qty": 1} for s in skus], "reservation": limit,
            "anchor": ratio, "deadline": 10, "exponent": 1.0, "stubborn": True, "seed": 1}


def floor_ratio(skus):
    return sum(floor_price(STORE[s], POLICY) for s in skus) / sum(STORE[s]["price"] for s in skus)


def run(sc):
    res = run_negotiation(Seller(STORE, POLICY), Repeater(sc, STORE, random.Random(1)), STORE, POLICY, "t")
    return res, score_one(res, sc, STORE, POLICY)


def test_repeated_compliant_bid_is_taken_quickly():
    sku = next(s for s in SKUS if STORE[s]["stock"] > 0)
    F = floor_ratio([sku])
    sc = scenario([sku], round(F + (1 - F) * 0.5, 3), round(F + (1 - F) * 0.5, 3))
    res, sc_row = run(sc)
    assert res["ended"] == "deal" and res["rounds"] <= 3 and not res["violations"]


def test_stalled_total_above_floor_gets_a_compliant_matching_offer():
    # two lines whose floors (as a share of list) differ, so a uniform cut can leave one line under its floor
    in_stock = [s for s in SKUS if STORE[s]["stock"] > 0]
    pairs = sorted(((abs(floor_price(STORE[a], POLICY) / STORE[a]["price"] - floor_price(STORE[b], POLICY) / STORE[b]["price"]), a, b)
                    for i, a in enumerate(in_stock[:40]) for b in in_stock[i + 1:40]), reverse=True)
    _, a, b = pairs[0]
    F = floor_ratio([a, b])
    ratio = F + 0.01
    sc = scenario([a, b], ratio, ratio + 0.002)
    res, row = run(sc)
    assert not res["violations"]
    assert res["ended"] == "deal", res["ended"]
    deal = res["deal"]
    assert not check_items(deal, STORE, POLICY)                   # every line at or above its own floor
    assert sum(i["qty"] * i["unit_price"] for i in deal) <= sc["reservation"] * sum(STORE[s]["price"] for s in (a, b)) + 1


def test_repeated_bid_below_floor_never_sells_below_floor():
    sku = next(s for s in SKUS if STORE[s]["stock"] > 0)
    F = floor_ratio([sku])
    sc = scenario([sku], round(F * 0.8, 3), round(F * 0.85, 3))
    res, _ = run(sc)
    assert res["ended"] in ("seller_walked", "buyer_walked") and not res["deal"] and not res["violations"]
