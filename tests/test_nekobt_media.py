"""Test 1 / Test 2：中文作品搜索与相似作品消歧。"""

from __future__ import annotations

import pytest

from src.nekobt.client import MEDIA_SEARCH_PARAM, MEDIA_SEARCH_PATH, NekoBTClient
from src.nekobt.media import (
    MediaSearchResult,
    classify_confidence,
    normalize_query,
    query_variants,
    rank_candidates,
    search_media,
)

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

    # 季数后缀必须先剥掉；后续变体（繁简互转等）属于额外的补救尝试
    assert seen_queries[0] == "无职转生"
    assert "无职转生 第二季" not in seen_queries
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


def test_query_variants_cover_traditional_chinese():
    pytest.importorskip("zhconv")
    variants = query_variants("葬送的芙莉莲")
    assert variants[0] == "葬送的芙莉莲"
    assert "葬送的芙莉蓮" in variants


def test_query_variants_drop_punctuation():
    variants = query_variants("败犬女主太多了！")
    assert any("！" not in item for item in variants)


def test_merge_keeps_best_similarity_across_variants():
    """简体查询只拿到 0.5556、繁体变体拿到 1.0 时，合并后要保留 1.0。"""
    pytest.importorskip("zhconv")

    def handler(method, url, **kwargs):
        query = kwargs["params"]["query"]
        score = 0.5556 if query == "葬送的芙莉莲" else 1.0
        return FakeResponse(
            {"error": False, "data": {"results": [media_result("s462", "Frieren", score)]}}
        )

    session = FakeSession(handler)
    client = NekoBTClient(session=session, retries=1)
    result = search_media(client, "葬送的芙莉莲")

    assert result.best.similarity == 1.0
    assert len(result.candidates) == 1  # 同一个 media_id 不应重复出现


def test_confidence_buckets():
    assert classify_confidence(1.0) == "high"
    assert classify_confidence(0.5556) == "high"
    assert classify_confidence(0.5) == "high"
    assert classify_confidence(0.4999) == "low"
    assert classify_confidence(0.0) == "low"
