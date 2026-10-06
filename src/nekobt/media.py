"""作品识别：把用户输入解析成 nekoBT 的 media_id。

链路优先级（见设计文档 4.1 / 5）::

    本地映射缓存 → Bangumi（中文强项）→ nekoBT 模糊搜索（兜底）

这个模块只负责最下面那层：nekoBT 媒体搜索的查询构造、结果合并与置信度分档。

实测（见 docs 附录 A）nekoBT 的匹配是**字符级模糊匹配**，没有分词也没有语义：

* 精确命中索引里的某个名字 → 1.0
* 部分重合 → 按字符比例给分（「葬送的芙莉莲」对索引里的「葬送的芙莉蓮」只有 0.5556）
* 完全不相干也可能拿 0.1~0.2，且没有相关性阈值，永远返回约 10 条

所以这里做三件事：

1. 生成查询变体（繁简互转、去标点），把「繁简差异」这类假阴性救回来
2. 多个变体的结果按 media_id 合并，取最高 similarity
3. 用 similarity 把候选分成高/低置信度，别让噪声和真候选混在一起
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:  # 可选依赖：没装就退化为不做繁简转换
    from zhconv import convert as _zh_convert
except ImportError:  # pragma: no cover - 取决于运行环境
    _zh_convert = None

#: 相似度分档（依据实测分布：精确命中 1.0，繁简差异约 0.5，噪声 ≤ 0.25）
HIGH_SIMILARITY = 0.5
NOISE_SIMILARITY = 0.25

#: 「第 N 季 / 第 N 部 / Season N / SN / 2nd Season」等季数后缀
SEASON_SUFFIX_PATTERNS = (
    re.compile(r"第\s*[0-9一二三四五六七八九十]+\s*[季部期]"),
    re.compile(r"\bseason\s*[0-9]+\b", re.IGNORECASE),
    re.compile(r"\b[0-9]+\s*(?:st|nd|rd|th)\s*season\b", re.IGNORECASE),
    re.compile(r"\bS[0-9]{1,2}\b"),
)

_PUNCTUATION = re.compile(r"[!！?？~～:：、,，.。\-—_·]+")


def normalize_query(raw: str) -> str:
    """剥离季数后缀与多余空白，得到适合检索的基础标题。

    注意：只剥离「季」信息，不剥离 OVA / 剧场版 / 特别篇 —— 那些是不同的作品实体。
    """
    text = (raw or "").strip()
    for pattern in SEASON_SUFFIX_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_—·、,，")
    return text or (raw or "").strip()


def to_simplified(text: str) -> str:
    """繁转简；zhconv 不可用时原样返回。"""
    if not text or _zh_convert is None:
        return text
    return _zh_convert(text, "zh-cn")


def to_traditional(text: str) -> str:
    """简转繁；zhconv 不可用时原样返回。"""
    if not text or _zh_convert is None:
        return text
    return _zh_convert(text, "zh-tw")


def query_variants(raw_query: str) -> List[str]:
    """生成去重后的查询变体（顺序即优先级）。"""
    base = normalize_query(raw_query)
    candidates = [base, to_simplified(base), to_traditional(base)]

    stripped = _PUNCTUATION.sub(" ", base).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    if stripped and stripped != base:
        candidates.append(stripped)

    seen: List[str] = []
    for item in candidates:
        item = (item or "").strip()
        if item and item not in seen:
            seen.append(item)
    return seen or [raw_query.strip()]


def classify_confidence(similarity: float) -> str:
    """把 similarity 分成 high / low 两档，低于噪声线的一律 low。"""
    return "high" if similarity >= HIGH_SIMILARITY else "low"


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

    # 以下字段由 Bangumi 桥接或本地缓存填充
    origin: str = "nekobt"
    bangumi_id: Optional[int] = None
    name_cn: Optional[str] = None
    name: Optional[str] = None
    matched_by: Optional[str] = None
    image: Optional[str] = None

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
            image=row.get("banner_url"),
            raw=row,
        )

    @property
    def confidence(self) -> str:
        return classify_confidence(self.similarity)

    def label(self) -> str:
        parts = [self.media_id, self.title]
        if self.year:
            parts.append(str(self.year))
        return " / ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "media_id": self.media_id,
            "title": self.title,
            "year": self.year,
            "similarity": round(self.similarity, 4),
            "anilist_id": self.anilist_id,
            "bangumi_id": self.bangumi_id,
            "name_cn": self.name_cn,
            "name": self.name,
            "origin": self.origin,
            "confidence": self.confidence,
            "matched_by": self.matched_by,
            "image": self.image,
        }


def rank_candidates(rows: Iterable[Dict[str, Any]]) -> List[MediaCandidate]:
    """按 similarity 降序（并列时新番优先）排序。"""
    candidates = [MediaCandidate.from_api(row) for row in rows]
    candidates.sort(key=lambda item: (item.similarity, item.year or 0), reverse=True)
    return candidates


def merge_candidates(groups: Sequence[Sequence[MediaCandidate]]) -> List[MediaCandidate]:
    """按 media_id 合并多组候选，保留 similarity 最高的一次。"""
    merged: Dict[str, MediaCandidate] = {}
    for group in groups:
        for candidate in group:
            if not candidate.media_id:
                continue
            existing = merged.get(candidate.media_id)
            if existing is None or candidate.similarity > existing.similarity:
                merged[candidate.media_id] = candidate
    result = list(merged.values())
    result.sort(key=lambda item: (item.similarity, item.year or 0), reverse=True)
    return result


@dataclass
class MediaSearchResult:
    query: str
    normalized_query: str
    candidates: List[MediaCandidate] = field(default_factory=list)
    used_fallback: bool = False
    tried_queries: List[str] = field(default_factory=list)

    @property
    def best(self) -> Optional[MediaCandidate]:
        return self.candidates[0] if self.candidates else None

    def top(self, n: int = 5) -> List[MediaCandidate]:
        return self.candidates[:n]

    @property
    def confident(self) -> List[MediaCandidate]:
        return [item for item in self.candidates if item.confidence == "high"]

    @property
    def noisy(self) -> List[MediaCandidate]:
        return [item for item in self.candidates if item.confidence == "low"]


def search_media(client, raw_query: str, *, top: int = 5) -> MediaSearchResult:
    """多查询变体检索并合并结果。

    变体是为了救回「繁简差异」造成的假阴性：输入简体、索引里存繁体时，
    单次查询只能拿到 0.5 左右的分，两个方向各查一次就能命中 1.0。
    """
    variants = query_variants(raw_query)
    groups: List[List[MediaCandidate]] = []
    used_fallback = False

    for variant in variants:
        rows = client.search_media(variant)
        if rows:
            groups.append(rank_candidates(rows))

    merged = merge_candidates(groups)
    if not merged and variants and variants[0] != raw_query.strip():
        rows = client.search_media(raw_query.strip())
        if rows:
            merged = rank_candidates(rows)
            used_fallback = True

    return MediaSearchResult(
        query=raw_query,
        normalized_query=normalize_query(raw_query),
        candidates=merged[: max(top, 1) * 3],
        used_fallback=used_fallback,
        tried_queries=variants,
    )
