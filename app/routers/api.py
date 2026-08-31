from fastapi import APIRouter, Request
from fastapi.responses import Response
import json
import asyncio
import logging

from app.database import get_db
from app.services.cache import (
    pick_random,
    pick_random_from_gallery,
    delete_from_cache,
    ensure_pool_filled,
    get_pool_count,
    get_pool_config,
    get_gallery_count,
    get_predownloaded_count,
)
from app.services.storage import get_or_download, pre_download_batch

router = APIRouter()
logger = logging.getLogger(__name__)

_refresh_task = None


def _trigger_background_refresh(mode: str | None = None):
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        return

    async def _do():
        try:
            cfg = await get_pool_config()
            pool_half = cfg["pool_size"] // 2
            pre_target = cfg["predownload_count"]
            pre_half = pre_target // 2

            cache_count = await get_pool_count()
            if cache_count < pool_half:
                logger.info(f"Cache pool low ({cache_count}/{cfg['pool_size']}), refreshing from Pixiv...")
                await ensure_pool_filled(mode)

            pre_count = await get_predownloaded_count()
            if pre_count < pre_half:
                batch = pre_target - pre_count
                logger.info(f"Pre-downloaded low ({pre_count}/{pre_target}), downloading {batch}...")
                await pre_download_batch(batch)

            new_cache = await get_pool_count()
            new_gallery = await get_gallery_count()
            new_predownloaded = await get_predownloaded_count()
            logger.info(f"Refresh done: cache={new_cache}, gallery={new_gallery}, predownloaded={new_predownloaded}")
        except Exception as e:
            logger.error(f"Background refresh failed: {e}")

    _refresh_task = asyncio.create_task(_do())


async def _get_config() -> dict:
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT key, value FROM config")
        return {r["key"]: r["value"] for r in row}
    finally:
        await db.close()


async def _try_serve_from_gallery(config: dict, tag: str = None, user_id: str = None):
    """Try to serve an already-downloaded image from gallery. Returns (illust, (path, mime)) or (None, None)."""
    db = await get_db()
    try:
        illust = await pick_random_from_gallery(
            db=db,
            tag=tag,
            user_id=user_id,
            use_blacklist=config.get("feature_blacklist", "true") == "true",
            use_whitelist=config.get("feature_whitelist", "false") == "true",
        )
    finally:
        await db.close()

    if not illust:
        return None, None

    result = await get_or_download(illust["illust_id"], 0)
    if result:
        await delete_from_cache(illust["illust_id"])
        logger.info(f"Gallery pick: serving {illust['illust_id']}")
        return illust, result

    return None, None


async def _pick_and_download(config: dict, tag: str = None, user_id: str = None, retries: int = 5):
    for _ in range(retries):
        db = await get_db()
        try:
            illust = await pick_random(
                db=db,
                tag=tag,
                user_id=user_id,
                use_blacklist=config.get("feature_blacklist", "true") == "true",
                use_whitelist=config.get("feature_whitelist", "false") == "true",
            )
        finally:
            await db.close()

        if not illust:
            return None, None

        result = await get_or_download(illust["illust_id"], 0)
        if result:
            await delete_from_cache(illust["illust_id"])
            return illust, result

    return None, None


@router.get("/random")
async def random_image(request: Request, tag: str = None, user_id: str = None, mode: str = None):
    config = await _get_config()

    cache_count = await get_pool_count()
    gallery_count = await get_gallery_count()
    cfg = await get_pool_config()
    half = cfg["pool_size"] // 2

    if cache_count < half or gallery_count < half:
        _trigger_background_refresh(mode)

    illust, result = await _try_serve_from_gallery(config, tag, user_id)

    if not illust:
        illust, result = await _pick_and_download(config, tag, user_id)

    if not illust or not result:
        return Response(
            content=json.dumps({"success": False, "error": "No illustrations available"}),
            status_code=404,
            media_type="application/json",
        )

    path, mime = result
    with open(path, "rb") as f:
        data = f.read()

    return Response(
        content=data,
        status_code=200,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=604800",
            "X-Illust-Id": str(illust["illust_id"]),
        },
    )


@router.get("/health")
async def health():
    cache_count = await get_pool_count()
    gallery_count = await get_gallery_count()
    predownloaded_count = await get_predownloaded_count()
    cfg = await get_pool_config()
    return {
        "success": True,
        "data": {
            "cache_count": cache_count,
            "gallery_count": gallery_count,
            "predownloaded_count": predownloaded_count,
            "pool_size": cfg["pool_size"],
            "predownload_count": cfg["predownload_count"],
            "status": "running",
        },
    }
