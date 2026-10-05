"""最小可用的 HTTP 假会话，让所有测试都离线可跑。"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional


class FakeResponse:
    def __init__(
        self,
        json_data: Any = None,
        *,
        content: bytes = b"",
        status_code: int = 200,
        text: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        url: str = "",
    ) -> None:
        self._json = json_data
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data, ensure_ascii=False)
        else:
            self.text = content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """记录所有请求，并按 handler 返回预设响应。"""

    def __init__(self, handler: Callable[..., FakeResponse]) -> None:
        self.handler = handler
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        record = {"method": method.upper(), "url": url, **kwargs}
        self.calls.append(record)
        return self.handler(method.upper(), url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    @property
    def last(self) -> Dict[str, Any]:
        return self.calls[-1]


def media_result(
    media_id: str,
    title: str,
    similarity: float,
    year: int = 2023,
    **extra: Any,
) -> Dict[str, Any]:
    row = {
        "id": media_id,
        "title": title,
        "year": year,
        "similarity": similarity,
        "genres": ["Adventure"],
        "anilist": {"display_id": 154587, "primary": {"id": 154587, "id_mal": 52991}},
    }
    row.update(extra)
    return row


def torrent_row(
    torrent_id: str,
    title: str,
    *,
    media_id: str = "s462",
    sub_lang: str = "",
    video_codec: int = 0,
    seeders: int = 10,
    leechers: int = 1,
    filesize: int = 1024 * 1024 * 1024,
    batch: bool = False,
    upgraded: bool = False,
) -> Dict[str, Any]:
    return {
        "id": torrent_id,
        "title": title,
        "media_id": media_id,
        "infohash": f"hash{torrent_id}",
        "magnet": f"magnet:?xt=urn:btih:hash{torrent_id}",
        "sub_lang": sub_lang,
        "fsub_lang": "",
        "video_codec": video_codec,
        "video_type": 9,
        "batch": batch,
        "upgraded": upgraded,
        "seeders": str(seeders),
        "leechers": str(leechers),
        "filesize": str(filesize),
        "hardsub": False,
        "uploaded_at": 1700000000000,
    }
