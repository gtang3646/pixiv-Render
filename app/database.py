import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pixiv.db")


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.execute("DELETE FROM config WHERE key = 'cache_pool_threshold'")
        await db.commit()
    finally:
        await db.close()


async def populate_cache_if_empty():
    from app.services.cache import ensure_pool_filled
    await ensure_pool_filled()


SCHEMA = """
CREATE TABLE IF NOT EXISTS blacklisted_tags (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  tag        TEXT NOT NULL UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blacklisted_users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL UNIQUE,
  user_name  TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS allowed_tags (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  tag        TEXT NOT NULL UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS allowed_users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL UNIQUE,
  user_name  TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS illust_cache (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  illust_id      INTEGER NOT NULL UNIQUE,
  title          TEXT,
  user_id        INTEGER,
  user_name      TEXT,
  tags           TEXT,
  view_count     INTEGER DEFAULT 0,
  bookmark_count INTEGER DEFAULT 0,
  like_count     INTEGER DEFAULT 0,
  page_count     INTEGER DEFAULT 1,
  image_urls     TEXT,
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cache_user ON illust_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_cache_updated ON illust_cache(updated_at);

CREATE TABLE IF NOT EXISTS config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_logs (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  method           TEXT,
  path             TEXT,
  status_code      INTEGER,
  ip               TEXT,
  response_time_ms INTEGER,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON api_logs(created_at);

CREATE TABLE IF NOT EXISTS stats_counters (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  date           TEXT NOT NULL,
  hour           INTEGER DEFAULT 0,
  total_requests INTEGER DEFAULT 0,
  cache_hits     INTEGER DEFAULT 0,
  cache_misses   INTEGER DEFAULT 0,
  UNIQUE(date, hour)
);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token      TEXT PRIMARY KEY,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON admin_sessions(expires_at);

CREATE TABLE IF NOT EXISTS media_files (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  illust_id   INTEGER NOT NULL,
  page        INTEGER NOT NULL DEFAULT 0,
  file_path   TEXT NOT NULL,
  file_size   INTEGER NOT NULL DEFAULT 0,
  mime_type   TEXT DEFAULT 'image/jpeg',
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(illust_id, page)
);
CREATE INDEX IF NOT EXISTS idx_media_created ON media_files(created_at);

INSERT OR IGNORE INTO config (key, value) VALUES
  ('admin_enabled', 'true'),
  ('feature_stats', 'true'),
  ('feature_logs', 'true'),
  ('feature_cache', 'true'),
  ('feature_blacklist', 'true'),
  ('feature_whitelist', 'false'),
  ('random_mode', 'uniform'),
  ('rate_limit', '60'),
  ('ranking_mode', 'daily'),
  ('cache_ttl_hours', '24'),
  ('storage_limit_mb', '512'),
  ('storage_cleanup_threshold', '80'),
  ('storage_check_interval', '60'),
  ('cache_pool_size', '256'),
  ('cache_predownload_count', '32');
"""
