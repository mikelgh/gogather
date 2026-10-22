"""transport 地理模块测试。"""

from app.transport import (
    _haversine_km,
    estimate_distance_km,
    recommend_transport,
    _normalize,
)


class TestHaversine:
    def test_known_distance(self):
        # 北京→上海 约 1070km（直线）
        d = _haversine_km(39.90, 116.40, 31.23, 121.47)
        assert 1000 < d < 1150

    def test_zero(self):
        assert _haversine_km(30.0, 120.0, 30.0, 120.0) == 0


class TestEstimateDistance:
    def test_known_cities(self):
        d = estimate_distance_km("广州", "深圳")
        assert d is not None and 80 < d < 150

    def test_normalizes_city_suffix(self):
        assert estimate_distance_km("广州市", "深圳市") == estimate_distance_km("广州", "深圳")

    def test_unknown_city_returns_none(self):
        assert estimate_distance_km("阿凡达星球", "深圳") is None


class TestRecommendTransport:
    def test_short_distance_train(self):
        r = recommend_transport("广州", "深圳")
        assert r["suggested"] == "train"

    def test_long_distance_flight(self):
        r = recommend_transport("广州", "哈尔滨")
        assert r["suggested"] == "flight"

    def test_mid_distance_ask(self):
        r = recommend_transport("广州", "武汉")  # ~800km
        assert r["suggested"] == "ask"

    def test_unknown_city_ask(self):
        r = recommend_transport("无名城", "深圳")
        assert r["suggested"] == "ask"
        assert r["distance_km"] is None


class TestNormalize:
    def test_strips_suffix(self):
        assert _normalize("杭州市") == "杭州"
        assert _normalize("三亚") == "三亚"
