"""按天分片生成（整体失败兜底路径）测试。"""
import re

from app import structured_planner as sp


class TestInferDayCount:
    def test_from_dates(self):
        assert sp._infer_day_count({"check_in": "2026-11-01", "check_out": "2026-11-04"}) == 4

    def test_fallback_2_days(self):
        assert sp._infer_day_count({}) == 2
        assert sp._infer_day_count({"check_in": "bad", "check_out": "2026-11-01"}) == 2

    def test_same_day_returns_2(self):
        assert sp._infer_day_count({"check_in": "2026-11-01", "check_out": "2026-11-01"}) == 2


class TestMergeDayBudgets:
    def test_merge_same_category_sums(self):
        rows = [
            [{"category": "餐饮", "description": "¥100×30人", "estimated_cost": "¥3000", "is_per_person": False}],
            [{"category": "餐饮", "description": "¥120×30人", "estimated_cost": "¥3600", "is_per_person": False}],
        ]
        merged = sp._merge_day_budgets(rows)
        assert len(merged) == 1
        assert merged[0]["estimated_cost"] == "¥6600"
        assert merged[0]["description"] == "¥100×30人 + ¥120×30人"
        # 合并结果应通过算术校验（+ 连接可逐组验算）
        assert sp._validate_budget_arithmetic({"budget": merged}, {"group_size": "30"}) == []

    def test_per_person_rows_dropped(self):
        rows = [[{"category": "餐饮", "description": "人均¥100", "estimated_cost": "¥100",
                  "is_per_person": True}]]
        assert sp._merge_day_budgets(rows) == []

    def test_pending_row(self):
        rows = [[{"category": "门票", "description": "需通过预订链接确认价格", "estimated_cost": ""}]]
        merged = sp._merge_day_budgets(rows)
        assert merged[0]["estimated_cost"] == "待确认"

    def test_mixed_pending_and_amounts(self):
        rows = [
            [{"category": "门票", "description": "¥45×30人", "estimated_cost": "¥1350"}],
            [{"category": "门票", "description": "需确认价格", "estimated_cost": "待确认"}],
        ]
        merged = sp._merge_day_budgets(rows)
        assert merged[0]["estimated_cost"] == "¥1350"
        assert "另有待确认项" in merged[0]["description"]


class TestMergeDayActionItems:
    def test_dedupe_and_backfill_ids(self):
        items = [
            [{"id": "hotel-1", "type": "hotel", "title": "酒店"},
             {"id": "act-1", "type": "activity", "title": "西湖"}],
            [{"id": "hotel-1", "type": "hotel", "title": "酒店"},
             {"title": "灵隐寺"}],
        ]
        merged = sp._merge_day_action_items(items)
        assert [it["title"] for it in merged] == ["酒店", "西湖", "灵隐寺"]
        assert merged[2]["id"] == "item-3"


class TestGeneratePlanByDays:
    def test_two_day_generation(self, monkeypatch):
        def fake_complete_json(system, prompt, **kwargs):
            m = re.search(r"第 (\d+)/(\d+)", prompt)
            day = int(m.group(1))
            if day == 1:
                return {
                    "timeline": [
                        {"time_range": "09:00-11:00", "title": "西湖漫步", "price": "免费"},
                        {"time_range": "Day 1 12:00-13:00", "title": "午餐", "price": "¥80/人"},
                    ],
                    "budget": [{"category": "餐饮", "description": "¥80×30人",
                                "estimated_cost": "¥2400", "is_per_person": False}],
                    "action_items": [{"id": "hotel-1", "type": "hotel", "title": "住宿：杭州酒店",
                                      "recommendations": [{"name": "杭州酒店", "price": "¥400/晚",
                                                           "booking_link": "https://x"}]}],
                }
            return {
                "timeline": [
                    {"time_range": "Day 2 09:00-11:00", "title": "灵隐寺", "price": "¥45/人"},
                    {"time_range": "Day 2 18:00-20:00", "title": "杭州返回上海（高铁G7536）", "price": "¥73/人"},
                ],
                "budget": [
                    {"category": "门票", "description": "¥45×30人",
                     "estimated_cost": "¥1350", "is_per_person": False},
                    {"category": "交通", "description": "¥73×30人",
                     "estimated_cost": "¥2190", "is_per_person": False},
                ],
                "action_items": [],
            }

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)
        params = {"city": "杭州", "origin": "上海", "group_size": "30人",
                  "check_in": "2026-11-01", "check_out": "2026-11-02"}
        result = sp._generate_plan_by_days(params, [("杭州景点", "西湖")], user_id="u", plan_id="p")
        assert result is not None
        assert result["generated_by"] == "day_slice"
        # 时间线按天顺序拼接，Day 1 缺失前缀被补上
        assert [it["time_range"] for it in result["timeline"]] == [
            "Day 1 09:00-11:00", "Day 1 12:00-13:00",
            "Day 2 09:00-11:00", "Day 2 18:00-20:00"]
        # 预算跨天合并 + 人均重算（2400+1350+2190=5940，÷30=198）
        assert [b["category"] for b in result["budget"]] == ["餐饮", "门票", "交通"]
        assert result["budget_per_person"] == "约¥198"
        # 房间分配代码计算（30人→15间双人间），并挂上酒店信息
        assert result["room_allocation"][0]["count"] == 15
        assert result["room_allocation"][0]["hotel_name"] == "杭州酒店"

    def test_partial_day_failure_still_returns_plan(self, monkeypatch):
        def fake_complete_json(system, prompt, **kwargs):
            m = re.search(r"第 (\d+)/(\d+)", prompt)
            if int(m.group(1)) == 2:
                return None
            return {"timeline": [{"time_range": f"Day {m.group(1)} 09:00-11:00", "title": "活动"}],
                    "budget": [], "action_items": []}

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)
        params = {"city": "杭州", "origin": "上海", "group_size": "30",
                  "check_in": "2026-11-01", "check_out": "2026-11-03"}
        result = sp._generate_plan_by_days(params, [], None)
        assert result is not None
        assert "Day 2 生成失败" in result["summary"]
        assert len(result["timeline"]) == 2

    def test_all_days_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr(sp._llm, "complete_json", lambda *a, **k: None)
        assert sp._generate_plan_by_days({"city": "杭州", "group_size": "30"}, [], None) is None

    def test_outline_days_drive_count(self, monkeypatch):
        captured = []

        def fake_complete_json(system, prompt, **kwargs):
            captured.append(prompt)
            m = re.search(r"第 (\d+)/(\d+)", prompt)
            return {"timeline": [{"time_range": f"Day {m.group(1)} 09:00-11:00", "title": "活动"}],
                    "budget": [], "action_items": []}

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)
        outline = {"days": [{"day": 1}, {"day": 2}, {"day": 3}]}
        result = sp._generate_plan_by_days({"city": "杭州", "group_size": "30"}, [], outline)
        assert len(captured) == 3
        assert "Day 3" in result["timeline"][2]["time_range"]
        # 最后一天的 prompt 含返程要求
        assert "返程" in captured[2]
