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

/* Minimal inline markdown for escaped text: `code`, **bold**, *em*, [links](url).
   Kept deliberately small to match the project's dependency-free style. */
function inlineMd(line) {
  /* Stash code spans as plain-ASCII placeholders (@@CODE0@@, …) so the bold/em
     passes can't mangle them, then restore by exact token. The token text keeps
     the restore regex from ever touching digits in entities like &#39;. */
  const keep = [];
  let s = line.replace(/`([^`\n]+)`/g, (m, c) => {
    keep.push('<code class="inline">' + c + '</code>');
    return '@@CODE' + (keep.length - 1) + '@@';
  });
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  return s.replace(/@@CODE(\d+)@@/g, (m, i) => keep[+i] != null ? keep[+i] : m);
}

function renderContent(text) {
  const esc = escapeHtml(text);
  const lines = esc.split('\n');
  let html = '', inCode = false, buf = [], curLang = '';
  for (const line of lines) {
    const fence = line.match(/^\s*```(.*?)\s*$/);
    if (fence) {
      if (inCode) {                       // closer
        inCode = false;
        html += '<div class="codewrap">'
          + (curLang ? '<div class="codehead">' + curLang + '</div>' : '')
          + '<pre class="codeblock"><code>' + buf.join('\n') + '</code></pre></div>';
        buf = [];
        curLang = '';
      } else {                            // opener; fence[1] is the language tag
        inCode = true;
        curLang = fence[1] || '';
      }
      continue;
    }
    if (inCode) buf.push(line);
    else html += inlineMd(line) + '\n';
  }
  if (inCode) {                           // unclosed fence: render the remainder as code
    html += '<div class="codewrap">'
      + (curLang ? '<div class="codehead">' + curLang + '</div>' : '')
      + '<pre class="codeblock"><code>' + buf.join('\n') + '</code></pre></div>';
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