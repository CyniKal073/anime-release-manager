"""配置校验与本地持久化。"""

from __future__ import annotations

import pytest

from src.config import ConfigError, Settings
from src.storage import JsonCache, MediaMappingRecord, Store


def test_settings_from_env_reads_values():
    settings = Settings.from_env(
        {
            "NEKOBT_BASE_URL": "https://nekobt.to",
            "QBIT_PASSWORD": "secret",
            "QBIT_SAVEPATH": r"D:\Anime\library",
        }
    )
    assert settings.qbit_password == "secret"
    assert settings.qbit_savepath == r"D:\Anime\library"
    assert settings.download_configured is True


def test_missing_password_raises_actionable_error():
    settings = Settings.from_env({})
    assert settings.download_configured is False
    with pytest.raises(ConfigError) as excinfo:
        settings.require_download_settings()
    assert "WebUI" in str(excinfo.value)


def test_invalid_number_raises():
    with pytest.raises(ConfigError):
        Settings.from_env({"HTTP_TIMEOUT": "abc"})


def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "app.db")
    store.save_mapping(
        MediaMappingRecord(
            input_title="葬送的芙莉莲",
            nekobt_media_id="s462",
            anilist_id=154587,
            confidence=0.5556,
            confirmed=True,
        )
    )
    record = store.get_mapping("葬送的芙莉莲")
    assert record is not None
    assert record.nekobt_media_id == "s462"
    assert record.confirmed is True

    store.record_download(
        torrent_id="1",
        torrent_hash="abc",
        title="[Group] Frieren - 01",
        save_path=r"D:\Anime\library",
        client="qbittorrent",
        status="started",
    )
    rows = store.list_downloads()
    assert len(rows) == 1
    assert rows[0]["torrent_id"] == "1"


def test_json_cache_hits_and_misses(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    assert cache.get("media:s462") is None
    cache.set("media:s462", {"id": "s462"})
    assert cache.get("media:s462") == {"id": "s462"}


def test_json_cache_respects_ttl(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=-1)
    cache.set("k", {"a": 1})
    assert cache.get("k") is None
