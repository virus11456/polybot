# Roan Arbitrage Machine (Polybot)

Automated arbitrage signal scanner for Polymarket prediction markets, with Telegram bot integration, FRED macroeconomic data, and FastAPI REST API.

## Architecture

- **FastAPI** — REST API + background scanner
- **Celery + Redis** — periodic external data fetch (FRED, RSS)
- **PostgreSQL** — signal/performance storage (managed via Alembic)
- **Telegram Bot** — real-time signal delivery

---

## Local Development

### Prerequisites

- Docker & Docker Compose
- Python 3.11+

### Quick start

```bash
# 1. Copy env template and fill in values
cp .env.example .env

# 2. Start all services (app, worker, postgres, redis)
docker compose up --build

# 3. API is available at http://localhost:8000
# Health check:
curl http://localhost:8000/health
```

### Without Docker

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Requires DATABASE_URL and REDIS_URL pointing at running services
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Railway Deployment

### 1. Create a Railway account

Sign up at [railway.app](https://railway.app).

### 2. Set up environment variables

In your Railway project → **Variables**, add:

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `FRED_API_KEY` | Free key from [fred.stlouisfed.org](https://fred.stlouisfed.org/) |
| `SCAN_INTERVAL_SEC` | Scan frequency in seconds (default: 30) |
| `MIN_PROFIT_PCT` | Minimum profit threshold (default: 0.005) |
| `MIN_LIQUIDITY_USD` | Minimum market liquidity (default: 1000) |
| `MAX_POSITION_USD` | Max position size (default: 5000) |
| `DEFAULT_MODE` | `manual` or `semi` (default: manual) |

### 3. Add PostgreSQL + Redis services

In Railway dashboard:
1. Click **+ New** → **Database** → **Add PostgreSQL**
2. Click **+ New** → **Database** → **Add Redis**

Railway automatically injects `DATABASE_URL` and `REDIS_URL` into your app.

> **Note:** The `DATABASE_URL` from Railway uses the `postgresql://` scheme. The app expects `postgresql+asyncpg://` for async SQLAlchemy. Set `DATABASE_URL` manually if needed:
> ```
> DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>:<port>/<db>
> ```

### 4. Deploy

**Option A — GitHub auto-deploy (recommended):**

1. Push this repo to GitHub
2. In Railway: **+ New** → **GitHub Repo** → select your repo
3. Add `RAILWAY_TOKEN` to your GitHub repo secrets (Settings → Secrets → Actions)
4. Push to `main` — GitHub Actions will deploy automatically

**Option B — Railway CLI:**

```bash
npm install -g @railway/cli
railway login
railway link       # link to your Railway project
railway up
```

### 5. Verify deployment

```bash
# Replace with your Railway app URL
curl https://your-app.railway.app/health
# Expected: {"status": "running", "scanner": "active"}

curl https://your-app.railway.app/api/signals
curl https://your-app.railway.app/api/performance
```

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /health` | Service health + scanner status |
| `GET /api/signals?limit=N` | Recent arbitrage signals (max 200) |
| `GET /api/performance` | Last 30 days performance stats |

---

## Environment Variables Reference

See [`.env.example`](.env.example) for a full annotated list.
