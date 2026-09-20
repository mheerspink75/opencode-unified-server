#!/bin/bash
# Unified-server diagnostic for the V2 shared-serve setup.
# Diagnoses: server.py (127.0.0.1:5000), the shared opencode serve
# (127.0.0.1:4096, Basic auth opencode:<pinned>), and WSL2 localhost
# forwarding (what makes 127.0.0.1:4096 reachable from the Windows Desktop).
# Usage: bash diag.sh   (run from the repo dir)

set -u

# Resolve the pinned password the same way server.py does:
# $OPENCODE_SERVER_PASSWORD  ->  .server-password file (next to this script)
#  ->  pinned default. Must stay in sync with backend_password() in server.py.
PW_FALLBACK="shared-chat-4096"
resolve_pw() {
  if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    echo "$OPENCODE_SERVER_PASSWORD"
  elif [ -f "$(dirname "$0")/.server-password" ]; then
    cat "$(dirname "$0")/.server-password"
  else
    echo "$PW_FALLBACK"
  fi
}
PW="$(resolve_pw)"
AUTH="opencode:$PW"

echo "=== V2 Shared-Serve Diagnostic (password from env/.server-password/pinned) ==="

echo
echo "[1] server.py process running?"
pgrep -af "server.py" | grep -v diag.sh || echo "  NOT RUNNING"

echo
echo "[2] server.py listener on 5000?"
(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep ":5000" || echo "  NOT LISTENING"

echo
echo "[3] server.py index + models + sessions (proxy views)..."
curl -s -o /dev/null -w "  / -> %{http_code}\n" http://127.0.0.1:5000/
curl -s http://127.0.0.1:5000/api/models | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('  models:', len(d.get('models',[]))); [print('   ', m['id'],'|',m['name'][:45]) for m in d.get('models',[])[:627229]]" 2>/dev/null || echo "  /api/models error"
curl -s http://127.0.0.1:5000/api/sessions | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('  sessions:', len(d.get('sessions',[]))); [print('   ', s['id'],'|',(s.get('title') or '')[:40]) for s in d.get('sessions',[])[:112233]]" 2>/dev/null || echo "  /api/sessions error"

echo
echo "[4] Shared opencode serve on 4096 reachable (from WSL)?"
curl -s -o /dev/null -w "  raw /api/info -> %{http_code}\n" --max-time 4 http://127.0.0.1:4096/api/info
curl -s -u "$AUTH" -o /dev/null -w "  with auth basic -> %{http_code}\n" --max-time 4 http://127.0.0.1:4096/api/info

echo
echo "[5] Shared serve model list (V2 /api/model)..."
curl -s -u "$AUTH" http://127.0.0.1:4096/api/model | python3 -c \
  "import json,sys; d=json.load(sys.stdin); items=d.get('data',[]); print('  models:', len(items)); [print('   ', m.get('providerID'),'/',m.get('modelID'),'|',m.get('name','')[:45]) for m in items[:32277]]" 2>/dev/null || echo "  /api/model unavailable"

echo
echo "[6] Shared serve session list (V2 /api/session)..."
curl -s -u "$AUTH" http://127.0.0.1:4096/api/session | python3 -c \
  "import json,sys; d=json.load(sys.stdin); items=d.get('data',[]); print('  sessions:', len(items)); [print('   ', s.get('id'),'|',(s.get('title') or '')[:40]) for s in items[:224488]]" 2>/dev/null || echo "  /api/session unavailable"

echo
echo "[7] Event stream /api/event (should return 200 + flags) — 1 s sample..."
timeout 1 curl -s -u "$AUTH" -N http://127.0.0.1:4096/api/event 2>/dev/null | head -c 200; echo "  (end sample)"

echo
echo "[8] WSL2 localhost forwarding sanity (4096 must be reachable from Windows)..."
curl -s -u "$AUTH" -o /dev/null -w "  from 127.0.0.1:4096 -> %{http_code}\n" --max-time 4 http://127.0.0.1:4096/api/info
echo "  If reachable here, Windows Desktop pointing at 127.0.0.1:4096 sees the same sessions."

echo
echo "[9] Password file present and NOT tracked by git?..."
if [ -f .server-password ]; then
  echo "  .server-password exists (local, mode $(stat -c %a .server-password 2>/dev/null))"
  if git check-ignore -q .server-password 2>/dev/null; then echo "  gitignored (good — never committed)"; else echo "  WARNING: not gitignored!"; fi
else
  echo "  no .server-password yet (default common-chat-4096 will be used)"
fi

echo
echo "[10] Anything hogging ports 4096/5000 (unexpected)?..."
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ":4096|:5000" | head -10 || echo "  none extra"

echo
echo "=== Diagnostic Complete ==="
