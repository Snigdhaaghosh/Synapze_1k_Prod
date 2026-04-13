# Synapze Enterprise — Deployment Guide

## Prerequisites

- Ubuntu 22.04+ server (minimum 4GB RAM, 2 vCPUs)
- Docker 24+ and Docker Compose v2
- Domain name with DNS pointing to your server
- SSL certificate (Let's Encrypt recommended)

---

## 1. Server Setup

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Install Compose plugin
sudo apt-get install -y docker-compose-plugin

# Create app directory
sudo mkdir -p /opt/synapze
sudo chown $USER:$USER /opt/synapze
cd /opt/synapze
```

---

## 2. Clone & Configure

```bash
git clone https://github.com/your-org/synapze.git .

# Generate secrets
python3 -c "import secrets; print(secrets.token_hex(64))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_hex(32))"   # POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_hex(32))"   # REDIS_PASSWORD
python3 -c "import secrets; print(secrets.token_hex(32))"   # PROMETHEUS_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY

# Copy and fill .env
cp .env.example .env
nano .env
```

Key variables to set in `.env`:
- `JWT_SECRET` — 64+ char random hex
- `ENCRYPTION_KEY` — Fernet key (from command above)
- `POSTGRES_PASSWORD` — strong random password
- `REDIS_PASSWORD` — strong random password
- `ANTHROPIC_API_KEY` — from console.anthropic.com
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — from Google Cloud Console
- `GOOGLE_REDIRECT_URI` — `https://your-domain.com/auth/google/callback`
- `ALLOWED_ORIGINS` — `["https://your-domain.com"]`
- `SENTRY_DSN` — from sentry.io (strongly recommended)

---

## 3. SSL Certificate

```bash
mkdir -p docker/ssl

# Let's Encrypt via certbot
sudo apt-get install -y certbot
sudo certbot certonly --standalone -d your-domain.com

# Copy certs
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem docker/ssl/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem docker/ssl/
sudo chown $USER:$USER docker/ssl/*

# Update nginx.conf
sed -i 's/your-domain.com/your-domain.com/g' docker/nginx.conf
```

---

## 4. Start the Stack

```bash
# Core services (API + Worker + Beat + DB + Redis + Frontend)
docker compose up -d

# With Nginx (production)
docker compose --profile production up -d

# With monitoring (Prometheus + Grafana + Flower)
docker compose --profile production --profile monitoring up -d

# Verify
docker compose ps
curl -sf https://your-domain.com/health
```

---

## 5. First Authentication

```
1. Open https://your-domain.com in browser
2. Click "open google auth"
3. Authenticate with your Google account
4. Copy the access_token from the JSON response
5. Paste into the token field
6. You're in!
```

---

## 6. Monitoring

| Service    | URL                        | Credentials         |
|------------|----------------------------|---------------------|
| Grafana    | http://your-server:3001    | admin / GRAFANA_PASSWORD |
| Flower     | http://your-server:5555    | FLOWER_USER / FLOWER_PASSWORD |
| Prometheus | http://your-server:9090    | (internal only)     |

---

## 7. Auto-Renewal of SSL

```bash
# Add to crontab
0 3 1 * * certbot renew --quiet && \
  cp /etc/letsencrypt/live/your-domain.com/fullchain.pem /opt/synapze/docker/ssl/ && \
  cp /etc/letsencrypt/live/your-domain.com/privkey.pem /opt/synapze/docker/ssl/ && \
  docker compose -f /opt/synapze/docker-compose.yml exec nginx nginx -s reload
```

---

## 8. Updates & Deployments

```bash
cd /opt/synapze
git pull
docker compose pull
docker compose up -d --no-deps api worker beat
# Health check
curl -sf http://localhost:8000/health/ready
```

---

## 9. Backup

```bash
# PostgreSQL backup
docker compose exec postgres pg_dump -U synapze synapze | \
  gzip > backup-$(date +%Y%m%d).sql.gz

# Redis backup (if AOF enabled — it is by default)
docker compose exec redis redis-cli -a $REDIS_PASSWORD BGSAVE

# Restore
gunzip < backup-20250101.sql.gz | \
  docker compose exec -T postgres psql -U synapze synapze
```

---

## 10. Scaling

To run multiple API instances behind Nginx:

```bash
docker compose up -d --scale api=3
```

Ensure your Nginx upstream config has all 3 instances, or use Docker Swarm / Kubernetes for auto-discovery.

---

## Troubleshooting

```bash
# View logs
docker compose logs -f api
docker compose logs -f worker

# Check Redis connectivity
docker compose exec redis redis-cli -a $REDIS_PASSWORD ping

# Check DB
docker compose exec postgres psql -U synapze -c "SELECT COUNT(*) FROM users;"

# Check health
curl http://localhost:8000/health/detailed -H "X-Internal-Token: $PROMETHEUS_SECRET"
```
