const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const send = require('../send_attempt');
const approvals = require('../approvals_view');

test('retry keys persist across reloads, change with content, and do not store bodies', async () => {
  const entries = new Map();
  const storage = { getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
  const payload = {to: 'recipient@example.com', body: 'Private mail content'};
  const first = await send.keyFor(payload, 'draft-one', storage);
  assert.equal(await send.keyFor(payload, 'draft-one', storage), first);
  delete require.cache[require.resolve('../send_attempt')];
  const reloaded = require('../send_attempt');
  assert.equal(await reloaded.keyFor(payload, 'draft-one', storage), first);
  assert.notEqual(await reloaded.keyFor({...payload, body: 'Changed'}, 'draft-one', storage), first);
  assert.notEqual(await reloaded.keyFor(payload, 'draft-two', storage), first);
  assert.ok(!JSON.stringify([...entries]).includes('Private mail content'));
  const beforeClear = await reloaded.keyFor(payload, 'draft-two', storage);
  reloaded.clear('draft-two', storage);
  assert.notEqual(await reloaded.keyFor(payload, 'draft-two', storage), beforeClear);
});

test('unknown and partial outcomes have distinct messages', () => {
  assert.match(send.message({status: 'unknown'}), /do not|will not/i);
  assert.match(send.message({submission_state: 'partially_accepted'}), /some recipients/);
  assert.match(send.message({success: true, sent_copy_saved: false}), /copy was not saved/);
  assert.match(send.message({success: true}), /Accepted by the server/);
  assert.equal(approvals.outcomeOf({status:'executed', outcome:'unknown'}, (_k, fallback) => fallback).cls, 'warn');
});

function pollingHarness(check) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.js'), 'utf8');
  const begin = source.indexOf('function startPolling()');
  const end = source.indexOf('// ── SERVER PYTHON', begin);
  const timers = new Map();
  let next = 0;
  const context = {
    notifPoller: null, pollingGeneration: 0, console: {error() {}},
    checkNewMail: check, checkFollowups: async () => {},
    setTimeout(callback, delay) { const id = ++next; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(source.slice(begin, end), context);
  return {context, timers, run() {
    const [id, timer] = timers.entries().next().value;
    timers.delete(id);
    return timer.callback();
  }};
}

test('polling does not overlap and stop cancels in-flight rearming', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const h = pollingHarness(() => pending);
  h.context.startPolling();
  h.context.startPolling();
  assert.equal(h.timers.size, 1);
  const running = h.run();
  assert.equal(h.timers.size, 0);
  h.context.stopPolling();
  release();
  await running;
  assert.equal(h.timers.size, 0);
});

test('polling backs off after failure', async () => {
  const h = pollingHarness(async () => { throw new Error('offline'); });
  h.context.startPolling();
  await h.run();
  assert.equal([...h.timers.values()][0].delay, 120000);
  h.context.stopPolling();
});
