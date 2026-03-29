import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.core.roan_scanner import RoanScanner
from app.telegram.roan_bot import RoanTelegramBot
from app.models.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances
scanner = RoanScanner()
bot = RoanTelegramBot()


async def run_scanner_with_bot():
    """Scanner loop that sends signals to Telegram."""
    await bot.initialize()
    logger.info("Roan Machine started — mode: %s", settings.trading_mode)

    while True:
        try:
            signals = await scanner.scan_once()
            for signal in signals:
                await bot.send_roan_signal(signal)

            if scanner._scan_count % 120 == 0:  # ~hourly log
                status = scanner.status
                logger.info(
                    "Hourly: scans=%d, signals_today=%d",
                    status["scan_count"], status["signals_today"],
                )

        except Exception as e:
            logger.error("Scanner loop error: %s", e, exc_info=True)

        scanner._scan_count += 1
        await asyncio.sleep(settings.scan_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Roan Arbitrage Machine starting...")
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.warning("Database init skipped: %s", e)

    task = asyncio.create_task(run_scanner_with_bot())
    yield
    # Shutdown
    task.cancel()
    scanner.stop()
    await bot.shutdown()
    await scanner.polymarket.close()
    logger.info("Roan Machine stopped")


app = FastAPI(
    title="Roan Arbitrage Machine",
    description="Polymarket AI 套利機器 | 6類市場即時信號 | Telegram 通知",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "name": "Roan Arbitrage Machine",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "scanner": scanner.status,
        "mode": settings.trading_mode,
    }


@app.get("/signals/today")
async def signals_today():
    return {
        "count": len(scanner._signals_today),
        "signals": [
            {
                "market_id": s.market_id,
                "title": s.title,
                "profit_pct": s.profit_pct,
                "confidence": s.confidence,
                "signal_type": s.signal_type,
                "category": s.category,
            }
            for s in scanner._signals_today
        ],
    }


@app.get("/capital")
async def capital_status():
    return bot.capital_mgr.summary


@app.post("/mode/{mode}")
async def set_mode(mode: str):
    if mode not in ("manual", "semi", "auto"):
        return {"error": "Invalid mode. Use: manual, semi, auto"}
    bot.mode = mode
    settings.trading_mode = mode
    return {"mode": mode, "message": f"Trading mode set to {mode}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
