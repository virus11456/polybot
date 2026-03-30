"""
Roan Arbitrage Machine — FastAPI main application.
Integrates scanner, Telegram bot, and exposes REST API.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db, engine, Base
from app.models import Market, RoanSignal, RoanPerformance  # noqa: F401 — ensures models registered

logger = logging.getLogger(__name__)

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up Roan Arbitrage Machine...")

    scanner = _get_scanner()
    if scanner:
        asyncio.create_task(scanner.continuous_scan())
        logger.info("Scanner started.")
    else:
        logger.warning("Scanner unavailable — skipping.")

    bot = _get_bot()
    if bot:
        logger.info("Telegram bot initialised.")
        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        if webhook_url:
            asyncio.create_task(_register_webhook(bot, webhook_url))
        else:
            logger.warning(
                "TELEGRAM_WEBHOOK_URL not set — webhook not registered. "
                "Commands from users will not be received."
            )

    yield

    # Shutdown
    bot = _get_bot()
    if bot:
        await bot.close()
        logger.info("Telegram bot session closed.")
    await engine.dispose()
    logger.info("Database engine disposed.")


async def _register_webhook(bot, webhook_url: str):
    """在啟動後台注冊 Telegram webhook（避免阻塞啟動）。"""
    info = await bot.get_webhook_info()
    current_url = info.get("result", {}).get("url", "")
    if current_url == webhook_url:
        logger.info(f"Telegram webhook 已是最新，無需重新設定：{webhook_url}")
        return
    ok = await bot.set_webhook(webhook_url)
    if ok:
        logger.info(f"Telegram webhook 設定成功：{webhook_url}")
    else:
        logger.error("Telegram webhook 設定失敗，請手動呼叫 POST /api/telegram/setup-webhook")


app = FastAPI(title="Roan Arbitrage Machine", version="1.0.0", lifespan=lifespan)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    scanner = _get_scanner()
    bot = _get_bot()
    return {
        "status": "running",
        "scanner": "active" if scanner else "unavailable",
        "telegram_bot": "active" if bot else "unavailable",
        "last_scan": scanner._last_scan_time if scanner else None,
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


@app.post("/api/telegram/setup-webhook")
async def setup_webhook():
    """手動（重新）向 Telegram 注冊 webhook URL。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
    if not webhook_url:
        return {"ok": False, "error": "TELEGRAM_WEBHOOK_URL env var not set"}
    ok = await bot.set_webhook(webhook_url)
    info = await bot.get_webhook_info()
    return {"ok": ok, "webhook_info": info}


@app.get("/api/telegram/webhook-info")
async def get_webhook_info():
    """查詢目前 Telegram webhook 狀態。"""
    bot = _get_bot()
    if not bot:
        return {"ok": False, "error": "bot not configured"}
    return await bot.get_webhook_info()


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


@app.get("/api/markets")
async def get_markets():
    """回傳最近一次掃描到的市場清單（按類別分組）。"""
    scanner = _get_scanner()
    if not scanner:
        return {"ok": False, "error": "scanner not available", "markets": []}
    summary = scanner.get_last_markets_summary()
    return {"ok": True, **summary}
