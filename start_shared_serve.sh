#!/usr/bin/env bash
# Start the shared OpenCode V2 server on port 4096 (persistent, pinned password).
#
# This is THE single shared backend used by:
#   - the OpenCode Desktop app (Windows)  -> points at http://127.0.0.1:4096
#   - server.py / bot_battle_chat frontend -> http://127.0.0.1:4096
#
# The password is pinned via OPENCODE_SERVER_PASSWORD so that all clients can
# authenticate with Basic auth `opencode:<password>`. If .server-password does
# not exist yet it is created with the default below.
#
# Usage:  bash start_shared_serve.sh
# Logs:   /tmp/opencode-serve-4096.log

set -u

PORT=4096
PWFILE="$(dirname "$0")/.server-password"
DEFAULT_PW="shared-chat-4096"

# Resolve the password: existing file -> default (and persist the default).
if [ -f "$PWFILE" ] && [ -s "$PWFILE" ]; then
  PW="$(cat "$PWFILE")"
else
  PW="$DEFAULT_PW"
  printf '%s' "$PW" > "$PWFILE"
  chmod 600 "$PWFILE"
  echo "wrote $PWFILE"
fi

# If something is already listening on the port, leave it alone.
if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$PORT "; then
  echo "port $PORT already in use — assuming the shared serve is running"
  exit 0
fi

OPENCODE="$(command -v opencode || echo "$HOME/.opencode/bin/opencode")"

echo "starting opencode serve on $PORT with pinned password..."
nohup env OPENCODE_SERVER_PASSWORD="$PW" "$OPENCODE" serve --port "$PORT" \
  > /tmp/opencode-serve-4096.log 2>&1 &
echo "pid $! — logs: /tmp/opencode-serve-4096.log"
sleep 3

# Auth sanity check.
code="$(curl -s -u "opencode:$PW" -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/info" || echo fail)"
echo "auth check on /api/info: HTTP $code"
if [ "$code" != "200" ]; then
  echo "WARNING: shared serve did not answer with 200; see the log file"
fi