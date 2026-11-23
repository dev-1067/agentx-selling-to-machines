"""Rezolve Labs integration point for SAUDA.

SAUDA must use at least one Rezolve Labs capability. The two that fit this agent are:

* TraceWare commerce claim verification: checks an agent's claims against its recorded tool
  actions and returns a decision with evidence. SAUDA records every price and stock lookup it makes
  (the "tool actions") and sends each outgoing sentence for verification before it is sent.
* Managed knowledge retrieval: holds the catalogue and policy the seller quotes from.

Status: Rezolve Labs credentials (the competition credit coupon) had not been issued when this was
written, and the API reference is only available inside a Rezolve Labs account. So this module
defines the interface and the exact data SAUDA sends, and runs the local verifier (sauda_guard,
which re-implements the official truthfulness rules) until credentials are configured:

    REZOLVE_API_KEY=...            # from the Rezolve Labs account
    REZOLVE_TRACEWARE_URL=...      # TraceWare verification endpoint from the API reference

When both are set, `TraceWareVerifier.verify` posts the claim and its evidence and uses Rezolve's
decision; the local verifier still runs first, so a network error can never let an unchecked claim
through. Nothing in the official evaluator path depends on the network.
"""
import json
import os
import urllib.request

from sauda_guard import check_claims


class LocalVerifier:
    name = "local (official truthfulness rules)"

    def verify(self, text, ledger, tool_actions):
        problems = check_claims(text, ledger)
        return {"approved": not problems, "problems": problems, "verifier": self.name}


class TraceWareVerifier:
    """Sends each claim with its supporting tool actions to Rezolve TraceWare."""
    name = "Rezolve TraceWare"

    def __init__(self, url, key, timeout=2.0):
        self.url, self.key, self.timeout = url, key, timeout
        self.local = LocalVerifier()

    def verify(self, text, ledger, tool_actions):
        first = self.local.verify(text, ledger, tool_actions)
        if not first["approved"]:
            return first                      # never send a claim the local rules already reject
        payload = {"claim": text, "tool_actions": tool_actions, "context": {"currency": "INR"}}
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                decision = json.loads(r.read())
            approved = bool(decision.get("approved", decision.get("decision") in ("pass", "approved", "verified")))
            return {"approved": approved, "problems": [] if approved else [decision], "verifier": self.name,
                    "evidence": decision}
        except Exception as e:                # fail closed: keep the locally verified result
            return dict(first, verifier=f"{self.local.name} (TraceWare unreachable: {type(e).__name__})")


def make_verifier():
    url, key = os.environ.get("REZOLVE_TRACEWARE_URL"), os.environ.get("REZOLVE_API_KEY")
    return TraceWareVerifier(url, key) if url and key else LocalVerifier()
