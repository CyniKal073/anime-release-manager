"""Bangumi API 客户端（可选）。

实测：开发机上 bgm.tv / api.bgm.tv 均不可达，因此这里的一切调用都必须是「可失败」的，
Bangumi 不可用时主流程照常运行。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class BangumiError(RuntimeError):
    pass


class BangumiUnavailable(BangumiError):
    """网络不可达或服务不可用。"""


class BangumiClient:
    def __init__(
        self,
        base_url: str = "https://api.bgm.tv",
        *,
        user_agent: str = "anime-release-manager/0.1 (https://github.com/)",
        session: Optional[Any] = None,
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def is_available(self) -> bool:
        try:
            response = self.session.get(self.base_url, headers=self._headers(), timeout=self.timeout)
        except Exception:  # noqa: BLE001
            return False
        return getattr(response, "status_code", 0) < 500

    def search_subjects(self, keyword: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            response = self.session.post(
                f"{self.base_url}/v0/search/subjects",
                params={"limit": limit},
                json={"keyword": keyword, "filter": {"type": [2]}},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise BangumiUnavailable(f"Bangumi 不可达：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise BangumiError(f"Bangumi 搜索失败 ({response.status_code})")
        return list((response.json() or {}).get("data") or [])

    def get_subject(self, subject_id: int) -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/v0/subjects/{subject_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise BangumiUnavailable(f"Bangumi 不可达：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise BangumiError(f"Bangumi 详情失败 ({response.status_code})")
        return response.json() or {}
