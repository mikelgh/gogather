"""分层修复测试：代码级确定性修复 + LLM 针对性修补。"""

from app import structured_planner as sp
from app.structured_planner import (
    _apply_code_fixes,
    _calc_total_from_desc,
    _patch_plan_issues,
    _run_validators,
)


class TestCalcTotalFromDesc:
    def test_yuan_first(self):
        assert _calc_total_from_desc("双人间¥400×15间×1晚") == 6000.0

    def test_multi_group(self):
        assert _calc_total_from_desc("¥10×30人门票+¥69×30人游船") == 2370.0

    def test_yuan_last(self):
        assert _calc_total_from_desc("30人×3天×5餐×¥100") == 45000.0

    def test_lowercase_x(self):
        assert _calc_total_from_desc("¥554 x 30人") == 16620.0

    def test_no_calc_returns_none(self):
        assert _calc_total_from_desc("酒店住宿，价格面议") is None
        assert _calc_total_from_desc("") is None


class TestApplyCodeFixes:
    def test_arithmetic_rewritten(self):
        plan = {"budget": [
            {"category": "餐饮", "description": "30人×3天×5餐×¥100",
             "estimated_cost": "¥15000", "is_per_person": False},
        ], "timeline": []}
        _apply_code_fixes(plan, {"group_size": "30"})
        assert plan["budget"][0]["estimated_cost"] == "¥45000"

    def test_correct_arithmetic_untouched(self):
        plan = {"budget": [
            {"category": "住宿", "description": "¥400×15间×1晚",
             "estimated_cost": "¥6000", "is_per_person": False},
        ], "timeline": []}
        _apply_code_fixes(plan, {"group_size": "30"})
        assert plan["budget"][0]["estimated_cost"] == "¥6000"

    def test_pending_item_not_touched(self):
        plan = {"budget": [
            {"category": "住宿", "description": "需通过预订链接确认价格",
             "estimated_cost": "", "is_per_person": False},
        ], "timeline": []}
        _apply_code_fixes(plan, {"group_size": "30"})
        assert plan["budget"][0]["estimated_cost"] == ""

    def test_day_sorted_when_all_times_parseable(self):
        plan = {"timeline": [
            {"time_range": "Day 1 14:00-16:00", "title": "下午茶"},
            {"time_range": "Day 1 09:00-11:00", "title": "上午活动"},
            {"time_range": "Day 2 10:00-11:00", "title": "次日"},
        ]}
        _apply_code_fixes(plan, {"group_size": "30"})
        assert [it["title"] for it in plan["timeline"]] == ["上午活动", "下午茶", "次日"]

    def test_day_not_sorted_when_time_missing(self):
        """当天存在无法解析时间的项时保守跳过排序。"""
        plan = {"timeline": [
            {"time_range": "Day 1 14:00-16:00", "title": "下午茶"},
            {"time_range": "Day 1 上午", "title": "模糊时段"},
        ]}
        _apply_code_fixes(plan, {"group_size": "30"})
        assert [it["title"] for it in plan["timeline"]] == ["下午茶", "模糊时段"]

    def test_per_person_recomputed_after_fix(self):
        plan = {
            "timeline": [],
            "budget": [
                {"category": "餐饮", "description": "30人×3天×5餐×¥100",
                 "estimated_cost": "¥45000", "is_per_person": False},
            ],
            "budget_per_person": "约¥100",
        }
        _apply_code_fixes(plan, {"group_size": "30"})
        assert plan["budget_per_person"] == "约¥1500"


class TestPatchPlanIssues:
    def test_no_patchable_marker_returns_none(self):
        """仅地理类问题（结构性）不触发修补，留给整体重试。"""
        plan = {"timeline": [
            {"time_range": "Day 1 07:30-09:00", "title": "成都出发", "lat": 30.57, "lng": 104.07},
            {"time_range": "Day 1 10:00-17:00", "title": "九寨沟游览", "lat": 33.26, "lng": 103.92},
            {"time_range": "Day 2 20:00-23:00", "title": "九寨沟返回广州（航班）", "notes": "送机"},
        ]}
        issues = _run_validators(plan, {"origin": "广州", "group_size": "30"}, [])
        assert any("地理不可行" in i for i in issues)
        assert all("缺少返程" not in i and "未在 budget" not in i for i in issues)
        assert _patch_plan_issues(plan, issues, {"origin": "广州", "group_size": "30"}, []) is None

    def test_llm_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(sp._llm, "complete_json", lambda *a, **k: None)
        plan = {"timeline": [{"time_range": "Day 2 10:00-11:00", "title": "退房"}], "budget": []}
        assert _patch_plan_issues(plan, ["Day 2 缺少返程交通"], {"group_size": "30"}, []) is None

    def test_patch_applied_with_mock_llm(self, monkeypatch):
        captured = {}

        def fake_complete_json(system, prompt, **kwargs):
            captured["system"] = system
            captured["prompt"] = prompt
            return {
                "budget": [
                    {"category": "住宿", "description": "¥400×15间×1晚",
                     "estimated_cost": "¥6000", "is_per_person": False},
                    {"category": "门票", "description": "¥128×30人",
                     "estimated_cost": "¥3840", "is_per_person": False},
                ],
                "timeline_additions": [
                    {"time_range": "Day 3 22:20-00:10", "title": "三亚飞往广州（AQ1112）",
                     "price": "¥549/人", "notes": "送机", "booking_link": None},
                ],
            }

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)

        plan = {
            "timeline": [
                {"time_range": "Day 1 09:00-11:00", "title": "天涯海角", "price": "¥128/人"},
                {"time_range": "Day 3 10:00-11:00", "title": "酒店退房"},
                {"time_range": "Day 3 14:00-16:00", "title": "自由活动"},
            ],
            "budget": [{"category": "住宿", "description": "¥400×15间×1晚",
                        "estimated_cost": "¥6000", "is_per_person": False}],
        }
        issues = [
            "Day 3 缺少返程交通：行程最后一天必须安排从目的地返回广州的航班/车次/大巴",
            "timeline「天涯海角」价格 ¥128/人 未在 budget 中找到对应条目，违反规则17（收费项必须全部进预算）",
        ]
        patched = _patch_plan_issues(
            plan, issues, {"group_size": "30人", "origin": "广州"},
            [("三亚交通", "航班 AQ1112 价格: ¥549")],
        )
        assert patched is not None
        # budget 替换为补丁版本（含新增门票行）
        assert len(patched["budget"]) == 2
        assert patched["budget"][1]["category"] == "门票"
        # 返程项插入到 Day 3 块末尾，而非整个 timeline 末尾（Day 3 恰好是最后一块）
        titles = [it["title"] for it in patched["timeline"]]
        assert titles[-1] == "三亚飞往广州（AQ1112）"
        # 原方案不被修改
        assert len(plan["budget"]) == 1
        assert len(plan["timeline"]) == 3
        # prompt 携带问题清单与人数
        assert "缺少返程" in captured["prompt"]
        assert "30" in captured["prompt"]

    def test_insert_between_day_blocks(self, monkeypatch):
        """补丁项插入同一天末尾，不落到后续其他天之后。"""

        def fake_complete_json(system, prompt, **kwargs):
            return {
                "budget": None,
                "timeline_additions": [
                    {"time_range": "Day 1 21:00-22:00", "title": "Day 1 晚间返程"},
                ],
            }

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)
        plan = {
            "timeline": [
                {"time_range": "Day 1 09:00-11:00", "title": "活动A"},
                {"time_range": "Day 2 09:00-11:00", "title": "活动B"},
            ],
            "budget": [],
        }
        patched = _patch_plan_issues(plan, ["Day 1 缺少返程交通"], {"group_size": "30"}, [])
        titles = [it["title"] for it in patched["timeline"]]
        assert titles == ["活动A", "Day 1 晚间返程", "活动B"]

    def test_invalid_additions_skipped(self, monkeypatch):
        def fake_complete_json(system, prompt, **kwargs):
            return {
                "budget": "not-a-list",
                "timeline_additions": ["garbage", {"title": "有效项", "time_range": "Day 2 22:00-23:00"}],
            }

        monkeypatch.setattr(sp._llm, "complete_json", fake_complete_json)
        plan = {
            "timeline": [{"time_range": "Day 2 10:00-11:00", "title": "退房"}],
            "budget": [{"category": "门票", "description": "x", "estimated_cost": "¥1"}],
        }
        patched = _patch_plan_issues(plan, ["Day 2 缺少返程交通"], {"group_size": "30"}, [])
        assert patched["budget"] == plan["budget"]  # 非法 budget 保留原样
        assert [it["title"] for it in patched["timeline"]] == ["退房", "有效项"]
