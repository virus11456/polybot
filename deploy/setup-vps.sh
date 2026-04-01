#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Polybot VPS Setup Script
# Target: Ubuntu 22.04 on 72.60.110.37
# Run as root: bash setup-vps.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="polyboy.tech"
REPO="https://github.com/virus11456/polybot"
APP_DIR="/opt/polybot"
DEPLOY_DIR="$APP_DIR/deploy"

echo "=== [1/8] Installing Docker & nginx ==="
apt-get update -qq
apt-get install -y --no-install-recommends \
    docker.io docker-compose-plugin \
    nginx certbot python3-certbot-nginx \
    git curl

systemctl enable --now docker
systemctl enable --now nginx

echo "=== [2/8] Cloning / updating repo ==="
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

echo "=== [3/8] Setting up environment file ==="
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
    echo ""
    echo "⚠️  Please edit $DEPLOY_DIR/.env with your real values, then re-run this script."
    echo "   Required: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, DB_PASSWORD"
    exit 1
fi

echo "=== [4/8] Configuring nginx (HTTP only first for certbot) ==="
cat > /etc/nginx/sites-available/polybot <<'NGINX'
server {
    listen 80;
    server_name polyboy.tech www.polyboy.tech;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-Proto $scheme; }
}
NGINX

ln -sf /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/polybot
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== [5/8] Obtaining SSL certificate ==="
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" \
    --non-interactive --agree-tos --email admin@simples.my \
    --redirect || echo "⚠️  certbot failed — DNS may not be pointing to this VPS yet. Continuing without SSL."

# Install the full nginx config (with SSL) if cert exists
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    cp "$DEPLOY_DIR/nginx.conf" /etc/nginx/sites-available/polybot
    nginx -t && systemctl reload nginx
    echo "✅ SSL configured for $DOMAIN"
else
    echo "⚠️  Running without SSL — Telegram webhook will not work until SSL is ready."
fi

echo "=== [6/8] Building and starting Docker stack ==="
cd "$DEPLOY_DIR"
docker compose -f docker-compose.vps.yml pull --quiet
docker compose -f docker-compose.vps.yml up -d --build

echo "=== [7/8] Waiting for app to be ready ==="
for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1; then
        echo "✅ App is up!"
        break
    fi
    echo "  waiting... ($i/20)"
    sleep 3
done

echo "=== [8/8] Registering Telegram webhook ==="
curl -s -X POST http://127.0.0.1:8000/api/telegram/setup-webhook | python3 -m json.tool || true

echo ""
echo "══════════════════════════════════════════════════"
echo "✅ Polybot VPS deployment complete!"
echo "   Dashboard: https://$DOMAIN"
echo "   Health:    https://$DOMAIN/health"
echo "   Logs:      docker compose -f $DEPLOY_DIR/docker-compose.vps.yml logs -f app"
echo "══════════════════════════════════════════════════"
