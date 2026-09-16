# Buy or Wait?

Python 3.11 financial decision engine, Gemini evidence extraction, and persistent terminal chat.
Run from the repository root with `dataset/` and `code/` next to one another.
If using the archive, extract its contents into `code/` next to the provided `dataset/`.

## Setup and run

```powershell
python -m pip install -r code/requirements.txt
$env:GEMINI_MODEL = 'gemini-3.1-flash-lite'
# Set GEMINI_API_KEY through your environment/secret manager. Do not paste it into chat.
python code/main.py audit
python code/main.py preflight
python code/main.py samples
python code/main.py
```

`GEMINI_API_KEY` is required for uncached evidence. `GEMINI_MODEL` defaults to the user-selected
`gemini-3.1-flash-lite`. The application reads credentials only from environment variables;
it does not automatically load `.env` files. In a POSIX shell use `export GEMINI_MODEL=...`.
Do not expect a key set in a different terminal after this process started to appear here.

All live API attempts, including preflight, evidence review, chat, and retries, are
limited to 15 calls/minute. Calls start at least 4.05 seconds apart; the program sleeps
and prints the remaining delay when needed. A locked timestamp file shares the limit
across processes and restarts in this checkout. Other programs/checkouts are not covered.
Calls that already take longer than 4.05 seconds need no extra pacing sleep.

Every returned API response is immediately saved, with redacted text and token usage,
to `.cache/api_responses/`. Valid structured results are cached in `.cache/gemini/`
and reused on rerun without calling Gemini or waiting for a rate-limit slot.
Malformed responses are saved for inspection but are not reused as valid financial evidence.
Credentials and hidden model reasoning are never written to these response records.

Bare invocation processes all evaluation requests, verifies each decision and atomically
writes root-level `output.csv`. Existing output is never replaced by a partial run.
An error exit code of 2 means the latest run is incomplete: any older output is stale.
Use `--dataset <directory>` and `--output <path>` to override data/output locations.

```powershell
python code/main.py batch --offline --diagnose
python code/main.py samples --offline --diagnose
python -m unittest discover -s code/tests -v
```

`--offline` permits validated local cache hits only. It never guesses image amounts or
ignores relevant messages. `--diagnose` continues through errors and writes diagnostics
under `code/evaluation/`, but produces a submission CSV only if every request succeeds.
Sample output fields are read only by the sample comparison path, never by the prediction engine.

## Saved terminal chat

```powershell
python code/main.py chat --user-id user_01
python code/main.py chat --user-id user_01 --session-id <printed-session-id>
```

Commands: `/request request_01`, `/history`, `/export`, `/exit`.
Natural-language requests are interpreted by Gemini; all calculations run through the same
engine and validator as batch mode. Missing amounts and dates trigger a clarification.
Hypothetical requests use the selected user's home currency and profile snapshot date.
They have no invented seller installment offers, and partial payment defaults to disallowed
unless explicitly supplied. Use `/request` to select an actual request with its supplied offers.
Changing to a date outside the supplied snapshot dates requires fresh profile data and is refused.

Each user and assistant message is immediately appended and flushed to
`chat_history/<session_id>.jsonl`. Decisions and selected request snapshots are saved with
assistant responses. Resume refuses another user's session. An incomplete trailing record
is preserved; recovery writes a continuation session instead of truncating the original.
Chat transcripts and caches are local, gitignored, and excluded from the code archive.
Credential, email, account-number, and explicitly labeled personal fields are redacted.
Free-form names cannot be reliably detected by regex: avoid entering unnecessary personal data.

Development conversations are captured by the coding-agent workflow in root `log.txt`.
The terminal application cannot read the Codex UI automatically. Earlier log entries contain
summaries, while subsequent workflow entries can include full visible responses.

```powershell
python code/main.py export
```

This writes `chat_transcript.txt` with the development log and separate saved app conversations.
No hidden reasoning is captured.

## Financial assumptions and auditability

- The profile balance is the opening balance on the request date; settled records on or before
  that date are history and are not replayed. Pending debits are reserved immediately.
- Forecasting covers the request date through day 90 inclusive. Same-day essential debits are
  checked before posted income; requested discretionary payments happen after those events.
- Repeated expenses need at least two observed complete months and recent activity. The maximum
  total of the latest three complete months is the conservative monthly budget. Multiple variable
  purchases are distributed daily; a single recurring bill uses its historical median day.
- Supported regular salary streams are separated by description, use the minimum of recent
  regular payments and stop after final payroll or explicit cancellation. One-off/uncertain
  income is not extrapolated. Currency conversion always needs the supplied dated rate.
- Exact duplicate representations and settled/cancelled authorization successors are reconciled;
  a lifecycle link alone does not eliminate a separate cash transaction.
- Gemini facts preserve message/image provenance. Unresolved required evidence stops the affected
  request. The cache key covers model, inputs, image bytes, prompt, and schema versions.
  An unresolved extraction receives one cached review against the original evidence:
  informational pending payouts need no amount, while missing required bills remain blocking.
  Platform payouts and app earnings are not extrapolated as guaranteed salary.
- Candidate plans follow supplied schedules and user preferences. Reductions use allowed minimum
  amounts; stops/reductions are mutually exclusive per event, with at most three changed events.
  Among equally ranked spending-change plans, fewer changes then stable action text break ties.
- Safe-now capacity and earliest full-payment date are calculated without spending changes.
  The validator independently replays the serialized chosen plan and spending actions.

These conservative recurrence rules are explicit heuristics, not hidden ground truth. Passing
the safety validator establishes consistency with the reconstructed forecast, not prediction
accuracy. Inspect sample discrepancies before submission; do not claim perfect sample accuracy.

## Usage and submission

Each batch run writes `code/evaluation/usage_report.md` and a JSON usage ledger. Reports distinguish
live calls, local cache hits, historical extraction tokens/costs, and incomplete runs.
Chat usage is written beside its chat history and is excluded from batch totals.

For the default model, standard paid-tier estimates use USD 0.25/million input tokens and
USD 1.50/million output tokens including thinking, verified from
[Google pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite) on 2026-09-12.
Override with `GEMINI_INPUT_USD_PER_MILLION` and `GEMINI_OUTPUT_USD_PER_MILLION` if needed.
Other models require explicit rates. Estimates do not assume a free tier or provider cache discount.

```powershell
python code/package.py
```

After a complete run, packages source, tests, prompts, pinned dependencies, README, and the usage
report into `code.zip`, and exports `chat_transcript.txt`. Submit those alongside `output.csv`.
`python code/package.py --allow-incomplete` creates a development archive explicitly marked
incomplete by its usage report; it does not create missing predictions or make a run submission-ready.

Current verification without an API key can validate the schema, unit/integration tests, and
requests without model evidence. Live model quality, complete sample accuracy, and the final
250-row output still require a successful keyed run.
