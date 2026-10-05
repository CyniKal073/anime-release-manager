"""CLI 与 Web UI 共用的业务逻辑层。

这一层只做编排：调用 nekoBT / qBittorrent / Release 解析，返回可直接序列化成
JSON 的普通字典，避免把业务逻辑写进路由或 CLI 里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings
from .nekobt.client import NekoBTClient, NekoBTError
from .nekobt.media import MediaSearchResult, search_media
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


def search_works(ctx: ServiceContext, title: str, *, top: int = 5) -> Dict[str, Any]:
    result: MediaSearchResult = search_media(ctx.nekobt, title, top=top)
    return {
        "query": result.query,
        "normalized_query": result.normalized_query,
        "used_fallback": result.used_fallback,
        "candidates": [
            {
                "media_id": item.media_id,
                "title": item.title,
                "year": item.year,
                "similarity": round(item.similarity, 4),
                "anilist_id": item.anilist_id,
            }
            for item in result.candidates
        ],
    }


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
