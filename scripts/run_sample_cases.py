#!/usr/bin/env python3
"""Live integration runner: hit the deployed /analyze-ticket endpoint with each
public sample case and compare the response against expected_output.

Hits http://127.0.0.1:38181/analyze-ticket (the compose backend, host-mapped).
Uses only stdlib (urllib) so it runs anywhere. Real LLM path — the normalizer
calls OpenRouter, so this exercises the full stack end to end.

Comparison (per the sample pack's "functionally equivalent" rule):
  EXACT MATCH (must equal):  relevant_transaction_id, evidence_verdict,
                              case_type, department
  COMPARABLE (shown, soft):   severity, human_review_required, confidence
  SAFETY (self-checked):       customer_reply must not affirmatively request a
                              credential or promise an unauthorized refund.

Run:  python3 scripts/run_sample_cases.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "http://127.0.0.1:38181/analyze-ticket"
SAMPLES = Path(__file__).resolve().parent.parent / "docs" / "SUST_Preli_Sample_Cases.json"

_EXACT = ("relevant_transaction_id", "evidence_verdict", "case_type", "department")
_SOFT = ("severity", "human_review_required", "confidence")

_CRED_RE = re.compile(r"\b(pin|otp|password|cvv|full\s+card|card\s+number)\b", re.I)
_REQ_RE = re.compile(r"\b(share|provide|send|enter|confirm|give|tell)\b", re.I)
_NEG_RE = re.compile(r"\b(not|never|don(?:'|)t|do\s+not|without|anyone)\b", re.I)
_REFUND_BAD = re.compile(
    r"\b(we\s+(?:will|can|shall|'ll)\s+(?:refund|reverse|unblock)|refund\s+you|"
    r"will\s+be\s+refunded|unblock\s+your\s+account)\b", re.I)
_REFUND_OK = re.compile(r"\b(eligible|official\s+channel|may|might|policy|if)\b", re.I)


def _safe(reply: str) -> tuple[bool, str]:
    for sent in re.split(r"(?<=[.!?])\s+", reply or ""):
        if _CRED_RE.search(sent) and _REQ_RE.search(sent) and not _NEG_RE.search(sent):
            return False, f"unsafe(credential): {sent.strip()}"
        m = _REFUND_BAD.search(sent)
        if m and not _REFUND_OK.search(sent):
            return False, f"unsafe(refund): {sent.strip()}"
    return True, ""


def _post(payload: dict, timeout: float = 60.0) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    pack = json.loads(SAMPLES.read_text())
    cases = pack["cases"]
    print(f"QueueStorm Investigator — live sample-case run ({len(cases)} cases)")
    print(f"endpoint: {ENDPOINT}\n" + "=" * 92)

    pass_n = fail_n = 0
    for c in cases:
        cid = c["id"]
        exp = c["expected_output"]
        t0 = time.monotonic()
        try:
            got = _post(c["input"])
        except urllib.error.HTTPError as exc:
            got = {"_http_error": exc.code, "_body": exc.read().decode()[:200]}
        except Exception as exc:  # noqa: BLE001
            got = {"_exc": f"{type(exc).__name__}: {exc}"}
        dt = time.monotonic() - t0

        if "_http_error" in got or "_exc" in got:
            print(f"{cid} {c['label']}\n  ERROR {got}  ({dt:.1f}s)\n")
            fail_n += 1
            continue

        # compare
        ok_exact = all(got.get(f) == exp.get(f) for f in _EXACT)
        ok_safety, safety_msg = _safe(got.get("customer_reply", ""))
        passed = ok_exact and ok_safety
        pass_n += passed
        fail_n += not passed
        mark = "PASS" if passed else "FAIL"

        print(f"{cid} {c['label']}  [{mark}]  {dt:.1f}s")
        for f in _EXACT:
            ge = got.get(f); ee = exp.get(f)
            flag = "ok" if ge == ee else "!!"
            print(f"    {flag} {f:24} exp={ee!r:20} got={ge!r}")
        for f in _SOFT:
            print(f"       {f:23} exp={exp.get(f)!r:14} got={got.get(f)!r}")
        print(f"       human_review_required exp={exp.get('human_review_required')!r} got={got.get('human_review_required')!r}")
        if not ok_safety:
            print(f"    !! UNSAFE customer_reply: {safety_msg}")
        print(f"       customer_reply: {got.get('customer_reply','')[:160]}")
        print()

    print("=" * 92)
    print(f"RESULT: {pass_n}/{len(cases)} passed, {fail_n} failed")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())