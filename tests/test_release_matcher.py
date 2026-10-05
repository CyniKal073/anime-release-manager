"""筛选与排序：可解释、不黑箱。"""

from __future__ import annotations

from src.release.matcher import (
    ReleasePreferences,
    apply_filter,
    default_preferences,
    rank_releases,
)
from src.release.models import Release


def make_release(
    torrent_id: str,
    *,
    resolution=None,
    source=None,
    video_codec=None,
    sub_lang=(),
    seeders=10,
    filesize=1_000_000_000,
    batch=None,
) -> Release:
    return Release(
        torrent_id=torrent_id,
        title=f"release-{torrent_id}",
        resolution=resolution,
        source=source,
        video_codec=video_codec,
        sub_lang=list(sub_lang),
        seeders=seeders,
        filesize=filesize,
        batch=batch,
    )


def test_default_ranking_matches_design_doc_example():
    """设计文档第 9 节的顺序：BD 优先于 WEB-DL，HEVC 优先于 H.264。"""
    releases = [
        make_release("web_h264", resolution="1080p", source="WEB-DL", video_codec="H.264", sub_lang=["zh-hans"]),
        make_release("bd_hevc", resolution="1080p", source="BDRip", video_codec="HEVC", sub_lang=["zh-hans"]),
        make_release("web_hevc", resolution="1080p", source="WEB-DL", video_codec="HEVC", sub_lang=["zh-hans"]),
        make_release("bd_h264", resolution="1080p", source="BDRip", video_codec="H.264", sub_lang=["zh-hans"]),
    ]
    ranked = rank_releases(releases, default_preferences())
    assert [item.release.torrent_id for item in ranked] == [
        "bd_hevc",
        "bd_h264",
        "web_hevc",
        "web_h264",
    ]


def test_filter_splits_matched_excluded_unknown():
    prefs = ReleasePreferences(resolutions=["1080p"], sources=["BDRip"])
    releases = [
        make_release("ok", resolution="1080p", source="BDRip"),
        make_release("wrong_res", resolution="2160p", source="BDRip"),
        make_release("wrong_src", resolution="1080p", source="WEB-DL"),
        make_release("unknown_src", resolution="1080p", source=None),
    ]
    result = apply_filter(releases, prefs)

    assert [item.torrent_id for item in result.matched] == ["ok"]
    assert sorted(item.torrent_id for item in result.excluded) == ["wrong_res", "wrong_src"]
    assert [item.torrent_id for item in result.unknown] == ["unknown_src"]


def test_subtitle_filter():
    prefs = ReleasePreferences(sub_langs=["zh-hans"])
    releases = [
        make_release("zh", sub_lang=["zh-hans", "en"]),
        make_release("en_only", sub_lang=["en"]),
        make_release("no_lang_info", sub_lang=[]),
    ]
    result = apply_filter(releases, prefs)
    assert [item.torrent_id for item in result.matched] == ["zh"]
    assert [item.torrent_id for item in result.excluded] == ["en_only"]
    assert [item.torrent_id for item in result.unknown] == ["no_lang_info"]


def test_min_seeders_and_batch():
    prefs = ReleasePreferences(min_seeders=5, require_batch=True)
    releases = [
        make_release("good", seeders=8, batch=True),
        make_release("few_seeds", seeders=1, batch=True),
        make_release("not_batch", seeders=9, batch=False),
        make_release("unknown_batch", seeders=9, batch=None),
    ]
    result = apply_filter(releases, prefs)
    assert [item.torrent_id for item in result.matched] == ["good"]
    assert sorted(item.torrent_id for item in result.excluded) == ["few_seeds", "not_batch"]
    assert [item.torrent_id for item in result.unknown] == ["unknown_batch"]


def test_ranking_prefers_more_seeders_when_otherwise_equal():
    releases = [
        make_release("low", resolution="1080p", source="BDRip", video_codec="HEVC", sub_lang=["zh-hans"], seeders=2),
        make_release("high", resolution="1080p", source="BDRip", video_codec="HEVC", sub_lang=["zh-hans"], seeders=90),
    ]
    ranked = rank_releases(releases, default_preferences())
    assert ranked[0].release.torrent_id == "high"
    assert ranked[0].score > ranked[1].score
