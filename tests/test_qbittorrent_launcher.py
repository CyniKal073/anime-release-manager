"""按需启动 qBittorrent 的逻辑（全部离线）。"""

from __future__ import annotations

from src.config import Settings
from src.qbittorrent import launcher


def make_settings(tmp_path, executable=""):
    return Settings(
        qbit_password="secret",
        qbit_executable=executable,
        db_path=tmp_path / "t.db",
        cache_dir=tmp_path / "c",
    )


def test_ensure_running_returns_ok_when_already_up(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "is_reachable", lambda settings, **kw: True)
    result = launcher.ensure_running(make_settings(tmp_path))
    assert result == {"ok": True, "launched": False}


def test_ensure_running_launches_and_waits_for_webui(tmp_path, monkeypatch):
    exe = tmp_path / "qbittorrent.exe"
    exe.write_bytes(b"MZ")

    state = {"reachable": False, "launched": 0, "sleeps": 0}

    def fake_reachable(settings, **kwargs):
        return state["reachable"]

    def fake_launch(path):
        state["launched"] += 1
        state["reachable"] = True  # 启动后第一次轮询就就绪
        return True

    def fake_sleep(seconds):
        state["sleeps"] += 1

    monkeypatch.setattr(launcher, "is_reachable", fake_reachable)
    monkeypatch.setattr(launcher, "launch", fake_launch)
    monkeypatch.setattr(launcher.time, "sleep", fake_sleep)

    result = launcher.ensure_running(make_settings(tmp_path, str(exe)), wait_seconds=10, poll=0.01)
    assert result == {"ok": True, "launched": True}
    assert state["launched"] == 1
    assert state["sleeps"] == 1


def test_ensure_running_reports_missing_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "is_reachable", lambda settings, **kw: False)
    result = launcher.ensure_running(make_settings(tmp_path, str(tmp_path / "nope.exe")))
    assert result["ok"] is False
    assert "找不到可执行文件" in result["error"]


def test_ensure_running_times_out_when_webui_never_comes_up(tmp_path, monkeypatch):
    exe = tmp_path / "qbittorrent.exe"
    exe.write_bytes(b"MZ")

    monkeypatch.setattr(launcher, "is_reachable", lambda settings, **kw: False)
    monkeypatch.setattr(launcher, "launch", lambda path: True)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)

    # 让 deadline 立刻过期
    ticks = iter([0.0, 100.0, 100.0])
    monkeypatch.setattr(launcher.time, "time", lambda: next(ticks, 100.0))

    result = launcher.ensure_running(make_settings(tmp_path, str(exe)), wait_seconds=1, poll=0.01)
    assert result["ok"] is False
    assert result["launched"] is True
    assert "WebUI 仍未就绪" in result["error"]


def test_is_reachable_false_on_connection_error(monkeypatch):
    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def webapi_version(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(launcher, "QBittorrentClient", BoomClient)
    assert launcher.is_reachable(make_settings_proxy()) is False


class make_settings_proxy:
    qbit_url = "http://127.0.0.1:8081"
    qbit_username = "admin"
    qbit_password = "x"
