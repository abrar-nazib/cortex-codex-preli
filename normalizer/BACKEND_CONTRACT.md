# BACKEND_CONTRACT.md
# Internal contract between backend service and normalizer (AI) service
# Status: DRAFT — must be confirmed by backend teammate before implementation

---

## 1. Who calls whom

Backend (FastAPI/Django) → calls → Normalizer (AI service)
Backend re-exposes the public POST /analyze-ticket to the judge harness.
Normalizer is INTERNAL only — never directly hit by the judge.

---

## 2. Internal endpoint the backend will call

Method:  POST
URL:     http://normalizer:9000/analyze-ticket
(docker-compose service name: normalizer, internal port: 9000)

Host port for my own testing: http://localhost:9000/analyze-ticket
Swagger UI for my own testing: http://localhost:9000/docs

---

## 3. Request body backend sends to normalizer

Backend forwards the judge's request to normalizer as-is.
No transformation. Exact shape from Problem Statement Section 5.

{
  "ticket_id":           "TKT-001",              // string, REQUIRED
  "complaint":           "I sent 5000 taka...",   // string, REQUIRED
  "language":            "en",                    // string, optional: en | bn | mixed
  "channel":             "in_app_chat",           // string, optional: in_app_chat | call_center | email | merchant_portal | field_agent
  "user_type":           "customer",              // string, optional: customer | merchant | agent | unknown
  "campaign_context":    "boishakh_bonanza_day_1",// string, optional
  "transaction_history": [                        // array, optional (may be empty)
    {
      "transaction_id": "TXN-9101",              // string
      "timestamp":      "2026-04-14T14:08:22Z",  // string, ISO 8601
      "type":           "transfer",              // string: transfer | payment | cash_in | cash_out | settlement | refund
      "amount":         5000,                    // number, BDT
      "counterparty":   "+8801719876543",         // string: phone, merchant ID, or agent ID
      "status":         "completed"              // string: completed | failed | pending | reversed
    }
  ],
  "metadata": {}                                 // object, optional
}

---

## 4. Response body normalizer returns to backend

Backend forwards this response to the judge unchanged.
No transformation. Exact shape from Problem Statement Section 6.

{
  "ticket_id":                "TKT-001",         // string, REQUIRED — must echo request value
  "relevant_transaction_id":  "TXN-9101",        // string or null, REQUIRED
  "evidence_verdict":         "consistent",      // enum, REQUIRED
  "case_type":                "wrong_transfer",  // enum, REQUIRED
  "severity":                 "high",            // enum, REQUIRED
  "department":               "dispute_resolution", // enum, REQUIRED
  "agent_summary":            "Customer reports sending 5000 BDT via TXN-9101 to wrong recipient.", // string, REQUIRED
  "recommended_next_action":  "Verify TXN-9101 details and initiate dispute process.", // string, REQUIRED
  "customer_reply":           "We have noted your concern about TXN-9101 and will investigate through official channels.", // string, REQUIRED
  "human_review_required":    true,              // boolean, REQUIRED
  "confidence":               0.9,               // number 0.0-1.0, optional
  "reason_codes":             ["wrong_transfer", "transaction_match"] // array of strings, optional
}

---

## 5. Enums — exact values, zero tolerance for variants

evidence_verdict:
  consistent
  inconsistent
  insufficient_data

case_type:
  wrong_transfer
  payment_failed
  refund_request
  duplicate_payment
  merchant_settlement_delay
  agent_cash_in_issue
  phishing_or_social_engineering
  other

severity:
  low
  medium
  high
  critical

department:
  customer_support
  dispute_resolution
  payments_ops
  merchant_operations
  agent_operations
  fraud_risk

---

## 6. HTTP status codes normalizer returns

200  — successful analysis, response body conforms to schema above
400  — malformed input (invalid JSON, missing required fields)
      body: { "error": "non-sensitive message" }  — NO stack traces, NO secrets
422  — semantically invalid input (e.g. empty complaint string)
      body: { "error": "non-sensitive message" }
500  — internal error
      body: { "error": "non-sensitive message" }  — NO stack traces, NO secrets

The normalizer NEVER crashes and stops responding.
A 400 or 500 with a clean error body is acceptable.
A process exit or hung response is NOT acceptable.

---

## 7. Health check

Method:  GET
URL:     http://normalizer:9000/health
Returns: { "status": "ok" }
Within:  60 seconds of service start

---

## 8. Timeout contract

Normalizer guarantees response within 10 seconds under normal load.
Absolute maximum: 25 seconds (keeps backend safely inside judge's 30s limit).
If LLM times out internally, normalizer falls back to rules and still responds.
Backend should set its own upstream timeout to 25 seconds when calling normalizer.

---

## 9. Docker networking

Service name in docker-compose: normalizer
Internal URL backend uses:      http://normalizer:9000
Host port for normalizer dev:   9000 → 9000 (no host-port mapping in compose; only reachable on the compose network)
Backend must be in the same docker-compose network as normalizer.
Network name (confirm with backend): [TBD — e.g. app-network]

---

## 10. Environment variables normalizer needs
CHECK .env.example

---

## 11. What backend does NOT need to do

- Backend does not transform or validate the AI response fields.
- Backend does not call OpenRouter directly.
- Backend does not implement any classification logic.
- Backend forwards judge request → gets normalizer response → returns to judge.

---

## 12. Open questions for backend teammate (answer before coding)

[ ] Confirm internal URL: http://normalizer:8000/analyze-ticket — correct?
[ ] Confirm docker-compose network name we'll both join.
[ ] Does backend do any field transformation, or pure passthrough?
[ ] Does backend add any fields before forwarding to judge (e.g. timestamps)?
[ ] Backend timeout when calling normalizer — set to 25s?
[ ] Any auth header needed on internal call, or open on internal network?
[ ] Confirm backend will create docker-compose.yaml first, I add normalizer block.

---

## 13. Confirmation log

Date confirmed: ___________
Confirmed by (backend teammate name): ___________
Changes from draft: ___________