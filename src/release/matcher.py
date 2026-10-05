"""Release 筛选与排序（设计文档第 8、9 节）。

筛选把结果分成三桶：

* ``matched``  —— 满足全部硬条件
* ``excluded`` —— 明确不符合
* ``unknown``  —— 关键字段没解析出来，无法判定，交给用户自己看

排序是可解释的加权打分，不做黑箱推荐。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .models import Release

DEFAULT_PREFERENCES = None  # 见 default_preferences()


@dataclass
class ReleasePreferences:
    """用户偏好。列表即优先级顺序，靠前 = 更想要。"""

    resolutions: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    video_codecs: List[str] = field(default_factory=list)
    sub_langs: List[str] = field(default_factory=list)

    require_batch: Optional[bool] = None
    min_seeders: Optional[int] = None


def default_preferences() -> ReleasePreferences:
    """设计文档第 9 节的默认偏好：1080p / BD > WEB-DL / HEVC > H.264 / 简中。"""
    return ReleasePreferences(
        resolutions=["1080p", "2160p", "720p"],
        sources=["BDRip", "WEB-DL", "WEBRip"],
        video_codecs=["HEVC", "H.264", "AV1"],
        sub_langs=["zh-hans", "zh-hant", "en"],
    )


@dataclass
class FilterResult:
    matched: List[Release] = field(default_factory=list)
    excluded: List[Release] = field(default_factory=list)
    unknown: List[Release] = field(default_factory=list)

    def all_ordered(self) -> List[Release]:
        return [*self.matched, *self.unknown, *self.excluded]

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.unknown) + len(self.excluded)


def _check_enum_field(
    release: Release,
    value: Optional[str],
    allowed: Sequence[str],
) -> str:
    """返回 'pass' / 'fail' / 'unknown'。"""
    if not allowed:
        return "pass"
    if value is None:
        return "unknown"
    return "pass" if value in allowed else "fail"


def apply_filter(releases: Sequence[Release], prefs: ReleasePreferences) -> FilterResult:
    result = FilterResult()

    for release in releases:
        verdicts = [
            _check_enum_field(release, release.resolution, prefs.resolutions),
            _check_enum_field(release, release.source, prefs.sources),
            _check_enum_field(release, release.video_codec, prefs.video_codecs),
        ]

        if prefs.sub_langs:
            if not release.sub_lang:
                verdicts.append("unknown")
            elif set(release.sub_lang) & set(prefs.sub_langs):
                verdicts.append("pass")
            else:
                verdicts.append("fail")

        if prefs.require_batch is not None:
            if release.batch is None:
                verdicts.append("unknown")
            else:
                verdicts.append("pass" if release.batch == prefs.require_batch else "fail")

        if prefs.min_seeders is not None:
            if release.seeders is None:
                verdicts.append("unknown")
            else:
                verdicts.append("pass" if release.seeders >= prefs.min_seeders else "fail")

        if "fail" in verdicts:
            result.excluded.append(release)
        elif "unknown" in verdicts:
            result.unknown.append(release)
        else:
            result.matched.append(release)

    return result


@dataclass
class RankedRelease:
    release: Release
    score: float
    reasons: List[str] = field(default_factory=list)


def _preference_score(value: Optional[str], ordered: Sequence[str], weight: float) -> float:
    if not ordered or value is None:
        return 0.0
    try:
        index = list(ordered).index(value)
    except ValueError:
        return 0.0
    return weight - (weight / len(ordered)) * index


def score_release(release: Release, prefs: ReleasePreferences) -> RankedRelease:
    score = 0.0
    reasons: List[str] = []

    res_score = _preference_score(release.resolution, prefs.resolutions, 1000.0)
    if res_score:
        score += res_score
        reasons.append(f"resolution={release.resolution}")

    src_score = _preference_score(release.source, prefs.sources, 500.0)
    if src_score:
        score += src_score
        reasons.append(f"source={release.source}")

    codec_score = _preference_score(release.video_codec, prefs.video_codecs, 250.0)
    if codec_score:
        score += codec_score
        reasons.append(f"codec={release.video_codec}")

    if prefs.sub_langs:
        matched_langs = [lang for lang in prefs.sub_langs if lang in release.sub_lang]
        if matched_langs:
            score += 200.0 - 50.0 * prefs.sub_langs.index(matched_langs[0])
            reasons.append(f"sub={matched_langs[0]}")

    if prefs.require_batch is not None and release.batch == prefs.require_batch:
        score += 50.0

    if release.seeders:
        # 做种数收益递减，避免少数热门种子碾压其它维度
        score += min(math.log10(release.seeders + 1) * 40.0, 120.0)
        reasons.append(f"seeders={release.seeders}")

    if release.filesize:
        # 同档位下体积更小者略优，仅作微小 tie-break
        score -= release.filesize / 1e12

    return RankedRelease(release=release, score=round(score, 3), reasons=reasons)


def rank_releases(
    releases: Sequence[Release],
    prefs: Optional[ReleasePreferences] = None,
) -> List[RankedRelease]:
    prefs = prefs or default_preferences()
    ranked = [score_release(release, prefs) for release in releases]
    ranked.sort(
        key=lambda item: (
            item.score,
            item.release.seeders or 0,
            -(item.release.filesize or 0),
        ),
        reverse=True,
    )
    return ranked
