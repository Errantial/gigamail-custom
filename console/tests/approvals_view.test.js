// The "waiting for you" / "recently decided" cards of the Automations view
// (approvals_view.js): what a request looks like on screen and what the
// outcome labels say.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const V = require('../approvals_view');
const t = (_key, fallback) => fallback;
const NOW = 1_800_000_000;

const WATCHER_REPLY = {
  request_id: 'req_abc', tool: 'reply_mail', status: 'pending', completed_by: 'watcher',
  expires_at: NOW + 488,
  args: { message_id: '3690', account_id: 2, body: 'Dear Sir,\nthanks.' },
  preview: {
    replying_to: { from: 'mario.rossi@example.com', subject: 'Message from the site' },
    body: 'Dear Sir,\nthanks.', rule_id: 'rule_x', rule_mode: 'semi',
    to_source: 'address taken from the body',
    attachments: [{ name: 'A.1.pdf', path: 'C:\\docs\\A.1.pdf', size_kb: 400 }],
  },
};

const AGENT_SEND = {
  request_id: 'req_def', tool: 'send_mail', status: 'pending', completed_by: 'agent',
  expires_at: NOW + 60,
  args: { to: 'anna.bianchi@example.com', attachments: ['C:/docs/B.2.pdf'] },
  preview: { to: 'anna.bianchi@example.com', subject: 'Visit <b>tomorrow</b>', body: 'Hello' },
};

test('a watcher draft: recipient and subject come from the mail it answers', () => {
  const s = V.summarize(WATCHER_REPLY, NOW);
  assert.equal(s.completedBy, 'watcher');
  assert.equal(s.to, 'mario.rossi@example.com');
  assert.equal(s.subject, 'Message from the site');
  assert.equal(s.ruleMode, 'semi');
  assert.equal(s.expiresIn, 488);
  assert.equal(s.expired, false);
  assert.deepEqual(s.attachments, ['A.1.pdf']);
});

test('an agent request: explicit recipient, attachments by file name', () => {
  const s = V.summarize(AGENT_SEND, NOW);
  assert.equal(s.completedBy, 'agent');
  assert.equal(s.to, 'anna.bianchi@example.com');
  assert.deepEqual(s.attachments, ['B.2.pdf']);
  assert.equal(V.addressOf([{ address: 'a@example.com' }, 'b@example.com']), 'a@example.com, b@example.com');
});

test('the pending card has approve and reject for that request and escapes the subject', () => {
  const html = V.pendingCard(AGENT_SEND, t, NOW);
  assert.match(html, /data-act="approve" data-id="req_def"/);
  assert.match(html, /data-act="reject" data-id="req_def"/);
  assert.ok(html.includes('Visit &lt;b&gt;tomorrow&lt;/b&gt;'));
  assert.ok(!html.includes('<b>tomorrow</b>'));
  assert.ok(html.includes('scade tra 1m00s'));
  assert.ok(html.includes('richiesta dell&#39;agente'));   // the label goes through the same escaping
});

test('the watcher card names the rule mode, the recipient source and the expiry', () => {
  const html = V.pendingCard(WATCHER_REPLY, t, NOW);
  assert.ok(html.includes('bozza del watcher'));
  assert.ok(html.includes('SEMI'));
  assert.ok(html.includes('address taken from the body'));
  assert.ok(html.includes('scade tra 8m08s'));
  assert.ok(html.includes('📎 A.1.pdf'));
  assert.ok(html.includes('Dear Sir,\nthanks.'));
});

test('outcome labels: executed means consumed, the provider outcome says sent', () => {
  const o = (patch) => V.outcomeOf(V.summarize({ ...AGENT_SEND, ...patch }, NOW), t).text;
  assert.equal(o({ status: 'executed', execution_outcome: 'ok' }), 'inviata');
  assert.equal(o({ status: 'executed', execution_outcome: 'failed' }), 'invio fallito');
  assert.equal(o({ status: 'executed', execution_outcome: 'dryrun' }), 'prova, non inviata');
  assert.equal(o({ status: 'executed' }), 'in esecuzione');
  assert.equal(o({ status: 'approved', completed_by: 'watcher' }), 'approvata, la invia il watcher');
  assert.equal(o({ status: 'approved' }), "approvata, la completa l'agente");
  assert.equal(o({ status: 'rejected' }), 'rifiutata');
  assert.equal(o({ status: 'pending', expires_at: NOW - 5 }), 'scaduta senza decisione');
});

test('the recent card says who decided, by channel', () => {
  const html = V.recentCard({ ...AGENT_SEND, status: 'rejected', decided_by: 'console:tester', decided_at: NOW }, t, NOW);
  assert.ok(html.includes('rifiutata'));
  assert.ok(html.includes('console'));
  assert.ok(!html.includes('console:tester'));          // the user name stays out of the card
  const tg = V.recentCard({ ...AGENT_SEND, status: 'approved', decided_by: 'telegram:123+pin', decided_at: NOW }, t, NOW);
  assert.ok(tg.includes('Telegram') && !tg.includes('123'));
});

test('badge and expiry formatting', () => {
  assert.equal(V.badgeText(0), '');
  assert.equal(V.badgeText(3), '3');
  assert.equal(V.badgeText(12), '9+');
  assert.equal(V.fmtEta(0, t), 'scaduta');
  assert.equal(V.fmtEta(45, t), 'scade tra 45s');
});

test('the Automations view hosts the lists and the sidebar icon carries the badge', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index_v2.html'), 'utf-8');
  assert.ok(html.includes('id="pendingList"'));
  assert.ok(html.includes('id="recentList"'));
  assert.match(html, /id="btnShowAutomations"[^>]*><span class="s-badge" id="pendingBadge" hidden>/);
  assert.ok(html.includes('src="approvals_view.js"'));
  const auto = fs.readFileSync(path.join(__dirname, '..', 'renderer_automations.js'), 'utf-8');
  assert.ok(auto.includes("api('/approvals')") && auto.includes("/approvals/recent"));
  assert.ok(auto.includes("$('pendingList')?.addEventListener('click', onPendingAction)"));
});
