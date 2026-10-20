# How to set up GigaMail with ChatGPT and Codex

This guide connects your existing mailbox to **Codex** and the **ChatGPT
desktop app** (including ChatGPT Work), which share the same setup: you
sign in with your ChatGPT account. When you are done you can
ask ChatGPT or Codex to search, read and summarize your mail and to draft replies.
Nothing is sent, deleted or put in your calendar until you approve it in
the GigaMail app.

It works with Microsoft 365 / Outlook.com and with any standard IMAP/SMTP
provider (Network Solutions, GoDaddy, Aruba, your own domain, …). You keep
your current mailbox and your current Outlook setup: GigaMail is just one
more client on the same mailbox, like your phone.

Time needed: about 15 minutes.

## What you need

- A Windows 10/11 or macOS computer.
- A ChatGPT plan that includes Codex.
- Your mailbox address and password, and for IMAP the server names your
  provider gives (IMAP and SMTP host and port). Most providers list them on
  a help page called "IMAP settings" or "email client settings".

## 1. Install GigaMail

Download the desktop app from [gigamail.ai](https://gigamail.ai) or from
the [latest release](https://github.com/adecubed/gigamail/releases/latest):

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
  password, and the IMAP and SMTP servers from your provider's help page.
  For most providers that is port **993** for IMAP and **587** or **465**
  for SMTP.

Your password is stored only on your computer, encrypted (on Windows the
key is protected by Windows and tied to your user account). It is never
sent to Codex or ChatGPT, and the AI cannot read it or add accounts.

When the account appears in the sidebar with your inbox, the mailbox part
is done.

## 3. Install Codex and sign in

GigaMail runs on your computer, so it works in the **ChatGPT desktop app**
and in **Codex**, not in chatgpt.com in the browser. The two share the
same configuration, which you write once with the Codex command line in
step 4.

Install the Codex CLI by following
[OpenAI's instructions](https://developers.openai.com/codex), then sign in
with your ChatGPT account. Install the ChatGPT desktop app too if you want
to use GigaMail from ChatGPT.

Check it works by opening a terminal (on Windows: **PowerShell**) and
running:

```bash
codex --version
```

## 4. Add GigaMail to Codex

In the same terminal, add the GigaMail plugin. It teaches Codex how
GigaMail works, including that every send waits for your approval:

```bash
codex plugin marketplace add adecubed/gigamail
codex plugin add gigamail@gigamail
```

Then tell Codex where the GigaMail server is. It ships inside the app you
installed in step 1, and it must use the same data folder as the app, so
that Codex sees your accounts and the app sees Codex's requests:

- **Windows**, in PowerShell. The first line finds the app, whether you
  installed it for yourself or for all users:

  ```powershell
  $gm = "$env:LOCALAPPDATA\Programs\GigaMail", "$env:ProgramFiles\GigaMail" | Where-Object { Test-Path "$_\resources\python\python.exe" } | Select-Object -First 1
  codex mcp add gigamail --env GIGAMAIL_ROOT="$env:APPDATA\ADE" -- "$gm\resources\python\python.exe" -m gigamail.server
  ```

  If `$gm` comes out empty, you chose another folder during the install:
  set it by hand, for example `$gm = "D:\Apps\GigaMail"`, and run the
  second line again.

- **macOS**:

  ```bash
  codex mcp add gigamail --env GIGAMAIL_ROOT="$HOME/.ade" -- /Applications/GigaMail.app/Contents/Resources/python/bin/python3 -m gigamail.server
  ```

This replaces the plugin's default server entry with one pointing at the
app. If you installed GigaMail in another folder, use that folder in the
path. Run `codex mcp get gigamail` to check what Codex will start.

Developers who prefer Python can instead run `pip install "gigamail[all]"`
and use `codex mcp add gigamail -- gigamail-server`.

## 5. Try it

Start a **new** chat in the ChatGPT desktop app, or a new Codex session
(tools load when a session starts), and ask:

> List my GigaMail accounts.

You should see the mailbox you connected in step 2. Then try:

> Summarize the unread mail from this week.
>
> Find the last message from the bank and tell me what it asks.
>
> Draft a reply to the last message from Laura saying Thursday works.

## 6. Approving what Codex prepares

Reading and searching need no approval. Sending, replying, deleting and
calendar changes do: Codex only gets a request number, and the action
waits in the GigaMail app.

Open GigaMail and find the request in **Automations** (from the next
release it has its own **Waiting for you** button). Review the request (recipient, subject, text,
attachments) and click **Approve** or **Reject**. On Windows you can
confirm with Windows Hello. Only then is the mail sent, exactly as you saw
it. Every action is recorded in a log.

## Privacy note

When you ask Codex to read or summarize a message, the text of that message
is sent to OpenAI to produce the answer, under your ChatGPT plan's data
terms. Your password never is. For highly confidential correspondence,
check OpenAI's terms for your plan; business plans offer stronger data
controls.

## Troubleshooting

- **Codex says it has no GigaMail tools**: start a new Codex session, and
  run `codex mcp list` to check that `gigamail` is listed and enabled. If
  it is not, repeat step 4 and check the path to the app's Python.
- **Codex sees no accounts**: open the GigaMail app once and check the
  account is listed there. Codex and the app share the same accounts.
- **IMAP login fails**: check the server names and ports with your
  provider. Some providers require an app password when two-step sign-in is
  on.
- **A send never happens**: it is waiting for you. Open GigaMail and look
  under **Automations** (or **Waiting for you** from the next release).

Questions or problems: open an issue at
[github.com/adecubed/gigamail/issues](https://github.com/adecubed/gigamail/issues)
or write to Founder@adecubed.com.

GigaMail is free and open source (AGPL-3.0-or-later).
