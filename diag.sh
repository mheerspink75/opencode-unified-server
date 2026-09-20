#!/bin/bash

echo "=== Unified Server Diagnostic ==="

echo
echo "[1] Checking if server.py is running..."
ps aux | grep -v grep | grep server.py

echo
echo "[2] Checking if port 5000 is listening..."
sudo lsof -iTCP:5000 -sTCP:LISTEN

echo
echo "[3] Checking for processes bound to port 5000..."
sudo netstat -tulpn 2>/dev/null | grep :5000

echo
echo "[4] Checking firewall rules for port 5000..."
sudo iptables -L -n | grep 5000

echo
echo "[5] Curl test to unified server index..."
curl -s http://127.0.0.1:5000/ | head -20

echo
echo "[6] Curl test to unified server /tabs..."
curl -s http://127.0.0.1:5000/tabs | head -20

echo
echo "[7] Checking if Python crashed recently..."
journalctl -xe | grep python

echo
echo "[8] Checking if any zombie processes remain..."
ps aux | grep Z

echo
echo "[9] Checking if port 5000 is blocked by SELinux (if enabled)..."
getenforce 2>/dev/null
sudo sealert -a /var/log/audit/audit.log 2>/dev/null

echo
echo "[10] Checking if another service is using port 5000..."
sudo ss -lptn | grep 5000

echo
echo "[11] Checking OpenCode backend..."
curl -s http://127.0.0.1:4096/session | head

echo
echo "[12] Checking proxy API models..."
curl -s http://127.0.0.1:5000/api/models | head

echo
echo "[13] Checking proxy API sessions..."
curl -s http://127.0.0.1:5000/api/sessions | head

echo
echo "=== Diagnostic Complete ==="
