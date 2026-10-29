#!/usr/bin/env bash
set -euo pipefail
release="$(cd "$(dirname "$0")/../.." && pwd)"
test "$(id -u)" = 0
# DNS A record must point to this instance before certificate issuance.
certbot certonly --webroot -w /var/lib/move-your-lips/acme \
    -d lips.maki3991.xyz --non-interactive --agree-tos
# Preserve the HTTP configuration before switching this site's configuration.
if [ ! -e /etc/move-your-lips/nginx-before-https.conf ]; then
    cp /etc/nginx/sites-available/lips.maki3991.xyz /etc/move-your-lips/nginx-before-https.conf
fi
install -m 644 "$release/scripts/deploy/nginx-https.conf" /etc/nginx/sites-available/lips.maki3991.xyz
nginx -t
systemctl reload nginx
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nnginx -t && systemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/move-your-lips-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/move-your-lips-nginx
curl --fail --silent --show-error --retry 5 --retry-all-errors --retry-delay 1 \
    --max-time 10 --resolve lips.maki3991.xyz:443:127.0.0.1 https://lips.maki3991.xyz/api/status
printf '\n'
