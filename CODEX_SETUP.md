# How to set up GigaMail with ChatGPT and Codex

GigaMail connects your existing mailbox to OpenAI's assistants: ask them
to search, read and summarize your mail and to draft replies. Nothing is
sent, deleted or put in your calendar until you approve it in GigaMail.

It works with Microsoft 365 / Outlook.com and with any standard IMAP/SMTP
provider (Network Solutions, GoDaddy, Aruba, your own domain, …). You keep
your mailbox and your Outlook setup as they are: GigaMail is one more
client on the same mailbox, like your phone.

Two ways to use it, after the same first two steps:

- **From ChatGPT** (the desktop app, ChatGPT Work included), with the
  GigaMail plugin.
- **From GigaMail itself**, in the bar at the bottom of the app, with
  Codex doing the work.

You can set up one or both.

## What you need

- A Windows 10/11 or macOS computer.
- A ChatGPT plan (ChatGPT Work, or a plan that includes Codex).
- Your mailbox address and password; for IMAP also the server names your
  provider gives (IMAP and SMTP host and port), usually on a help page
  called "IMAP settings" or "email client settings".

## 1. Install GigaMail

Download the app from [gigamail.ai](https://gigamail.ai) or from the
[latest release](https://github.com/adecubed/gigamail/releases/latest):

- **Windows**: `GigaMail-Setup.exe`. It is code-signed; the certificate is
  new, so Windows may say "unrecognized app" on first launch: click
  **More info**, then **Run anyway**.
- **macOS**: `GigaMail-arm64.dmg` (Apple Silicon) or `GigaMail-x64.dmg`
  (Intel).

No administrator password is needed and the app updates itself.

## 2. Connect your mailbox

Open GigaMail. The first-run guide opens by itself; otherwise click
**+ Account**.

- **Microsoft 365 / Outlook.com**: choose Microsoft and sign in in the
  browser window that opens.
- **Any other provider (IMAP)**: choose IMAP and enter your address,
  password, and the IMAP and SMTP servers from your provider. For most
  providers that is port **993** for IMAP and **587** or **465** for SMTP.

Your password is stored only on your computer, encrypted (on Windows the
key is protected by Windows and tied to your user account). It is never
sent to ChatGPT or Codex, and the AI cannot read it or add accounts.

When your inbox shows up in GigaMail, the mailbox part is done.

## 3a. Use it from ChatGPT

1. Install the ChatGPT desktop app and sign in. GigaMail runs on your
   computer, so it works in the desktop app, not in chatgpt.com in the
   browser.
2. In the app, open the **Plugins** directory, search for **GigaMail** and
   install it.
3. In GigaMail, click **Connect** under **ChatGPT and Codex**: in the
   first-run guide, or in **Automations**, box "Notifications and agent".
   When it says **connected**, close and reopen the ChatGPT app.
4. In a **new** chat, ask: *List my GigaMail accounts.*

The plugin alone teaches ChatGPT how GigaMail works; **Connect** gives it
access to the GigaMail on this computer. Both are needed.

## 3b. Use it from GigaMail

1. Install the [Codex CLI](https://developers.openai.com/codex) and sign in
   with your ChatGPT account (run `codex` once in a terminal).
2. In GigaMail, open **Automations** and choose **Codex CLI** as the agent.
3. Type in the bar at the bottom of GigaMail: *Summarize the last mail
   from …*

GigaMail sets Codex up by itself the first time.

## 4. Approving what the assistant prepares

Reading and searching need no approval. Sending, replying, deleting and
calendar changes do: the assistant only gets a request number, and the
action waits in GigaMail under **Waiting for you** (in the bar's own card
when you asked from GigaMail). Review it (recipient, subject, text,
attachments) and click **Approve** or **Reject**. Only then does it run,
exactly as you saw it. Every action is recorded in a log.

## Privacy note

When the assistant reads or summarizes a message, the text of that message
is sent to OpenAI to produce the answer, under your ChatGPT plan's data
terms. Your password never is. For highly confidential correspondence,
check OpenAI's terms for your plan; business plans offer stronger data
controls.

## Troubleshooting

- **ChatGPT says the skill is there but GigaMail is not connected**: click
  **Connect** in GigaMail (step 3a), then close and reopen the ChatGPT app
  and start a new chat.
- **GigaMail says "points to another GigaMail"**: an older setup is
  registered. Click **Reconnect**.
- **The assistant sees no accounts**: check the account is listed in
  GigaMail. They share the same accounts.
- **The bar in GigaMail says "agent not found"**: the Codex CLI is not
  installed, or GigaMail was open while you installed it: restart
  GigaMail.
- **The bar says the agent ended with an error right after an update**:
  update the Codex CLI (`npm i -g @openai/codex@latest`) and restart
  GigaMail. The ChatGPT app and an older Codex CLI do not always agree on
  their shared settings.
- **IMAP login fails**: check the server names and ports with your
  provider. Some providers require an app password when two-step sign-in is
  on.
- **A send never happens**: it is waiting for you under **Waiting for you**.

## With GigaMail 0.5.3 or earlier

These versions have no **Connect** button. Register GigaMail in Codex by
hand, once, with the Codex CLI. In PowerShell:

```powershell
$gm = "$env:LOCALAPPDATA\Programs\GigaMail", "$env:ProgramFiles\GigaMail" | Where-Object { Test-Path "$_\resources\python\python.exe" } | Select-Object -First 1
codex mcp add gigamail --env GIGAMAIL_ROOT="$env:APPDATA\ADE" -- "$gm\resources\python\python.exe" -m gigamail.server
```

On macOS:

```bash
codex mcp add gigamail --env GIGAMAIL_ROOT="$HOME/.ade" -- /Applications/GigaMail.app/Contents/Resources/python/bin/python3 -m gigamail.server
```

Approvals are under **Automations** in these versions.

Questions or problems: open an issue at
[github.com/adecubed/gigamail/issues](https://github.com/adecubed/gigamail/issues)
or write to Founder@adecubed.com.

GigaMail is free and open source (AGPL-3.0-or-later).
