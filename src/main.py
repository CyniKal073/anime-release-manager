"""CLI 入口：中文作品名 → Release 列表 → 用户选择 → 下载。

用法示例::

    python -m src.main search "葬送的芙莉莲"
    python -m src.main search "无职转生" --no-download
    python -m src.main doctor
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from .config import ConfigError, Settings, load_dotenv_best_effort
from .nekobt.client import NekoBTError
from .nekobt.client import NekoBTClient
from .nekobt.media import MediaCandidate, MediaSearchResult, search_media
from .qbittorrent.client import QBittorrentClient, QBittorrentError
from .release.matcher import (
    FilterResult,
    RankedRelease,
    ReleasePreferences,
    apply_filter,
    rank_releases,
)
from .release.models import Release
from .release.parser import build_release
from .storage import MediaMappingRecord, Store


def format_candidates(candidates: Sequence[MediaCandidate]) -> str:
    lines: List[str] = []
    for index, item in enumerate(candidates, start=1):
        year = f" / {item.year}" if item.year else ""
        lines.append(f"[{index}] {item.title}{year}")
        lines.append(f"    media_id={item.media_id}  similarity={item.similarity:.4f}")
    return "\n".join(lines)


def format_releases(ranked: Sequence[RankedRelease]) -> str:
    lines: List[str] = []
    for index, item in enumerate(ranked, start=1):
        release = item.release
        group = release.group or "?"
        # 标题本身通常已经带 [Group] 前缀，避免重复显示
        title = release.title
        if not title.lstrip().startswith(f"[{group}]"):
            title = f"[{group}] {title}"
        lines.append(f"[{index}] {title}")
        lines.append(
            f"    {release.summary()}"
            f"  |  {release.size_text}"
            f"  |  Seed {release.seeders if release.seeders is not None else '?'}"
            f"  |  score {item.score}"
        )
    return "\n".join(lines)


def format_filter_report(result: FilterResult) -> str:
    return (
        f"符合条件 {len(result.matched)} 条，"
        f"字段未知 {len(result.unknown)} 条，"
        f"不符合 {len(result.excluded)} 条"
    )


def _prompt_index(prompt: str, upper: int, *, allow_skip: bool = False) -> Optional[int]:
    while True:
        raw = input(prompt).strip()
        if allow_skip and raw in {"", "q", "skip"}:
            return None
        if raw.isdigit() and 1 <= int(raw) <= upper:
            return int(raw)
        print(f"请输入 1-{upper} 之间的序号" + ("，或直接回车跳过" if allow_skip else ""))


def build_preferences(args: argparse.Namespace) -> ReleasePreferences:
    prefs = ReleasePreferences()
    if args.resolution:
        prefs.resolutions = list(args.resolution)
    if args.source:
        prefs.sources = list(args.source)
    if args.codec:
        prefs.video_codecs = list(args.codec)
    if args.sub_lang:
        prefs.sub_langs = list(args.sub_lang)
    if getattr(args, "batch", False):
        prefs.require_batch = True
    if getattr(args, "min_seeders", None) is not None:
        prefs.min_seeders = args.min_seeders
    return prefs


def fetch_releases(
    client: NekoBTClient,
    media_id: str,
    *,
    query: Optional[str] = None,
    limit: int = 50,
    sub_lang: Optional[str] = None,
) -> List[Release]:
    page = client.search_torrents(
        media_id=media_id,
        query=query,
        limit=limit,
        sort_by="best",
        sub_lang=sub_lang,
    )
    return [build_release(row) for row in page.results]


def cmd_doctor(settings: Settings) -> int:
    ok = True
    print("== nekoBT ==")
    try:
        client = NekoBTClient(
            settings.nekobt_base_url,
            api_key=settings.nekobt_api_key,
            timeout=settings.http_timeout,
            retries=settings.http_retries,
            user_agent=settings.user_agent,
        )
        rows = client.search_media("葬送的芙莉莲", limit=1)
        top = rows[0].get("title") if rows else None
        print(f"  连通正常，中文检索命中：{top}")
    except NekoBTError as exc:
        ok = False
        print(f"  失败：{exc}")

    print("== qBittorrent ==")
    try:
        settings.require_download_settings()
    except ConfigError as exc:
        ok = False
        print("  未配置：" + str(exc).replace("\n", "\n  "))
    else:
        try:
            qbit = QBittorrentClient(
                settings.qbit_url,
                username=settings.qbit_username,
                password=settings.qbit_password,
                timeout=settings.http_timeout,
            )
            print(f"  WebAPI 版本：{qbit.check_connection()}")
        except QBittorrentError as exc:
            ok = False
            print(f"  失败：{exc}")

    print("== 结论 ==")
    print("  全部正常" if ok else "  存在问题，请按上面的提示处理")
    return 0 if ok else 1


def cmd_search(settings: Settings, args: argparse.Namespace) -> int:
    client = NekoBTClient(
        settings.nekobt_base_url,
        api_key=settings.nekobt_api_key,
        timeout=settings.http_timeout,
        retries=settings.http_retries,
        user_agent=settings.user_agent,
    )

    result = search_media(client, args.title, top=args.top)
    if not result.candidates:
        print(f"没有找到「{args.title}」对应的作品。")
        return 2

    print(f"用户输入：{result.query}")
    if result.normalized_query != result.query:
        print(f"检索用标题：{result.normalized_query}（已剥离季数后缀）")
    print()
    print(format_candidates(result.candidates))
    print()

    choice = _prompt_index("请选择作品序号（回车跳过，q 退出）：", len(result.candidates), allow_skip=True)
    if choice is None:
        print("已取消。")
        return 0
    candidate = result.candidates[choice - 1]

    store = Store(settings.db_path)
    store.save_mapping(
        MediaMappingRecord(
            input_title=args.title,
            nekobt_media_id=candidate.media_id,
            anilist_id=candidate.anilist_id,
            confidence=candidate.similarity,
            confirmed=True,
        )
    )

    releases = fetch_releases(
        client,
        candidate.media_id,
        limit=args.limit,
        sub_lang=args.sub_lang_filter,
    )
    if not releases:
        print("该作品下没有检索到 Release。")
        return 2

    prefs = build_preferences(args)
    filtered = apply_filter(releases, prefs)
    if filtered.matched:
        pool = rank_releases(filtered.matched, prefs)
        pool_label = "符合条件"
    elif filtered.unknown:
        pool = rank_releases(filtered.unknown, prefs)
        pool_label = "没有完全符合条件的 Release；下面是「字段无法判定」的结果，供参考"
    elif filtered.excluded:
        pool = rank_releases(filtered.excluded, prefs)
        pool_label = "没有符合条件的 Release；下面是全部被排除的结果，仅供参考"
    else:
        pool = []
        pool_label = ""

    print()
    print(format_filter_report(filtered))
    if not pool:
        print("没有可展示的 Release。")
        return 2
    print()
    print(f"{pool_label}（按偏好排序，展示前 {min(len(pool), args.show)} 条）：")
    print(format_releases(pool[: args.show]))

    if args.no_download:
        return 0

    print()
    choice = _prompt_index("请选择 Release 序号（回车跳过）：", min(len(pool), args.show), allow_skip=True)
    if choice is None:
        print("已取消。")
        return 0
    selected = pool[choice - 1].release

    return download_release(settings, client, selected)


def download_release(settings: Settings, client: NekoBTClient, release: Release) -> int:
    print()
    print(f"准备下载：{release.title}")

    try:
        content, filename = client.download_torrent(release.torrent_id, public=True)
    except NekoBTError as exc:
        print(f"获取 .torrent 失败：{exc}")
        return 1
    print(f"已获取 .torrent：{filename}（{len(content)} 字节）")

    try:
        settings.require_download_settings()
    except ConfigError as exc:
        print(f"无法提交下载：{exc}")
        return 1

    qbit = QBittorrentClient(
        settings.qbit_url,
        username=settings.qbit_username,
        password=settings.qbit_password,
        timeout=settings.http_timeout,
    )
    try:
        version = qbit.check_connection()
        qbit.add_torrent(
            torrent_bytes=content,
            torrent_filename=filename,
            savepath=settings.qbit_savepath,
            category=settings.qbit_category,
            tags=settings.qbit_tags,
        )
    except QBittorrentError as exc:
        print(f"提交到 qBittorrent 失败：{exc}")
        return 1

    Store(settings.db_path).record_download(
        torrent_id=release.torrent_id,
        torrent_hash=release.infohash,
        title=release.title,
        save_path=settings.qbit_savepath,
        client="qbittorrent",
        status="started",
    )
    print(f"已提交到 qBittorrent（WebAPI {version}），保存目录：{settings.qbit_savepath}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anime-release-manager")
    sub = parser.add_subparsers(dest="command")

    search = sub.add_parser("search", help="按中文作品名检索 Release")
    search.add_argument("title")
    search.add_argument("--top", type=int, default=5, help="展示的作品候选数量")
    search.add_argument("--show", type=int, default=10, help="展示的 Release 数量")
    search.add_argument("--limit", type=int, default=50, help="向 nekoBT 请求的 Release 数量")
    search.add_argument("--resolution", action="append", default=[], help="分辨率偏好，可重复")
    search.add_argument("--source", action="append", default=[], help="来源偏好，可重复")
    search.add_argument("--codec", action="append", default=[], help="视频编码偏好，可重复")
    search.add_argument("--sub-lang", action="append", default=[], help="字幕语言偏好，可重复")
    search.add_argument("--sub-lang-filter", default=None, help="直接用 sub_lang 参数过滤服务端结果")
    search.add_argument("--batch", action="store_true", help="只看合集")
    search.add_argument("--min-seeders", type=int, default=None)
    search.add_argument("--no-download", action="store_true", help="只检索不下载")
    search.add_argument("--json", action="store_true", help="输出 JSON（暂未实现交互）")

    sub.add_parser("doctor", help="自检 nekoBT 与 qBittorrent 连通性")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv_best_effort()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    settings = Settings.from_env()
    if args.command == "doctor":
        return cmd_doctor(settings)
    if args.command == "search":
        return cmd_search(settings, args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
