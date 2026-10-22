"""planner 编排层测试：并行飞猪查询（build_sections）、方案组装（plan）、编造检测。

飞猪客户端整体打桩（monkeypatch planner.flyai），不发起任何真实查询。
"""

import pytest

from app import planner
from app import transport as transport_mod


class StubFlyai:
    """可编程的假 flyai 模块：记录调用、按方法返回结构化/空/异常结果。

    各方法返回值约定：
    - dict 含 "structured"：走 format_structured 结构化渲染
    - dict 含 "text"：纯文本直出
    - Exception 实例：调用时抛出
    - None：返回空文本（触发 ai_search 兜底/告警）
    """

    def __init__(self, hotel=None, poi=None, flight=None, train=None, ai=None):
        self._results = {
            "search_hotel": hotel, "search_poi": poi,
            "search_flight": flight, "search_train": train, "ai_search": ai,
        }
        self.calls = {name: [] for name in self._results}

    def _record(self, name, args):
        self.calls[name].append(args)
        result = self._results[name]
        if isinstance(result, Exception):
            raise result
        return result

    def search_hotel(self, **opts):
        return self._record("search_hotel", opts)

    def search_poi(self, city_name=None):
        return self._record("search_poi", city_name)

    def search_flight(self, origin=None, destination=None, dep_date=None):
        return self._record("search_flight", (origin, destination, dep_date))

    def search_train(self, origin=None, destination=None, dep_date=None):
        return self._record("search_train", (origin, destination, dep_date))

    def ai_search(self, query):
        return self._record("ai_search", query)

    @staticmethod
    def extract_data(result):
        if isinstance(result, dict):
            if result.get("structured") is not None:
                return result["structured"]
            return result.get("text", "")
        return result or ""

    @staticmethod
    def format_structured(data, limit=8):
        if not isinstance(data, dict) or not data:
            return ""
        return "\n".join(f"名称：{k}\n价格：{v}" for k, v in list(data.items())[:limit])


def _structured(text_pairs: dict) -> dict:
    return {"structured": dict(text_pairs), "status": 0, "message": "ok"}


def _empty() -> dict:
    return {"structured": {}, "text": "", "status": 0, "message": "empty"}


def _inp(**over) -> dict:
    base = {
        "mode": "event", "city": "西安",
        "check_in": "2026-11-01", "check_out": "2026-11-02",
        "origin": "北京", "group_size": "20人", "vibe": "轻松",
        "budget_tier": "舒适型", "transport_mode": "auto",
    }
    base.update(over)
    return base


@pytest.fixture()
def full_stub(monkeypatch):
    """hotel/poi/交通/美食全部有数据的桩。"""
    stub = StubFlyai(
        hotel=_structured({"成都希尔顿": "¥600/晚"}),
        poi=_structured({"兵马俑": "¥120", "回民街": "免费"}),
        flight=_structured({"MU2101": "¥850"}),
        train=_structured({"G658": "¥515.5"}),
        ai=_structured({"成都火锅": "人均¥150"}),
    )
    monkeypatch.setattr(planner, "flyai", stub)
    return stub


class TestBuildSections:
    def test_locks_medium_distance_premise(self):
        """北京→西安约 900km，auto 模式应触发飞机/高铁双查对比。"""
        assert transport_mod.recommend_transport("北京", "西安")["suggested"] == "ask"

    def test_queries_all_sources_in_parallel(self, full_stub):
        sections, warnings = planner.build_sections(_inp())

        assert warnings == []
        titles = [t for t, _ in sections]
        assert "🏨 住宿（含实时房价）" in titles
        assert "🎡 玩法（含门票信息）" in titles
        assert "🍜 美食聚餐" in titles
        # 中距离 auto：去程/返程各出一组飞机+高铁对比
        assert any("✈️🚄 去程（北京→西安，飞机/高铁对比）" == t for t in titles)
        assert any("✈️🚄 返程（西安→北京，飞机/高铁对比）" == t for t in titles)

        combined = next(d for t, d in sections if "飞机/高铁对比" in t and "去程" in t)
        assert "【飞机方案】" in combined and "【高铁方案】" in combined

        # 查询参数正确：住宿带日期、景点带城市、交通按往返方向×日期
        assert full_stub.calls["search_hotel"] == [
            {"dest_name": "西安", "check_in_date": "2026-11-01", "check_out_date": "2026-11-02"}
        ]
        assert full_stub.calls["search_poi"] == ["西安"]
        assert sorted(full_stub.calls["search_flight"]) == sorted([
            ("北京", "西安", "2026-11-01"), ("西安", "北京", "2026-11-02"),
        ])
        assert sorted(full_stub.calls["search_train"]) == sorted([
            ("北京", "西安", "2026-11-01"), ("西安", "北京", "2026-11-02"),
        ])
        # 美食：从景点结果提取商圈（兵马俑/回民街）+ 全市兜底，至少 3 次检索
        food_queries = full_stub.calls["ai_search"]
        assert any("兵马俑附近" in q for q in food_queries)
        assert any("20人" in q and "人均100-200元" in q for q in food_queries)

    def test_transport_mode_flight_skips_train(self, monkeypatch):
        stub = StubFlyai(
            hotel=_structured({"酒店": "¥500"}),
            poi=_structured({"景点": "免费"}),
            flight=_structured({"MU2101": "¥850"}),
            ai=_structured({"餐厅": "人均¥100"}),
        )
        monkeypatch.setattr(planner, "flyai", stub)
        sections, warnings = planner.build_sections(_inp(transport_mode="flight"))

        assert warnings == []
        assert stub.calls["search_train"] == []
        titles = [t for t, _ in sections]
        assert "✈️ 去程（北京→西安，含实时票价）" in titles
        assert "✈️ 返程（西安→北京，含实时票价）" in titles

    def test_empty_results_fall_back_to_ai_search_then_warn(self, monkeypatch):
        stub = StubFlyai(hotel=_empty(), poi=_empty(), ai=_empty())
        monkeypatch.setattr(planner, "flyai", stub)
        sections, warnings = planner.build_sections(_inp())

        assert sections == []
        # 住宿/玩法各自兜底一次 ai_search，加上美食 1 次基础查询
        assert len(stub.calls["ai_search"]) >= 3
        joined = "\n".join(warnings)
        assert "🏨 住宿（备选）" in joined
        assert "🎡 玩法（备选）" in joined
        assert "🍜 美食聚餐" in joined
        assert "飞猪未返回数据" in joined

    def test_transport_without_dates_warns_and_skips_query(self, full_stub):
        sections, warnings = planner.build_sections(_inp(check_in="", check_out=""))

        assert full_stub.calls["search_flight"] == []
        assert full_stub.calls["search_train"] == []
        assert any("未指定出行日期" in w for w in warnings)
        titles = [t for t, _ in sections]
        assert "🏨 住宿（含实时房价）" in titles

    def test_query_exception_is_reported_not_raised(self, monkeypatch):
        stub = StubFlyai(
            hotel=RuntimeError("boom"),
            poi=_structured({"景点": "免费"}),
            ai=_structured({"餐厅": "人均¥100"}),
        )
        monkeypatch.setattr(planner, "flyai", stub)
        sections, warnings = planner.build_sections(_inp())

        assert any(w.startswith("hotel") and "查询异常" in w and "boom" in w for w in warnings)
        assert "🎡 玩法（含门票信息）" in [t for t, _ in sections]


class TestPlan:
    def test_uses_polish_result_and_appends_data_note(self, full_stub, monkeypatch):
        def fake_polish(inp, scenario, raw, mode, sections):
            assert "🏨" in raw and "兵马俑" in raw
            return f"# 润色版 {inp['city']}", [], True

        monkeypatch.setattr(planner, "_polish", fake_polish)
        result = planner.plan(_inp())

        assert result["used_llm"] is True
        assert result["markdown"].startswith("# 润色版 西安")
        assert "## 📋 数据说明" in result["markdown"]
        assert result["markdown"].rstrip().endswith("预订链接。")
        assert result["data_captured_at"]
        assert result["city"] == "西安"
        assert {s["title"] for s in result["sections"]} >= {"🏨 住宿（含实时房价）", "🎡 玩法（含门票信息）"}

    def test_falls_back_to_raw_markdown_when_polish_fails(self, full_stub, monkeypatch):
        monkeypatch.setattr(planner, "_polish", lambda *a: (None, [], False))
        result = planner.plan(_inp())

        assert result["used_llm"] is False
        assert result["markdown"].startswith("# 西安 出行方案")
        assert "## 🏨 住宿（含实时房价）" in result["markdown"]
        assert "团体出行贴士" in result["markdown"]  # event 模式附团体建议

    def test_no_sections_yields_explicit_warning(self, monkeypatch):
        stub = StubFlyai(hotel=_empty(), poi=_empty(), ai=_empty())
        monkeypatch.setattr(planner, "flyai", stub)
        monkeypatch.setattr(planner, "_polish", lambda *a: (None, [], False))
        result = planner.plan(_inp())

        assert result["sections"] == []
        assert any("全部飞猪查询均无返回" in w for w in result["warnings"])

    def test_explore_mode_omits_group_tips(self, full_stub, monkeypatch):
        monkeypatch.setattr(planner, "_polish", lambda *a: (None, [], False))
        result = planner.plan(_inp(mode="explore", origin=""))
        assert "团体出行贴士" not in result["markdown"]


class TestDetectFabrication:
    def test_flags_tokens_missing_from_raw(self):
        raw = "CA1233 ¥850 G456"
        composed = "推荐航班 CA9999 价格 ¥1200 高铁 G789 接驳"
        suspects = planner.detect_fabrication(composed, raw)
        assert "CA9999" in suspects
        assert "¥1200" in suspects
        assert "G789" in suspects

    def test_passes_tokens_present_in_raw(self):
        raw = "CA1233 ¥850"
        assert planner.detect_fabrication("航班 CA1233 只要 ¥850", raw) == []

    def test_flag_wrapper_produces_single_warning(self):
        raw = "G456"
        warnings = planner.flag_suspect_fabrication("建议乘 G789", raw)
        assert len(warnings) == 1 and "G789" in warnings[0]
        assert planner.flag_suspect_fabrication("乘 G456", raw) == []
