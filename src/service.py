"""CLI 与 Web UI 共用的业务逻辑层。

这一层只做编排：调用 nekoBT / qBittorrent / Release 解析，返回可直接序列化成
JSON 的普通字典，避免把业务逻辑写进路由或 CLI 里。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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
from .qbittorrent.launcher import ensure_running
from .release.matcher import (
    FilterResult,
    ReleasePreferences,
    apply_filter,
    default_preferences,
    rank_releases,
)
from .release.models import Release
from .release.parser import build_release
from .storage import JsonCache, MediaMappingRecord, Store

#: 存进 settings 表里的默认偏好
PREFS_KEY = "preferences"
#: 新番日历的缓存 key 与有效期（秒）
CALENDAR_KEY = "bangumi:calendar"
CALENDAR_TTL = 1800
#: Bangumi → nekoBT 的桥接结果、以及 Bangumi 搜索结果
BRIDGE_TTL = 7 * 24 * 3600
SEARCH_TTL = 600


@dataclass
class ServiceContext:
    settings: Settings
    nekobt: NekoBTClient
    store: Store
    _qbit: Optional[QBittorrentClient] = field(default=None, repr=False)
    _bangumi: Optional[BangumiClient] = field(default=None, repr=False)
    _cache: Optional[JsonCache] = field(default=None, repr=False)
    _bridge_cache: Optional[JsonCache] = field(default=None, repr=False)
    _search_cache: Optional[JsonCache] = field(default=None, repr=False)
    #: 自检专用探针（短超时、不重试）；测试里可注入假实现
    nekobt_probe: Optional[Any] = None
    bangumi_probe: Optional[Any] = None

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

    @property
    def cache(self) -> JsonCache:
        if self._cache is None:
            self._cache = JsonCache(self.settings.cache_dir, ttl_seconds=CALENDAR_TTL)
        return self._cache

    @property
    def bridge_cache(self) -> JsonCache:
        """「Bangumi 条目 → nekoBT media_id」的桥接结果，长期有效。"""
        if self._bridge_cache is None:
            self._bridge_cache = JsonCache(self.settings.cache_dir, ttl_seconds=BRIDGE_TTL)
        return self._bridge_cache

    @property
    def search_cache(self) -> JsonCache:
        """Bangumi 搜索结果，短缓存，避免同一关键词反复打接口。"""
        if self._search_cache is None:
            self._search_cache = JsonCache(self.settings.cache_dir, ttl_seconds=SEARCH_TTL)
        return self._search_cache


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

    cache_key = f"bridge:{name}"
    cached = ctx.bridge_cache.get(cache_key)
    if cached:
        return MediaCandidate(
            media_id=str(cached.get("media_id") or ""),
            title=cached.get("title") or name,
            year=cached.get("year"),
            similarity=float(cached.get("similarity") or 0.0),
            anilist_id=cached.get("anilist_id"),
            matched_by="bridge-cache",
        )

    try:
        rows = ctx.nekobt.search_media(name, limit=3)
    except NekoBTError:
        return None
    ranked = rank_candidates(rows)
    best = ranked[0] if ranked else None
    if best is not None and best.media_id:
        ctx.bridge_cache.set(
            cache_key,
            {
                "media_id": best.media_id,
                "title": best.title,
                "year": best.year,
                "similarity": best.similarity,
                "anilist_id": best.anilist_id,
            },
        )
    return best


def _bridge_many(ctx: ServiceContext, names: Sequence[str]) -> Dict[str, Optional[MediaCandidate]]:
    """并行桥接多个条目。

    串行时每次要等一个网络往返（实测 5 条 = 4.3s），并行后 2.3s。
    """
    unique = [name for name in dict.fromkeys(names) if name]
    if not unique:
        return {}
    workers = min(len(unique), 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda item: _bridge_to_nekobt(ctx, item), unique))
    return dict(zip(unique, results))


def _media_detail(ctx: ServiceContext, media_id: str) -> Dict[str, Any]:
    """取 nekoBT 媒体详情（用于标题与封面），带长期缓存。

    命中本地映射时如果还要为「显示标题」多打一次网络请求，缓存就白做了。
    """
    if not media_id:
        return {}
    cache_key = f"media:{media_id}"
    cached = ctx.bridge_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        detail = ctx.nekobt.get_media(media_id)
    except Exception:  # noqa: BLE001 - 取不到详情不影响主流程
        return {}
    if detail:
        ctx.bridge_cache.set(
            cache_key,
            {
                "title": detail.get("title"),
                "year": detail.get("year"),
                "banner_url": detail.get("banner_url"),
            },
        )
    return detail


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
        detail = _media_detail(ctx, cached.nekobt_media_id)
        payload["source"] = "cache"
        payload["candidates"] = [
            {
                "media_id": cached.nekobt_media_id,
                "title": detail.get("title") or title,
                "year": detail.get("year"),
                "similarity": 1.0,
                "image": detail.get("banner_url"),
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
        limit = max(top, 3)
        search_key = f"bangumi:search:{title}:{limit}"
        subjects = ctx.search_cache.get(search_key)
        if subjects is None:
            try:
                subjects = ctx.bangumi.search_subjects(title, limit=limit)
                ctx.search_cache.set(search_key, subjects)
            except BangumiError as exc:
                subjects = []
                payload["notes"].append(f"Bangumi 查询失败，已降级：{exc}")

        candidates: List[Dict[str, Any]] = []
        for rank, subject in enumerate(subjects):
            name = (subject.get("name") or "").strip()
            item = {
                "bangumi_id": subject.get("id"),
                "name_cn": subject.get("name_cn"),
                "name": name,
                "title": subject.get("name_cn") or name or title,
                "year": _year_from_date(subject.get("date")),
                "similarity": None,
                "image": BangumiClient.pick_image(subject.get("images")),
                # media_id 留到用户点选时再解析（惰性桥接）：
                # 搜索阶段只需要 Bangumi 一次请求，冷启动从 5~6s 降到 ~2.2s。
                "media_id": None,
                "needs_bridge": bool(name),
                "anilist_id": None,
                "origin": "bangumi",
                "rank": rank,
                # 相关性由 Bangumi 的排序位置体现
                "confidence": "high" if rank < 2 else "low",
                "matched_by": f"bangumi:{subject.get('id')}",
            }
            candidates.append(item)

        if candidates:
            candidates.sort(key=lambda item: item["rank"])
            payload["source"] = "bangumi"
            payload["candidates"] = candidates
            return payload
        payload["notes"].append("Bangumi 没有命中条目，已退回 nekoBT 模糊搜索")
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


def bridge_candidate(ctx: ServiceContext, name: str) -> Dict[str, Any]:
    """把一个 Bangumi 条目解析成 nekoBT media_id（用户点选时调用）。

    结果会缓存 7 天，所以同一部作品只会真正桥接一次。
    """
    if not name.strip():
        return {"ok": False, "error": "缺少条目原名，无法桥接"}
    bridged = _bridge_to_nekobt(ctx, name.strip())
    if bridged is None or not bridged.media_id:
        return {
            "ok": False,
            "media_id": None,
            "error": "nekoBT 里没找到对应媒体（可能没有收录这部作品）",
        }
    return {
        "ok": True,
        "media_id": bridged.media_id,
        "title": bridged.title,
        "year": bridged.year,
        "similarity": round(bridged.similarity, 4),
        "anilist_id": bridged.anilist_id,
        "image": bridged.image,
        "cached": bridged.matched_by == "bridge-cache",
    }


def calendar_view(ctx: ServiceContext, *, refresh: bool = False) -> Dict[str, Any]:
    """本季连载（Bangumi 每日放送），带本地缓存。"""
    if not refresh:
        cached = ctx.cache.get(CALENDAR_KEY)
        if cached:
            return {"ok": True, "source": "cache", "days": cached}

    try:
        days = ctx.bangumi.calendar()
    except Exception as exc:  # noqa: BLE001 - 可降级项，不能把首页拖挂
        return {
            "ok": False,
            "source": "bangumi",
            "days": [],
            "error": str(exc)[:300],
            "hint": "Bangumi 不可用（通常需要开启 VPN）；开启后刷新即可看到本季连载。",
        }

    ctx.cache.set(CALENDAR_KEY, days)
    return {"ok": True, "source": "bangumi", "days": days}


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

    # 只有真要下载时才碰 qBittorrent：没在跑就尝试把它拉起来
    ready = ensure_running(settings)
    if not ready.get("ok"):
        raise QBittorrentError(ready.get("error") or "qBittorrent 未就绪")
    launched = bool(ready.get("launched"))

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
        "launched_client": launched,
        "torrent_id": torrent_id,
        "filename": filename,
        "size": len(content),
        "save_path": target,
        "paused": paused,
        "torrent_hash": torrent_hash or infohash,
    }


def _probe_nekobt(ctx: ServiceContext) -> Dict[str, Any]:
    settings = ctx.settings
    probe = ctx.nekobt_probe or NekoBTClient(
        settings.nekobt_base_url,
        api_key=settings.nekobt_api_key,
        timeout=min(settings.http_timeout, 6.0),
        retries=1,
        user_agent=settings.user_agent,
    )
    rows = probe.search_media("葬送的芙莉莲", limit=1)
    return {"ok": True, "sample": (rows[0].get("title") if rows else None)}


def _probe_bangumi(ctx: ServiceContext) -> Dict[str, Any]:
    settings = ctx.settings
    probe = ctx.bangumi_probe or BangumiClient(
        user_agent=f"{settings.user_agent} (https://github.com/CyniKal073)",
        timeout=5.0,
        availability_ttl=0,
    )
    available = probe.is_available(refresh=True)
    return {
        "ok": available,
        "url": getattr(probe, "base_url", "https://api.bgm.tv"),
        "error": None if available else "无法访问 api.bgm.tv（通常需要开启 VPN）",
    }


def health(ctx: ServiceContext) -> Dict[str, Any]:
    """自检。

    设计约束：

    * **任何一项失败都不能让接口报 5xx** —— 页面一打开就会调它；
    * 每项用短超时、不重试的探针，并且 catch 到最宽；
    * 三项并发探测，否则 nekoBT 与 Bangumi 都不可达时要串行等十几秒。
    """
    probes = {
        "nekobt": _probe_nekobt,
        "bangumi": _probe_bangumi,
    }
    report: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {name: pool.submit(func, ctx) for name, func in probes.items()}
        for name, future in futures.items():
            try:
                report[name] = future.result()
            except Exception as exc:  # noqa: BLE001 - 自检不能抛出去
                report[name] = {"ok": False, "error": str(exc)[:400]}

    # 只探测「识别链路」是否可用。
    # qBittorrent 不再在打开页面时检查——它只在真正下载时才需要，
    # 到那时由 qbittorrent/launcher.ensure_running() 按需拉起。
    report["ok"] = bool(report["nekobt"].get("ok"))
    report["qbittorrent"] = {"checked": False, "hint": "仅在下载时检查，未运行会自动尝试启动"}
    return report
