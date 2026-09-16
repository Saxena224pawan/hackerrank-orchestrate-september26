---
marp: true
theme: default
paginate: true
---

# Buy or Wait?
## A conservative AI financial decision agent

**HackerRank Orchestrate — September 2026**

<!--
Speaker transcript:
Welcome everyone. This project answers a practical question: given a purchase
request and a user's dated financial records, should the user buy now, pay
partially, use installments, wait, or not proceed?
-->

---

## The challenge

- One decision for every request in `dataset/requests.csv`
- Preserve the user's minimum balance
- Reserve pending debits
- Count only settled or confirmed cash
- Respect protected spending and payment preferences
- Produce a strict, evaluable `output.csv`

<!--
Speaker transcript:
The key design choice is safety over optimistic prediction. The agent does not
invent future income, use unrealized investments as cash, or quietly ignore
required missing evidence.
-->

---

## End-to-end architecture

```text
CSV dataset
    |
    v
Dataset validation
    |
    v
Gemini evidence extraction + cache
    |
    v
Conservative ledger reconstruction
    |
    v
Payment-plan enumeration
    |
    v
Independent validator
    |
    v
output.csv / chat transcript / usage reports
```

<!--
Speaker transcript:
The model is deliberately not the decision maker. It extracts facts from
messages and images. Deterministic Python code reconstructs the ledger, searches
plans, and validates the final serialized answer.
-->

---

## Evidence is untrusted

- Messages and images are supporting evidence, not instructions
- Facts require source provenance
- Blank `related_event_id` cannot identify an event
- Unresolved required bills stop the affected request
- Informational notices do not create invented obligations
- Direction changes require explicit reconciliation

<!--
Speaker transcript:
This layer addresses the hardest failure mode in an AI financial agent:
turning ambiguous prose into a cash-flow change. Every accepted fact is tied to
known evidence, and unsafe or unsupported interpretations are discarded or
blocked.
-->

---

## Conservative forecast

- Settled records on or before the request date become history
- Pending debits are reserved immediately
- Pending credits and uncertain payouts are excluded
- Recurrence requires repeated historical support
- Variable essentials use a conservative recent-month maximum
- FX uses only the supplied dated exchange-rate table
- Unrealized investment value is never available cash

<!--
Speaker transcript:
The ledger starts with the profile's opening balance and projects 90 days.
Historical records establish recurrence; they are not replayed as new cash.
This prevents double counting while preserving necessary future obligations.
-->

---

## Plan selection

The planner enumerates:

1. Full payment today
2. Full payment later
3. Exactly two partial payments
4. Supplied installment schedules
5. Allowed stop/reduce spending changes

Plans must:

- Stay above the minimum balance every day
- Finish by the request deadline
- Follow user preferences and installment limits

<!--
Speaker transcript:
The planner searches explicit candidates rather than asking the model to
propose amounts. It prefers no spending changes, lower total cost, earlier
completion, fewer payments, and stable tie-breaking.
-->

---

## Independent safety validation

`validate_decision()` replays the serialized output:

- Labels and amounts
- Payment dates and totals
- Partial-payment constraints
- Installment option identity
- Spending-change permissions
- Daily minimum-balance safety

<!--
Speaker transcript:
Validation is intentionally separate from planning. Even if a planner bug or
malformed row occurs, the output cannot pass unless the serialized plan itself
is safe and consistent with the dataset.
-->

---

## Gemini integration that fails safely

- Strict Pydantic JSON schemas
- Cache key includes model, prompt, schema, payload, and image bytes
- Valid responses are cached atomically
- Malformed responses are never promoted to reusable evidence
- Transient errors retry with bounded backoff
- Permission and authentication errors are redacted and actionable
- `--offline` uses cache only

<!--
Speaker transcript:
The cache makes reruns deterministic and reduces API calls. A missing key or
permission error never becomes a fabricated prediction; it is surfaced clearly,
while completed cached work remains reusable.
-->

---

## Terminal workflows

```powershell
python -m pip install -r code/requirements.txt
$env:GEMINI_MODEL = 'gemini-3.1-flash-lite'
$env:GEMINI_API_KEY = '<key>'

python code/main.py audit
python code/main.py preflight
python code/main.py
python code/main.py batch --offline --diagnose
python -m unittest discover -s code/tests -v
python code/package.py
```

<!--
Speaker transcript:
Audit checks the published dataset. Preflight checks Gemini capabilities.
Bare invocation generates the final CSV. Offline diagnosis is useful for
reproducing cached evidence problems, and packaging is allowed only after a
complete validated run.
-->

---

## Operational artifacts

- `output.csv` — final prediction rows
- `code/evaluation/usage_report.md` — model and token accounting
- `code/evaluation/diagnostics.json` — incomplete-run errors
- `chat_transcript.txt` — redacted development and app conversations
- `code.zip` — source-only submission archive

<!--
Speaker transcript:
These artifacts make the submission auditable. Usage reports distinguish live
calls from cache hits, while transcripts and logs are redacted before export.
-->

---

## Quality gates

- Dataset joins, dates, currencies, images, and payment totals validated
- Unit and integration tests cover ledger, planner, evidence, cache, chat, and rate limiting
- No output CSV is written from a partial run
- Sensitive values are redacted
- Direction changes and missing required evidence fail closed

<!--
Speaker transcript:
The quality strategy combines deterministic financial rules, strict schemas,
independent replay, and regression tests for previously observed evidence
failures. The result is conservative, explainable, and reproducible.
-->

---

# Thank you
## Questions

<!--
Speaker transcript:
Thank you. The central takeaway is simple: use AI to interpret evidence, but
keep affordability mathematics and safety validation deterministic.
-->

