/* Stable retry keys survive a composer reload without storing email bodies. */
(function () {
  const memory = new Map();
  async function keyFor(payload, scope, storage = globalThis.sessionStorage) {
    const encoded = new globalThis.TextEncoder().encode(JSON.stringify(payload));
    const digest = await globalThis.crypto.subtle.digest('SHA-256', encoded);
    const fingerprint = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
    const storageKey = `gm_send_attempt:${scope}`;
    let previous;
    try { previous = JSON.parse(storage.getItem(storageKey) || 'null'); } catch (_) { /* storage unavailable */ }
    if (previous && previous.fingerprint === fingerprint) return previous.key;
    const known = memory.get(storageKey);
    if (known && known.fingerprint === fingerprint) return known.key;
    const record = { fingerprint, key: globalThis.crypto.randomUUID() };
    try { storage.setItem(storageKey, JSON.stringify(record)); } catch (_) { /* use in-memory fallback */ }
    memory.set(storageKey, record);
    return record.key;
  }
  function message(result, italian = false) {
    if (result.status === 'unknown' || result.submission_state === 'unknown') {
      return italian ? 'Esito incerto: controlla Inviate prima di riprovare. Lo stesso tentativo non verrà reinviato.'
        : 'Outcome unknown: check Sent before retrying. The same attempt will not be resent.';
    }
    if (result.submission_state === 'partially_accepted') {
      return italian ? 'Accettata solo per alcuni destinatari. Controlla quelli rifiutati.'
        : 'Accepted for some recipients only. Check the refused recipients.';
    }
    if (result.sent_copy_saved === false && result.success) {
      return italian ? 'Accettata dal server, ma copia in Inviate non salvata.'
        : 'Accepted by the server, but the Sent copy was not saved.';
    }
    return italian ? 'Accettata dal server ✓' : 'Accepted by the server ✓';
  }
  function clear(scope, storage = globalThis.sessionStorage) {
    const key = `gm_send_attempt:${scope}`;
    memory.delete(key);
    try { storage.removeItem(key); } catch (_) { /* storage unavailable */ }
  }
  const api = { keyFor, message, clear };
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof window !== 'undefined') window.MailSendAttempt = api;
})();
