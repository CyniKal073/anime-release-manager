"""qBittorrent WebAPI v2 客户端（离线单元测试）。"""

from __future__ import annotations

import pytest

from src.qbittorrent.client import QBittorrentClient, QBittorrentError

from tests.fakes import FakeResponse, FakeSession


def _client(handler):
    session = FakeSession(handler)
    return (
        QBittorrentClient(
            "http://127.0.0.1:8080", username="admin", password="secret", session=session
        ),
        session,
    )


def test_login_sends_referer_and_credentials():
    def handler(method, url, **kwargs):
        return FakeResponse(text="Ok.")

    client, session = _client(handler)
    client.login()

    call = session.last
    assert call["url"].endswith("/api/v2/auth/login")
    assert call["data"] == {"username": "admin", "password": "secret"}
    assert call["headers"]["Referer"] == "http://127.0.0.1:8080"


def test_login_failure_raises_actionable_error():
    def handler(method, url, **kwargs):
        return FakeResponse(text="Fails.", status_code=403)

    client, _ = _client(handler)
    with pytest.raises(QBittorrentError) as excinfo:
        client.login()
    assert "QBIT_USERNAME" in str(excinfo.value)


def test_add_torrent_uses_multipart_and_savepath():
    def handler(method, url, **kwargs):
        return FakeResponse(text="Ok.")

    client, session = _client(handler)
    client.add_torrent(
        torrent_bytes=b"d4:infod4:name4:teste",
        torrent_filename="frieren.torrent",
        savepath=r"D:\Anime\library",
        category="anime",
        tags="nekobt",
    )

    call = session.last
    assert call["url"].endswith("/api/v2/torrents/add")
    assert call["data"]["savepath"] == r"D:\Anime\library"
    assert call["data"]["category"] == "anime"
    assert call["data"]["tags"] == "nekobt"
    assert call["data"]["paused"] == "false"
    filename, payload, content_type = call["files"]["torrents"]
    assert filename == "frieren.torrent"
    assert payload.startswith(b"d")
    assert content_type == "application/x-bittorrent"


def test_add_torrent_accepts_url_instead_of_file():
    def handler(method, url, **kwargs):
        return FakeResponse(text="Ok.")

    client, session = _client(handler)
    client.add_torrent(url="magnet:?xt=urn:btih:abc")
    assert session.last["data"]["urls"] == "magnet:?xt=urn:btih:abc"
    assert session.last["files"] is None


def test_version_endpoint_and_5_0_task_endpoints():
    def handler(method, url, **kwargs):
        return FakeResponse(text="2.11.2")

    client, session = _client(handler)
    assert client.webapi_version() == "2.11.2"
    assert session.last["url"].endswith("/api/v2/app/webapiVersion")

    client.stop(["abc"])
    assert session.last["url"].endswith("/api/v2/torrents/stop")
    assert session.last["data"]["hashes"] == "abc"

    client.start(["abc"])
    assert session.last["url"].endswith("/api/v2/torrents/start")

    client.delete(["abc"], delete_files=True)
    assert session.last["url"].endswith("/api/v2/torrents/delete")
    assert session.last["data"]["deleteFiles"] == "true"


def test_403_on_task_triggers_relogin():
    state = {"logins": 0, "adds": 0}

    def handler(method, url, **kwargs):
        if url.endswith("/api/v2/auth/login"):
            state["logins"] += 1
            return FakeResponse(text="Ok.")
        state["adds"] += 1
        if state["adds"] == 1:
            return FakeResponse(text="Forbidden", status_code=403)
        return FakeResponse(text="Ok.")

    client, _session = _client(handler)
    client.add_torrent(torrent_bytes=b"d4:infod4:name4:teste")
    assert state["logins"] == 2
    assert state["adds"] == 2
