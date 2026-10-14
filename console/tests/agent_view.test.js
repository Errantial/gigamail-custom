// Unit tests of renderer_agent.js (window.AgentView): the mails the agent
// mentions become links that open the mail.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

const BAR = `
  <div id="agentBar"><div id="agentThread"></div>
    <div id="agentChip" hidden><span id="agentChipText"></span><button id="agentChipX"></button></div>
    <textarea id="agentInput"></textarea><button id="agentSend"></button><button id="agentClear"></button>
  </div>
  <div id="mailDetail"></div>`;

function load({ activeAccount = 6, group = null } = {}) {
  const dom = new JSDOM(`<!DOCTYPE html><body>${BAR}</body>`, { runScripts: 'outside-only' });
  const w = dom.window;
  const calls = { pane: [], window: [] };
  w.activeAccountId = activeAccount;
  w.activeGroup = group;
  w.openMail = (id, folder) => calls.pane.push({ id, folder });
  w.electronAPI = {
    agentCommand: async () => ({ answer: '' }),
    // the page builds `data` in its own realm: copy it to compare it here
    openMailWindow: (data) => calls.window.push({ ...data }),
  };
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'renderer_agent.js'), 'utf-8'),
    dom.getInternalVMContext());
  return { w, calls };
}

// The script hooks its click handler on DOMContentLoaded, which JSDOM fires
// just after construction.
function ready(w) {
  if (w.document.readyState !== 'loading') return Promise.resolve();
  return new Promise((r) => w.document.addEventListener('DOMContentLoaded', () => r(), { once: true }));
}

function render(w, text) {
  const th = w.document.getElementById('agentThread');
  th.innerHTML = w.AgentView.formatAnswer(text);
  return th;
}

test('a mail link becomes a clickable link with account, id and folder', () => {
  const { w } = load();
  const th = render(w, '- [Invoice March](gigamail://mail/6/arch-123?folder=INBOX) from Mario');
  const a = th.querySelector('a.agent-mail-link');
  assert.ok(a);
  assert.equal(a.textContent, 'Invoice March');
  assert.equal(a.dataset.account, '6');
  assert.equal(a.dataset.id, 'arch-123');
  assert.equal(a.dataset.folder, 'INBOX');
  assert.ok(th.textContent.includes('from Mario'));
});

test('ids with slashes and padding survive, folder is optional', () => {
  const { w } = load();
  const th = render(w, '[Hello](gigamail://mail/2/AAMk/x+y==)');
  const a = th.querySelector('a.agent-mail-link');
  assert.equal(a.dataset.id, 'AAMk/x+y==');
  assert.equal(a.dataset.folder, '');
});

test('the subject is escaped: markup in it does not run', () => {
  const { w } = load();
  const th = render(w, '[<img src=x onerror="window.__xss=1">](gigamail://mail/6/42)');
  assert.equal(th.querySelectorAll('img').length, 0);
  assert.ok(th.querySelector('a.agent-mail-link').textContent.includes('<img'));
});

test('other links stay plain text: only the gigamail://mail scheme is clickable', () => {
  const { w } = load();
  const th = render(w, '[pay here](https://evil.example.com) and [x](javascript:alert(1))');
  assert.equal(th.querySelectorAll('a').length, 0);
  assert.ok(th.textContent.includes('https://evil.example.com'));
});

test('bold and line breaks still work around a link', () => {
  const { w } = load();
  const th = render(w, '**Found:**\n[A](gigamail://mail/6/1)');
  assert.ok(th.querySelector('strong'));
  assert.ok(th.querySelector('br'));
  assert.ok(th.querySelector('a.agent-mail-link'));
});

test('click on a mail of the account on screen opens it in the reading pane', async () => {
  const { w, calls } = load({ activeAccount: 6 });
  await ready(w);
  const th = render(w, '[A](gigamail://mail/6/arch-9?folder=Posta%20inviata)');
  th.querySelector('a').dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.deepEqual(calls.pane, [{ id: 'arch-9', folder: 'Posta inviata' }]);
  assert.equal(calls.window.length, 0);
});

test('click on a mail of another account opens its own window for that account', async () => {
  const { w, calls } = load({ activeAccount: 1 });
  await ready(w);
  const th = render(w, '[A](gigamail://mail/6/77?folder=INBOX)');
  th.querySelector('a').dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.equal(calls.pane.length, 0);
  assert.deepEqual(calls.window, [{ id: '77', folder: 'INBOX', account_id: 6 }]);
});

test('in a merged view of several accounts the mail opens in its own window', async () => {
  const { w, calls } = load({ activeAccount: 6, group: 'g1' });
  await ready(w);
  const th = render(w, '[A](gigamail://mail/6/77)');
  th.querySelector('a').dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.equal(calls.pane.length, 0);
  assert.deepEqual(calls.window, [{ id: '77', folder: null, account_id: 6 }]);
});
