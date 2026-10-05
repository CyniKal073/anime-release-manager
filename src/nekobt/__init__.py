"""nekoBT 客户端：媒体检索 + Release / Torrent 检索。"""

from .client import (
    MEDIA_SEARCH_PARAM,
    MEDIA_SEARCH_PATH,
    TORRENT_SEARCH_PATH,
    NekoBTClient,
    NekoBTError,
    TorrentSearchPage,
)
from .media import MediaCandidate, MediaSearchResult, normalize_query, rank_candidates

__all__ = [
    "MEDIA_SEARCH_PARAM",
    "MEDIA_SEARCH_PATH",
    "TORRENT_SEARCH_PATH",
    "MediaCandidate",
    "MediaSearchResult",
    "NekoBTClient",
    "NekoBTError",
    "TorrentSearchPage",
    "normalize_query",
    "rank_candidates",
]
