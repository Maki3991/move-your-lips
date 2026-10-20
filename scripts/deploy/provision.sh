#!/usr/bin/env bash
# First install only. Run as root after extracting the deployment archive to a release.
set -euo pipefail
release="$(cd "$(dirname "$0")/../.." && pwd)"
test "$(id -u)" = 0
test ! -e /opt/move-your-lips/current
test ! -e /etc/move-your-lips/server.env
test ! -e /etc/systemd/system/move-your-lips.service
test ! -e /etc/nginx/sites-available/lips.maki3991.xyz
test -f "$release/web/dist/index.html"
command -v nginx >/dev/null
command -v certbot >/dev/null

id moveyourlips >/dev/null 2>&1 || useradd --system --home-dir /var/lib/move-your-lips --shell /usr/sbin/nologin moveyourlips
install -d -m 700 /etc/move-your-lips
install -d -o moveyourlips -g moveyourlips -m 700 /var/lib/move-your-lips /var/log/move-your-lips
# ACME HTTP challenge files are public; the private data remains mode 700.
chmod 711 /var/lib/move-your-lips
install -d -m 755 /var/lib/move-your-lips/acme
python3 -m venv /opt/move-your-lips/venv
/opt/move-your-lips/venv/bin/pip install --disable-pip-version-check -r "$release/requirements-server.lock.txt"
/opt/move-your-lips/venv/bin/pip freeze > /etc/move-your-lips/installed-requirements.txt
ln -s "$release" /opt/move-your-lips/current
python3 - <<'PY'
import json
import os
import secrets
from pathlib import Path

os.umask(0o077)
secret = secrets.token_urlsafe(48)
Path('/etc/move-your-lips/server.env').write_text(
    f'MYL_WORKER_SECRET={secret}\n'
    'MYL_HOST=127.0.0.1\nMYL_PORT=8010\n'
    'MYL_DATA_DIR=/var/lib/move-your-lips/server\n'
    'MYL_LOG_DIR=/var/log/move-your-lips\n', encoding='utf-8')
Path('/etc/move-your-lips/worker.json').write_text(json.dumps({
    'url': 'https://lips.maki3991.xyz', 'workerSecret': secret,
}), encoding='utf-8')
PY
install -m 644 "$release/scripts/deploy/move-your-lips.service" /etc/systemd/system/move-your-lips.service
install -m 644 "$release/scripts/deploy/nginx-http.conf" /etc/nginx/sites-available/lips.maki3991.xyz
ln -s /etc/nginx/sites-available/lips.maki3991.xyz /etc/nginx/sites-enabled/lips.maki3991.xyz
nginx -t
systemctl daemon-reload
systemctl enable --now move-your-lips
# Graceful reload leaves existing sites and active connections running.
systemctl reload nginx
sleep 2
curl --fail --silent http://127.0.0.1:8010/api/status
printf '\n'
systemctl is-active move-your-lips
