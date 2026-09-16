# Buy or Wait? Speaker Transcript

## Slide 1 — Buy or Wait?

Welcome everyone. This project answers a practical question: given a purchase
request and a user's dated financial records, should the user buy now, pay
partially, use installments, wait, or not proceed?

## Slide 2 — The challenge

The key design choice is safety over optimistic prediction. The agent does not
invent future income, use unrealized investments as cash, or quietly ignore
required missing evidence.

## Slide 3 — End-to-end architecture

The model is deliberately not the decision maker. It extracts facts from
messages and images. Deterministic Python code reconstructs the ledger, searches
plans, and validates the final serialized answer.

## Slide 4 — Evidence is untrusted

This layer addresses the hardest failure mode in an AI financial agent: turning
ambiguous prose into a cash-flow change. Every accepted fact is tied to known
evidence, and unsafe or unsupported interpretations are discarded or blocked.

## Slide 5 — Conservative forecast

The ledger starts with the profile's opening balance and projects 90 days.
Historical records establish recurrence; they are not replayed as new cash. This
prevents double counting while preserving necessary future obligations.

## Slide 6 — Plan selection

The planner searches explicit candidates rather than asking the model to propose
amounts. It prefers no spending changes, lower total cost, earlier completion,
fewer payments, and stable tie-breaking.

## Slide 7 — Independent safety validation

Validation is intentionally separate from planning. Even if a planner bug or
malformed row occurs, the output cannot pass unless the serialized plan itself
is safe and consistent with the dataset.

## Slide 8 — Gemini integration that fails safely

The cache makes reruns deterministic and reduces API calls. A missing key or
permission error never becomes a fabricated prediction; it is surfaced clearly,
while completed cached work remains reusable.

## Slide 9 — Terminal workflows

Audit checks the published dataset. Preflight checks Gemini capabilities. Bare
invocation generates the final CSV. Offline diagnosis is useful for reproducing
cached evidence problems, and packaging is allowed only after a complete
validated run.

## Slide 10 — Operational artifacts

These artifacts make the submission auditable. Usage reports distinguish live
calls from cache hits, while transcripts and logs are redacted before export.

## Slide 11 — Quality gates

The quality strategy combines deterministic financial rules, strict schemas,
independent replay, and regression tests for previously observed evidence
failures. The result is conservative, explainable, and reproducible.

## Slide 12 — Thank you

Thank you. The central takeaway is simple: use AI to interpret evidence, but
keep affordability mathematics and safety validation deterministic.

