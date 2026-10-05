"""SQLite 持久化与 JSON 文件缓存。

只保存真正有价值的数据：作品映射、搜索缓存、下载历史。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_mapping (
    input_title     TEXT PRIMARY KEY,
    nekobt_media_id TEXT NOT NULL,
    anilist_id      INTEGER,
    bgm_id          INTEGER,
    confidence      REAL,
    confirmed       INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS downloads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    torrent_id   TEXT NOT NULL,
    torrent_hash TEXT,
    title        TEXT,
    save_path    TEXT,
    client       TEXT,
    status       TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class MediaMappingRecord:
    input_title: str
    nekobt_media_id: str
    anilist_id: Optional[int] = None
    bgm_id: Optional[int] = None
    confidence: Optional[float] = None
    confirmed: bool = False


class Store:
    """极简 SQLite 封装。"""

    def __init__(self, db_path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_mapping(self, record: MediaMappingRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO media_mapping
                    (input_title, nekobt_media_id, anilist_id, bgm_id, confidence, confirmed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(input_title) DO UPDATE SET
                    nekobt_media_id = excluded.nekobt_media_id,
                    anilist_id      = excluded.anilist_id,
                    bgm_id          = excluded.bgm_id,
                    confidence      = excluded.confidence,
                    confirmed       = excluded.confirmed,
                    updated_at      = excluded.updated_at
                """,
                (
                    record.input_title,
                    record.nekobt_media_id,
                    record.anilist_id,
                    record.bgm_id,
                    record.confidence,
                    1 if record.confirmed else 0,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )

    def get_mapping(self, input_title: str) -> Optional[MediaMappingRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_mapping WHERE input_title = ?", (input_title,)
            ).fetchone()
        if row is None:
            return None
        return MediaMappingRecord(
            input_title=row["input_title"],
            nekobt_media_id=row["nekobt_media_id"],
            anilist_id=row["anilist_id"],
            bgm_id=row["bgm_id"],
            confidence=row["confidence"],
            confirmed=bool(row["confirmed"]),
        )

    def record_download(
        self,
        *,
        torrent_id: str,
        torrent_hash: Optional[str],
        title: str,
        save_path: str,
        client: str,
        status: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads
                    (torrent_id, torrent_hash, title, save_path, client, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    torrent_id,
                    torrent_hash,
                    title,
                    save_path,
                    client,
                    status,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )

    def list_downloads(self, limit: int = 20) -> List[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return list(rows)

    # ------------------------------------------------------------ settings

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def all_settings(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}


class JsonCache:
    """按 key 缓存 JSON 响应，用于减少重复 API 请求。"""

    def __init__(self, cache_dir, ttl_seconds: int = 3600) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key)
        return self.cache_dir / f"{safe[:120]}.json"

    def get(self, key: str):
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if time.time() - payload.get("_cached_at", 0) > self.ttl_seconds:
            return None
        return payload.get("data")

    def set(self, key: str, data) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"_cached_at": time.time(), "data": data}
        self._path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
