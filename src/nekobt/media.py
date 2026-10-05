"""作品识别：把用户输入的中文名解析成 nekoBT 的 media_id。

链路（实测唯一可行的一条）::

    中文名 → media/search → media_id → torrents/search?media_id=...

两个必须处理的问题：

* AniList 官方 API 的 search 不覆盖 synonym，中文查不到东西，所以不能拿它当入口。
* 带季数后缀的查询（``无职转生 第二季``）会命中错误作品，必须先把后缀剥掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

#: 「第 N 季 / 第 N 部 / Season N / SN / 2nd Season」等季数后缀
SEASON_SUFFIX_PATTERNS = (
    re.compile(r"第\s*[0-9一二三四五六七八九十]+\s*[季部期]"),
    re.compile(r"\bseason\s*[0-9]+\b", re.IGNORECASE),
    re.compile(r"\b[0-9]+\s*(?:st|nd|rd|th)\s*season\b", re.IGNORECASE),
    re.compile(r"\bS[0-9]{1,2}\b"),
)


def normalize_query(raw: str) -> str:
    """剥离季数后缀与多余空白，得到适合检索的基础标题。

    注意：只剥离「季」信息，不剥离 OVA / 剧场版 / 特别篇 —— 那些是不同的作品实体。
    """
    text = (raw or "").strip()
    for pattern in SEASON_SUFFIX_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_—·、,，")
    return text or (raw or "").strip()


@dataclass
class MediaCandidate:
    media_id: str
    title: str
    year: Optional[int] = None
    similarity: float = 0.0
    anilist_id: Optional[int] = None
    mal_id: Optional[int] = None
    genres: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "MediaCandidate":
        anilist = row.get("anilist") or {}
        primary = anilist.get("primary") or {}
        anilist_id = anilist.get("display_id") or primary.get("id")
        return cls(
            media_id=str(row.get("id") or ""),
            title=row.get("title") or "",
            year=row.get("year"),
            similarity=float(row.get("similarity") or 0.0),
            anilist_id=int(anilist_id) if anilist_id else None,
            mal_id=primary.get("id_mal"),
            genres=list(row.get("genres") or []),
            raw=row,
        )

    def label(self) -> str:
        parts = [self.media_id, self.title]
        if self.year:
            parts.append(str(self.year))
        return " / ".join(parts)


def rank_candidates(rows: Iterable[Dict[str, Any]]) -> List[MediaCandidate]:
    """按 similarity 降序（并列时新番优先）排序。"""
    candidates = [MediaCandidate.from_api(row) for row in rows]
    candidates.sort(key=lambda item: (item.similarity, item.year or 0), reverse=True)
    return candidates


@dataclass
class MediaSearchResult:
    query: str
    normalized_query: str
    candidates: List[MediaCandidate] = field(default_factory=list)
    used_fallback: bool = False

    @property
    def best(self) -> Optional[MediaCandidate]:
        return self.candidates[0] if self.candidates else None

    def top(self, n: int = 5) -> List[MediaCandidate]:
        return self.candidates[:n]


def search_media(client, raw_query: str, *, top: int = 5) -> MediaSearchResult:
    """先按剥离季数后的标题检索，失败再退回原始输入。"""
    normalized = normalize_query(raw_query)
    rows = client.search_media(normalized)
    used_fallback = False

    if not rows and normalized != raw_query.strip():
        rows = client.search_media(raw_query.strip())
        used_fallback = True

    return MediaSearchResult(
        query=raw_query,
        normalized_query=normalized,
        candidates=rank_candidates(rows)[:top],
        used_fallback=used_fallback,
    )
