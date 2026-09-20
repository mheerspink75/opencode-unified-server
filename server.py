#!/usr/bin/env python3
"""Unified OpenCode server: backend session viewer, pages, auto-refresh,
and multi-tab debugging all in one self-contained file.

Runs entirely from a single process on http://127.0.0.1:5000 with no second
command and no second process. Session data is read from the OpenCode backend at
http://127.0.0.1:4096 (external, read-only); everything else is served here.

Endpoints:
  GET  /                               bot battle frontend (index.html)
  GET  /index.html                     bot battle frontend (same page)
  GET  /tabs                           tabs viewer
  GET  /tabs/view?id=<id>              single tab viewer
  GET  /tabs/multiplex                 multiplex (iframes) viewer
  GET  /debug                          debug dashboard
  GET  /server/<b64>/session/<id>      backend session viewer
  GET  /css/styles.css                 chat frontend stylesheet
  GET  /js/app.js                      chat frontend script
  GET  /js/viewer.js                   shared message renderer for chat + viewers
  GET  /models, /api/models            all models available from the backend
  GET  /api/tabs/full                  all tabs with full transcripts
  GET  /api/tab/<id>/html              tab snapshot (title, url, messages)
  GET  /api/sessions/full              all sessions and full transcripts
  GET  /api/sessions                   session list for the frontend sidebar
  GET  /api/history?session_id=<id>    one session's messages
  POST /api/chat/stream                SSE chat stream (create/reuse session, relay text)
  POST /api/delete                     delete a session
  <anything else>                      forwarded to the backend (status/headers kept)

All pages auto-refresh every 2 seconds, use absolute URLs only (port always
present, no "//", no relative fetches), and are fully self-contained with no
client-side state. Output is deterministic: no randomness and no timestamps.

Zero-dependency (Python standard library only). The OpenCode binary is never
modified or assumed file-accessible.

Run:  python3 server.py
The default browser opens with five tabs. Interrupt with Ctrl+C to stop cleanly.
"""

import base64
import json
import re
import signal
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import subprocess
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 5000

BACKEND = "http://127.0.0.1:4096"

SERVER_LABEL = "local"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")

PROXY_BASE = f"http://{HOST}:{PORT}"
_PROXY_NETLOC = f"{HOST}:{PORT}"
_BACKEND_NETLOC = "127.0.0.1:4096"

INDEX_HTML = Path(__file__).resolve().parent / "index.html"
CSS_FILE = Path(__file__).resolve().parent / "css" / "styles.css"
APP_JS = Path(__file__).resolve().parent / "js" / "app.js"
VIEWER_JS = Path(__file__).resolve().parent / "js" / "viewer.js"


def fetch_models():
    """Return all models available from the OpenCode backend. Returns an empty
    list if the backend cannot be reached."""
    try:
        data = http_get_json("/config/providers")
        providers = data.get("providers", []) if isinstance(data, dict) else data
        models = []
        for p in providers:
            pname = p.get("name", "")
            for mid, m in (p.get("models", {}) or {}).items():
                name = m.get("name", mid) if isinstance(m, dict) else mid
                models.append({
                    "id": (p.get("id", "") + "/" + mid),
                    "name": (pname + ": " + name) if pname else name,
                })
        return models
    except Exception:
        return []


def b64(s):
    """URL-safe base64, unpadded, as used by OpenCode viewer URLs."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


SERVER_B64 = b64(SERVER_LABEL)


# ---------------------------------------------------------------------------
# URL construction and port preservation.
# The server never emits a URL a browser can rewrite: every URL is absolute with
# an explicit scheme, host and port, and every path is normalized (single leading
# slash, never "//", never a missing port).
# ---------------------------------------------------------------------------

def normalize_path(path):
    """Normalize a path: strip leading slashes, collapse duplicate slashes, keep
    any query. The result never begins with '//' and has no stray leading slash."""
    if not path:
        return ""
    base, sep, query = path.partition("?")
    cleaned = re.sub(r"/{2,}", "/", base).strip("/")
    return (cleaned + sep + query) if sep else cleaned


def _join(scheme, netloc, path):
    """Build 'scheme://netloc/<normalized path>'. Port is part of netloc, so it is
    always present; the path never begins with '//'."""
    np = normalize_path(path)
    return scheme + "://" + netloc + ("/" + np if np else "")


def build_absolute(path):
    """Absolute browser-facing URL on the proxy origin, port always present."""
    return _join("http", _PROXY_NETLOC, path)


def backend_url(path):
    """Absolute backend URL, port always present."""
    return _join("http", _BACKEND_NETLOC, path)


def validate_url(url, expect_port=PORT):
    """Validate a URL and return a corrected absolute URL with the port guaranteed
    present and a normalized path. Returns None if it cannot be made safe."""
    if not url:
        return None
    if url.startswith("//"):
        url = "http:" + url
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return None
    scheme = (u.scheme or "http").lower()
    if scheme != "http":
        return None
    host = (u.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return None
    port = u.port if u.port else expect_port
    return _join(scheme, host + ":" + str(port),
                 u.path + ("?" + u.query if u.query else ""))


def safety_rewrite_url(location):
    """Safety layer: rewrite any URL sent to a browser so it is absolute, on the
    proxy origin (port 5000), with a normalized path. Repairs relative,
    scheme-relative, portless and backend-forwarded URLs so the browser always
    stays on the proxy and can never drop the port."""
    if not location:
        return location
    if location.startswith("//"):
        location = "http:" + location
    try:
        u = urllib.parse.urlsplit(location)
    except Exception:
        return build_absolute(location)
    if u.scheme and u.netloc:
        host = (u.hostname or "").lower()
        path = u.path + ("?" + u.query if u.query else "")
        if host in ALLOWED_HOSTS and u.port != PORT:
            return _join("http", _PROXY_NETLOC, path)
        return validate_url(location) or build_absolute(path)
    return build_absolute(u.path + ("?" + u.query if u.query else ""))


# ---------------------------------------------------------------------------
# Backend session logic.
# ---------------------------------------------------------------------------

def http_get_json(path):
    """GET a JSON endpoint from the backend."""
    with urllib.request.urlopen(backend_url(path), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_json(path, payload):
    """POST a JSON payload to the backend and return the JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        backend_url(path), data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


def list_sessions():
    """Return the list of open sessions from the backend."""
    data = http_get_json("/session")
    if isinstance(data, list):
        return data
    return data.get("sessions", data.get("items", []))


def default_session_id():
    """Pick a stable default session id for the auto-opened tabs."""
    try:
        sessions = list_sessions()
        if sessions:
            return sessions[0]["id"]
    except Exception:
        pass
    return "default"


def fetch_messages(session_id):
    """Flatten one session's backend messages into [{role, text}] for the user
    and assistant roles only (the same view the chat UI shows). Uses the backend
    /session/<id>/message endpoint, which is the one that reliably exists."""
    try:
        raw = http_get_json("/session/" + urllib.parse.quote(session_id) + "/message")
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("messages", [])
    msgs = []
    for m in raw if isinstance(raw, list) else []:
        role = m.get("info", {}).get("role") or m.get("role")
        parts = m.get("parts")
        if isinstance(parts, list):
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        else:
            text = m.get("text", "") or ""
        if role in ("user", "assistant") and text.strip():
            msgs.append({"role": role, "text": text})
    return msgs


def build_tab(s):
    """Build a tab snapshot dict for one session."""
    session_id = s["id"]
    return {
        "tabId": session_id,
        "title": s.get("title", "Untitled"),
        "url": build_absolute("/server/" + SERVER_B64 + "/session/" + session_id),
        "messages": fetch_messages(session_id),
    }


# ---------------------------------------------------------------------------
# HTML viewer pages. All pages auto-refresh every 2 seconds, use absolute URLs,
# and are fully self-contained.
# ---------------------------------------------------------------------------

PAGE_CSS = """
:root{--code-bg:#26272e;--code-border:#3a3b44;--code-radius:8px;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
body{font-family:system-ui,sans-serif;background:#1e1f24;color:#e6e6e6;margin:0;padding:1rem;}
h1{font-size:1.3rem;}
.nav{display:flex;gap:1rem;margin:1rem 0;}
a{color:#6fa8ff;}
.tab{border:1px solid #3a3b44;border-radius:8px;padding:1rem;margin:1rem 0;background:#17181c;overflow-wrap:anywhere;}
.tab h2{font-size:1rem;margin:0 0 .25rem;}
.meta{font-size:.75rem;opacity:.6;margin-bottom:.5rem;}
.msg{margin:.35rem 0;line-height:1.45;}
.role{font-weight:600;font-size:.8rem;opacity:.7;}
.codeblock{background:var(--code-bg);border:1px solid var(--code-border);border-radius:var(--code-radius);padding:.6rem .8rem;margin:.4rem 0;font-family:var(--mono);font-size:.85rem;line-height:1.5;white-space:pre-wrap;overflow-x:auto;}
.codeblock code{font-family:inherit;display:block;}
iframe{width:100%;height:62vh;border:1px solid #3a3b44;border-radius:8px;background:#17181c;}
"""

PAGE_JS = ('var PROXY_BASE = "' + PROXY_BASE + '";\n'
           + VIEWER_JS.read_text(encoding="utf-8"))

AUTO_REFRESH_JS = "setTimeout(function(){ window.location.reload(); }, 2000);"

TABS_JS = r"""
async function load(){
  const box=document.getElementById('box');
  let d;try{d=await (await fetch(PROXY_BASE+'/api/tabs/full')).json();}catch(e){box.innerHTML='<div class="tab">error: '+escapeHtml(e.message)+'</div>';return;}
  box.innerHTML='';
  for(const t of (d.tabs||[])){
    const el=document.createElement('div');el.className='tab';
    el.innerHTML='<h2>'+escapeHtml(t.title)+'</h2><div class="meta">id: '+escapeHtml(t.tabId)+' · url: <a href="'+escapeHtml(t.url)+'">'+escapeHtml(t.url)+'</a> · '+(t.messages||[]).length+' msgs</div>'+bubbles(t.messages);
    box.appendChild(el);
  }
}
load();
"""

TAB_VIEW_JS = r"""
async function load(){
  const id=new URLSearchParams(location.search).get('id')||'';
  const box=document.getElementById('box');
  let t;try{t=await (await fetch(PROXY_BASE+'/api/tab/'+encodeURIComponent(id)+'/html')).json();}catch(e){box.innerHTML='<div class="tab">error: '+escapeHtml(e.message)+'</div>';return;}
  if(t.error){box.innerHTML='<div class="tab">not found: '+escapeHtml(t.error)+'</div>';return;}
  box.innerHTML='<div class="tab"><h2>'+escapeHtml(t.title)+'</h2><div class="meta">id: '+escapeHtml(t.tabId)+' · url: <a href="'+escapeHtml(t.url)+'">'+escapeHtml(t.url)+'</a></div>'+bubbles(t.messages)+'</div>';
}
load();
"""

MULTIPLEX_JS = r"""
async function load(){
  const box=document.getElementById('frames');
  let d;try{d=await (await fetch(PROXY_BASE+'/api/tabs/full')).json();}catch(e){box.innerHTML='<div class="tab">error: '+escapeHtml(e.message)+'</div>';return;}
  for(const t of (d.tabs||[])){
    const w=document.createElement('div');w.className='tab';
    w.innerHTML='<h2>'+escapeHtml(t.title)+'</h2><iframe src="'+PROXY_BASE+'/tabs/view?id='+encodeURIComponent(t.tabId)+'"></iframe>';
    box.appendChild(w);
  }
}
load();
"""

DEBUG_JS = r"""
async function load(){
  const box=document.getElementById('box');
  let tabs, sessions;
  try{tabs=await (await fetch(PROXY_BASE+'/api/tabs/full')).json();}catch(e){tabs={tabs:[]};}
  try{sessions=await (await fetch(PROXY_BASE+'/api/sessions/full')).json();}catch(e){sessions={sessions:[]};}
  let h='<section class="tab"><h2>Tabs ('+(tabs.tabs||[]).length+')</h2>';
  for(const t of (tabs.tabs||[])){h+='<div class="msg"><b>'+escapeHtml(t.title)+'</b> <span class="role">'+escapeHtml(t.tabId)+'</span><br>'+bubbles(t.messages)+'</div>';}
  h+='</section><section class="tab"><h2>Sessions ('+(sessions.sessions||[]).length+')</h2>';
  for(const s of (sessions.sessions||[])){h+='<div class="msg"><b>'+escapeHtml(s.title)+'</b> <span class="role">'+escapeHtml(s.id)+'</span> — '+(s.messages||[]).length+' msgs<br>'+bubbles(s.messages)+'</div>';}
  h+='</section>';
  box.innerHTML=h;
}
load();
"""

_NAV = ("<div class='nav'><a href='" + build_absolute("/") + "'>/</a>"
        "<a href='" + build_absolute("/tabs") + "'>/tabs</a>"
        "<a href='" + build_absolute("/debug") + "'>/debug</a>"
        "<a href='" + build_absolute("/tabs/multiplex") + "'>multiplex</a></div>")


def page(title, body_html, script):
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>" + title +
            "</title><style>" + PAGE_CSS + "</style></head><body><h1>" + title + "</h1>" +
            body_html + "<script>" + PAGE_JS + "\n" + script + "\n" + AUTO_REFRESH_JS +
            "</script></body></html>")


def page_tabs():
    return page("Open Tabs", _NAV + "<div id='box'></div>", TABS_JS)


def page_tab_view():
    """Single-tab viewer; the session id comes from ?id= in the URL (TAB_VIEW_JS)."""
    return page("Tab View", _NAV + "<div id='box'></div>", TAB_VIEW_JS)


def page_multiplex():
    return page("Tab Multiplex", _NAV + "<div id='frames'></div>", MULTIPLEX_JS)


def page_debug():
    return page("Debug Dashboard", _NAV + "<div id='box'></div>", DEBUG_JS)


BACKEND_VIEWER_JS = r"""
async function load(){
  const box=document.getElementById('box');
  let d;
  try{d=await (await fetch(PROXY_BASE+'/api/tab/'+encodeURIComponent(BACKEND_VIEWER_ID)+'/html')).json();}
  catch(e){box.innerHTML='<div class="tab">error: '+escapeHtml(e.message)+'</div>';return;}
  if(d.error){box.innerHTML='<div class="tab">not found: '+escapeHtml(d.error)+'</div>';return;}
  box.innerHTML='<div class="tab"><h2>'+escapeHtml(d.title)+'</h2><div class="meta">id: '+escapeHtml(d.tabId)+' · url: <a href="'+escapeHtml(d.url)+'">'+escapeHtml(d.url)+'</a></div>'+bubbles(d.messages)+'</div>';
}
load();
"""


def page_backend_viewer(session_id):
    """Backend session viewer: client-rendered transcript, auto-refreshing."""
    script = ("var BACKEND_VIEWER_ID = " + json.dumps(session_id) + ";\n" + BACKEND_VIEWER_JS)
    return page("Backend Session " + session_id, _NAV + "<div id='box'></div>", script)


# ---------------------------------------------------------------------------
# HTTP handler: routes + backend forwarding.
# ---------------------------------------------------------------------------

STATIC = {
    "/css/styles.css": (CSS_FILE, "text/css; charset=utf-8"),
    "/js/app.js": (APP_JS, "application/javascript; charset=utf-8"),
    "/js/viewer.js": (VIEWER_JS, "application/javascript; charset=utf-8"),
}

class Handler(BaseHTTPRequestHandler):
    server_version = "OpenCodeUnified/1.0"

    def log_message(self, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body, status=200):
        body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type="text/html; charset=utf-8"):
        body = Path(path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return None

    def _sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_chat(self, body):
        """Bridge the frontend's /api/chat/stream to the OpenCode backend: create
        or reuse a session, subscribe to the backend event stream, post the prompt,
        and relay the assistant's text deltas as SSE to the browser."""
        try:
            provider, model = body["model"].split("/", 1)
            sid = body.get("session_id")
            new_session = body.get("new_session") is True
            if new_session:
                sid = http_post_json("/session", {"title": body.get("title") or "Web chat"})["id"]
            elif not sid:
                self._send_json({"error": "session_id required (or set new_session=true)"}, 400)
                return
        except Exception as e:
            self._send_json({"error": str(e)}, 502)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self._sse({"session_id": sid})

        events = None
        try:
            # Subscribe to the backend event stream BEFORE posting the prompt.
            events = urllib.request.urlopen(backend_url("/event"), timeout=600)
            http_post_json("/session/" + urllib.parse.quote(sid) + "/prompt_async", {
                "model": {"providerID": provider, "modelID": model},
                "parts": [{"type": "text", "text": body["text"]}],
            })
            part_types = {}
            for line in events:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                try:
                    ev = json.loads(line[5:].decode("utf-8"))
                except Exception:
                    continue
                props = ev.get("properties", {})
                if props.get("sessionID") != sid:
                    continue
                if ev.get("type") == "message.part.updated":
                    part = props.get("part", {})
                    part_types[part.get("id")] = part.get("type")
                elif (ev.get("type") == "message.part.delta"
                      and props.get("field") == "text"
                      and part_types.get(props.get("partID")) == "text"):
                    self._sse({"delta": props.get("delta", "")})
                elif ev.get("type") in ("session.idle", "session.error"):
                    if ev.get("type") == "session.error":
                        err = props.get("error")
                        if isinstance(err, dict):
                            err = err.get("message") or json.dumps(err)
                        self._sse({"error": str(err)})
                    self._sse({"done": True})
                    return
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._sse({"error": str(e)})
            except Exception:
                pass
        finally:
            if events is not None:
                try:
                    events.close()
                except Exception:
                    pass

    def _forward(self):
        parts = urllib.parse.urlsplit(self.path)
        target = BACKEND + parts.path
        if parts.query:
            target += "?" + parts.query
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else None
        out_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "connection", "content-length",
                                 "transfer-encoding", "accept-encoding")
        }
        req = urllib.request.Request(target, data=body, method=self.command,
                                     headers=out_headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                status = resp.status
                headers = resp.getheaders()
                data = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            headers = e.headers.items() if hasattr(e.headers, "items") else []
            data = e.read()
        except urllib.error.URLError as e:
            self._send_json({"error": "backend unreachable: " + str(e)}, 502)
            return
        self.send_response(status)
        hop = ("content-length", "transfer-encoding", "connection",
               "content-encoding", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers", "upgrade")
        for k, v in headers:
            if k.lower() not in hop:
                if k.lower() == "location":
                    v = safety_rewrite_url(v)
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_file(str(INDEX_HTML))
            elif path in STATIC:
                _file, _ctype = STATIC[path]
                self._send_file(str(_file), _ctype)
            elif path in ("/models", "/api/models"):
                self._send_json({"models": fetch_models()})
            elif path == "/api/sessions":
                sessions = [{
                    "id": s["id"],
                    "title": s.get("title", "Untitled"),
                    "updated": s.get("time", {}).get("updated", ""),
                } for s in list_sessions()]
                sessions.sort(key=lambda s: s["updated"], reverse=True)
                self._send_json({"sessions": sessions[:50]})
            elif path.startswith("/api/history"):
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                sid = qs.get("session_id", [""])[0]
                self._send_json({"messages": fetch_messages(sid)})
            elif path == "/api/tabs/full":
                self._send_json({"tabs": [build_tab(s) for s in list_sessions()]})
            elif path == "/api/sessions/full":
                self._send_json({"sessions": [{
                    "id": s["id"],
                    "title": s.get("title", "Untitled"),
                    "updated": s.get("time", {}).get("updated", ""),
                    "messages": fetch_messages(s["id"]),
                } for s in list_sessions()]})
            elif path.startswith("/api/tab/") and path.endswith("/html"):
                sid = path[len("/api/tab/"):-len("/html")]
                match = next((s for s in list_sessions() if s["id"] == sid), None)
                if match:
                    self._send_json(build_tab(match))
                else:
                    self._send_json({
                        "tabId": sid, "title": "Untitled",
                        "url": build_absolute("/server/" + SERVER_B64 + "/session/" + sid),
                        "messages": [],
                    })
            elif path == "/tabs":
                self._send_html(page_tabs())
            elif path == "/tabs/view":
                self._send_html(page_tab_view())
            elif path == "/tabs/multiplex":
                self._send_html(page_multiplex())
            elif path == "/debug":
                self._send_html(page_debug())
            elif re.match(r"^/server/[^/]+/session/[^/]+$", path):
                parts = path.split("/")
                sid = parts[-1]
                self._send_html(page_backend_viewer(sid))
            else:
                self._forward()
        except Exception as e:
            try:
                self._send_json({"error": str(e)}, 502)
            except Exception:
                pass

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        body = self.read_body()
        if body is None:
            return self._send_json({"error": "invalid JSON"}, 400)
        if path == "/api/chat/stream":
            self._stream_chat(body)
        elif path == "/api/delete":
            try:
                sid = body["session_id"]
                req = urllib.request.Request(
                    backend_url("/session/" + urllib.parse.quote(sid)), method="DELETE")
                urllib.request.urlopen(req, timeout=600)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 502)
        else:
            self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def do_OPTIONS(self):
        self._forward()

    def do_HEAD(self):
        self._forward()


# ---------------------------------------------------------------------------
# Startup and shutdown.
# ---------------------------------------------------------------------------

_backend_proc = None
def wait_for_port(host, port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False

def start_backend():
    global _backend_proc
    # Already running?
    try:
        with socket.create_connection(("127.0.0.1", 4096), timeout=1):
            return True
    except OSError:
        pass
    try:
        _backend_proc = subprocess.Popen(
            ["opencode", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("Failed to start opencode:", e)
        return False
    if not wait_for_port("127.0.0.1", 4096, timeout=30):
        print("Timed out waiting for opencode on port 4096")
        try:
            _backend_proc.terminate()
        except Exception:
            pass
        return False
    return True



def open_tabs(default):
    """Open exactly five tabs, every URL on http://127.0.0.1:5000 with the port
    explicitly present (never a bare host, never https, never port 80/443)."""
    base = f"http://127.0.0.1:{PORT}"
    urls = [
        base + "/",
        base + "/tabs",
        base + "/tabs/view?id=" + urllib.parse.quote(default),
        base + "/tabs/multiplex",
        base + "/server/" + SERVER_B64 + "/session/" + urllib.parse.quote(default),
    ]
    for u in urls:
        threading.Thread(target=webbrowser.open, args=(u,), daemon=True).start()


def main():
    if not start_backend():
        print("Startup failed")
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    default = default_session_id()
    open_tabs(default)
    print("Server started")

    def _handle_sigint(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_sigint)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()

        global _backend_proc
        if _backend_proc is not None:
            try:
                _backend_proc.terminate()
                _backend_proc.wait(timeout=5)
            except Exception:
                try:
                    _backend_proc.kill()
                except Exception:
                    pass

    print("Server stopped")



if __name__ == "__main__":
    main()