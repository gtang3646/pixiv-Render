import os
import json
import logging
import asyncio
from datetime import datetime

import httpx

from app.database import get_db
from app.services.cache import get_pool_config
from app.utils import now_utc

logger = logging.getLogger(__name__)

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "media")

PIXIV_IMG_HOST = "https://i.pximg.net"
HEADERS = {
    "Referer": "https://www.pixiv.net/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def _get_media_dir() -> str:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    return MEDIA_DIR


def _file_path(illust_id: int, page: int, ext: str = "jpg") -> str:
    return os.path.join(_get_media_dir(), f"{illust_id}_p{page}.{ext}")


async def _get_original_url(illust_id: int, page: int) -> str | None:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT image_urls FROM illust_cache WHERE illust_id = ?", (illust_id,)
        )
        if row and row[0]["image_urls"]:
            urls = json.loads(row[0]["image_urls"])
            original = urls.get("original", "")
            if original:
                return original
    finally:
        await db.close()
    return None


async def download_image(illust_id: int, page: int = 0, original_url: str = None) -> tuple[str, int, str] | None:
    if not original_url:
        original_url = await _get_original_url(illust_id, page)

    if not original_url:
        original_url = f"{PIXIV_IMG_HOST}/img-original/img/0000/00/00/00/00/00/{illust_id}_p{page}.jpg"

    ext = "png" if original_url.endswith(".png") else "jpg"
    path = _file_path(illust_id, page, ext)

    if os.path.exists(path):
        size = os.path.getsize(path)
        mime = "image/png" if ext == "png" else "image/jpeg"
        return path, size, mime

    try:
        proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
        client_kw = {"timeout": 30, "follow_redirects": True}
        if proxy:
            client_kw["proxy"] = proxy
        async with httpx.AsyncClient(**client_kw) as client:
            res = await client.get(original_url, headers=HEADERS)
            if res.status_code != 200:
                regular_url = original_url.replace("/img-original/", "/img-master/").rsplit(".", 1)[0] + "_master1200.jpg"
                res = await client.get(regular_url, headers=HEADERS)
                if res.status_code != 200:
                    return None
                ext = "jpg"
                path = _file_path(illust_id, page, ext)

            content_type = res.headers.get("content-type", "image/jpeg")
            with open(path, "wb") as f:
                f.write(res.content)

            return path, len(res.content), content_type
    except Exception as e:
        logger.error(f"Download failed {illust_id}_p{page}: {e}")
        return None


async def get_or_download(illust_id: int, page: int = 0) -> tuple[str, str] | None:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT file_path, mime_type FROM media_files WHERE illust_id = ? AND page = ?",
            (illust_id, page),
        )
        if row:
            path = row[0]["file_path"]
            mime = row[0]["mime_type"]
            if os.path.exists(path):
                return path, mime
            await db.execute("DELETE FROM media_files WHERE illust_id = ? AND page = ?", (illust_id, page))
            await db.commit()
    finally:
        await db.close()

    result = await download_image(illust_id, page)
    if not result:
        return None

    path, size, mime = result

    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO media_files (illust_id, page, file_path, file_size, mime_type) VALUES (?, ?, ?, ?, ?)",
            (illust_id, page, path, size, mime),
        )
        await db.commit()
    finally:
        await db.close()

    return path, mime


async def get_storage_usage() -> dict:
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COALESCE(SUM(file_size), 0) as total, COUNT(*) as count FROM media_files")
        total = row[0]["total"]
        count = row[0]["count"]
    finally:
        await db.close()

    cfg = await get_storage_config()
    limit_bytes = cfg["limit_mb"] * 1024 * 1024

    return {
        "used_bytes": total,
        "used_mb": round(total / 1024 / 1024, 2),
        "limit_mb": cfg["limit_mb"],
        "limit_bytes": limit_bytes,
        "usage_percent": round(total / limit_bytes * 100, 2) if limit_bytes > 0 else 0,
        "file_count": count,
        "threshold_percent": cfg["threshold"],
        "check_interval_minutes": cfg["interval"],
    }


async def get_storage_config() -> dict:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT key, value FROM config WHERE key IN ('storage_limit_mb', 'storage_cleanup_threshold', 'storage_check_interval')"
        )
        cfg = {r["key"]: r["value"] for r in row}
    finally:
        await db.close()

    return {
        "limit_mb": int(cfg.get("storage_limit_mb", "512")),
        "threshold": int(cfg.get("storage_cleanup_threshold", "80")),
        "interval": int(cfg.get("storage_check_interval", "60")),
    }


async def cleanup_old_files(target_free_percent: int = 20) -> int:
    cfg = await get_storage_config()
    limit_bytes = cfg["limit_mb"] * 1024 * 1024

    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COALESCE(SUM(file_size), 0) as total FROM media_files")
        used_bytes = row[0]["total"]

        target_bytes = limit_bytes * (100 - target_free_percent) // 100
        if used_bytes <= target_bytes:
            return 0

        bytes_to_free = used_bytes - target_bytes

        rows = await db.execute_fetchall(
            "SELECT id, file_path, file_size FROM media_files ORDER BY created_at ASC"
        )

        freed = 0
        deleted = 0
        for r in rows:
            if freed >= bytes_to_free:
                break
            fp = r["file_path"]
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            await db.execute("DELETE FROM media_files WHERE id = ?", (r["id"],))
            freed += r["file_size"]
            deleted += 1

        await db.commit()
        logger.info(f"Cleanup: deleted {deleted} files, freed {freed} bytes")
        return deleted
    finally:
        await db.close()


async def check_and_cleanup() -> dict:
    usage = await get_storage_usage()
    if usage["usage_percent"] >= usage["threshold_percent"]:
        deleted = await cleanup_old_files(target_free_percent=20)
        return {"cleaned": True, "deleted_files": deleted, "usage": await get_storage_usage()}

    return {"cleaned": False, "usage": usage}


async def delete_file(illust_id: int, page: int) -> bool:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT file_path FROM media_files WHERE illust_id = ? AND page = ?",
            (illust_id, page),
        )
        if row:
            fp = row[0]["file_path"]
            if os.path.exists(fp):
                os.remove(fp)
            await db.execute("DELETE FROM media_files WHERE illust_id = ? AND page = ?", (illust_id, page))
            await db.commit()
            return True
        return False
    finally:
        await db.close()


async def delete_all_files() -> int:
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT file_path FROM media_files")
        count = 0
        for r in rows:
            fp = r["file_path"]
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            count += 1
        await db.execute("DELETE FROM media_files")
        await db.commit()
        return count
    finally:
        await db.close()


_periodic_task = None


async def _periodic_check():
    while True:
        try:
            cfg = await get_storage_config()
            await asyncio.sleep(cfg["interval"] * 60)
            await check_and_cleanup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Periodic storage check failed: {e}")
            await asyncio.sleep(60)


def start_periodic_check():
    global _periodic_task
    if _periodic_task is None or _periodic_task.done():
        _periodic_task = asyncio.create_task(_periodic_check())
        logger.info("Started periodic storage check")


def stop_periodic_check():
    global _periodic_task
    if _periodic_task and not _periodic_task.done():
        _periodic_task.cancel()
        logger.info("Stopped periodic storage check")


async def pre_download_batch(count: int = 10) -> int:
    """Pick up to `count` items from illust_cache that aren't yet in media_files, download and save."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT illust_id, image_urls FROM illust_cache
            WHERE illust_id NOT IN (SELECT illust_id FROM media_files)
            ORDER BY RANDOM() LIMIT ?""",
            (count,),
        )
    finally:
        await db.close()

    if not rows:
        logger.info("Pre-download: no items in cache need downloading")
        return 0

    logger.info(f"Pre-download: found {len(rows)} items to download")
    sem = asyncio.Semaphore(5)

    async def _download(r):
        illust_id = r["illust_id"]
        image_urls = json.loads(r["image_urls"]) if r["image_urls"] else {}
        original_url = image_urls.get("original", "")
        async with sem:
            try:
                result = await download_image(illust_id, 0, original_url=original_url)
                if result:
                    path, size, mime = result
                    db2 = await get_db()
                    try:
                        await db2.execute(
                            "INSERT OR IGNORE INTO media_files (illust_id, page, file_path, file_size, mime_type) VALUES (?, ?, ?, ?, ?)",
                            (illust_id, 0, path, size, mime),
                        )
                        await db2.commit()
                    finally:
                        await db2.close()
                    logger.info(f"Pre-download: OK {illust_id} ({size} bytes)")
                    return True
                else:
                    logger.warning(f"Pre-download: download returned None for {illust_id}")
            except Exception as e:
                logger.error(f"Pre-download failed for {illust_id}: {e}")
        return False

    tasks = [_download(r) for r in rows]
    results = await asyncio.gather(*tasks)
    downloaded = sum(1 for r in results if r)

    logger.info(f"Pre-download done: {downloaded}/{len(rows)} items downloaded")

    cfg = await get_pool_config()
    await _trim_gallery(cfg["pool_size"])

    return downloaded

    logger.info(f"Pre-download done: {downloaded}/{len(rows)} items downloaded")

    cfg = await get_pool_config()
    await _trim_gallery(cfg["pool_size"])

    return downloaded


async def _trim_gallery(max_size: int):
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM media_files")
        count = row[0]["cnt"]
        if count <= max_size:
            return
        excess = count - max_size
        rows = await db.execute_fetchall(
            "SELECT id, file_path FROM media_files ORDER BY created_at ASC LIMIT ?", (excess,)
        )
        for r in rows:
            fp = r["file_path"]
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
        await db.execute(
            "DELETE FROM media_files WHERE id IN (SELECT id FROM media_files ORDER BY created_at ASC LIMIT ?)",
            (excess,),
        )
        await db.commit()
        logger.info(f"Gallery trimmed: removed {excess} files (max={max_size})")
    finally:
        await db.close()
