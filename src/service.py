"""CLI 与 Web UI 共用的业务逻辑层。

这一层只做编排：调用 nekoBT / qBittorrent / Release 解析，返回可直接序列化成
JSON 的普通字典，避免把业务逻辑写进路由或 CLI 里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings
from .bangumi.client import BangumiClient, BangumiError
from .nekobt.client import NekoBTClient, NekoBTError
from .nekobt.media import (
    MediaCandidate,
    MediaSearchResult,
    classify_confidence,
    rank_candidates,
    search_media,
)
from .qbittorrent.client import QBittorrentClient, QBittorrentError
from .release.matcher import (
    FilterResult,
    ReleasePreferences,
    apply_filter,
    default_preferences,
    rank_releases,
)
from .release.models import Release
from .release.parser import build_release
from .storage import MediaMappingRecord, Store

#: 存进 settings 表里的默认偏好
PREFS_KEY = "preferences"


@dataclass
class ServiceContext:
    settings: Settings
    nekobt: NekoBTClient
    store: Store
    _qbit: Optional[QBittorrentClient] = field(default=None, repr=False)
    _bangumi: Optional[BangumiClient] = field(default=None, repr=False)

    @property
    def qbit(self) -> QBittorrentClient:
        if self._qbit is None:
            self._qbit = QBittorrentClient(
                self.settings.qbit_url,
                username=self.settings.qbit_username,
                password=self.settings.qbit_password,
                timeout=self.settings.http_timeout,
            )
        return self._qbit

    @property
    def bangumi(self) -> BangumiClient:
        if self._bangumi is None:
            self._bangumi = BangumiClient(
                user_agent=f"{self.settings.user_agent} (https://github.com/CyniKal073)",
                timeout=min(self.settings.http_timeout, 12.0),
            )
        return self._bangumi


def build_context(settings: Optional[Settings] = None) -> ServiceContext:
    settings = settings or Settings.from_env()
    return ServiceContext(
        settings=settings,
        nekobt=NekoBTClient(
            settings.nekobt_base_url,
            api_key=settings.nekobt_api_key,
            timeout=settings.http_timeout,
            retries=settings.http_retries,
            user_agent=settings.user_agent,
        ),
        store=Store(settings.db_path),
    )


def preferences_to_dict(prefs: ReleasePreferences) -> Dict[str, Any]:
    return {
        "resolutions": list(prefs.resolutions),
        "sources": list(prefs.sources),
        "video_codecs": list(prefs.video_codecs),
        "sub_langs": list(prefs.sub_langs),
        "require_batch": prefs.require_batch,
        "min_seeders": prefs.min_seeders,
    }


def preferences_from_dict(data: Optional[Dict[str, Any]]) -> ReleasePreferences:
    data = data or {}
    return ReleasePreferences(
        resolutions=list(data.get("resolutions") or []),
        sources=list(data.get("sources") or []),
        video_codecs=list(data.get("video_codecs") or []),
        sub_langs=list(data.get("sub_langs") or []),
        require_batch=data.get("require_batch"),
        min_seeders=data.get("min_seeders"),
    )


def has_rules(prefs: ReleasePreferences) -> bool:
    return bool(
        prefs.resolutions
        or prefs.sources
        or prefs.video_codecs
        or prefs.sub_langs
        or prefs.require_batch is not None
        or prefs.min_seeders is not None
    )


def load_preferences(store: Store) -> ReleasePreferences:
    """读取用户保存的偏好；没存过就返回空偏好（= 不筛选，只用默认偏好排序）。"""
    raw = store.get_setting(PREFS_KEY)
    if not raw:
        return ReleasePreferences()
    try:
        return preferences_from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return ReleasePreferences()


def save_preferences(store: Store, prefs: ReleasePreferences) -> None:
    store.set_setting(PREFS_KEY, json.dumps(preferences_to_dict(prefs), ensure_ascii=False))


def _year_from_date(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    head = str(value)[:4]
    return int(head) if head.isdigit() else None


def _bridge_to_nekobt(ctx: ServiceContext, name: str) -> Optional[MediaCandidate]:
    """用 Bangumi 的原名去 nekoBT 找对应媒体。

    实测 6/6 精确命中：Bangumi 的 ``name`` 是日文原名，而 nekoBT 的索引里有
    AniList 的 ``native`` 字段，两边是对得上的。
    """
    if not name:
        return None
    try:
        rows = ctx.nekobt.search_media(name, limit=3)
    except NekoBTError:
        return None
    ranked = rank_candidates(rows)
    return ranked[0] if ranked else None


def resolve_work(ctx: ServiceContext, title: str, *, top: int = 5) -> Dict[str, Any]:
    """作品识别：本地缓存 → Bangumi → nekoBT 模糊搜索。

    返回值里的 ``source`` 标明这次走的是哪条路，便于 UI 提示与排查。
    """
    title = (title or "").strip()
    payload: Dict[str, Any] = {
        "query": title,
        "source": None,
        "candidates": [],
        "notes": [],
    }

    # 1) 本地已确认过的映射：直接用，不再打网络
    cached = ctx.store.get_mapping(title)
    if cached and cached.confirmed:
        detail: Dict[str, Any] = {}
        try:
            detail = ctx.nekobt.get_media(cached.nekobt_media_id)
        except NekoBTError:
            detail = {}
        payload["source"] = "cache"
        payload["candidates"] = [
            {
                "media_id": cached.nekobt_media_id,
                "title": detail.get("title") or title,
                "year": detail.get("year"),
                "similarity": 1.0,
                "anilist_id": cached.anilist_id,
                "bangumi_id": cached.bgm_id,
                "name_cn": None,
                "name": None,
                "origin": "cache",
                "confidence": "high",
                "matched_by": "local-cache",
            }
        ]
        return payload

    # 2) Bangumi：中文检索的第一入口
    if ctx.bangumi.is_available():
        try:
            subjects = ctx.bangumi.search_subjects(title, limit=max(top, 3))
        except BangumiError as exc:
            subjects = []
            payload["notes"].append(f"Bangumi 查询失败，已降级：{exc}")

        candidates: List[Dict[str, Any]] = []
        for rank, subject in enumerate(subjects):
            name = (subject.get("name") or "").strip()
            bridged = _bridge_to_nekobt(ctx, name)
            item = {
                "bangumi_id": subject.get("id"),
                "name_cn": subject.get("name_cn"),
                "name": name,
                "title": subject.get("name_cn") or name or title,
                "year": _year_from_date(subject.get("date")),
                # similarity 表示「Bangumi 原名 → nekoBT 媒体」的桥接质量，
                # 不是这个条目和用户查询的相关性；相关性由 Bangumi 的排序
                # 位置（rank）体现，所以置信度按 rank 判定。
                "similarity": round(bridged.similarity, 4) if bridged else 0.0,
                "media_id": bridged.media_id if bridged else None,
                "anilist_id": bridged.anilist_id if bridged else None,
                "origin": "bangumi",
                "rank": rank,
                "confidence": "high" if (rank < 2 and bridged) else "low",
                "matched_by": f"bangumi:{subject.get('id')}",
            }
            candidates.append(item)

        if any(item["media_id"] for item in candidates):
            candidates.sort(key=lambda item: (item["media_id"] is None, item["rank"]))
            payload["source"] = "bangumi"
            payload["candidates"] = candidates
            return payload
        if candidates:
            payload["notes"].append(
                "Bangumi 命中条目，但在 nekoBT 里没找到对应媒体，已退回模糊搜索"
            )
    else:
        payload["notes"].append("Bangumi 不可用（网络不通或未启用），使用 nekoBT 模糊搜索")

    # 3) 兜底：nekoBT 模糊搜索
    result: MediaSearchResult = search_media(ctx.nekobt, title, top=top)
    payload["source"] = "nekobt"
    payload["normalized_query"] = result.normalized_query
    payload["used_fallback"] = result.used_fallback
    payload["tried_queries"] = result.tried_queries
    payload["candidates"] = [item.to_dict() for item in result.candidates]
    return payload


def confirm_mapping(
    ctx: ServiceContext,
    *,
    input_title: str,
    media_id: str,
    anilist_id: Optional[int] = None,
    bangumi_id: Optional[int] = None,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """用户确认候选后写入本地映射，之后同一输入直接命中缓存。"""
    ctx.store.save_mapping(
        MediaMappingRecord(
            input_title=input_title,
            nekobt_media_id=media_id,
            anilist_id=anilist_id,
            bgm_id=bangumi_id,
            confidence=confidence,
            confirmed=True,
        )
    )
    return {"ok": True, "input_title": input_title, "media_id": media_id}


def releases_to_dict(release: Release) -> Dict[str, Any]:
    return {
        "torrent_id": release.torrent_id,
        "title": release.title,
        "group": release.group,
        "season": release.season,
        "episode": release.episode,
        "source": release.source,
        "resolution": release.resolution,
        "video_codec": release.video_codec,
        "audio_codec": release.audio_codec,
        "sub_lang": release.sub_lang,
        "fsub_lang": release.fsub_lang,
        "batch": release.batch,
        "upgraded": release.upgraded,
        "filesize": release.filesize,
        "size_text": release.size_text,
        "seeders": release.seeders,
        "leechers": release.leechers,
        "infohash": release.infohash,
        "media_id": release.media_id,
    }


def fetch_releases(
    ctx: ServiceContext,
    media_id: str,
    *,
    query: Optional[str] = None,
    limit: int = 100,
    sub_lang: Optional[str] = None,
) -> List[Release]:
    page = ctx.nekobt.search_torrents(
        media_id=media_id,
        query=query,
        limit=limit,
        sort_by="best",
        sub_lang=sub_lang,
    )
    return [build_release(row) for row in page.results]


def list_releases(
    ctx: ServiceContext,
    media_id: str,
    *,
    prefs: Optional[ReleasePreferences] = None,
    limit: int = 100,
    sort: str = "preference",
    include_unknown: bool = True,
) -> Dict[str, Any]:
    prefs = prefs or load_preferences(ctx.store)
    releases = fetch_releases(ctx, media_id, limit=limit)

    # 没有任何偏好就不过滤，但仍按默认偏好排序
    filter_prefs = prefs if has_rules(prefs) else ReleasePreferences()
    rank_prefs = prefs if filter_prefs is prefs else default_preferences()

    filtered: FilterResult = apply_filter(releases, filter_prefs)

    def ranked(items: Sequence[Release]) -> List[Dict[str, Any]]:
        if sort == "seeders":
            ordered = sorted(items, key=lambda r: (r.seeders or 0), reverse=True)
            return [{"release": releases_to_dict(r), "score": None, "reasons": []} for r in ordered]
        if sort == "size":
            ordered = sorted(items, key=lambda r: (r.filesize or 0))
            return [{"release": releases_to_dict(r), "score": None, "reasons": []} for r in ordered]
        return [
            {
                "release": releases_to_dict(item.release),
                "score": item.score,
                "reasons": item.reasons,
            }
            for item in rank_releases(items, rank_prefs)
        ]

    return {
        "media_id": media_id,
        "total": len(releases),
        "counts": {
            "matched": len(filtered.matched),
            "unknown": len(filtered.unknown),
            "excluded": len(filtered.excluded),
        },
        "matched": ranked(filtered.matched),
        "unknown": ranked(filtered.unknown) if include_unknown else [],
        "excluded": ranked(filtered.excluded) if include_unknown else [],
    }


def submit_download(
    ctx: ServiceContext,
    torrent_id: str,
    *,
    paused: bool = False,
    infohash: Optional[str] = None,
    title: Optional[str] = None,
    save_path: Optional[str] = None,
) -> Dict[str, Any]:
    settings = ctx.settings
    settings.require_download_settings()

    content, filename = ctx.nekobt.download_torrent(torrent_id, public=True)
    target = save_path or settings.qbit_savepath

    ctx.qbit.add_torrent(
        torrent_bytes=content,
        torrent_filename=filename,
        savepath=target,
        category=settings.qbit_category,
        tags=settings.qbit_tags,
        paused=paused,
    )

    # qBittorrent 的 hash 就是 torrent 的 infohash，能对上就回填
    torrent_hash = None
    if infohash:
        try:
            for row in ctx.qbit.torrents_info():
                if (row.get("hash") or "").lower() == infohash.lower():
                    torrent_hash = row.get("hash")
                    break
        except QBittorrentError:
            torrent_hash = None

    ctx.store.record_download(
        torrent_id=torrent_id,
        torrent_hash=torrent_hash or infohash,
        title=title or filename,
        save_path=target,
        client="qbittorrent",
        status="paused" if paused else "started",
    )

    return {
        "ok": True,
        "torrent_id": torrent_id,
        "filename": filename,
        "size": len(content),
        "save_path": target,
        "paused": paused,
        "torrent_hash": torrent_hash or infohash,
    }


def health(ctx: ServiceContext) -> Dict[str, Any]:
    report: Dict[str, Any] = {"nekobt": {}, "qbittorrent": {}}

    try:
        rows = ctx.nekobt.search_media("葬送的芙莉莲", limit=1)
        report["nekobt"] = {"ok": True, "sample": (rows[0].get("title") if rows else None)}
    except NekoBTError as exc:
        report["nekobt"] = {"ok": False, "error": str(exc)}

    try:
        ctx.settings.require_download_settings()
    except Exception as exc:  # noqa: BLE001 - ConfigError
        report["qbittorrent"] = {"ok": False, "error": str(exc)}
    else:
        try:
            report["qbittorrent"] = {
                "ok": True,
                "webapi_version": ctx.qbit.webapi_version(),
                "app_version": ctx.qbit.app_version(),
                "endpoints": ctx.qbit.probe_task_endpoints(),
                "url": ctx.settings.qbit_url,
            }
        except QBittorrentError as exc:
            report["qbittorrent"] = {"ok": False, "error": str(exc)}

    report["ok"] = bool(report["nekobt"].get("ok") and report["qbittorrent"].get("ok"))
    return report
