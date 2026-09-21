/* Opencode Chat — bot battle frontend logic.
   Served at /js/app.js by server.py; depends on /js/viewer.js for
   escapeHtml / renderContent (loaded first). */

const chat = document.getElementById('chat');
const input = document.getElementById('input');
const modelA = document.getElementById('modelA');
const modelB = document.getElementById('modelB');
const statusEl = document.getElementById('status');
const sessionsEl = document.getElementById('sessions');
const sidebar = document.getElementById('sidebar');
const battleBtn = document.getElementById('battle');
const sendBtn = document.getElementById('send');
const stopBtn = document.getElementById('stop');
const battlebar = document.getElementById('battlebar');
const battleturn = document.getElementById('battleturn');
const sessFilter = document.getElementById('sessfilter');
const newchatBtn = document.getElementById('newchat');
const toastsEl = document.getElementById('toasts');

/* ---------- Small UI utilities ---------- */
function toast(msg) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  toastsEl.appendChild(t);
  setTimeout(() => t.remove(), 5000);
}

/* Disable/enable controls that must not fire while a stream is active. */
function setBusyUI(b) {
  sendBtn.disabled = b;
  battleBtn.disabled = b;
  newchatBtn.disabled = b;
}

const EMPTY_HTML = document.getElementById('empty').outerHTML;   // saved for reinsertion
function updateEmpty() {
  let empty = document.getElementById('empty');
  if (!empty && !chat.querySelector('.msg')) {
    chat.insertAdjacentHTML('afterbegin', EMPTY_HTML);
    empty = document.getElementById('empty');
    for (const b of empty.querySelectorAll('.prompt')) wirePrompt(b);
  }
  if (empty) empty.style.display = chat.querySelector('.msg') ? 'none' : '';
}

function wirePrompt(b) {
  b.onclick = () => {
    let t = b.textContent;
    if (t.startsWith('⚔') && !battleMode) battleBtn.click();
    input.value = t.replace(/^⚔ Battle:\s*/, '');
    autoGrow();
    input.focus();
  };
}

/* Attach one copy button per code block inside `root` (idempotent). */
function addCodeCopyButtons(root) {
  for (const wrap of root.querySelectorAll('.codewrap')) {
    if (wrap.querySelector('.actions')) continue;
    const btns = document.createElement('div');
    btns.className = 'actions';
    const b = document.createElement('button');
    b.textContent = 'Copy';
    b.title = 'Copy code';
    b.onclick = () => {
      const code = wrap.querySelector('code');
      navigator.clipboard.writeText(code ? code.innerText : '').then(() => {
        b.textContent = 'Copied!';
        setTimeout(() => b.textContent = 'Copy', 1200);
      });
    };
    btns.appendChild(b);
    wrap.appendChild(btns);
  }
}

let sessionId = localStorage.getItem('oc-chat-session') || null;   // your chat with Bot A
let battleSession = null;               // ONE shared battle-mode session (no per-bot fork)
let activeController = null;            // in-flight stream's AbortController (Stop can cancel it)
let busy = false;
let battleMode = false;
let battleAbort = false;

/* ---------- Avatar component factory ---------- */
function makeAvatar(el, captionEl) {
  const STATES = ['idle', 'thinking', 'speaking', 'error'];
  let errorTimer = null;
  return {
    setState(s) {
      if (!STATES.includes(s)) return;
      clearTimeout(errorTimer);
      el.dataset.state = s;
      if (s === 'thinking') captionEl.textContent = '';
      if (s === 'error') errorTimer = setTimeout(() => this.setState('idle'), 4000);
    },
    setMessage(text) {
      captionEl.textContent = text || '';
      captionEl.title = text || '';
    },
    get state() { return el.dataset.state; }
  };
}
const avatarA = makeAvatar(document.getElementById('avatarA'), document.getElementById('capA'));
const avatarB = makeAvatar(document.getElementById('avatarB'), document.getElementById('capB'));

/* ---------- Model dropdowns ---------- */
async function loadModels() {
  try {
    const r = await fetch('/api/models');
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    for (const sel of [modelA, modelB]) {
      sel.innerHTML = '';
      for (const m of data.models) {
        const o = document.createElement('option');
        o.value = m.id;
        o.textContent = m.name;
        sel.appendChild(o);
      }
    }
    const savedA = localStorage.getItem('oc-chat-model');
    const savedB = localStorage.getItem('oc-chat-model-b');
    modelA.value = savedA || 'nvidia/deepseek-ai/deepseek-v4-flash-0731';   // Bot A: DeepSeek V4 Flash 0731
    modelB.value = savedB || 'opencode/big-pickle';                          // Bot B: Big Pickle
    for (const sel of [modelA, modelB]) {
      if (![...sel.options].some(o => o.value === sel.value)) {
        sel.selectedIndex = sel.selectedIndex === 0 && sel.options.length > 1 ? 1 : 0;
      }
    }
    updateNames();
    statusEl.textContent = data.models.length ? '' : 'no models found in opencode';
  } catch (e) {
    statusEl.textContent = 'models failed to load: ' + e.message;
    toast('Models failed to load: ' + e.message);
  }
}

function shortName(sel) {
  const t = sel.options[sel.selectedIndex]?.textContent || '?';
  return t.replace(/^[^:]+:\s*/, '');   // strip provider prefix
}
function updateNames() {
  document.getElementById('nameA').textContent = 'A: ' + shortName(modelA);
  document.getElementById('nameB').textContent = 'B: ' + shortName(modelB);
}
modelA.onchange = () => { localStorage.setItem('oc-chat-model', modelA.value); updateNames(); };
modelB.onchange = () => { localStorage.setItem('oc-chat-model-b', modelB.value); updateNames(); };

/* ---------- Sidebar ---------- */
let sessionsCache = [];   // last /api/sessions payload, for client-side filtering

function renderSessions() {
  const q = sessFilter.value.trim().toLowerCase();
  sessionsEl.innerHTML = '';
  for (const s of sessionsCache) {
    if (q && !s.title.toLowerCase().includes(q)) continue;
    const d = document.createElement('div');
    d.className = 'sess' + (s.id === sessionId ? ' active' : '');
    const label = document.createElement('div');
    label.className = 'label';
    label.append(document.createTextNode(s.title));
    const sm = document.createElement('small');
    sm.textContent = new Date(s.updated).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    label.append(sm);
    label.title = s.title + ' (double-click to rename)';
    label.onclick = () => openSession(s.id);
    label.ondblclick = () => renameSession(s, label);
    const del = document.createElement('button');
    del.className = 'del';
    del.textContent = '✕';
    del.title = 'Delete this chat';
    del.onclick = e => { e.stopPropagation(); deleteSession(s.id, s.title); };
    d.append(label, del);
    sessionsEl.appendChild(d);
  }
}

function renameSession(s, label) {
  if (busy) return;
  const inp = document.createElement('input');
  inp.className = 'rename';
  inp.value = s.title;
  label.replaceWith(inp);
  inp.focus();
  inp.select();
  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    const title = inp.value.trim();
    if (commit && title && title !== s.title) {
      try {
        const r = await fetch('/api/session/' + encodeURIComponent(s.id), {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title })
        });
        const data = await r.json();
        if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
        s.title = title;
      } catch (e) {
        toast('Rename failed: ' + e.message);
      }
    }
    loadSessions();
  };
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') finish(false);
  };
  inp.onblur = () => finish(true);
}

async function loadSessions() {
  try {
    const r = await fetch('/api/sessions');
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    sessionsCache = data.sessions;
    renderSessions();
  } catch (e) { /* leave sidebar as-is */ }
}
sessFilter.addEventListener('input', renderSessions);

async function deleteSession(sid, title) {
  if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
  try {
    const r = await fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid })
    });
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    if (sid === sessionId) {
      sessionId = null;
      localStorage.removeItem('oc-chat-session');
      chat.innerHTML = '';
    }
    loadSessions();
  } catch (e) {
    toast('Delete failed: ' + e.message);
  }
}

async function openSession(sid) {
  if (busy) return;
  sessionId = sid;
  localStorage.setItem('oc-chat-session', sid);
  chat.innerHTML = '';
  loadSessions();
  try {
    const r = await fetch('/api/history?session_id=' + encodeURIComponent(sid));
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    const frag = document.createDocumentFragment();   // batch builds to avoid layout thrashing
    for (const m of data.messages) frag.append(makeMsg(m.role, m.text));
    chat.append(frag);
    addCodeCopyButtons(chat);
    updateEmpty();
    chat.scrollTop = chat.scrollHeight;
  } catch (e) {
    addMsg('error', 'Could not load history: ' + e.message);
    toast('Could not load history: ' + e.message);
  }
}

/* Message rendering (escapeHtml / renderContent) lives in /js/viewer.js,
   shared with the server-side viewer pages. */

/* Builds a message bubble element WITHOUT appending it (used by reload batching).
   `onRetry` (optional) adds a Retry button to the bubble's hover actions. */
function makeMsg(role, text, who, onRetry) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.title = new Date().toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' });
  d._raw = text;
  if (who) {
    const w = document.createElement('span');
    w.className = 'who';
    w.textContent = who;
    d.append(w);
  }
  const c = document.createElement('div');
  c.className = 'content';
  c.innerHTML = renderContent(text);
  d._content = c;
  d.append(c);
  const actions = document.createElement('div');
  actions.className = 'actions';
  const copy = document.createElement('button');
  copy.textContent = 'Copy';
  copy.title = 'Copy message text';
  copy.onclick = () => {
    navigator.clipboard.writeText(d._raw).then(() => {
      copy.textContent = 'Copied!';
      setTimeout(() => copy.textContent = 'Copy', 1200);
    });
  };
  actions.appendChild(copy);
  if (onRetry) {
    const retry = document.createElement('button');
    retry.textContent = 'Retry';
    retry.title = 'Try this request again';
    retry.onclick = onRetry;
    actions.appendChild(retry);
  }
  d.append(actions);
  return d;
}

function addMsg(role, text, who, onRetry) {
  const d = makeMsg(role, text, who, onRetry);
  chat.appendChild(d);
  addCodeCopyButtons(d);
  updateEmpty();
  chat.scrollTop = chat.scrollHeight;
  return d;
}

/* ---------- Core streaming call ---------- */
/* Streams one reply for `model` into a new bubble; returns {text, session_id, error}.
   Uses an AbortController so ⚔ Stop can cancel the fetch immediately; renderContent()
   is rAF-debounced to at most one DOM write per frame. */
async function streamReply({ model, text, session_id, new_session, avatar, who, title, onRetry }) {
  avatar.setState('thinking');
  const bubble = addMsg('assistant', '…', who);
  const ac = new AbortController();
  activeController = ac;
  const timer = setTimeout(() => ac.abort(), 180000);   // never hang forever on one turn
  let raf = 0;
  try {
    const r = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, text, session_id, new_session, title }),
      signal: ac.signal
    });
    if (!r.ok) throw new Error((await r.text()) || ('HTTP ' + r.status));
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let out = '', buf = '', sid = session_id;
    for (; ;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(5)); } catch { continue; }
        if (ev.session_id) sid = ev.session_id;
        if (ev.error) throw new Error(ev.error);
        if (ev.delta) {
          if (!out) avatar.setState('speaking');
          out += ev.delta;
          if (!raf) raf = requestAnimationFrame(() => {
            raf = 0;
            bubble._content.innerHTML = renderContent(out);
            chat.scrollTop = chat.scrollHeight;   // scroll anchoring
          });
        }
      }
    }
    if (raf) cancelAnimationFrame(raf);
    bubble._raw = out || '(empty reply)';
    bubble._content.innerHTML = renderContent(bubble._raw);
    addCodeCopyButtons(bubble);
    avatar.setMessage(out.slice(0, 120));
    avatar.setState('idle');
    return { text: out, session_id: sid, error: null };
  } catch (e) {
    if (raf) cancelAnimationFrame(raf);
    if (battleAbort) {   // user stopped: clean exit, no error bubble, session preserved
      bubble._raw = '(stopped)';
      bubble._content.innerHTML = renderContent(bubble._raw);
      avatar.setState('idle');
      return { text: '', session_id, error: null };
    }
    const msg = (e && e.message) ? e.message : 'Request failed';
    bubble.className = 'msg error';             // surface server/provider errors visibly
    bubble._raw = msg;
    bubble._content.innerHTML = renderContent(msg);
    if (onRetry) {
      const retry = document.createElement('button');
      retry.textContent = 'Retry';
      retry.title = 'Try this request again';
      retry.onclick = () => { bubble.remove(); onRetry(); };
      const actions = bubble.querySelector('.actions');
      if (actions) actions.appendChild(retry);
    }
    toast(msg);
    avatar.setState('error');
    avatar.setMessage(msg.slice(0, 80));
    return { text: '', session_id, error: msg };
  } finally {
    clearTimeout(timer);
    if (activeController === ac) activeController = null;
  }
}

/* ---------- You chat with Bot A ---------- */
async function send(textOverride) {
  const text = (textOverride != null ? textOverride : input.value).trim();
  if (!text || busy || !modelA.value) return;
  busy = true;
  setBusyUI(true);
  input.value = '';
  autoGrow();
  localStorage.setItem('oc-chat-model', modelA.value);
  addMsg('user', text);
  stopBtn.style.display = '';   // Stop can cancel your own message too
  try {
    const res = await streamReply({
      model: modelA.value, text, session_id: sessionId, new_session: sessionId == null,
      avatar: avatarA, who: shortName(modelA), title: 'Web chat',
      onRetry: () => send(text)
    });
    if (!res.error) {   // only advance the session on success; errors are shown in-bubble
      sessionId = res.session_id;
      localStorage.setItem('oc-chat-session', sessionId);
    }
    loadSessions();
  } catch (e) {
    addMsg('error', 'Error: ' + e.message);
    toast(e.message);
    avatarA.setState('error');
    avatarA.setMessage(e.message.slice(0, 80));
  } finally {
    stopBtn.style.display = 'none';
    busy = false;
    setBusyUI(false);
  }
  input.focus();
}

/* ---------- Bot battle: A and B talk to each other ---------- */
const MAX_BATTLE_TURNS = 100;   // hard safety cap only; battle otherwise runs until agreement/stop

async function startBattle() {
  const topic = input.value.trim() || 'Debate: is the tune feature-tagged "Opencode Chat" the best way to chat with AI?';
  if (busy) return;
  busy = true;
  setBusyUI(true);
  battleAbort = false;
  battleSession = null;         // each battle = ONE fresh unified session
  input.value = '';
  autoGrow();
  sendBtn.style.display = 'none';
  stopBtn.style.display = '';
  avatarA.setState('idle');
  avatarB.setState('idle');
  addMsg('user', '⚔ Battle topic: ' + topic, 'You (referee)');
  const sys = n => `You are bot ${n} in a friendly two-bot chat for a human audience. Topic: "${topic}". Keep each reply short (2-4 sentences), casual, and respond directly to the other bot. No emojis. When you genuinely agree with the other bot, END your reply with the single token "[AGREED]" and nothing after it.`;
  let lastMsg = `Start the conversation. ${sys('A')}`;
  try {
    for (let i = 0; i < MAX_BATTLE_TURNS && !battleAbort; i++) {
      const aTurn = i % 2 === 0;
      battleturn.textContent = 'Turn ' + (i + 1) + ' — Bot ' + (aTurn ? 'A' : 'B')
        + ' (' + shortName(aTurn ? modelA : modelB) + ')';
      const res = await streamReply({
        model: aTurn ? modelA.value : modelB.value,
        text: lastMsg,
        session_id: battleSession,
        new_session: i === 0,               // create the ONE session on turn 0 only
        avatar: aTurn ? avatarA : avatarB,
        who: (aTurn ? 'A: ' : 'B: ') + shortName(aTurn ? modelA : modelB),
        title: 'Bot battle'
      });
      if (res.error) break;                 // server/provider error surfaced in the bubble
      if (!res.session_id) {
        if (battleAbort) break;              // stopped before the session was created
        throw new Error('lost session id on turn ' + i);
      }
      battleSession = res.session_id;       // reuse the same session for every later turn
      const agreed = /\[agreed\]/i.test(res.text);
      if (agreed) {
        addMsg('assistant', '🤝 The bots reached an agreement.', 'Referee');
        break;
      }
      if (!res.text) break;                 // empty reply ends the battle
      // The full transcript already lives in the session history, so the next
      // turn only needs the floor handed to the other bot — no need to repeat
      // the previous reply in the prompt (context stays linear, no echo bait).
      lastMsg = sys(aTurn ? 'B' : 'A') + '\n\nReply to the previous message.';
    }
  } catch (e) {
    addMsg('error', 'Battle error: ' + e.message);
    avatarA.setState('error');
    avatarB.setState('error');
  }
  battleAbort = false;
  busy = false;
  setBusyUI(false);
  battleturn.textContent = '';
  sendBtn.style.display = '';
  stopBtn.style.display = 'none';
  loadSessions();
}

/* ---------- Wiring ---------- */
sendBtn.onclick = () => battleMode ? startBattle() : send();
stopBtn.onclick = () => {
  if (!busy) return;   // no active stream to stop
  battleAbort = true;
  if (activeController) activeController.abort();   // immediately cancel the active fetch/SSE
};
battleBtn.onclick = () => {
  if (busy) return;   // don't toggle battle mode while a stream is active
  battleMode = !battleMode;
  battleBtn.classList.toggle('on', battleMode);
  battlebar.hidden = !battleMode;
  battleturn.textContent = battleMode ? 'Give the bots a topic and press Start' : '';
  input.placeholder = battleMode
    ? 'Give the bots a topic, then press Enter/Start…'
    : 'Type a message… (Enter to send, Shift+Enter for newline)';
  sendBtn.textContent = battleMode ? 'Start' : 'Send';
};
newchatBtn.onclick = () => {
  if (busy) return;   // don't start a new chat while a stream is active
  sessionId = null;
  localStorage.removeItem('oc-chat-session');
  chat.querySelectorAll('.msg').forEach(m => m.remove());
  updateEmpty();
  loadSessions();
  input.focus();
};
document.getElementById('toggle').onclick = () => {
  sidebar.classList.toggle('collapsed');
  localStorage.setItem('oc-chat-sidebar', sidebar.classList.contains('collapsed') ? '0' : '1');
};
if (localStorage.getItem('oc-chat-sidebar') === '0') sidebar.classList.add('collapsed');
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }
});

/* Textarea grows with content up to ~6 lines. */
function autoGrow() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 150) + 'px';
}
input.addEventListener('input', autoGrow);

/* Empty-state sample prompts fill the input (battle prompts also start battle mode). */
for (const b of document.querySelectorAll('#empty .prompt')) wirePrompt(b);

loadModels();
loadSessions();
if (sessionId) openSession(sessionId);