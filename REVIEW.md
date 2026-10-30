# Reliability, efficiency, and usability review

Implementation follow-up: these findings are addressed in the current custom
checkout. See [CUSTOM.md](CUSTOM.md) for behavior, configuration, and practical
limits. The findings below describe the original reviewed state.

Reviewed 2026-10-28 against the current checkout, including the uncommitted
optional-send-approval change. This is a source review with isolated mocked
reproductions, not a live-provider, desktop accessibility, or load test.
No real mailbox was accessed. Product code was not changed for this review.

## Fix first

### 1. Durable send tracking and caller-provided idempotency (high)

`src/gigamail/server.py:117–138` executes immediate mail directly, without
creating an approval or execution record. Repeating a call after a lost
response can send it again. The pending-reply lookup cannot discover completed
immediate sends, and a check followed by a send is not an atomic claim.

Add a durable outbound-operation record for both approval modes, keyed by
account and a caller-provided idempotency key. Claim before execution and
persist the result. Track queued, sending, accepted, failed, and unknown
outcomes. A disconnect after submission is unknown, not proof of failure;
do not automatically resend it. Preserve deliberate repeated sends by letting
the caller supply a new key. This does not make SMTP exactly-once.

Acceptance: repeated and concurrent calls with the same key invoke the provider
once; restart recovery preserves an uncertain operation without resending it.

### 2. Preserve successful sends when outcome logging fails (high)

`src/gigamail/server.py:135` calls `audit()` after the provider completes,
outside the exception handler. `policy.audit()` writes directly to disk and
can raise. Reproduced with a successful mocked provider followed by an
`OSError` from the final audit: the caller receives an exception after the
message was sent. This issue exists in the new immediate-send code.
The approved path also has post-execution persistence/audit failure exposure.

Use a common outcome-recording strategy across MCP, console, and watcher.
The console already has a useful `_audit_result` helper in
`src/gigamail/http_api/common.py:28`. Surface an audit warning while preserving
the known provider result, with durable reconciliation where available.

Acceptance: a final audit failure never turns provider acceptance into a
retryable send failure; a failure before submission remains distinguishable.

### 3. Scope reply identity to account and folder (high)

`src/gigamail/core/reply_guard.py:53` takes only `message_id`; its approval
lookup compares only that id. `src/gigamail/core/rules.py:377` also queries
handled rows by message id. IMAP UIDs can repeat across folders and accounts.
Reproduced: a pending reply in account 2 / Archive matches a lookup for UID
42 without any account or folder check. `server._reply_already_pending` can
then return an unrelated approval preview and request id.

Pass account and normalized folder throughout the lookup and store schema;
include IMAP UIDVALIDITY where the backend exposes it. Keep equivalent scope
in an eventual outbound-operation key. Add cross-account, cross-folder, and
concurrent-reply regression tests.

### 4. Bound network operations and report failures accurately (high)

`src/gigamail/core/mail.py` has some 30-second list timeouts but omits them
from message reads (line 42), attachment metadata reads (line 54), sends
(line 140), search, and multiple writes. SMTP constructors at
`src/gigamail/core/imap_client.py:1610` and `:1614` also omit explicit timeouts.
Slow providers can occupy workers indefinitely.

Separately, `mail.py:79–82` ignores PATCH response status. Reproduced: a Graph
403 is reported as success by `mail_router.set_read_status` at line 457.

Use a shared provider transport with explicit connect/read deadlines, consistent
status checking, structured errors, and bounded retry/backoff for safe reads.
Keep send retry policy separate because a timeout can follow acceptance.

Acceptance: hangs terminate within configured bounds; 401/403/429/5xx outcomes
are represented correctly; submission uncertainty never triggers blind resend.

## Workflow correctness

### 5. Preserve threading when replying with attachments (medium)

`src/gigamail/core/mail.py:129` chooses the reply endpoint only when there
are no attachments. With attachments it builds a new `sendMail` payload and
drops `reply_to_id`. A mocked request confirmed that the outgoing payload
contains no original-message reference. This conflicts with the tool's
promise to reply in the existing thread.

Implement a provider-supported reply-draft / attachment / send workflow,
verifying the current provider contract before implementation. Test replies
with and without attachments against the same threading expectation.

### 6. Make reads side-effect free (medium)

`src/gigamail/core/mail.py:72–75` marks a Microsoft message read whenever it
is fetched. Agent reads and approval previews can therefore clear unread
status without an explicit mark-read action. The IMAP reader intentionally
uses BODY.PEEK, so behavior also differs between providers.

Remove this side effect from the core fetch operation. Have the desktop UI
explicitly mark a message read when the person opens it, while MCP reads,
background processing, and previews remain observational.

### 7. Reject malformed explicit account IDs (medium)

`src/gigamail/core/mail_router.py:34–37` silently selects the active account
if an explicitly supplied account id cannot be converted to an integer.
Numeric-but-missing ids already fail without this fallback. Typed API
boundaries reduce exposure, but internal and legacy callers still reach the
router directly.

Raise a clear invalid-account error for malformed explicit ids. Only omitted
ids should select the active account; test every provider-facing entry point.

### 8. Never silently drop an attachment during send construction (medium)

`src/gigamail/core/imap_client.py:1593–1603` catches attachment construction
errors, prints them, then proceeds to SMTP. A malformed attachment can turn
into an apparently successful email without the expected document.

Validate and build all attachments before opening SMTP; abort the entire
operation on an invalid attachment and identify the offending filename.
Apply the same policy to Graph's skipped empty attachment data. Add malformed
base64, missing-field, and invalid-MIME-type tests.

## Efficiency and usability improvements

### 9. Reuse provider connections and avoid overlapping polling

The Graph backend uses standalone `requests.get/post/...` calls. A scoped,
pooled transport can reuse connections and centralize deadlines and telemetry.
Avoid mutable account authorization headers in a shared session.

`console/main.js:229–239` runs async polling from setInterval without an
in-flight guard. If a check takes longer than the interval, another can start.
`console/renderer_automations.js:444` also retrieves pending approvals, recent
approvals, and rules for badge refreshes every 30 seconds.

Schedule the next poll after completion, add cancellation/backoff, and use a
small status-summary endpoint for badges. Measure request count, provider
latency, and overlapping work before and after. No performance benchmark was
run for this review.

### 10. Expose the effective send mode and simplify setup

The custom setting is currently an environment variable documented in
`CUSTOM.md`. Tool descriptions describe the normal two-phase contract and
then append an exception; there is no first-class effective-mode display.
The desktop's console-send consent setting is a separate mechanism.

Add a server status field showing effective MCP send mode, expose it in the
console, and provide a CLI configuration/status command. Consider per-account
settings for mixed personal/business mailboxes. Make error text actionable
and consistent across the English/Italian UI. Clearly distinguish queued,
provider-accepted, partially accepted, sent-copy failure, and unknown states.
The status must not imply final delivery when the provider only acknowledges
acceptance. Include a diagnostic command covering executable path, data root,
account login state, server version, send mode, and connectivity without
printing credentials.

### 11. Use atomic file replacement for caches

`src/gigamail/core/auth.py:117` and `src/gigamail/core/sent_cache.py:89` truncate
files before rewriting them. A crash can leave partial JSON/token data;
concurrent readers can see an incomplete file. The sent cache catches parse
errors, but that causes unnecessary fallback work.

Write to a temporary file in the same directory, flush, and replace atomically;
serialize concurrent token-cache updates as needed. Prefer persistent SQLite
records for state that must survive a send or process crash. Test interrupted
writes and concurrent refreshes.

## Suggested implementation sequence

1. Durable outbound operations and audit failure handling.
2. Account/folder scoping, request deadlines, and truthful response status.
3. Reply threading, side-effect-free reads, and attachment validation.
4. Effective-mode controls and diagnostics.
5. Pooling, polling, and atomic cache writes, measured against a baseline.

Preserve the current tests and add failure-injection and concurrency coverage
for the cases above. The existing optional-send tests cover the happy path,
default mode, pending approvals, dry run, and exceptions, but stub out the reply
guard and do not cover post-send audit failure or concurrent/retried submission.
