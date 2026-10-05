"""V1 Web API 测试（全部离线，用假客户端注入）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app, get_context
from src.config import Settings
from src.nekobt.client import TorrentSearchPage
from src.service import ServiceContext, build_context
from src.storage import Store

from tests.fakes import media_result, torrent_row


class FakeNekoBT:
    def __init__(self, media_rows=None, torrent_rows=None, torrent_bytes=b"d4:infod4:name4:teste"):
        self.media_rows = media_rows if media_rows is not None else [
            media_result("s462", "Frieren: Beyond Journey's End", 0.5556),
            media_result("m641", "JoJo no Kimyou na Bouken", 0.0, year=2007),
        ]
        self.torrent_rows = torrent_rows if torrent_rows is not None else [
            torrent_row("1", "[BD] Frieren - S02 (BD 1080p HEVC)", sub_lang="zh-hans", video_codec=2, seeders=31),
            torrent_row("2", "[WEB] Frieren - S02 (WEB 1080p H.264)", sub_lang="en", video_codec=1, seeders=88),
            torrent_row("3", "[TV] Frieren - 38 (TV 1080p HEVC)", sub_lang="", video_codec=2, seeders=5),
        ]
        self.torrent_bytes = torrent_bytes
        self.downloaded = []

    def search_media(self, query, limit=None):
        return self.media_rows

    def search_torrents(self, **kwargs):
        return TorrentSearchPage(results=self.torrent_rows, meta={})

    def get_media(self, media_id):
        for row in self.media_rows:
            if row.get("id") == media_id:
                return {"id": media_id, "title": row.get("title"), "year": row.get("year")}
        return {"id": media_id}

    def download_torrent(self, torrent_id, public=True):
        self.downloaded.append(torrent_id)
        return self.torrent_bytes, f"{torrent_id}.torrent"


class FakeQbit:
    def __init__(self):
        self.added = []

    def add_torrent(self, **kwargs):
        self.added.append(kwargs)

    def torrents_info(self, **kwargs):
        return []

    def webapi_version(self):
        return "2.9.3"

    def app_version(self):
        return "v4.6.5.10"

    def probe_task_endpoints(self):
        return {"stop": "http 404", "pause": "ok"}


class FakeBangumi:
    def __init__(self, subjects=None, available=True):
        self.available = available
        self.subjects = subjects if subjects is not None else [
            {
                "id": 464376,
                "name_cn": "败犬女主太多了！",
                "name": "負けヒロインが多すぎる！",
                "date": "2024-07-13",
            },
            {
                "id": 550507,
                "name_cn": "败犬女主太多了！第二季",
                "name": "負けヒロインが多すぎる！ 第2期",
                "date": None,
            },
        ]
        self.calls = []

    def is_available(self, refresh=False):
        return self.available

    def search_subjects(self, keyword, limit=5):
        self.calls.append(keyword)
        return self.subjects[:limit]


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(
        qbit_password="secret",
        qbit_savepath=r"D:\Anime\library",
        db_path=tmp_path / "test.db",
    )
    context = ServiceContext(
        settings=settings,
        nekobt=FakeNekoBT(),
        store=Store(settings.db_path),
    )
    context._qbit = FakeQbit()
    context._bangumi = FakeBangumi()
    return context


@pytest.fixture
def offline_ctx(tmp_path):
    """Bangumi 不可用的情况。"""
    settings = Settings(qbit_password="secret", db_path=tmp_path / "off.db")
    context = ServiceContext(settings=settings, nekobt=FakeNekoBT(), store=Store(settings.db_path))
    context._qbit = FakeQbit()
    context._bangumi = FakeBangumi(available=False)
    return context


@pytest.fixture
def client(ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: ctx
    return TestClient(app)


def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "动漫 Release 管理器" in res.text


def test_health_reports_both_services(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["qbittorrent"]["webapi_version"] == "2.9.3"
    assert body["qbittorrent"]["endpoints"]["pause"] == "ok"


def test_search_returns_candidates_with_similarity(client):
    res = client.get("/api/search", params={"title": "葬送的芙莉莲"})
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "bangumi"
    first = body["candidates"][0]
    assert first["media_id"] == "s462"
    assert first["confidence"] == "high"
    assert first["name_cn"] == "败犬女主太多了！"
    assert first["origin"] == "bangumi"
    assert first["year"] == 2024


def test_search_falls_back_to_nekobt_when_bangumi_down(offline_ctx):
    app = create_app()
    app.dependency_overrides[get_context] = lambda: offline_ctx
    body = TestClient(app).get("/api/search", params={"title": "葬送的芙莉莲"}).json()

    assert body["source"] == "nekobt"
    assert any("Bangumi 不可用" in note for note in body["notes"])
    assert body["candidates"][0]["media_id"] == "s462"
    # 0.5556 属于可信档；0.0 的噪声被标成 low
    assert body["candidates"][0]["confidence"] == "high"
    assert body["candidates"][1]["confidence"] == "low"


def test_confirmed_mapping_is_used_before_any_network_call(client, ctx):
    payload = {"input_title": "葬送的芙莉莲", "media_id": "s462", "anilist_id": 154587, "confidence": 1.0}
    assert client.post("/api/mapping", json=payload).json()["ok"] is True

    body = client.get("/api/search", params={"title": "葬送的芙莉莲"}).json()
    assert body["source"] == "cache"
    assert body["candidates"][0]["media_id"] == "s462"
    assert body["candidates"][0]["matched_by"] == "local-cache"
    # 命中缓存后不应该再去问 Bangumi
    assert ctx._bangumi.calls == []


def test_bangumi_candidate_without_nekobt_match_is_marked(offline_ctx):
    ctx = offline_ctx
    ctx._bangumi = FakeBangumi(subjects=[{"id": 1, "name_cn": "某个作品", "name": "", "date": None}])
    ctx.nekobt.media_rows = []
    app = create_app()
    app.dependency_overrides[get_context] = lambda: ctx
    body = TestClient(app).get("/api/search", params={"title": "某个作品"}).json()

    # 桥接失败 → 退回 nekoBT 模糊搜索
    assert body["source"] == "nekobt"
    assert any("没找到对应媒体" in note for note in body["notes"])


def test_search_rejects_blank_title(client):
    assert client.get("/api/search", params={"title": "   "}).status_code == 400


def test_releases_returns_three_buckets(client):
    res = client.get("/api/media/s462/releases")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert sum(body["counts"].values()) == 3
    assert body["matched"][0]["release"]["torrent_id"]


def test_releases_filter_narrows_matched(client):
    res = client.get(
        "/api/media/s462/releases",
        params=[("resolution", "1080p"), ("source", "BDRip"), ("codec", "HEVC")],
    )
    body = res.json()
    assert [item["release"]["torrent_id"] for item in body["matched"]] == ["1"]
    # 2 号编码是 H.264，明确不符合 → 排除
    assert [item["release"]["torrent_id"] for item in body["excluded"]] == ["2"]
    # 3 号标题只写 TV，来源解析不出（不猜）→ 字段未知
    assert [item["release"]["torrent_id"] for item in body["unknown"]] == ["3"]


def test_releases_subtitle_filter_puts_missing_info_into_unknown(client):
    res = client.get("/api/media/s462/releases", params={"sub_lang": "zh-hans"})
    body = res.json()
    assert [i["release"]["torrent_id"] for i in body["matched"]] == ["1"]
    assert [i["release"]["torrent_id"] for i in body["excluded"]] == ["2"]
    assert [i["release"]["torrent_id"] for i in body["unknown"]] == ["3"]


def test_releases_sort_by_seeders(client):
    res = client.get("/api/media/s462/releases", params={"sort": "seeders"})
    body = res.json()
    seeders = [i["release"]["seeders"] for i in body["matched"]]
    assert seeders == sorted(seeders, reverse=True)


def test_download_submits_to_qbittorrent_and_records_history(client, ctx):
    res = client.post(
        "/api/download",
        json={"torrent_id": "1", "title": "Frieren S02", "paused": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["paused"] is True
    assert body["save_path"] == r"D:\Anime\library"

    added = ctx._qbit.added[0]
    assert added["savepath"] == r"D:\Anime\library"
    assert added["category"] == "anime"
    assert added["paused"] is True
    assert added["torrent_filename"] == "1.torrent"

    history = client.get("/api/downloads").json()["items"]
    assert history[0]["torrent_id"] == "1"
    assert history[0]["status"] == "paused"


def test_download_without_password_returns_400(tmp_path):
    settings = Settings(qbit_password="", db_path=tmp_path / "t.db")
    context = ServiceContext(settings=settings, nekobt=FakeNekoBT(), store=Store(settings.db_path))
    context._qbit = FakeQbit()
    app = create_app()
    app.dependency_overrides[get_context] = lambda: context
    res = TestClient(app).post("/api/download", json={"torrent_id": "1"})
    assert res.status_code == 400
    assert "QBIT_PASSWORD" in res.json()["detail"]


def test_preferences_roundtrip(client):
    payload = {
        "resolutions": ["1080p"],
        "sources": ["BDRip"],
        "video_codecs": ["HEVC"],
        "sub_langs": ["zh-hans"],
        "require_batch": False,
        "min_seeders": 10,
    }
    assert client.post("/api/preferences", json=payload).json()["ok"] is True

    got = client.get("/api/preferences").json()
    assert got["preferences"]["resolutions"] == ["1080p"]
    assert got["preferences"]["min_seeders"] == 10
    assert got["defaults"]["resolutions"][0] == "1080p"


def test_saved_preferences_are_used_when_no_query_filters(client, ctx):
    ctx.store.set_setting(
        "preferences",
        '{"resolutions": ["1080p"], "sources": ["BDRip"], "video_codecs": ["HEVC"], '
        '"sub_langs": [], "require_batch": null, "min_seeders": null}',
    )
    body = client.get("/api/media/s462/releases").json()
    assert [i["release"]["torrent_id"] for i in body["matched"]] == ["1"]


def test_build_context_wires_real_clients(tmp_path):
    settings = Settings(db_path=tmp_path / "x.db", cache_dir=tmp_path / "cache")
    context = build_context(settings)
    assert context.nekobt.base_url.startswith("https://")
    assert context.qbit.url.startswith("http://")
