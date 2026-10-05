"""Test 1 / Test 2：中文作品搜索与相似作品消歧。"""

from __future__ import annotations

import pytest

from src.nekobt.client import MEDIA_SEARCH_PARAM, MEDIA_SEARCH_PATH, NekoBTClient
from src.nekobt.media import MediaSearchResult, normalize_query, rank_candidates, search_media

from tests.fakes import FakeResponse, FakeSession, media_result


def test_test1_chinese_search_hits_frieren():
    """Test 1：输入「葬送的芙莉莲」，首位必须是 s462。"""

    def handler(method, url, **kwargs):
        assert MEDIA_SEARCH_PATH in url
        return FakeResponse(
            {
                "error": False,
                "data": {
                    "results": [
                        media_result("s462", "Frieren: Beyond Journey's End", 0.5556),
                        media_result("m641", "JoJo no Kimyou na Bouken: Phantom Blood", 0.0, year=2007),
                        media_result("m1130", "MOBILE SUIT GUNDAM HATHAWAY", 0.0, year=2026),
                    ]
                },
            }
        )

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    result = search_media(client, "葬送的芙莉莲")

    assert isinstance(result, MediaSearchResult)
    assert result.best is not None
    assert result.best.media_id == "s462"
    assert result.best.anilist_id == 154587


def test_test7_search_param_is_query_not_q():
    """Test 7：参数名必须是 query。写成 q 不会报错，只会静默失效。"""

    def handler(method, url, **kwargs):
        return FakeResponse({"error": False, "data": {"results": []}})

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    client.search_media("葬送的芙莉莲")

    params = session.last["params"]
    assert MEDIA_SEARCH_PARAM == "query"
    assert params["query"] == "葬送的芙莉莲"
    assert "q" not in params


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("无职转生 第二季", "无职转生"),
        ("无职转生第二季", "无职转生"),
        ("葬送的芙莉莲 第2季", "葬送的芙莉莲"),
        ("无职转生 S2", "无职转生"),
        ("Mushoku Tensei Season 2", "Mushoku Tensei"),
        ("Mushoku Tensei 2nd Season", "Mushoku Tensei"),
        ("葬送的芙莉莲", "葬送的芙莉莲"),
    ],
)
def test_test2_season_suffix_is_stripped(raw, expected):
    assert normalize_query(raw) == expected


def test_test2_does_not_strip_movie_or_ova():
    """OVA / 剧场版是不同的作品实体，不能当季数剥掉。"""
    assert normalize_query("葬送的芙莉莲 特别篇") == "葬送的芙莉莲 特别篇"
    assert normalize_query("剧场版 某作品") == "剧场版 某作品"


def test_test2_search_uses_stripped_title():
    """带季数后缀的整串会命中错误作品，必须先剥离再检索。"""
    seen_queries = []

    def handler(method, url, **kwargs):
        query = kwargs["params"]["query"]
        seen_queries.append(query)
        if query == "无职转生":
            results = [media_result("s153", "Mushoku Tensei: Jobless Reincarnation", 0.9, year=2021)]
        else:
            results = [media_result("s7953", "Biaoren", 0.3333)]
        return FakeResponse({"error": False, "data": {"results": results}})

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    result = search_media(client, "无职转生 第二季")

    assert seen_queries == ["无职转生"]
    assert result.best.media_id == "s153"
    assert result.normalized_query == "无职转生"


def test_rank_candidates_sorts_by_similarity_then_year():
    rows = [
        media_result("a", "noise", 0.0, year=2024),
        media_result("b", "hit", 0.5, year=2020),
        media_result("c", "also noise", 0.0, year=2019),
    ]
    ranked = rank_candidates(rows)
    assert [item.media_id for item in ranked] == ["b", "a", "c"]
