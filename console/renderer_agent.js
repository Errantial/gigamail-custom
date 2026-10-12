// renderer_agent.js — la barra in fondo alla scheda di destra: l'utente
// chiede qualsiasi cosa all'agente. Le domande tornano come risposta; le
// azioni pericolose tornano come richieste da approvare qui, nella scheda.
(function () {
  const L = (key, it) => (window.i18n && window.i18n.t) ? window.i18n.t(key) : it;
  const lang = () => (window.i18n && window.i18n.lang) || 'it';
  const $ = (id) => document.getElementById(id);
  const escHtml = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const history = [];        // ultimi giri, mandati all'agente come contesto
  let busy = false;
  let useMail = true;        // il chip "questa mail" e' attivo

  // testo dell'agente: escape, poi solo grassetto e a capo
  function formatAnswer(text) {
    return escHtml(text)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
  }

  function openMail() {
    const id = window._currentMailId;
    if (!id || !document.querySelector('#mailDetail .mail-detail-subject')) return null;
    const subj = document.querySelector('#mailDetail .mail-detail-subject');
    return { id, folder: window._currentMailFolder || null,
      subject: subj ? subj.textContent.trim() : '' };
  }

  function refreshChip() {
    const chip = $('agentChip');
    if (!chip) return;
    const mail = openMail();
    if (!mail || !useMail) { chip.hidden = true; return; }
    chip.hidden = false;
    $('agentChipText').textContent = mail.subject || L('agent_this_mail', 'Questa mail');
  }

  function scrollThread() {
    const th = $('agentThread');
    if (th) th.scrollTop = th.scrollHeight;
  }

  function addBubble(kind, html) {
    const th = $('agentThread');
    const el = document.createElement('div');
    el.className = `agent-msg ${kind}`;
    el.innerHTML = html;
    th.appendChild(el);
    $('agentBar').classList.add('has-thread');
    scrollThread();
    return el;
  }

  function addThinking() {
    return addBubble('agent thinking',
      `<span class="agent-dots"><i></i><i></i><i></i></span> ${escHtml(L('agent_thinking', 'L\'agente sta lavorando…'))}`);
  }

  function approvalCard(a) {
    const el = addBubble('approval', `
      <div class="agent-appr-head">${escHtml(L('agent_waiting', 'In attesa della tua approvazione'))}</div>
      <div class="agent-appr-title">${escHtml(a.subject || a.tool)}</div>
      ${a.summary ? `<div class="agent-appr-sum">${escHtml(a.summary)}</div>` : ''}
      <div class="agent-appr-actions">
        <button class="agent-btn ghost" data-act="reject">${escHtml(L('agent_reject', 'Rifiuta'))}</button>
        <button class="agent-btn primary" data-act="approve">${escHtml(L('agent_approve', 'Approva'))}</button>
      </div>`);
    el.dataset.requestId = a.request_id;
    const state = (cls, text) => {
      el.classList.add(cls);
      el.querySelector('.agent-appr-head').textContent = text;
      el.querySelector('.agent-appr-actions').innerHTML =
        '';
    };
    el.querySelector('[data-act="approve"]').addEventListener('click', async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        // la verifica Windows Hello / Touch ID la chiede il backend
        await window.electronAPI.approveRequest(a.request_id);
      } catch (e) {
        ev.currentTarget.disabled = false;
        addBubble('agent error', escHtml(`${L('agent_not_approved', 'Non approvata')}: ${e.message || e}`));
        return;
      }
      state('approved', L('agent_approved', 'Approvata'));
      const wait = addThinking();
      try {
        const r = await window.electronAPI.agentComplete({ request_id: a.request_id, lang: lang() });
        wait.remove();
        if (r.status === 'executed') {
          el.querySelector('.agent-appr-head').textContent = L('agent_executed', 'Approvata ed eseguita');
        }
        addBubble('agent', formatAnswer(r.answer || L('agent_done', 'Fatto.')));
        history.push({ role: 'agent', text: r.answer || '' });
      } catch (e) {
        wait.remove();
        addBubble('agent error', escHtml(e.message || String(e)));
      }
    });
    el.querySelector('[data-act="reject"]').addEventListener('click', async () => {
      try {
        await window.electronAPI.rejectRequest(a.request_id);
        state('rejected', L('agent_rejected', 'Rifiutata: non verrà eseguita'));
      } catch (e) {
        addBubble('agent error', escHtml(e.message || String(e)));
      }
    });
  }

  function autoGrow() {
    const ta = $('agentInput');
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
    $('agentSend').disabled = busy || !ta.value.trim();
  }

  async function send() {
    const ta = $('agentInput');
    const question = ta.value.trim();
    if (!question || busy) return;
    busy = true;
    ta.value = '';
    autoGrow();
    const mail = useMail ? openMail() : null;
    addBubble('user', escHtml(question) +
      (mail ? `<div class="agent-msg-ctx">${escHtml(mail.subject || L('agent_this_mail', 'Questa mail'))}</div>` : ''));
    const wait = addThinking();
    const body = {
      question, lang: lang(), history: history.slice(-6),
      account_id: (typeof activeAccountId !== 'undefined' && activeAccountId) || null,
      mail_id: mail ? mail.id : null, folder: mail ? mail.folder : null,
    };
    history.push({ role: 'user', text: question });
    try {
      const r = await window.electronAPI.agentCommand(body);
      wait.remove();
      addBubble('agent', formatAnswer(r.answer || L('no_answer', '(nessuna risposta)')));
      history.push({ role: 'agent', text: r.answer || '' });
      (r.approvals || []).forEach(approvalCard);
    } catch (e) {
      wait.remove();
      const msg = String(e.message || e);
      addBubble('agent error', escHtml(/^not found$/i.test(msg)
        ? L('agent_old_backend', 'Il backend in uso non conosce ancora questa funzione: chiudi GigaMail (anche dall\'icona vicino all\'orologio) e riaprila.')
        : /agent|agente|claude|codex/i.test(msg) && /install|trovat|found|unavailable/i.test(msg)
        ? L('agent_unavailable', 'Nessun agente trovato. Installa Claude Code o Codex e riprova.')
        : msg));
    } finally {
      busy = false;
      autoGrow();
    }
  }

  function clearThread() {
    $('agentThread').innerHTML = '';
    history.length = 0;
    $('agentBar').classList.remove('has-thread');
  }

  function init() {
    const ta = $('agentInput');
    if (!ta || !window.electronAPI || !window.electronAPI.agentCommand) return;
    ta.addEventListener('input', autoGrow);
    ta.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(); }
    });
    $('agentSend').addEventListener('click', send);
    $('agentClear').addEventListener('click', clearThread);
    $('agentChipX').addEventListener('click', () => { useMail = false; refreshChip(); });
    // nuova mail aperta: il chip torna attivo e mostra il suo oggetto
    let lastId = null;
    new MutationObserver(() => {
      if (window._currentMailId !== lastId) { lastId = window._currentMailId; useMail = true; }
      refreshChip();
    }).observe($('mailDetail'), { childList: true, subtree: false });
    refreshChip();
    autoGrow();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
