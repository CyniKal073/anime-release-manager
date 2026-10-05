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


def test_torrent_filename_prefers_rfc5987_field():
    """nekoBT 同时返回 filename= 和 filename*=，解析时不能把整段响应头吃进去。"""
    disposition = (
        'attachment; filename="%5BGroup%5D%20Frieren%20-%2001.torrent"; '
        "filename*=UTF-8''%5BGroup%5D%20Frieren%20-%2001.torrent"
    )

    def handler(method, url, **kwargs):
        return FakeResponse(
            content=b"d4:infod4:name4:teste",
            headers={"Content-Disposition": disposition},
        )

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    content, filename = client.download_torrent("123")

    assert content.startswith(b"d")
    assert filename == "[Group] Frieren - 01.torrent"
    assert "filename*=" not in filename


def test_torrent_filename_falls_back_to_plain_filename():
    def handler(method, url, **kwargs):
        return FakeResponse(
            content=b"d4:infod4:name4:teste",
            headers={"Content-Disposition": 'attachment; filename="plain.torrent"'},
        )

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    _content, filename = client.download_torrent("123")
    assert filename == "plain.torrent"


def test_torrent_filename_defaults_when_header_missing():
    def handler(method, url, **kwargs):
        return FakeResponse(content=b"d4:infod4:name4:teste")

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    _content, filename = client.download_torrent("999")
    assert filename == "999.torrent"


def test_non_torrent_payload_is_rejected():
    """拿到 HTML 错误页时不能当成 .torrent 存下来。"""
    import pytest

    def handler(method, url, **kwargs):
        return FakeResponse(content=b"<!DOCTYPE html><html>nope</html>")

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    with pytest.raises(Exception):
        client.download_torrent("123")
