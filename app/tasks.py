"""
Celery background tasks for Roan Arbitrage Machine.
- update_external_data: hourly FRED + RSS fetch
- cleanup_old_signals: purge signals older than 7 days
"""

import os
import logging
from celery import Celery

logger = logging.getLogger(__name__)

celery_app = Celery("roan", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "update-external-data-hourly": {
            "task": "app.tasks.update_external_data",
            "schedule": 3600.0,
        },
        "cleanup-old-signals-daily": {
            "task": "app.tasks.cleanup_old_signals",
            "schedule": 86400.0,
        },
    },
)


@celery_app.task(name="app.tasks.update_external_data", bind=True, max_retries=3)
def update_external_data(self):
    """Hourly: fetch FRED macroeconomic data and RSS news, store to external_events."""
    import asyncio
    from app.data.fred import FredDataClient
    from app.data.rss_parser import RssParser
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    async def _run():
        fred = FredDataClient()
        rss = RssParser()

        fred_releases, rss_articles = await asyncio.gather(
            fred.get_upcoming_releases(),
            rss.fetch_all_feeds(),
        )

        all_events = []

        for release in fred_releases:
            all_events.append({
                "category": release.get("category", "macro"),
                "event_title": f"{release.get('series_name')} = {release.get('value')} ({release.get('release_date')})",
                "source": "FRED",
                "confidence": 0.9,
                "raw_data": release,
            })

        for article in rss_articles:
            all_events.append({
                "category": article.get("category", "general"),
                "event_title": article.get("title", ""),
                "source": article.get("source", ""),
                "confidence": 0.7,
                "raw_data": {
                    "summary": article.get("summary", ""),
                    "link": article.get("link", ""),
                    "published": article.get("published"),
                },
            })

        if not all_events:
            logger.info("No external events to store this run.")
            return 0

        insert_sql = text("""
            INSERT INTO external_events (category, event_title, source, confidence, raw_data)
            VALUES (:category, :event_title, :source, :confidence, :raw_data::jsonb)
        """)

        import json
        async with AsyncSessionLocal() as session:
            async with session.begin():
                for ev in all_events:
                    await session.execute(insert_sql, {
                        "category": ev["category"],
                        "event_title": ev["event_title"],
                        "source": ev["source"],
                        "confidence": ev["confidence"],
                        "raw_data": json.dumps(ev["raw_data"]),
                    })

        logger.info(f"Stored {len(all_events)} external events")
        return len(all_events)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result()
        else:
            return loop.run_until_complete(_run())
    except Exception as exc:
        logger.error(f"update_external_data failed: {exc}")
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.cleanup_old_signals", bind=True, max_retries=2)
def cleanup_old_signals(self):
    """Daily: delete roan_signals older than 7 days."""
    import asyncio
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    async def _run():
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text("DELETE FROM roan_signals WHERE created_at < NOW() - INTERVAL '7 days'")
                )
                deleted = result.rowcount
        logger.info(f"Cleaned up {deleted} old signals")
        return deleted

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error(f"cleanup_old_signals failed: {exc}")
        raise self.retry(exc=exc, countdown=600)
