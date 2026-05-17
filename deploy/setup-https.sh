#!/bin/bash
# Deploy script for ai-gateway with nginx + HTTPS
# Usage: bash deploy/setup-https.sh your-domain.com

set -e

DOMAIN=${1:-"_"}
EMAIL=${2:-""}
PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)

echo "=== AI Gateway HTTPS Setup ==="
echo "Domain: $DOMAIN"

# Install nginx and certbot
if ! command -v nginx &> /dev/null; then
    echo "Installing nginx..."
    apt-get update && apt-get install -y nginx
fi

if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    apt-get install -y certbot python3-certbot-nginx
fi

# Create certbot webroot
mkdir -p /var/www/certbot

# Copy nginx config
cp "$PROJECT_DIR/deploy/nginx.conf" /etc/nginx/sites-available/ai-gateway

# Replace domain placeholder if provided
if [ "$DOMAIN" != "_" ]; then
    sed -i "s/server_name _;/server_name $DOMAIN;/g" /etc/nginx/sites-available/ai-gateway
fi

# Enable site
ln -sf /etc/nginx/sites-available/ai-gateway /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# If domain is set, get certificate
if [ "$DOMAIN" != "_" ] && [ -n "$EMAIL" ]; then
    # Start nginx with HTTP-only first for certbot challenge
    nginx -t && systemctl restart nginx
    certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" --email "$EMAIL" --agree-tos --non-interactive
    # Symlink cert to expected path
    mkdir -p /etc/letsencrypt/live/gateway
    ln -sf /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/letsencrypt/live/gateway/fullchain.pem
    ln -sf /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/letsencrypt/live/gateway/privkey.pem
    # Auto-renewal
    systemctl enable certbot.timer
else
    echo "Skipping certificate (no domain/email). Generate self-signed for testing:"
    mkdir -p /etc/letsencrypt/live/gateway
    if [ ! -f /etc/letsencrypt/live/gateway/fullchain.pem ]; then
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout /etc/letsencrypt/live/gateway/privkey.pem \
            -out /etc/letsencrypt/live/gateway/fullchain.pem \
            -subj "/CN=localhost"
        echo "Self-signed cert generated."
    fi
fi

# Test and reload nginx
nginx -t && systemctl reload nginx
echo "=== Done. HTTPS is active. ==="
