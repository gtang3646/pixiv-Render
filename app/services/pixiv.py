import httpx
import os
from typing import Optional

PIXIV_BASE = "https://www.pixiv.net"
HEADERS = {
    "Referer": "https://www.pixiv.net/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")


def _client_kwargs():
    kw = {"timeout": 10, "follow_redirects": True}
    if PROXY:
        kw["proxy"] = PROXY
    return kw


async def fetch_ranking(mode: str = "daily", page: int = 1) -> list[dict]:
    url = f"{PIXIV_BASE}/ranking.php"
    params = {"format": "json", "mode": mode, "content": "illust", "p": page}

    async with httpx.AsyncClient(**_client_kwargs()) as client:
        res = await client.get(url, headers=HEADERS, params=params)
        res.raise_for_status()
        data = res.json()

    return [
        {
            "id": item["illust_id"],
            "title": item["title"],
            "user_id": item["user_id"],
            "user_name": item["user_name"],
            "view_count": item["view_count"],
            "page_count": item["illust_page_count"],
        }
        for item in data.get("contents", [])
    ]


async def fetch_illust_detail(illust_id: int) -> Optional[dict]:
    url = f"{PIXIV_BASE}/ajax/illust/{illust_id}"

    async with httpx.AsyncClient(**_client_kwargs()) as client:
        res = await client.get(url, headers=HEADERS)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        data = res.json()

    if data.get("error"):
        return None

    body = data["body"]
    return {
        "id": body["id"],
        "title": body["title"],
        "user_id": body["userId"],
        "user_name": body["userName"],
        "tags": [t["tag"] for t in body["tags"]["tags"]],
        "view_count": body["viewCount"],
        "bookmark_count": body["bookmarkCount"],
        "like_count": body["likeCount"],
        "page_count": body["pageCount"],
        "image_urls": body["urls"],
    }


async def fetch_recommend(illust_id: int, limit: int = 30) -> list[int]:
    url = f"{PIXIV_BASE}/ajax/illust/{illust_id}/recommend/init"
    params = {"limit": limit, "lang": "zh"}

    async with httpx.AsyncClient(**_client_kwargs()) as client:
        res = await client.get(url, headers=HEADERS, params=params)
        if res.status_code != 200:
            return []
        data = res.json()

    if data.get("error"):
        return []

    return [int(i) for i in data.get("body", {}).get("illusts", [])]
