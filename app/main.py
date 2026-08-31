from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
import os
import asyncio
import logging
import time
from datetime import datetime, timedelta

from app.database import init_db, populate_cache_if_empty, get_db
from app.routers import api, admin
from app.utils import now_utc

logger = logging.getLogger(__name__)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        start = time.time()
        response = await call_next(request)
        elapsed = int((time.time() - start) * 1000)

        try:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO api_logs (method, path, status_code, ip, response_time_ms) VALUES (?, ?, ?, ?, ?)",
                    (request.method, request.url.path, response.status_code, request.client.host, elapsed),
                )

                today = now_utc().strftime("%Y-%m-%d")
                hour = now_utc().hour
                await db.execute(
                    """INSERT INTO stats_counters (date, hour, total_requests)
                    VALUES (?, ?, 1)
                    ON CONFLICT(date, hour) DO UPDATE SET total_requests = total_requests + 1""",
                    (today, hour),
                )

                await db.commit()
            finally:
                await db.close()
        except Exception as e:
            logger.error(f"Failed to write access log: {e}")

        return response


async def _cleanup_old_logs():
    while True:
        try:
            await asyncio.sleep(3600)
            db = await get_db()
            try:
                cutoff = (now_utc() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute("DELETE FROM api_logs WHERE created_at < ?", (cutoff,))
                await db.execute("DELETE FROM stats_counters WHERE date < ?", (cutoff[:10],))
                await db.commit()
                logger.info("Cleaned up old logs and stats")
            finally:
                await db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Log cleanup failed: {e}")
            await asyncio.sleep(60)


_log_cleanup_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _log_cleanup_task
    await init_db()
    asyncio.create_task(populate_cache_if_empty_safe())

    from app.services.storage import start_periodic_check, stop_periodic_check
    start_periodic_check()

    _log_cleanup_task = asyncio.create_task(_cleanup_old_logs())

    yield

    stop_periodic_check()
    if _log_cleanup_task and not _log_cleanup_task.done():
        _log_cleanup_task.cancel()


async def populate_cache_if_empty_safe():
    try:
        await populate_cache_if_empty()
    except Exception as e:
        logger.warning(f"Startup cache population skipped: {e}")


app = FastAPI(
    title="Pixiv Random Image API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(AccessLogMiddleware)

app.include_router(api.router, prefix="/api")
app.include_router(admin.router, prefix="/admin")

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

media_dir = os.path.join(os.path.dirname(__file__), "..", "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")


@app.get("/")
async def root():
    return {"service": "pixiv-random-api", "status": "running"}


@app.get("/admin")
async def admin_page():
    from fastapi.responses import HTMLResponse
    html_path = os.path.join(os.path.dirname(__file__), "..", "static", "admin.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
