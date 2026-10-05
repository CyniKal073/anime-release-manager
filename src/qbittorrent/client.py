"""qBittorrent WebUI API（WebAPI v2）客户端。

版本注意：5.0 起暂停/恢复的接口名是 ``stop`` / ``start``（旧的 ``pause`` / ``resume`` 已更名）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests


class QBittorrentError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class QBittorrentClient:
    def __init__(
        self,
        url: str = "http://127.0.0.1:8080",
        *,
        username: str = "admin",
        password: str = "",
        session: Optional[Any] = None,
        timeout: float = 20.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self._logged_in = False

    # ------------------------------------------------------------------ 鉴权

    def _base_headers(self) -> Dict[str, str]:
        # 登录需要 Referer（CSRF 保护），否则可能被拒。
        return {"Referer": self.url, "Origin": self.url}

    def login(self) -> None:
        response = self.session.post(
            f"{self.url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers=self._base_headers(),
            timeout=self.timeout,
        )
        status = getattr(response, "status_code", 200)
        body = (getattr(response, "text", "") or "").strip()
        if status == 403 or body == "Fails.":
            raise QBittorrentError(
                "qBittorrent 登录失败：用户名或密码错误。"
                "请检查 .env 中的 QBIT_USERNAME / QBIT_PASSWORD。",
                status=status,
            )
        if status >= 400:
            raise QBittorrentError(f"qBittorrent 登录请求失败 ({status})", status=status)
        self._logged_in = True

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, **kwargs: Any):
        self._ensure_login()
        headers = {**self._base_headers(), **kwargs.pop("headers", {})}
        response = self.session.request(
            method, f"{self.url}{path}", headers=headers, timeout=self.timeout, **kwargs
        )
        status = getattr(response, "status_code", 200)
        if status == 403:
            # SID 过期或被 CSRF 拒绝，重新登录一次
            self._logged_in = False
            self.login()
            headers = {**self._base_headers(), **kwargs.pop("headers", {})}
            response = self.session.request(
                method, f"{self.url}{path}", headers=headers, timeout=self.timeout, **kwargs
            )
            status = getattr(response, "status_code", 200)
        if status >= 400:
            raise QBittorrentError(
                f"qBittorrent 请求失败 ({status})：{getattr(response, 'text', '')[:200]}",
                status=status,
            )
        return response

    # ------------------------------------------------------------------ 自检

    def webapi_version(self) -> str:
        return self._request("GET", "/api/v2/app/webapiVersion").text.strip()

    def app_version(self) -> str:
        return self._request("GET", "/api/v2/app/version").text.strip()

    def check_connection(self) -> str:
        """启动自检：WebUI 未启用 / 端口错误 / 账号密码错误都要在这里就暴露。"""
        return self.webapi_version()

    # ------------------------------------------------------------------ 任务

    def add_torrent(
        self,
        *,
        torrent_bytes: Optional[bytes] = None,
        torrent_filename: str = "release.torrent",
        url: Optional[str] = None,
        savepath: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        paused: bool = False,
    ) -> None:
        if not torrent_bytes and not url:
            raise ValueError("必须提供 torrent_bytes 或 url 之一")

        data: Dict[str, Any] = {}
        if url:
            data["urls"] = url
        if savepath:
            data["savepath"] = savepath
        if category:
            data["category"] = category
        if tags:
            data["tags"] = tags
        data["paused"] = "true" if paused else "false"

        files = None
        if torrent_bytes:
            files = {
                "torrents": (
                    torrent_filename,
                    torrent_bytes,
                    "application/x-bittorrent",
                )
            }

        self._request("POST", "/api/v2/torrents/add", data=data, files=files)

    def torrents_info(
        self,
        *,
        hashes: Optional[Iterable[str]] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if hashes:
            params["hashes"] = "|".join(hashes)
        if category:
            params["category"] = category
        response = self._request("GET", "/api/v2/torrents/info", params=params)
        return response.json()

    def _hashes_value(self, hashes: Sequence[str]) -> str:
        return "|".join(hashes) if hashes else "all"

    def stop(self, hashes: Sequence[str]) -> None:
        self._request("POST", "/api/v2/torrents/stop", data={"hashes": self._hashes_value(hashes)})

    def start(self, hashes: Sequence[str]) -> None:
        self._request("POST", "/api/v2/torrents/start", data={"hashes": self._hashes_value(hashes)})

    def delete(self, hashes: Sequence[str], *, delete_files: bool = False) -> None:
        self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": self._hashes_value(hashes), "deleteFiles": "true" if delete_files else "false"},
        )
