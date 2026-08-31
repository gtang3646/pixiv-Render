import json
import logging
import asyncio
from app.database import get_db
from app.services.pixiv import fetch_ranking, fetch_illust_detail, fetch_recommend
from app.utils import now_utc

logger = logging.getLogger(__name__)


async def get_pool_config() -> dict:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT key, value FROM config WHERE key IN ('cache_pool_size', 'cache_predownload_count', 'ranking_mode')"
        )
        cfg = {r["key"]: r["value"] for r in row}
    finally:
        await db.close()
    return {
        "pool_size": int(cfg.get("cache_pool_size", "100")),
        "predownload_count": int(cfg.get("cache_predownload_count", "10")),
        "ranking_mode": cfg.get("ranking_mode", "daily"),
    }


async def get_pool_count() -> int:
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM illust_cache")
        return row[0]["cnt"] if row else 0
    finally:
        await db.close()


async def _insert_illust(detail: dict) -> bool:
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT 1 FROM illust_cache WHERE illust_id = ?", (detail["id"],))
        if row:
            return False
        await db.execute(
            """INSERT INTO illust_cache
            (illust_id, title, user_id, user_name, tags,
             view_count, bookmark_count, like_count,
             page_count, image_urls, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                detail["id"],
                detail["title"],
                detail["user_id"],
                detail["user_name"],
                json.dumps(detail["tags"]),
                detail["view_count"],
                detail["bookmark_count"],
                detail["like_count"],
                detail["page_count"],
                json.dumps(detail["image_urls"]),
                now_utc().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def refresh_cache(mode: str = "daily") -> bool:
    try:
        logger.info(f"Refreshing cache with mode={mode}")

        all_illusts = []
        for page in range(1, 4):
            illusts = await fetch_ranking(mode, page)
            all_illusts.extend(illusts)
            if len(illusts) < 30:
                break

        logger.info(f"Got {len(all_illusts)} illusts from ranking, fetching details...")

        sem = asyncio.Semaphore(5)

        async def _fetch(item):
            async with sem:
                try:
                    return await fetch_illust_detail(item["id"])
                except Exception as e:
                    logger.error(f"Detail failed for {item['id']}: {e}")
                    return None

        tasks = [_fetch(item) for item in all_illusts]
        results = await asyncio.gather(*tasks)

        count = 0
        for detail in results:
            if detail and await _insert_illust(detail):
                count += 1

        logger.info(f"Inserted {count} items into cache")
        return count > 0

    except Exception as e:
        logger.error(f"Cache refresh failed: {e}")
        return False


async def refresh_cache_from_recommend(illust_id: int) -> int:
    try:
        recommend_ids = await fetch_recommend(illust_id, limit=30)
        if not recommend_ids:
            return 0

        sem = asyncio.Semaphore(5)

        async def _fetch(rid):
            async with sem:
                try:
                    return await fetch_illust_detail(rid)
                except Exception as e:
                    logger.error(f"Detail failed for recommend {rid}: {e}")
                    return None

        tasks = [_fetch(rid) for rid in recommend_ids]
        results = await asyncio.gather(*tasks)

        count = 0
        for detail in results:
            if detail and await _insert_illust(detail):
                count += 1

        logger.info(f"Inserted {count} items from recommend of {illust_id}")
        return count
    except Exception as e:
        logger.error(f"Recommend refresh failed for {illust_id}: {e}")
        return 0


async def pick_random(
    db,
    tag: str | None = None,
    user_id: str | None = None,
    use_blacklist: bool = True,
    use_whitelist: bool = False,
) -> dict | None:
    bl_tags = []
    bl_users = []
    wl_tags = []
    wl_users = []

    if use_blacklist:
        row = await db.execute_fetchall("SELECT tag FROM blacklisted_tags")
        bl_tags = [r["tag"] for r in row]
        row = await db.execute_fetchall("SELECT user_id FROM blacklisted_users")
        bl_users = [r["user_id"] for r in row]

    if use_whitelist:
        row = await db.execute_fetchall("SELECT tag FROM allowed_tags")
        wl_tags = [r["tag"] for r in row]
        row = await db.execute_fetchall("SELECT user_id FROM allowed_users")
        wl_users = [r["user_id"] for r in row]

    for _ in range(20):
        row = await db.execute_fetchall(
            "SELECT * FROM illust_cache ORDER BY RANDOM() LIMIT 1"
        )
        if not row:
            return None

        cache = dict(row[0])
        illust_id = cache["illust_id"]
        illust_tags = json.loads(cache["tags"]) if cache["tags"] else []
        illust_user_id = cache["user_id"]

        skip = False
        if use_blacklist:
            if any(t in bl_tags for t in illust_tags):
                skip = True
            if illust_user_id and illust_user_id in bl_users:
                skip = True

        if not skip and use_whitelist:
            if tag and tag not in wl_tags and tag not in illust_tags:
                skip = True
            if user_id:
                uid = int(user_id) if user_id.isdigit() else user_id
                if uid not in wl_users and illust_user_id != uid:
                    skip = True

        if not skip and tag and not use_whitelist:
            if tag not in illust_tags:
                skip = True

        if not skip and user_id and not use_whitelist:
            uid = int(user_id) if user_id.isdigit() else user_id
            if illust_user_id != uid:
                skip = True

        if skip:
            continue

        return _row_to_dict(cache)

    return None


async def delete_from_cache(illust_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM illust_cache WHERE illust_id = ?", (illust_id,))
        await db.commit()
    finally:
        await db.close()


async def get_gallery_count() -> int:
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM media_files")
        return row[0]["cnt"] if row else 0
    finally:
        await db.close()


async def get_predownloaded_count() -> int:
    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM media_files m INNER JOIN illust_cache c ON c.illust_id = m.illust_id"
        )
        return row[0]["cnt"] if row else 0
    finally:
        await db.close()


async def pick_random_from_gallery(
    db,
    tag: str | None = None,
    user_id: str | None = None,
    use_blacklist: bool = True,
    use_whitelist: bool = False,
) -> dict | None:
    """Pick a random illustration that already has a file in media_files."""
    bl_tags, bl_users, wl_tags, wl_users = [], [], [], []

    if use_blacklist:
        row = await db.execute_fetchall("SELECT tag FROM blacklisted_tags")
        bl_tags = [r["tag"] for r in row]
        row = await db.execute_fetchall("SELECT user_id FROM blacklisted_users")
        bl_users = [r["user_id"] for r in row]

    if use_whitelist:
        row = await db.execute_fetchall("SELECT tag FROM allowed_tags")
        wl_tags = [r["tag"] for r in row]
        row = await db.execute_fetchall("SELECT user_id FROM allowed_users")
        wl_users = [r["user_id"] for r in row]

    need_filter = bool(bl_tags or bl_users or wl_tags or wl_users or tag or user_id)

    if need_filter:
        rows = await db.execute_fetchall(
            """SELECT c.illust_id, c.title, c.user_id, c.user_name, c.tags,
               c.view_count, c.bookmark_count, c.like_count, c.page_count, c.image_urls
            FROM media_files m
            JOIN illust_cache c ON c.illust_id = m.illust_id"""
        )
        candidates = []
        for r in rows:
            cache = dict(r)
            illust_tags = json.loads(cache["tags"]) if cache["tags"] else []
            illust_user_id = cache["user_id"]

            skip = False
            if use_blacklist:
                if any(t in bl_tags for t in illust_tags):
                    skip = True
                if illust_user_id and illust_user_id in bl_users:
                    skip = True
            if not skip and use_whitelist:
                if tag and tag not in wl_tags and tag not in illust_tags:
                    skip = True
                if user_id:
                    uid = int(user_id) if user_id.isdigit() else user_id
                    if uid not in wl_users and illust_user_id != uid:
                        skip = True
            if not skip and tag and not use_whitelist:
                if tag not in illust_tags:
                    skip = True
            if not skip and user_id and not use_whitelist:
                uid = int(user_id) if user_id.isdigit() else user_id
                if illust_user_id != uid:
                    skip = True
            if not skip:
                candidates.append(cache)

        if not candidates:
            return None
        import random
        return _row_to_dict(random.choice(candidates))
    else:
        row = await db.execute_fetchall(
            """SELECT c.illust_id, c.title, c.user_id, c.user_name, c.tags,
               c.view_count, c.bookmark_count, c.like_count, c.page_count, c.image_urls
            FROM media_files m
            JOIN illust_cache c ON c.illust_id = m.illust_id
            ORDER BY RANDOM() LIMIT 1"""
        )
        if not row:
            return None
        return _row_to_dict(dict(row[0]))


async def ensure_pool_filled(mode: str | None = None):
    cfg = await get_pool_config()
    count = await get_pool_count()
    half = cfg["pool_size"] // 2

    if count >= half:
        return

    logger.info(f"Cache pool below 50% ({count}/{cfg['pool_size']}), refreshing...")

    if not mode:
        mode = cfg["ranking_mode"]

    await refresh_cache(mode)

    new_count = await get_pool_count()
    if new_count < half:
        logger.info(f"Still below 50% after ranking refresh ({new_count}), trying recommend...")
        sample_row = None
        db = await get_db()
        try:
            row = await db.execute_fetchall("SELECT illust_id FROM illust_cache ORDER BY RANDOM() LIMIT 1")
            if row:
                sample_row = row[0]["illust_id"]
        finally:
            await db.close()

        if sample_row:
            await refresh_cache_from_recommend(sample_row)

    await _trim_cache(cfg["pool_size"])


async def _trim_cache(max_size: int):
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM illust_cache")
        count = row[0]["cnt"]
        if count <= max_size:
            return
        excess = count - max_size
        await db.execute(
            "DELETE FROM illust_cache WHERE id IN (SELECT id FROM illust_cache ORDER BY created_at ASC LIMIT ?)",
            (excess,),
        )
        await db.commit()
        logger.info(f"Cache trimmed: removed {excess} items (max={max_size})")
    finally:
        await db.close()


def _row_to_dict(cache: dict) -> dict:
    return {
        "illust_id": cache["illust_id"],
        "title": cache["title"],
        "user_id": cache["user_id"],
        "user_name": cache["user_name"],
        "tags": json.loads(cache["tags"]) if cache["tags"] else [],
        "view_count": cache["view_count"],
        "bookmark_count": cache["bookmark_count"],
        "like_count": cache["like_count"],
        "page_count": cache["page_count"],
        "image_urls": json.loads(cache["image_urls"]) if cache["image_urls"] else {},
    }
