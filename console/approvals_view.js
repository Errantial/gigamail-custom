// approvals_view.js — the "waiting for you" and "recently decided" cards of
// the Automations view: the requests the watcher's rules and the agent
// raise, with Approve (Windows Hello, from the backend) and Reject.
//
// Pure rendering, shared by the window (window.ApprovalsView) and the node
// tests (module.exports). The translation function `t(key, fallback)` is
// passed in: window.i18n.t in the console, an identity in the tests.
(function () {
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  // The preview stores recipients as a string, an {address} object or a
  // list of either: one address string out of any of them.
  function addressOf(v) {
    if (!v) return '';
    if (Array.isArray(v)) return v.map(addressOf).filter(Boolean).join(', ');
    if (typeof v === 'object') return String(v.address || v.email || '');
    return String(v);
  }

  function summarize(rec, now = Date.now() / 1000) {
    const p = rec.preview || {};
    const a = rec.args || {};
    const replying = p.replying_to && typeof p.replying_to === 'object' ? p.replying_to : null;
    const attachments = (p.attachments || a.attachments || [])
      .map((x) => (typeof x === 'string' ? x.split(/[\\/]/).pop() : x && x.name))
      .filter(Boolean);
    return {
      id: rec.request_id,
      tool: rec.tool || '',
      status: rec.status || 'pending',
      completedBy: rec.completed_by === 'watcher' ? 'watcher' : 'agent',
      to: addressOf(p.to) || (replying ? addressOf(replying.from) : ''),
      subject: String(p.subject || (replying && replying.subject) || ''),
      body: String(p.body || a.body || ''),
      expiresIn: Math.max(0, Math.round((rec.expires_at || 0) - now)),
      expired: !!rec.expired || (!!rec.expires_at && rec.expires_at <= now),
      ruleMode: String(p.rule_mode || ''),
      toSource: String(p.to_source || ''),
      attachments,
      decidedBy: String(rec.decided_by || ''),
      decidedAt: rec.decided_at || null,
      outcome: String(rec.execution_outcome || ''),
      accepted: rec.provider_result && typeof rec.provider_result === 'object'
        ? rec.provider_result.accepted : undefined,
    };
  }

  function fmtEta(sec, t) {
    if (sec <= 0) return t('pending_expired', 'scaduta');
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    const when = m > 0 ? `${m}m${String(s).padStart(2, '0')}s` : `${s}s`;
    return `${t('pending_expires_in', 'scade tra')} ${when}`;
  }

  // "console:simon" → console, "telegram:123+pin" → Telegram, "cli:simon" → terminal
  function fmtBy(by, t) {
    const chan = String(by || '').split(':')[0];
    if (chan === 'console') return t('by_console', 'console');
    if (chan === 'telegram') return t('by_telegram', 'Telegram');
    if (chan === 'cli') return t('by_cli', 'terminale');
    return by ? esc(by) : '';
  }

  function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  // One short label for what happened to a decided request. "executed"
  // alone means consumed, not delivered: the outcome says whether it went.
  function outcomeOf(s, t) {
    if (s.status === 'executed') {
      if (s.outcome === 'ok') return { cls: 'ok', text: t('st_sent', 'inviata') };
      if (s.outcome === 'dryrun') return { cls: 'warn', text: t('st_dryrun', 'prova, non inviata') };
      if (s.outcome) return { cls: 'err', text: t('st_send_failed', 'invio fallito') };
      return { cls: 'warn', text: t('st_executing', 'in esecuzione') };
    }
    if (s.status === 'approved') {
      return { cls: 'warn', text: s.completedBy === 'watcher'
        ? t('st_approved_watcher', 'approvata, la invia il watcher')
        : t('st_approved_agent', "approvata, la completa l'agente") };
    }
    if (s.status === 'rejected') return { cls: 'err', text: t('st_rejected', 'rifiutata') };
    if (s.status === 'pending' && s.expired) return { cls: 'err', text: t('st_expired', 'scaduta senza decisione') };
    return { cls: '', text: esc(s.status) };
  }

  function head(s, t) {
    const origin = s.completedBy === 'watcher'
      ? `<span class="ai-tag">${esc(t('pending_from_watcher', 'bozza del watcher'))}${s.ruleMode ? ' · ' + esc(s.ruleMode.toUpperCase()) : ''}</span>`
      : `<span class="ai-tag">${esc(t('pending_from_agent', "richiesta dell'agente"))}</span>`;
    const title = s.subject || s.tool;
    const to = s.to ? `<div class="ai-card-sub">${esc(t('pending_to', 'a'))} ${esc(s.to)}`
      + (s.toSource ? ` <span class="hint">(${esc(s.toSource)})</span>` : '') + '</div>' : '';
    return `${origin} <span class="hint">${esc(s.tool)} · ${esc(s.id)}</span>
          <div class="ai-card-title" style="margin-top:4px">${esc(title)}</div>${to}`;
  }

  function bodyBlock(s, t) {
    if (!s.body && !s.attachments.length) return '';
    const att = s.attachments.length
      ? `<div class="ai-card-sub">📎 ${esc(s.attachments.join(', '))}</div>` : '';
    const body = s.body
      ? `<details class="pending-body"><summary class="hint">${esc(t('pending_body', 'Testo'))}</summary>`
        + `<pre class="hint" style="white-space:pre-wrap;margin:6px 0 0">${esc(s.body)}</pre></details>` : '';
    return att + body;
  }

  function pendingCard(rec, t, now) {
    const s = summarize(rec, now);
    return `
      <div class="ai-card pending-card" data-request="${esc(s.id)}">
        ${head(s, t)}
        ${bodyBlock(s, t)}
        <div class="ai-card-row" style="margin-top:8px">
          <span class="ai-card-status ${s.expiresIn < 120 ? 'warn' : ''}">${fmtEta(s.expiresIn, t)}</span>
          <span style="flex:1"></span>
          <button class="chip-btn" data-act="approve" data-id="${esc(s.id)}">✅ ${esc(t('pending_approve', 'Approva'))}</button>
          <button class="chip-btn danger" data-act="reject" data-id="${esc(s.id)}">✖ ${esc(t('pending_reject', 'Rifiuta'))}</button>
        </div>
      </div>`;
  }

  function recentCard(rec, t, now) {
    const s = summarize(rec, now);
    const o = outcomeOf(s, t);
    const by = s.decidedBy ? ` · ${fmtBy(s.decidedBy, t)}` : '';
    const when = fmtTime(s.decidedAt || rec.expires_at);
    return `
      <div class="ai-card recent-card" data-request="${esc(s.id)}">
        ${head(s, t)}
        <div class="ai-card-row" style="margin-top:6px">
          <span class="ai-card-status ${o.cls}">${o.text}</span>
          <span class="hint">${esc(when)}${by}</span>
        </div>
      </div>`;
  }

  // Sidebar badge: how many requests wait, capped so the dot stays a dot.
  function badgeText(n) {
    if (!n || n < 1) return '';
    return n > 9 ? '9+' : String(n);
  }

  const ApprovalsView = { summarize, pendingCard, recentCard, outcomeOf, fmtEta, badgeText, addressOf };
  if (typeof window !== 'undefined') window.ApprovalsView = ApprovalsView;
  if (typeof module !== 'undefined' && module.exports) module.exports = ApprovalsView;
})();
