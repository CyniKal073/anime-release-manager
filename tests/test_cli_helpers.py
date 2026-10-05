"""CLI 辅助逻辑：默认偏好与参数解析。"""

from __future__ import annotations

from src.main import build_parser, has_any_rule
from src.release.matcher import ReleasePreferences, default_preferences


def test_has_any_rule_false_for_empty_preferences():
    """没传任何偏好参数时，不应该当成"用户要求筛选"。"""
    assert has_any_rule(ReleasePreferences()) is False


def test_has_any_rule_true_when_any_dimension_set():
    assert has_any_rule(ReleasePreferences(resolutions=["1080p"])) is True
    assert has_any_rule(ReleasePreferences(sub_langs=["zh-hans"])) is True
    assert has_any_rule(ReleasePreferences(require_batch=False)) is True
    assert has_any_rule(ReleasePreferences(min_seeders=0)) is True


def test_default_preferences_match_design_doc():
    """设计文档第 9 节的默认优先级，排序必须依赖它。"""
    prefs = default_preferences()
    assert prefs.resolutions[0] == "1080p"
    assert prefs.sources[:2] == ["BDRip", "WEB-DL"]
    assert prefs.video_codecs[:2] == ["HEVC", "H.264"]
    assert prefs.sub_langs[0] == "zh-hans"


def test_parser_accepts_paused_and_no_download():
    args = build_parser().parse_args(["search", "葬送的芙莉莲", "--paused"])
    assert args.paused is True

    args = build_parser().parse_args(["search", "葬送的芙莉莲", "--no-download"])
    assert args.no_download is True
    assert args.paused is False
