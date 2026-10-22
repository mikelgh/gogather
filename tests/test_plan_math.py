"""方案数值逻辑测试：人均重算、天数推导、房间分配、链接清洗。"""

from app.structured_planner import (
    _recompute_budget_per_person,
    _compute_days_nights,
    calculate_room_allocation,
    _validate_booking_links,
)


class TestRecomputeBudgetPerPerson:
    def test_recomputes_when_diff_large(self):
        plan = {
            "budget": [
                {"category": "住宿", "estimated_cost": "¥50000", "is_per_person": False},
                {"category": "交通", "estimated_cost": "¥20000", "is_per_person": False},
            ],
            "budget_per_person": "约¥100",
        }
        _recompute_budget_per_person(plan, {"group_size": "50"})
        assert plan["budget_per_person"] == "约¥1400"

    def test_keeps_when_close(self):
        plan = {
            "budget": [{"category": "门票", "estimated_cost": "¥3000", "is_per_person": False}],
            "budget_per_person": "约¥100",
        }
        _recompute_budget_per_person(plan, {"group_size": "30"})
        assert plan["budget_per_person"] == "约¥100"  # 100 vs 100，差 < 20 保留

    def test_skips_per_person_rows(self):
        plan = {
            "budget": [
                {"category": "门票", "estimated_cost": "¥3000", "is_per_person": False},
                {"category": "人均参考", "estimated_cost": "¥99999", "is_per_person": True},
            ],
            "budget_per_person": "",
        }
        _recompute_budget_per_person(plan, {"group_size": "30"})
        assert plan["budget_per_person"] == "约¥100"

    def test_empty_budget_untouched(self):
        plan = {"budget": [], "budget_per_person": "约¥1200"}
        _recompute_budget_per_person(plan, {"group_size": "30"})
        assert plan["budget_per_person"] == "约¥1200"

    def test_bad_group_size_untouched(self):
        plan = {"budget": [{"estimated_cost": "¥3000", "is_per_person": False}], "budget_per_person": "约¥1"}
        _recompute_budget_per_person(plan, {"group_size": "abc"})
        assert plan["budget_per_person"] == "约¥1"


class TestComputeDaysNights:
    def test_seven_days_six_nights(self):
        plan = {"summary": "50人广州→成都 6天5晚 行程"}
        _compute_days_nights(plan, {"check_in": "2026-11-01", "check_out": "2026-11-07"})
        assert "7天6晚" in plan["summary"]

    def test_two_days_one_night(self):
        plan = {"summary": "2天2晚 快速游"}
        _compute_days_nights(plan, {"check_in": "2026-10-25", "check_out": "2026-10-26"})
        assert "2天1晚" in plan["summary"]

    def test_no_dates_untouched(self):
        plan = {"summary": "3天2晚"}
        _compute_days_nights(plan, {})
        assert plan["summary"] == "3天2晚"

    def test_inverted_dates_untouched(self):
        plan = {"summary": "3天2晚"}
        _compute_days_nights(plan, {"check_in": "2026-11-07", "check_out": "2026-11-01"})
        assert plan["summary"] == "3天2晚"


class TestRoomAllocation:
    def test_even_group(self):
        rooms = calculate_room_allocation(30)
        assert len(rooms) == 1
        assert rooms[0]["room_type"] == "双人间"
        assert rooms[0]["count"] == 15

    def test_odd_group(self):
        rooms = calculate_room_allocation(31)
        assert len(rooms) == 2
        assert rooms[0]["count"] == 15
        assert rooms[1]["room_type"] == "单人间"
        assert rooms[1]["count"] == 1

    def test_invalid(self):
        assert calculate_room_allocation(0) == []
        assert calculate_room_allocation(-5) == []


class TestBookingLinks:
    def test_valid_link_kept(self):
        plan = {"timeline": [
            {"booking_link": "https://router.feizhu.com/?url=https%3A%2F%2Fhoteldetail"},
        ]}
        _validate_booking_links(plan)
        assert plan["timeline"][0]["booking_link"] is not None

    def test_non_http_nulled(self):
        plan = {"timeline": [{"booking_link": "train_gz_cd"}]}
        _validate_booking_links(plan)
        assert plan["timeline"][0]["booking_link"] is None

    def test_placeholder_token_nulled(self):
        plan = {"timeline": [{"booking_link": "https://router.feizhu.com/?url=jiuzhaigou"}]}
        _validate_booking_links(plan)
        assert plan["timeline"][0]["booking_link"] is None

    def test_empty_untouched(self):
        plan = {"timeline": [{"booking_link": None}]}
        _validate_booking_links(plan)
        assert plan["timeline"][0]["booking_link"] is None
