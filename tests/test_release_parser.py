"""标题解析：能可靠解析就保存，不能确定就留 None。"""

from __future__ import annotations

import pytest

from src.release.parser import build_release, parse_title

from tests.fakes import torrent_row


def test_parse_web_dl_and_episode():
    parsed = parse_title("[FrixySubs] Sousou no Frieren - S02E10 (38) [1080p CR WEB-DL H.264 AAC]")
    assert parsed.resolution == "1080p"
    assert parsed.source == "WEB-DL"
    assert parsed.video_codec == "H.264"
    assert parsed.audio_codec == "AAC"
    assert parsed.season == 2
    assert parsed.episode == "10"
    assert parsed.batch is False


def test_parse_bd_hevc():
    parsed = parse_title("[kikuri] Sousou no Frieren - 38 (BD 1080p HEVC Opus)")
    assert parsed.source == "BDRip"
    assert parsed.resolution == "1080p"
    assert parsed.video_codec == "HEVC"
    assert parsed.audio_codec == "Opus"
    assert parsed.episode == "38"


def test_bare_web_and_tv_are_not_guessed():
    """宁可 Source = None，也不要猜成 BDRip / WEB-DL。"""
    assert parse_title("[9volt] Frieren - 38 (WEB 1080p HEVC EAC-3)").source is None
    assert parse_title("[NanakoRaws] Frieren - S02E10 (AT-X TV 1080p HEVC AAC)").source is None


def test_batch_detection():
    parsed = parse_title("[NanakoRaws] Sousou no Frieren S2 01-10 (TV 1080p HEVC AAC)")
    assert parsed.batch is True
    assert parse_title("[Group] Anime - 01 [1080p]").batch is False


def test_unknown_fields_stay_none():
    parsed = parse_title("[SubsPlease] Some Random Anime - 01")
    assert parsed.source is None
    assert parsed.resolution is None
    assert parsed.video_codec is None


@pytest.mark.parametrize(
    "title,expected",
    [
        ("... [1080p] ...", "1080p"),
        ("... 1920x1080 ...", "1080p"),
        ("... 3840x2160 ...", "2160p"),
        ("... 2160p ...", "2160p"),
        ("... [720p] ...", "720p"),
    ],
)
def test_resolution_parsing(title, expected):
    assert parse_title(title).resolution == expected


def test_build_release_merges_api_and_title():
    row = torrent_row(
        "42",
        "[Group] Frieren - 01 [1080p][AV1]",
        sub_lang="zh-hans,zh-hant",
        video_codec=3,
        seeders=35,
        filesize=1954215872,
    )
    release = build_release(row)

    assert release.torrent_id == "42"
    assert release.resolution == "1080p"
    assert release.video_codec == "AV1"
    assert release.sub_lang == ["zh-hans", "zh-hant"]
    assert release.seeders == 35
    assert release.filesize == 1954215872
    assert release.size_text.endswith("GB")
    assert release.has_subtitle("zh-hans")


def test_codec_falls_back_to_api_enum():
    row = torrent_row("7", "[Group] Frieren - 02 [1080p]", video_codec=2)
    assert build_release(row).video_codec == "HEVC"
