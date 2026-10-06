"""Bangumi 客户端的纯逻辑部分（离线）。"""

from __future__ import annotations

from src.bangumi.client import BangumiClient


def test_pick_image_prefers_common_and_upgrades_scheme():
    images = {"small": "http://lain.bgm.tv/a.jpg", "common": "http://lain.bgm.tv/b.jpg"}
    assert BangumiClient.pick_image(images) == "https://lain.bgm.tv/b.jpg"


def test_pick_image_falls_back_through_sizes():
    assert BangumiClient.pick_image({"grid": "https://x/g.jpg"}) == "https://x/g.jpg"
    assert BangumiClient.pick_image({"large": "https://x/l.jpg"}) == "https://x/l.jpg"


def test_pick_image_handles_missing():
    assert BangumiClient.pick_image(None) is None
    assert BangumiClient.pick_image({}) is None
    assert BangumiClient.pick_image({"common": ""}) is None


def test_availability_is_memoized():
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200

    class CountingSession:
        def get(self, *args, **kwargs):
            calls["n"] += 1
            return FakeResponse()

    client = BangumiClient(session=CountingSession(), availability_ttl=60)
    assert client.is_available() is True
    assert client.is_available() is True  # 命中缓存，不再探测
    assert calls["n"] == 1

    assert client.is_available(refresh=True) is True
    assert calls["n"] == 2
