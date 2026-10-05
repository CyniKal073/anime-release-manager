"""Test 3 / Test 7：Release 检索与筛选参数。"""

from __future__ import annotations

from src.nekobt.client import TORRENT_SEARCH_PATH, NekoBTClient

from tests.fakes import FakeResponse, FakeSession, torrent_row


def _client(rows):
    def handler(method, url, **kwargs):
        assert TORRENT_SEARCH_PATH in url
        return FakeResponse({"error": False, "data": {"results": rows, "search": {"limit": 50}}})

    session = FakeSession(handler)
    return NekoBTClient(session=session, retries=1), session


def test_test3_sub_lang_filter_uses_correct_param():
    """Test 3：字幕筛选参数名是 sub_lang，不是 subtitle_language。"""
    rows = [torrent_row("1", "[Group] Frieren - 01 [1080p][HEVC]", sub_lang="zh-hans,en")]
    client, session = _client(rows)
    page = client.search_torrents(media_id="s462", sub_lang="zh-hans", limit=50)

    params = session.last["params"]
    assert params["sub_lang"] == "zh-hans"
    assert "subtitle_language" not in params
    assert len(page) == 1


def test_test7_torrent_search_uses_media_id():
    """Test 7：Release 检索主路径是 media_id，而不是中文关键字。"""
    rows = [torrent_row("1", "[Group] Frieren - 01", media_id="s462")]
    client, session = _client(rows)
    page = client.search_torrents(media_id="s462")

    params = session.last["params"]
    assert params["media_id"] == "s462"
    assert "query" not in params
    assert all(row["media_id"] == "s462" for row in page.results)


def test_none_params_are_dropped_and_bools_serialized():
    rows = [torrent_row("1", "[Group] Frieren - 01")]
    client, session = _client(rows)
    client.search_torrents(media_id="s462", batch=True, video_codec=2, offset=None)

    params = session.last["params"]
    assert params["batch"] == "true"
    assert params["video_codec"] == 2
    assert "offset" not in params
    assert "upgraded" not in params


def test_video_codec_enum_is_numeric():
    rows = [torrent_row("1", "[Group] Frieren - 01")]
    client, session = _client(rows)
    client.search_torrents(media_id="s462", video_codec=2)
    assert session.last["params"]["video_codec"] == 2


def test_429_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(status_code=429, text="", headers={"Retry-After": "0"})
        return FakeResponse({"error": False, "data": {"results": [], "search": {}}})

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=3, sleep=lambda _seconds: None)
    page = client.search_torrents(media_id="s462")

    assert calls["n"] == 2
    assert page.results == []
