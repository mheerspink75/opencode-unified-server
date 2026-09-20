/* Shared message-rendering helpers for the chat UI (index.html -> app.js) and
   the viewer pages (/tabs, /debug, ...). Single source of truth for
   escapeHtml, renderContent and bubbles; viewer pages inline this file
   server-side (PAGE_JS in server.py) and the chat page loads it as
   /js/viewer.js before /js/app.js.

   Rendering pipeline: escape first, then a line-based fence scan:
   - a line starting with ``` (optionally a language tag) opens a code block;
   - a following bare ``` line closes it (leading/trailing whitespace tolerated);
   - other ```-containing lines stay as code content (nested-safe);
   - a never-closed fence degrades to a code block for the remainder.
   Newlines are preserved by CSS white-space:pre-wrap. */

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderContent(text) {
  const esc = escapeHtml(text);
  const lines = esc.split('\n');
  let html = '', inCode = false, buf = [];
  for (const line of lines) {
    const fence = line.match(/^\s*```(.*?)\s*$/);
    if (fence) {
      if (inCode) {                       // closer
        inCode = false;
        html += '<pre class="codeblock"><code>' + buf.join('\n') + '</code></pre>';
        buf = [];
      } else {                            // opener (language tag in fence[1] is ignored)
        inCode = true;
      }
      continue;
    }
    if (inCode) buf.push(line);
    else html += line + '\n';
  }
  if (inCode) {                           // unclosed fence: render the remainder as code
    html += '<pre class="codeblock"><code>' + buf.join('\n') + '</code></pre>';
  }
  return html || '…';
}

function bubbles(messages) {
  let h = '';
  for (const m of messages || []) {
    h += '<div class="msg"><span class="role">' + escapeHtml(m.role || '') + ':</span> ' + renderContent(m.text || '') + '</div>';
  }
  return h;
}