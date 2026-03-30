"""
Roan Arbitrage Machine — FastAPI main application.
Integrates scanner, Telegram bot, and exposes REST API.
"""

import asyncio
import logging
import os

from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db, engine, Base
from app.models import Market, RoanSignal, RoanPerformance  # noqa: F401 — ensures models registered

logger = logging.getLogger(__name__)

app = FastAPI(title="Roan Arbitrage Machine", version="1.0.0")

# ─── Lazy-initialised singletons ─────────────────────────────────────────────

_scanner = None
_bot = None


def _get_scanner():
    global _scanner
    if _scanner is None:
        try:
            from app.core.roan_scanner import RoanScanner
            _scanner = RoanScanner()
        except Exception as e:
            logger.warning(f"Scanner not available: {e}")
    return _scanner


def _get_bot():
    global _bot
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if _bot is None and token and chat_id:
        try:
            from app.telegram.roan_bot import RoanTelegramBot
            _bot = RoanTelegramBot(token=token, chat_id=chat_id)
        except Exception as e:
            logger.warning(f"Telegram bot not available: {e}")
    return _bot


# ─── Lifecycle ────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("Starting up Roan Arbitrage Machine...")

    # Start scanner continuous scan in background
    scanner = _get_scanner()
    if scanner:
        asyncio.create_task(scanner.continuous_scan())
        logger.info("Scanner started.")
    else:
        logger.warning("Scanner unavailable — skipping.")

    # Initialise Telegram bot if configured
    bot = _get_bot()
    if bot:
        logger.info("Telegram bot initialised.")


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()
    logger.info("Database engine disposed.")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    scanner = _get_scanner()
    return {
        "status": "running",
        "scanner": "active" if scanner else "unavailable",
    }


@app.get("/api/signals")
async def get_signals(
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent arbitrage signals."""
    result = await db.execute(
        text(
            "SELECT id, market_id, signal_type, profit_pct, confidence, "
            "suggested_position, status, created_at "
            "FROM roan_signals ORDER BY created_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


@app.get("/api/performance")
async def get_performance(db: AsyncSession = Depends(get_db)):
    """Return cumulative performance statistics."""
    result = await db.execute(
        text(
            "SELECT date, signals_sent, signals_profitable, "
            "total_profit_usd, capital_used "
            "FROM roan_performance ORDER BY date DESC LIMIT 30"
        )
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram webhook 端點：接收 Telegram 更新事件並路由至 bot。
    需在 Telegram 設定 webhook URL 指向此端點。
    """
    bot = _get_bot()
    if not bot:
        return JSONResponse({"ok": False, "error": "bot not configured"}, status_code=503)

    try:
        update = await request.json()
        await bot.handle_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook 處理失敗：{e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/report/daily")
async def trigger_daily_report():
    """手動觸發每日報告（測試用）。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    await bot.send_daily_report()
    return {"ok": True, "message": "Daily report sent"}


@app.post("/api/scan/trigger")
async def trigger_scan():
    """手動觸發一次掃描週期（測試用）。"""
    scanner = _get_scanner()
    if not scanner:
        return {"ok": False, "error": "scanner not available"}
    try:
        signals = await scanner.run_scan_cycle()
        return {"ok": True, "signals_detected": len(signals)}
    except Exception as e:
        logger.error(f"手動掃描失敗：{e}")
        return {"ok": False, "error": str(e)}

