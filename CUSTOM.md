# Custom GigaMail

Based on https://github.com/adecubed/gigamail, retaining upstream history
and the AGPL-3.0-or-later license. The `upstream` remote tracks the original.

## Optional immediate email

Set `GIGAMAIL_SEND_REQUIRE_APPROVAL=false` in the MCP server environment
and restart the server. Only the explicit value `false` (case insensitive)
disables approval. An unset environment variable uses saved settings; `true`
and other explicit values require approval.

With this option, `send_mail` and `reply_mail` send on the first call without
`request_id`. Existing requests still need approval and use their stored
payload. Other operations retain their normal approval requirements.
Account selection, signatures, attachment validation, pending reply checks,
audit logging, and `ADE_MAIL_DRYRUN` still apply. Immediate calls do not create approval requests. Durable operation keys now
protect retries; the approval queue rate cap remains separate. See the retry
contract below.

Example MCP server environment:

```json
{"GIGAMAIL_SEND_REQUIRE_APPROVAL": "false"}
```

Install this checkout with Python 3.10 or newer:

```sh
python -m pip install -e '.[all]'
```

Use this checkout's `gigamail-server` executable in your MCP configuration.
The hosted upstream service and existing installations are unaffected.

## Send mode and diagnostics

```sh
gigamail send-mode                       # effective global mode
gigamail send-mode immediate             # save a global default
gigamail send-mode approval --account-id 1
gigamail send-mode inherit --account-id 1 # remove an account override
gigamail doctor                          # no network; never prints credentials
gigamail doctor --connectivity           # read-only provider login/access check
```

Environment overrides take precedence over saved per-account settings, which
in turn take precedence over the saved global default. Without configuration,
approval is required. Saved settings are read on each call; environment changes
require restarting the server. `list_accounts` reports each account's effective
`send_mode`. The desktop Automations view shows the default and per-account
modes. This configuration controls MCP sends; console clicks and watcher rules
retain their own authorization mechanisms.

## Reliable sends and retries

Supply `idempotency_key` on `send_mail` or `reply_mail` and keep the same key and
payload when retrying. Keys are scoped to the account. Reusing a key for a
changed email fails instead of sending it. The desktop composers retain keys
across retries and reloads without storing message bodies in the retry metadata.
Approved requests also use stable operation keys. Replies without an explicit
key use a stable account/folder/message identity. To deliberately send another
reply to the same original message, supply a new key.

All mail sent through the router now has an operation record in `mail/outbound.db`.
It stores a payload fingerprint and provider result, not the email body or
attachment contents. Results distinguish `accepted`, `partially_accepted`,
`failed`, and `unknown`; a claim can also remain `sending` after interruption.
A concurrent retry or abandoned claim returns an uncertain result and never
submits again. The authenticated console API exposes
`GET /mail/operations/{operation_id}?account_id=1` to inspect an operation.

No local database can guarantee exactly-once SMTP delivery. If the connection
fails after submission, check Sent/provider records before deciding whether to
send a new operation. Do not delete an uncertain operation to force a retry.
Without a caller-provided key, a new-mail call gets a new operation id: after a
lost response, repeating such a call can still duplicate the email. Use a key
before the first attempt. The approval queue rate cap is separate from tracking;
immediate sends do not inherit that cap.

## Reliability and efficiency changes

- Reply guard records now include account and folder, with optional UIDVALIDITY
  scope for callers that have it. Existing records migrate automatically. Older
  records and current readers without UIDVALIDITY cannot distinguish a mailbox
  epoch reset.
- Microsoft fetches are observational. The desktop explicitly marks opened mail
  read, and failed mark-read writes are no longer reported as success.
- Microsoft replies with attachments create a reply draft, update its recipients
  and body, attach documents, then send the draft. Documents of 3 MB or more are
  rejected before submission; upload sessions for larger files are not implemented.
  An interrupted draft preparation can leave an unsent draft for manual cleanup.
- Invalid attachment content/type aborts the send; explicit malformed account ids
  never fall back to the active account.
- Graph mail uses per-thread connection pools, 5-second connect / 30-second read
  timeouts, and up to two bounded retries for safe reads. Writes have no automatic
  retries. SMTP connections have an explicit 30-second socket timeout. Socket
  deadlines are per operation, not a total workflow deadline.
- Post-send audit/tracking failures preserve the known provider result and return
  a warning. An uncertain result stays distinct from definite rejection.
- Notification polling runs after the previous check finishes, backs off on
  failures, and cannot rearm after cancellation. Badge refreshes use compact
  metadata instead of full messages and rule documents, and prevent overlap.
- Token and sent-cache writes use atomic replacement. Token updates use an OS
  file lock and merge changed records to preserve concurrent account refreshes.

Provider workflow references:
[create reply draft](https://learn.microsoft.com/en-us/graph/api/message-createreply?view=graph-rest-1.0),
[attach document](https://learn.microsoft.com/en-us/graph/api/message-post-attachments?view=graph-rest-1.0),
[send draft](https://learn.microsoft.com/en-us/graph/api/message-send?view=graph-rest-1.0).

## Validation and handoff

Validated on 2026-10-28:

- Python: 1,196 passed, 6 skipped.
- Desktop JavaScript: 166 passed.
- Python Ruff, desktop ESLint, and `git diff --check`: passed.
- CLI setting persistence smoke-tested in a separate temporary data directory.
- Failure injection covers concurrent retries, abandoned claims, lost provider
  responses, audit/tracking failures, schema migration, account/folder collisions,
  attachment construction, cache replacement, and concurrent token updates.
- Live mailbox sends, packaged Electron execution, and platform-specific consent
  prompts were not exercised. No real email was sent.

Published source repository: https://github.com/Errantial/gigamail-custom

The custom branch retains upstream history and the AGPL license. This source
publication does not create a tagged release, publish a Python package, or
install the custom version into an existing mailbox setup.
