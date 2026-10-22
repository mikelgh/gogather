"""后生成校验器测试：返程/预算完整性/算术/时间/地理。"""

from app.structured_planner import (
    _validate_budget_arithmetic,
    _validate_budget_completeness,
    _validate_geographic_feasibility,
    _validate_return_transport,
    _validate_time_consistency,
    _run_validators,
    _money,
)


class TestReturnTransport:
    def test_missing_return_detected(self):
        plan = {"timeline": [
            {"time_range": "Day 1 14:00-15:00", "title": "三亚凤凰机场抵达", "notes": "接机"},
            {"time_range": "Day 3 10:00-11:00", "title": "酒店退房", "notes": ""},
        ]}
        issues = _validate_return_transport(plan, {"origin": "广州"})
        assert len(issues) == 1
        assert "Day 3" in issues[0]
        assert "返程" in issues[0]

    def test_return_flight_passes(self):
        plan = {"timeline": [
            {"time_range": "Day 1 14:00-15:00", "title": "抵达"},
            {"time_range": "Day 3 22:20-00:10", "title": "三亚飞往广州（AQ1112）", "notes": "送机"},
        ]}
        assert _validate_return_transport(plan, {"origin": "广州"}) == []

    def test_return_train_passes(self):
        plan = {"timeline": [
            {"time_range": "Day 2 22:22-23:14", "title": "杭州至上海（高铁G7536）", "notes": ""},
        ]}
        assert _validate_return_transport(plan, {"origin": "上海"}) == []

    def test_transport_on_last_day_but_wrong_direction(self):
        """最后一天有交通工具但不是返回 origin → 应报警。"""
        plan = {"timeline": [
            {"time_range": "Day 2 09:00-10:00", "title": "机场大巴去景区", "notes": ""},
        ]}
        issues = _validate_return_transport(plan, {"origin": "广州"})
        assert len(issues) == 1

    def test_placeholder_return_passes(self):
        """标注"需自行预订返程交通"视为有返程交代。"""
        plan = {"timeline": [
            {"time_range": "Day 2 10:00-11:00", "title": "需自行预订返程交通", "notes": "飞猪未返回返程数据"},
        ]}
        assert _validate_return_transport(plan, {"origin": "广州"}) == []

    def test_empty_timeline_no_issue(self):
        assert _validate_return_transport({"timeline": []}, {"origin": "广州"}) == []


class TestBudgetCompleteness:
    def test_unit_price_times_group_matches_total(self):
        """timeline 单价 × 人数 = budget 总价 → 不报。"""
        plan = {
            "timeline": [{"title": "广州飞往三亚（AQ1113）", "price": "¥549.00/人"}],
            "budget": [{"category": "去程交通", "description": "AQ1113 ×30人", "estimated_cost": "¥16470"}],
        }
        assert _validate_budget_completeness(plan, {"group_size": "30人"}) == []

    def test_total_divided_by_group_matches_unit(self):
        """budget 总价 ÷ 人数 ≈ timeline 单价（±5%）→ 不报。"""
        plan = {
            "timeline": [{"title": "门票", "price": "¥120/人"}],
            "budget": [{"category": "门票", "description": "兵马俑门票", "estimated_cost": "¥3700"}],
        }
        assert _validate_budget_completeness(plan, {"group_size": "30人"}) == []

    def test_unit_price_in_budget_text(self):
        plan = {
            "timeline": [{"title": "酒店", "price": "¥418/间/晚"}],
            "budget": [{"category": "住宿", "description": "¥418×15间×2晚", "estimated_cost": "¥12540"}],
        }
        assert _validate_budget_completeness(plan, {"group_size": "30人"}) == []

    def test_missing_budget_item_detected(self):
        plan = {
            "timeline": [{"title": "蜀风雅韵川剧", "price": "¥128/人"}],
            "budget": [{"category": "住宿", "description": "¥400×15间", "estimated_cost": "¥6000"}],
        }
        issues = _validate_budget_completeness(plan, {"group_size": "30人"})
        assert len(issues) == 1
        assert "蜀风雅韵" in issues[0]

    def test_free_and_placeholder_skipped(self):
        plan = {
            "timeline": [
                {"title": "西湖漫步", "price": "免费"},
                {"title": "灵隐寺", "price": "点击预订链接查看实时价格"},
                {"title": "酒店", "price": "待确认"},
            ],
            "budget": [],
        }
        assert _validate_budget_completeness(plan, {"group_size": "30人"}) == []


class TestBudgetArithmetic:
    def test_wrong_multiplication_detected(self):
        plan = {"budget": [
            {"category": "餐饮", "description": "30人×3天×5餐×¥100", "estimated_cost": "¥15000", "is_per_person": False},
        ]}
        issues = _validate_budget_arithmetic(plan, {"group_size": "30"})
        assert len(issues) == 1
        assert "45000" in issues[0]

    def test_correct_calculations_pass(self):
        plan = {"budget": [
            {"category": "住宿", "description": "双人间¥400×15间×1晚", "estimated_cost": "¥6000", "is_per_person": False},
            {"category": "餐饮", "description": "30人×3天×5餐×¥100", "estimated_cost": "¥45000", "is_per_person": False},
            {"category": "门票", "description": "¥10×30人门票+¥69×30人游船", "estimated_cost": "¥2370", "is_per_person": False},
        ]}
        assert _validate_budget_arithmetic(plan, {"group_size": "30"}) == []

    def test_pending_item_skipped(self):
        plan = {"budget": [
            {"category": "住宿", "description": "需通过预订链接确认价格", "estimated_cost": "", "is_per_person": False},
        ]}
        assert _validate_budget_arithmetic(plan, {"group_size": "30"}) == []

    def test_per_person_row_skipped(self):
        plan = {"budget": [
            {"category": "餐饮", "description": "人均¥100", "estimated_cost": "¥100", "is_per_person": True},
        ]}
        assert _validate_budget_arithmetic(plan, {"group_size": "30"}) == []


class TestTimeConsistency:
    def test_opening_time_contradiction(self):
        plan = {"timeline": [
            {"time_range": "Day 1 15:30-17:00", "title": "熊猫基地", "notes": "7:30开园即入"},
        ]}
        issues = _validate_time_consistency(plan)
        assert len(issues) == 1
        assert "熊猫基地" in issues[0]

    def test_day_time_ordering(self):
        """同日内时间倒序 → 报警。"""
        plan = {"timeline": [
            {"time_range": "Day 1 14:00-16:00", "title": "下午活动"},
            {"time_range": "Day 1 09:00-11:00", "title": "上午活动"},
        ]}
        issues = _validate_time_consistency(plan)
        assert any("时间顺序异常" in i for i in issues)

    def test_normal_day_passes(self):
        plan = {"timeline": [
            {"time_range": "Day 1 09:00-11:00", "title": "上午活动"},
            {"time_range": "Day 1 14:00-16:00", "title": "下午活动"},
            {"time_range": "Day 2 09:00-11:00", "title": "次日上午"},
        ]}
        assert _validate_time_consistency(plan) == []


class TestGeographicFeasibility:
    def test_chengdu_jiuzhaigou_same_day(self):
        plan = {"timeline": [
            {"time_range": "Day 4 07:30-09:00", "title": "成都出发", "lat": 30.57, "lng": 104.07},
            {"time_range": "Day 4 10:00-17:00", "title": "九寨沟游览", "lat": 33.26, "lng": 103.92},
        ]}
        issues = _validate_geographic_feasibility(plan)
        assert len(issues) == 1
        assert "299" in issues[0] or "298" in issues[0]

    def test_same_city_activities_pass(self):
        plan = {"timeline": [
            {"time_range": "Day 1 09:00-11:00", "title": "西湖", "lat": 30.24, "lng": 120.15},
            {"time_range": "Day 1 14:00-16:00", "title": "灵隐寺", "lat": 30.24, "lng": 120.10},
        ]}
        assert _validate_geographic_feasibility(plan) == []

    def test_far_apart_different_days_pass(self):
        plan = {"timeline": [
            {"time_range": "Day 1 09:00-11:00", "title": "成都", "lat": 30.57, "lng": 104.07},
            {"time_range": "Day 2 09:00-11:00", "title": "九寨沟", "lat": 33.26, "lng": 103.92},
        ]}
        assert _validate_geographic_feasibility(plan) == []

    def test_items_without_coords_skipped(self):
        plan = {"timeline": [
            {"time_range": "Day 1 09:00-11:00", "title": "无坐标A"},
            {"time_range": "Day 1 14:00-16:00", "title": "无坐标B"},
        ]}
        assert _validate_geographic_feasibility(plan) == []


class TestRunValidators:
    def test_bad_plan_reports_all_categories(self):
        plan = {
            "timeline": [
                {"time_range": "Day 1 15:30-17:00", "title": "熊猫基地", "notes": "7:30开园即入", "price": "¥55/人",
                 "lat": 30.73, "lng": 104.15},
                {"time_range": "Day 2 10:00-11:00", "title": "酒店退房"},
            ],
            "budget": [
                {"category": "住宿", "description": "¥400×25间×5晚", "estimated_cost": "¥50000", "is_per_person": False},
            ],
        }
        issues = _run_validators(plan, {"origin": "广州", "group_size": "50"}, [])
        kinds = "".join(issues)
        assert "返程" in kinds
        assert "熊猫基地" in kinds          # 预算漏项（¥55 未进预算）
        assert "时间矛盾" in kinds or "开园" in kinds

    def test_good_plan_no_issues(self):
        plan = {
            "timeline": [
                {"time_range": "Day 1 09:00-12:00", "title": "西湖", "price": "免费", "lat": 30.24, "lng": 120.15},
                {"time_range": "Day 1 14:00-17:00", "title": "灵隐寺", "price": "¥45/人"},
                {"time_range": "Day 2 09:00-10:00", "title": "杭州至上海（高铁G7536）"},
            ],
            "budget": [{"category": "门票", "description": "¥45×30人", "estimated_cost": "¥1350", "is_per_person": False}],
        }
        assert _run_validators(plan, {"origin": "上海", "group_size": "30"}, []) == []


class TestMoney:
    def test_simple(self):
        assert _money("¥6000") == 6000

    def test_expression_takes_last(self):
        assert _money("¥321×30 = ¥9630") == 9630

    def test_commas(self):
        assert _money("¥12,340") == 12340

    def test_none(self):
        assert _money("待确认") is None
        assert _money("") is None
        assert _money(None) is None
