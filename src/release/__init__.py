"""Release 领域模型、标题解析与筛选排序。"""

from .matcher import (
    FilterResult,
    RankedRelease,
    ReleasePreferences,
    apply_filter,
    rank_releases,
)
from .models import (
    VIDEO_CODEC_BY_ID,
    AnimeMetadata,
    DownloadTask,
    MediaMapping,
    Release,
)
from .parser import ParsedTitle, build_release, parse_title

__all__ = [
    "VIDEO_CODEC_BY_ID",
    "AnimeMetadata",
    "DownloadTask",
    "FilterResult",
    "MediaMapping",
    "ParsedTitle",
    "RankedRelease",
    "Release",
    "ReleasePreferences",
    "apply_filter",
    "build_release",
    "parse_title",
    "rank_releases",
]
