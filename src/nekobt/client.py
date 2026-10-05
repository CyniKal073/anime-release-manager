"""nekoBT HTTP 客户端。

设计约束（来自实测，见 docs/design-nekobt-bangumi.md 附录 A）：

* 搜索参数名是 ``query``。写成 ``q`` 服务端不会报错，只会静默忽略搜索条件，
  表现为返回最新 50 条而不是搜索结果 —— 所以这里把它固化成常量，并由测试守住。
* ``torrents/search`` 不索引中文，中文必须走 ``media/search`` 拿 media_id。
* ``per_page`` 无效，分页用 ``limit`` / ``offset``。
* ``video_codec`` 是数字枚举（1=H.264, 2=HEVC, 3=AV1）。
* ``sub_lang`` 才是字幕语言参数名。
"""

from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import requests

MEDIA_SEARCH_PATH = "/api/v1/media/search"
MEDIA_DETAIL_PATH = "/api/v1/media/{media_id}"
TORRENT_SEARCH_PATH = "/api/v1/torrents/search"
TORRENT_DETAIL_PATH = "/api/v1/torrents/{torrent_id}"
TORRENT_DOWNLOAD_PATH = "/api/v1/torrents/{torrent_id}/download"

#: 实测：参数名是 query，不是 q。
MEDIA_SEARCH_PARAM = "query"

#: 已知的允许值，避免传错值拿到 500 或 HTML 错误页。
ALLOWED_SORT_BY = ("best", "seeders")


class NekoBTError(RuntimeError):
    """nekoBT 请求失败。"""

    def __init__(self, message: str, *, status: Optional[int] = None, url: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.url = url


@dataclass
class TorrentSearchPage:
    results: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.results)


def _clean_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        else:
            cleaned[key] = value
    return cleaned


class NekoBTClient:
    def __init__(
        self,
        base_url: str = "https://nekobt.to",
        *,
        api_key: Optional[str] = None,
        session: Optional[Any] = None,
        timeout: float = 20.0,
        retries: int = 3,
        user_agent: str = "anime-release-manager/0.1",
        sleep=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.retries = max(1, retries)
        self.user_agent = user_agent
        self._sleep = sleep

    # ------------------------------------------------------------------ 内部

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.api_key:
            # 说明：非公开种子（private_magnet）才需要 Key，具体头部形式尚未实测。
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str, **kwargs: Any) -> str:
        return self.base_url + path.format(**kwargs)

    def _get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        raw: bool = False,
        path_kwargs: Optional[Mapping[str, Any]] = None,
    ):
        url = self._url(path, **(path_kwargs or {}))
        cleaned = _clean_params(params or {})
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(
                    url, params=cleaned, headers=self._headers(), timeout=self.timeout
                )
            except Exception as exc:  # noqa: BLE001 - 网络层异常统一重试
                last_error = exc
                if attempt >= self.retries:
                    break
                self._sleep(min(2 ** attempt, 8))
                continue

            status = getattr(response, "status_code", 200)

            if status == 429:
                delay = self._retry_after(response, default=min(2 ** attempt, 8))
                last_error = NekoBTError("nekoBT 触发限流 (429)", status=status, url=url)
                if attempt >= self.retries:
                    break
                self._sleep(delay)
                continue

            if 500 <= status < 600:
                last_error = NekoBTError(f"nekoBT 服务端错误 ({status})", status=status, url=url)
                if attempt >= self.retries:
                    break
                self._sleep(min(2 ** attempt, 8))
                continue

            if status >= 400:
                raise NekoBTError(
                    f"nekoBT 请求失败 ({status})：{getattr(response, 'text', '')[:200]}",
                    status=status,
                    url=url,
                )

            if raw:
                return response
            return response.json()

        raise NekoBTError(f"nekoBT 请求失败：{last_error}", url=url)

    @staticmethod
    def _retry_after(response: Any, *, default: float) -> float:
        headers = getattr(response, "headers", {}) or {}
        raw = headers.get("Retry-After") if hasattr(headers, "get") else None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _unwrap_data(payload: Any):
        if isinstance(payload, dict):
            return payload.get("data")
        return payload

    # ------------------------------------------------------------------ 媒体

    def search_media(self, query: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """媒体（作品）搜索。中文可命中，因为这里索引了 AniList 的 synonyms。"""
        payload = self._get(
            MEDIA_SEARCH_PATH,
            params={MEDIA_SEARCH_PARAM: query, "limit": limit},
        )
        data = self._unwrap_data(payload)
        if isinstance(data, dict):
            rows = data.get("results") or data.get("media") or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        return list(rows)

    def get_media(self, media_id: str) -> Dict[str, Any]:
        payload = self._get(MEDIA_DETAIL_PATH, path_kwargs={"media_id": media_id})
        return self._unwrap_data(payload) or {}

    # --------------------------------------------------------------- Torrent

    def search_torrents(
        self,
        *,
        media_id: Optional[str] = None,
        query: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: Optional[int] = None,
        sort_by: Optional[str] = None,
        sub_lang: Optional[str] = None,
        video_codec: Optional[int] = None,
        video_type: Optional[int] = None,
        batch: Optional[bool] = None,
        upgraded: Optional[bool] = None,
    ) -> TorrentSearchPage:
        """Torrent 搜索。

        主路径是 media_id；``query`` 只用于兜底，而且不索引中文。
        """
        params = {
            "media_id": media_id,
            "query": query,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sub_lang": sub_lang,
            "video_codec": video_codec,
            "video_type": video_type,
            "batch": batch,
            "upgraded": upgraded,
        }
        payload = self._get(TORRENT_SEARCH_PATH, params=params)
        data = self._unwrap_data(payload)
        if isinstance(data, dict):
            return TorrentSearchPage(
                results=list(data.get("results") or []),
                meta=dict(data.get("search") or {}),
            )
        return TorrentSearchPage(results=list(data or []))

    def get_torrent(self, torrent_id: str) -> Dict[str, Any]:
        payload = self._get(TORRENT_DETAIL_PATH, path_kwargs={"torrent_id": torrent_id})
        return self._unwrap_data(payload) or {}

    def download_torrent(self, torrent_id: str, *, public: bool = True):
        """获取 .torrent 字节内容，返回 (content, filename)。"""
        params = {"public": "true"} if public else None
        response = self._get(
            TORRENT_DOWNLOAD_PATH,
            params=params,
            raw=True,
            path_kwargs={"torrent_id": torrent_id},
        )
        content = response.content
        if not content.startswith(b"d"):
            raise NekoBTError(
                f"返回的不是合法的 .torrent 内容（可能是权限错误页）：{content[:120]!r}",
                url=getattr(response, "url", None),
            )
        return content, self._filename_from(response, torrent_id)

    @staticmethod
    def _filename_from(response: Any, torrent_id: str) -> str:
        headers = getattr(response, "headers", {}) or {}
        disposition = headers.get("Content-Disposition") if hasattr(headers, "get") else None
        if disposition:
            # 优先 RFC 5987 的 filename*=UTF-8''...（nekoBT 实际用的就是这种）
            match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.IGNORECASE)
            if match:
                name = urllib.parse.unquote(match.group(1).strip())
                if name.lower().endswith(".torrent"):
                    return name
            # 退回普通 filename="..." / filename=...
            match = re.search(r'filename\s*=\s*"([^"]*)"', disposition, re.IGNORECASE)
            if not match:
                match = re.search(r"filename\s*=\s*([^;]+)", disposition, re.IGNORECASE)
            if match:
                name = urllib.parse.unquote(match.group(1).strip().strip('"'))
                if name.lower().endswith(".torrent"):
                    return name
        return f"{torrent_id}.torrent"
