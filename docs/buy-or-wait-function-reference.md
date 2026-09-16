# Buy or Wait? Function Reference

This document explains the runtime implementation in `code/`. The system is
deterministic for financial calculations; Gemini is used only to extract
evidence and parse free-form chat requests.

## Runtime flow

1. `main.main()` parses the command line.
2. `app.App` loads and validates the dataset.
3. `evidence.Gemini.extract()` resolves relevant messages and images.
4. `ledger.reconstruct()` builds a conservative 90-day forecast.
5. `planner.decide()` enumerates safe payment candidates.
6. `validate.validate_decision()` independently replays the selected plan.
7. `App.batch()` writes `output.csv` only when every request is valid.

## `code/main.py`

### `main(argv=None)`

CLI entry point. Builds the argument parser, constructs `App`, and dispatches
`batch`, `samples`, `chat`, `audit`, `preflight`, or `export`. Converts expected
`DataError`, `OSError`, and `ValueError` exceptions into a non-zero exit code.

## `code/app.py`

### `App.__init__(root, dataset=None, offline=False, mode='batch')`

Stores the repository root, loads `Dataset`, and creates the `Gemini` adapter.
`offline=True` permits validated cache reads but never fabricates evidence.

### `App.evaluate(request)`

Runs one request end to end: request validation, evidence extraction, ledger
reconstruction, plan selection, and independent decision validation. Returns the
serialized output row or raises `DataError`.

### `App.batch(samples=False, diagnose=False, output=None)`

Evaluates every sample or evaluation request. Collects diagnostics, writes usage
reports, and writes the requested output atomically only if all rows succeed.
With `diagnose=True`, it continues after errors so all failing request IDs are
reported.

### `Chat.__init__(app, user_id, session_id=None)`

Validates the user, opens a persistent `History`, and restores the selected
request and latest decision from saved messages.

### `Chat.handle(text)`

Persists the user message, dispatches it through `_handle`, converts expected
calculation errors into a user-facing response, and persists the assistant
response and decision.

### `Chat._handle(text)`

Implements `/request`, `/history`, `/export`, and `/exit`. For natural language,
asks Gemini for a typed `ChatIntent`, requires amount/date/deadline fields, then
uses the same `App.evaluate()` engine as batch mode.

### `Chat.run()`

Runs the interactive terminal loop until `/exit`, EOF, or Ctrl+C. Always writes
the chat usage report in `finally`.

### `describe(row)`

Formats a decision row into readable chat output with status, payment plan,
earliest full-payment date, and spending changes.

## `code/data.py`

### `Dataset.__init__(path)`

Loads all CSV tables, builds indexes and per-user groupings, validates joins,
dates, amounts, statuses, images, exchange rates, and payment-option totals.

### `Dataset.index(name, key)`

Builds a dictionary keyed by a required column. Rejects empty or duplicate keys.

### `Dataset.validate()`

Enforces the dataset contract: valid profiles and requests, known event states
and directions, same-user evidence links, existing image files, valid FX rates,
and mathematically consistent payment options.

### `Dataset.validate_request(r)`

Validates request amount, request/deadline ordering, and the boolean partial
payment flag.

### `Dataset.image_path(row)`

Maps an image ID to `dataset/media/images/<image_id>.png` and rejects path
traversal outside the image directory.

### `Dataset.convert(amount, currency, home, when)`

Converts a monetary amount to the user's home currency using the supplied dated
exchange rate. Missing rates raise `DataError`; live FX calls are never made.

## `code/domain.py`

### `DataError`

Application-specific exception for invalid data, unsafe plans, unavailable
evidence, and provider failures.

### `money(value)`

Parses a finite, non-negative `Decimal`. Rejects invalid, infinite, and negative
values.

### `fmt(value)`

Quantizes a decimal to cents and removes unnecessary trailing zeroes for output.

### `floor_money(value)`

Rounds a decimal down to cents, used when a safe payment must not exceed capacity.

### `day(value)`

Parses an ISO `YYYY-MM-DD` date and raises `DataError` for invalid dates.

### `choices(value)`

Splits a pipe-delimited profile setting into a set of enabled choices.

### `Fact`

Pydantic schema for one evidence amendment. It carries provenance, optional event
or category targeting, action, amount, date, direction, status, and explanation.

### `Evidence`

Pydantic schema containing extracted `facts` and unresolved evidence messages.

### `ChatIntent`

Pydantic schema for natural-language chat classification and optional request
fields.

### `Flow`

Immutable signed cash-flow record. Credits are positive and debits negative.

### `Adjustment`

Immutable spending-change candidate. `text()` serializes it as `stop:<event_id>`
or `reduce_to:<event_id>:<amount>`.

### `Adjustment.text(self)`

Returns the required output syntax for a spending adjustment.

### `Forecast`

Mutable aggregate containing opening balance, minimum balance, projected flows,
adjustable events, and evidence notes.

### `Candidate`

Immutable payment-plan candidate containing method, payments, adjustments, and an
option ID.

### `Candidate.rank(self)`

Provides the stable preference ordering: no spending changes, lower total cost,
earlier start, fewer payments, stable option ID, and stable action text.

## `code/evidence.py`

### `resolve_unapproved_bonus(result, messages)`

Normalizes an unresolved bonus/payout extraction when supporting messages show
the money is unapproved, pending, variable, or otherwise not spendable. It
preserves unresolved errors for required expenses and unsupported certainty.

### `atomic_json(path, value)`

Writes JSON to a temporary sibling file, flushes and syncs it, then replaces the
target. Prevents partial cache or report records.

### `probe_png()`

Creates a small valid RGB PNG in memory for the Gemini preflight image test.

### `api_error(exc, purpose, model)`

Builds a redacted, actionable provider error. Adds specific hints for HTTP
authentication, permission, model, schema, and quota failures without exposing
keys.

### `Gemini.__init__(root, mode='batch', offline=False)`

Reads model/key settings from environment variables, initializes the cache,
usage ledger, and shared rate limiter. Credentials are never loaded from files.

### `Gemini.call(payload, schema, images=(), purpose='evidence')`

Computes a cache key over prompt version, model, schema, payload, and image bytes.
Reads validated cached results first; otherwise calls Gemini with strict JSON
schema output, records redacted usage, validates the response, and atomically
stores only valid results. Retries transient provider failures with bounded
backoff.

### `Gemini.preflight()`

Sends the generated PNG and an empty-evidence task to verify image input and
structured output before live extraction. Runs once per adapter instance.

### `Gemini.extract(dataset, request)`

Selects request-relevant messages and images, includes user events, calls Gemini,
and performs one evidence review when unresolved items are returned. Enforces
source and same-user event provenance, rejects unsafe direction changes, ignores
non-actionable informational notices, and blocks unresolved required evidence.

### `Gemini.intent(text, history, current)`

Parses natural-language chat into the strict `ChatIntent` schema.

### `Gemini.report(path, requests, complete, errors=())`

Aggregates live/cache token usage and estimated cost into Markdown and JSON usage
reports. Includes incomplete-run diagnostics and never includes credentials.

## `code/ledger.py`

### `month_add(d, n, wanted_day=None)`

Adds calendar months while clamping the day to the destination month's length.

### `monthly_dates(start, end, anchor)`

Yields monthly dates in a forecast window using a stable day-of-month anchor.

### `reconstruct(dataset, request, evidence)`

Applies validated evidence to user events, rejects direction changes, excludes
non-cash/unrealized and unsupported income, reconciles lifecycle successors,
converts currencies, separates historical settled records, infers conservative
recurring expenses and salary, and returns a 90-day `Forecast`.

## `code/planner.py`

### `changed_flows(forecast, changes)`

Applies stop/reduction adjustments to recurring debit flows.

### `balances(forecast, payments=(), changes=())`

Replays daily balances in conservative order: essential flows, then requested
payments. Returns each day's balance and running low point.

### `feasible(forecast, payments=(), changes=())`

Checks payment dates, non-negative amounts, forecast bounds, and minimum-balance
compliance.

### `capacity(forecast, requested)`

Calculates safe payment capacity today with cent-level binary search and finds
the earliest date a full payment is feasible without spending changes.

### `adjustment_sets(forecast, profile)`

Generates allowed zero-to-three event adjustment combinations while respecting
protected categories, flexibility, and minimum reducible amounts.

### `candidates(forecast, request, profile, options, safe, earliest, changes)`

Yields eligible full-payment, wait, partial-payment, and supplied installment
candidates, enforcing user preferences, deadlines, limits, and exact schedules.

### `decide(forecast, request, profile, options)`

Ranks candidates, selects the best safe plan, constructs the required output
fields and explanation, or returns a conservative `not_recommended` result.

## `code/validate.py`

### `require(condition, message)`

Raises `DataError` when a validation condition is false.

### `validate_decision(row, request, profile, options, forecast)`

Validates output labels and amounts, payment-plan syntax and totals, deadlines,
minimum balance, allowed methods, spending-change syntax, and consistency with
the independently replayed forecast.

## `code/history.py`

### `redact(text)`

Removes credentials, bearer tokens, private keys, email addresses, account
numbers, and other sensitive fields from persisted text.

### `scrub(value)`

Recursively applies `redact` to strings inside dictionaries and lists.

### `History.__init__(root, user_id, session_id=None)`

Loads append-only JSONL history, validates session ownership, and preserves a
truncated final record by creating a continuation session.

### `History.append(...)`

Redacts, serializes, flushes, and fsyncs one user or assistant message.

### `History.export(self)`

Writes the current session as a readable text transcript.

### `render(messages)`

Formats message records with timestamps and roles.

### `export_all(root)`

Combines the development log and all saved application sessions into the root
`chat_transcript.txt`.

## `code/rate_limit.py`

### `locked(path)`

Context manager that serializes access to the shared rate-limit timestamp file.

### `RateLimiter.__init__(root, clock=None, sleep=None, announce=print)`

Configures the persistent limiter and injectable timing functions used by tests.

### `RateLimiter.wait()`

Ensures live calls are at least 4.05 seconds apart and remain within the
15-calls-per-minute contract across processes.

## `code/package.py`

### `package(root, allow_incomplete=False)`

Checks that a complete usage report and `output.csv` exist, archives source-only
submission files, and exports the transcript. Refuses incomplete archives unless
explicitly overridden.

