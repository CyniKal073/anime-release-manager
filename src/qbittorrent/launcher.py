"""按需启动 qBittorrent。

页面一打开就去探测 qBittorrent 并不合理——用户可能几小时都不下载一次，
而没开客户端时那一下必然是失败。所以改成：自检不再碰它，
只有真的要下载时才尝试连上，连不上就把它拉起来。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .client import QBittorrentClient, QBittorrentError


def is_reachable(settings, *, timeout: float = 2.0) -> bool:
    """快速探测 WebUI 是否已经在跑（不抛异常）。"""
    try:
        client = QBittorrentClient(
            settings.qbit_url,
            username=settings.qbit_username,
            password=settings.qbit_password,
            timeout=timeout,
        )
        client.webapi_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def launch(executable: str) -> bool:
    """启动 qBittorrent（脱离当前进程，关闭终端不影响它）。"""
    path = Path(executable)
    if not executable or not path.exists():
        return False
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(path)], creationflags=flags, close_fds=True)
    else:  # pragma: no cover - 本项目主要面向 Windows
        subprocess.Popen([str(path)], start_new_session=True, close_fds=True)
    return True


def ensure_running(settings, *, wait_seconds: float = 45.0, poll: float = 2.0) -> Dict[str, Any]:
    """确保 qBittorrent WebUI 可用；必要时启动客户端并等待就绪。"""
    if is_reachable(settings):
        return {"ok": True, "launched": False}

    launched = launch(getattr(settings, "qbit_executable", ""))
    if not launched:
        return {
            "ok": False,
            "launched": False,
            "error": (
                f"qBittorrent 没有运行，且找不到可执行文件（QBIT_EXECUTABLE="
                f"{getattr(settings, 'qbit_executable', '')!r}）。请手动启动客户端。"
            ),
        }

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(poll)
        if is_reachable(settings):
            return {"ok": True, "launched": True}

    return {
        "ok": False,
        "launched": True,
        "error": (
            f"已尝试启动 qBittorrent，但 {wait_seconds:.0f} 秒内 WebUI 仍未就绪。"
            "请确认：客户端已启用 WebUI、端口与 QBIT_URL 一致。"
        ),
    }
