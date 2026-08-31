from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import json
import os
import secrets
from datetime import datetime, timedelta
from app.utils import now_utc, utc_to_local, local_now_str

from app.database import get_db
from app.services.cache import refresh_cache

router = APIRouter()

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "exports")


def get_session_token(request: Request) -> str | None:
    cookie = request.headers.get("cookie", "")
    for part in cookie.split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0] == "admin_session":
            return kv[1]
    return None


async def require_auth(request: Request):
    token = get_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT token FROM admin_sessions WHERE token = ? AND expires_at > datetime('now')",
            (token,),
        )
        if not row:
            raise HTTPException(status_code=401, detail="Session expired")
    finally:
        await db.close()


@router.post("/login")
async def login(request: Request):
    body = await request.json()
    password = body.get("password", "")

    import os
    correct = os.environ.get("ADMIN_PASSWORD", "")
    if not correct or password != correct:
        return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)

    token = secrets.token_hex(32)
    expires = (now_utc() + timedelta(hours=24)).isoformat()

    db = await get_db()
    try:
        await db.execute("INSERT INTO admin_sessions (token, expires_at) VALUES (?, ?)", (token, expires))
        await db.commit()
    finally:
        await db.close()

    resp = JSONResponse({"success": True, "data": {"token": token}})
    resp.set_cookie("admin_session", token, httponly=True, samesite="strict", max_age=86400)
    return resp


@router.get("/logout")
async def logout(request: Request):
    token = get_session_token(request)
    if token:
        db = await get_db()
        try:
            await db.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
            await db.commit()
        finally:
            await db.close()

    resp = JSONResponse({"success": True})
    resp.delete_cookie("admin_session")
    return resp


@router.get("/config")
async def get_config(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT key, value FROM config")
        config = {r["key"]: r["value"] for r in row}
    finally:
        await db.close()
    return {"success": True, "data": config}


@router.put("/config")
async def update_config(request: Request):
    await require_auth(request)
    body = await request.json()

    db = await get_db()
    try:
        for key, value in body.items():
            await db.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, str(value)),
            )
        await db.commit()
    finally:
        await db.close()

    return {"success": True}


@router.get("/blacklist/tags")
async def get_blacklist_tags(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT tag FROM blacklisted_tags")
        tags = [r["tag"] for r in row]
    finally:
        await db.close()
    return {"success": True, "data": tags}


@router.post("/blacklist/tags")
async def add_blacklist_tag(request: Request):
    await require_auth(request)
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        return JSONResponse({"success": False, "error": "Missing tag"}, status_code=400)

    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO blacklisted_tags (tag) VALUES (?)", (tag,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.delete("/blacklist/tags/{tag}")
async def delete_blacklist_tag(tag: str, request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        await db.execute("DELETE FROM blacklisted_tags WHERE tag = ?", (tag,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.get("/blacklist/users")
async def get_blacklist_users(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT user_id, user_name FROM blacklisted_users")
        users = [{"user_id": r["user_id"], "user_name": r["user_name"]} for r in row]
    finally:
        await db.close()
    return {"success": True, "data": users}


@router.post("/blacklist/users")
async def add_blacklist_user(request: Request):
    await require_auth(request)
    body = await request.json()
    user_id = body.get("user_id")
    user_name = body.get("user_name", "")
    if not user_id:
        return JSONResponse({"success": False, "error": "Missing user_id"}, status_code=400)

    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO blacklisted_users (user_id, user_name) VALUES (?, ?)", (user_id, user_name))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.delete("/blacklist/users/{user_id}")
async def delete_blacklist_user(user_id: int, request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        await db.execute("DELETE FROM blacklisted_users WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.get("/allowed/tags")
async def get_allowed_tags(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT tag FROM allowed_tags")
        tags = [r["tag"] for r in row]
    finally:
        await db.close()
    return {"success": True, "data": tags}


@router.post("/allowed/tags")
async def add_allowed_tag(request: Request):
    await require_auth(request)
    body = await request.json()
    tag = body.get("tag", "").strip()
    if not tag:
        return JSONResponse({"success": False, "error": "Missing tag"}, status_code=400)

    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO allowed_tags (tag) VALUES (?)", (tag,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.delete("/allowed/tags/{tag}")
async def delete_allowed_tag(tag: str, request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        await db.execute("DELETE FROM allowed_tags WHERE tag = ?", (tag,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.get("/allowed/users")
async def get_allowed_users(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT user_id, user_name FROM allowed_users")
        users = [{"user_id": r["user_id"], "user_name": r["user_name"]} for r in row]
    finally:
        await db.close()
    return {"success": True, "data": users}


@router.post("/allowed/users")
async def add_allowed_user(request: Request):
    await require_auth(request)
    body = await request.json()
    user_id = body.get("user_id")
    user_name = body.get("user_name", "")
    if not user_id:
        return JSONResponse({"success": False, "error": "Missing user_id"}, status_code=400)

    db = await get_db()
    try:
        await db.execute("INSERT OR IGNORE INTO allowed_users (user_id, user_name) VALUES (?, ?)", (user_id, user_name))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.delete("/allowed/users/{user_id}")
async def delete_allowed_user(user_id: int, request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        await db.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    return {"success": True}


@router.get("/stats/overview")
async def stats_overview(request: Request):
    await require_auth(request)
    db = await get_db()
    try:
        cache_row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM illust_cache")
        cache_count = cache_row[0]["cnt"] if cache_row else 0

        gallery_row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM media_files")
        gallery_count = gallery_row[0]["cnt"] if gallery_row else 0

        predownloaded_row = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM media_files m INNER JOIN illust_cache c ON c.illust_id = m.illust_id"
        )
        predownloaded_count = predownloaded_row[0]["cnt"] if predownloaded_row else 0

        tags_row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM blacklisted_tags")
        bl_tags = tags_row[0]["cnt"] if tags_row else 0

        users_row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM blacklisted_users")
        bl_users = users_row[0]["cnt"] if users_row else 0

        cfg_row = await db.execute_fetchall(
            "SELECT key, value FROM config WHERE key IN ('cache_pool_size', 'cache_predownload_count')"
        )
        cfg = {r["key"]: r["value"] for r in cfg_row}
    finally:
        await db.close()

    return {
        "success": True,
        "data": {
            "cache_count": cache_count,
            "gallery_count": gallery_count,
            "predownloaded_count": predownloaded_count,
            "pool_size": int(cfg.get("cache_pool_size", "256")),
            "predownload_count": int(cfg.get("cache_predownload_count", "32")),
            "blacklisted_tags": bl_tags,
            "blacklisted_users": bl_users,
        },
    }


@router.get("/stats/chart")
async def stats_chart(request: Request, days: int = 1):
    await require_auth(request)
    from app.utils import LOCAL_TZ
    offset_hours = LOCAL_TZ.utcoffset(None).total_seconds() // 3600

    db = await get_db()
    try:
        if days == 1:
            rows = await db.execute_fetchall(
                "SELECT date, hour, total_requests FROM stats_counters WHERE date = date('now') ORDER BY hour"
            )
            agg = {}
            for r in rows:
                local_h = (r["hour"] + int(offset_hours)) % 24
                agg[local_h] = agg.get(local_h, 0) + r["total_requests"]
            data = [{"label": f"{h:02d}:00", "value": agg.get(h, 0)} for h in range(24)]
        else:
            rows = await db.execute_fetchall(
                "SELECT date, hour, total_requests FROM stats_counters ORDER BY date, hour"
            )
            agg = {}
            for r in rows:
                dt = datetime.strptime(r["date"], "%Y-%m-%d")
                local_dt = dt + timedelta(hours=offset_hours)
                local_date = local_dt.strftime("%Y-%m-%d")
                if local_date not in agg:
                    agg[local_date] = 0
                agg[local_date] += r["total_requests"]
            sorted_dates = sorted(agg.keys())
            cutoff_display = (now_utc() - timedelta(days=days)).date().isoformat()
            data = [{"label": d, "value": agg.get(d, 0)} for d in sorted_dates if d >= cutoff_display]
    finally:
        await db.close()

    return {"success": True, "data": data}


@router.get("/logs")
async def get_logs(request: Request, page: int = 1, limit: int = 50):
    await require_auth(request)
    offset = (page - 1) * limit

    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM api_logs")
        total = row[0]["cnt"] if row else 0

        row = await db.execute_fetchall(
            "SELECT * FROM api_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        logs = []
        for r in row:
            d = dict(r)
            try:
                dt = datetime.strptime(d["created_at"], "%Y-%m-%d %H:%M:%S")
                d["created_at"] = utc_to_local(dt).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            logs.append(d)
    finally:
        await db.close()

    return {"success": True, "data": {"logs": logs, "total": total, "page": page, "limit": limit}}


@router.post("/export")
async def export_config(request: Request):
    await require_auth(request)

    db = await get_db()
    try:
        config_row = await db.execute_fetchall("SELECT key, value FROM config")
        config = {r["key"]: r["value"] for r in config_row}

        bl_tags_row = await db.execute_fetchall("SELECT tag FROM blacklisted_tags")
        bl_tags = [r["tag"] for r in bl_tags_row]

        bl_users_row = await db.execute_fetchall("SELECT user_id, user_name FROM blacklisted_users")
        bl_users = [{"user_id": r["user_id"], "user_name": r["user_name"]} for r in bl_users_row]

        al_tags_row = await db.execute_fetchall("SELECT tag FROM allowed_tags")
        al_tags = [r["tag"] for r in al_tags_row]

        al_users_row = await db.execute_fetchall("SELECT user_id, user_name FROM allowed_users")
        al_users = [{"user_id": r["user_id"], "user_name": r["user_name"]} for r in al_users_row]
    finally:
        await db.close()

    export_data = {
        "config": config,
        "blacklisted_tags": bl_tags,
        "blacklisted_users": bl_users,
        "allowed_tags": al_tags,
        "allowed_users": al_users,
        "exported_at": local_now_str(),
    }

    os.makedirs(EXPORT_DIR, exist_ok=True)
    filename = f"pixiv-config-{now_utc().strftime('%Y%m%d-%H%M%S')}.json"
    filepath = os.path.join(EXPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return {"success": True, "data": export_data, "filename": filename}


@router.post("/import")
async def import_config(request: Request):
    await require_auth(request)
    body = await request.json()

    db = await get_db()
    try:
        if "config" in body:
            for key, value in body["config"].items():
                await db.execute(
                    "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (key, str(value)),
                )

        if "blacklisted_tags" in body:
            await db.execute("DELETE FROM blacklisted_tags")
            for tag in body["blacklisted_tags"]:
                await db.execute("INSERT OR IGNORE INTO blacklisted_tags (tag) VALUES (?)", (tag,))

        if "blacklisted_users" in body:
            await db.execute("DELETE FROM blacklisted_users")
            for user in body["blacklisted_users"]:
                await db.execute(
                    "INSERT OR IGNORE INTO blacklisted_users (user_id, user_name) VALUES (?, ?)",
                    (user.get("user_id"), user.get("user_name", "")),
                )

        if "allowed_tags" in body:
            await db.execute("DELETE FROM allowed_tags")
            for tag in body["allowed_tags"]:
                await db.execute("INSERT OR IGNORE INTO allowed_tags (tag) VALUES (?)", (tag,))

        if "allowed_users" in body:
            await db.execute("DELETE FROM allowed_users")
            for user in body["allowed_users"]:
                await db.execute(
                    "INSERT OR IGNORE INTO allowed_users (user_id, user_name) VALUES (?, ?)",
                    (user.get("user_id"), user.get("user_name", "")),
                )

        await db.commit()
    finally:
        await db.close()

    return {"success": True}


@router.post("/cache/refresh")
async def refresh_cache_endpoint(request: Request):
    await require_auth(request)
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    mode = body.get("mode", "daily")
    success = await refresh_cache(mode)
    return {"success": success}


@router.get("/storage")
async def get_storage(request: Request):
    await require_auth(request)
    from app.services.storage import get_storage_usage
    usage = await get_storage_usage()
    return {"success": True, "data": usage}


@router.get("/storage/gallery")
async def get_gallery(request: Request, page: int = 1, limit: int = 20):
    await require_auth(request)
    offset = (page - 1) * limit

    db = await get_db()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM media_files")
        total = row[0]["cnt"] if row else 0

        rows = await db.execute_fetchall(
            "SELECT m.illust_id, m.page, m.file_path, m.file_size, m.mime_type, m.created_at, "
            "c.title, c.user_name, c.user_id "
            "FROM media_files m LEFT JOIN illust_cache c ON m.illust_id = c.illust_id "
            "ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        files = [dict(r) for r in rows]
    finally:
        await db.close()

    return {"success": True, "data": {"files": files, "total": total, "page": page, "limit": limit}}


@router.post("/storage/cleanup")
async def trigger_cleanup(request: Request):
    await require_auth(request)
    from app.services.storage import cleanup_old_files
    deleted = await cleanup_old_files(target_free_percent=20)
    from app.services.storage import get_storage_usage
    usage = await get_storage_usage()
    return {"success": True, "data": {"deleted_files": deleted, "usage": usage}}


@router.delete("/storage/files")
async def delete_all_media(request: Request):
    await require_auth(request)
    from app.services.storage import delete_all_files
    count = await delete_all_files()
    return {"success": True, "data": {"deleted_count": count}}


@router.delete("/storage/file/{illust_id}/{page}")
async def delete_media_file(illust_id: int, page: int, request: Request):
    await require_auth(request)
    from app.services.storage import delete_file
    ok = await delete_file(illust_id, page)
    return {"success": ok}
