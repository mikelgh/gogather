"""database.py 修订历史：快照保存、查询、回滚，及修订/回滚 API 端点。"""

import uuid

import pytest


def _plan_payload(user_id: str) -> dict:
    return {
        "id": str(uuid.uuid4())[:12],
        "user_id": user_id,
        "created_at": "2026-10-22T10:00:00",
        "updated_at": "2026-10-22T10:00:00",
        "phase": "planning",
        "original_input": "20人去西安团建",
        "conversation": [],
        "params": {"city": "西安", "group_size": "20人", "origin": "北京"},
        "summary": "初版摘要",
        "action_items": [{"id": "hotel-1", "type": "hotel", "title": "酒店", "status": "pending"}],
        "timeline": [{"time_range": "Day 1 09:00-11:00", "title": "兵马俑"}],
        "budget": [{"category": "住宿", "estimated_cost": "¥4000", "is_per_person": False}],
        "room_allocation": [],
        "budget_per_person": "约¥800",
        "share_code": None,
        "members": [],
        "markdown": "# 方案",
        "warnings": [],
        "used_llm": False,
    }


@pytest.fixture()
def seeded(temp_db):
    """已落库的方案 + 两份修订快照。返回 (database, plan, rid1, rid2)。"""
    db = temp_db
    plan = _plan_payload("owner-1")
    db.save_plan(plan)
    rid1 = db.save_plan_revision(
        plan["id"], 1, "首次生成",
        {
            "summary": "初版摘要",
            "timeline": [{"time_range": "Day 1 09:00-11:00", "title": "兵马俑"}],
            "budget": [{"category": "住宿", "estimated_cost": "¥4000", "is_per_person": False}],
            "action_items": [{"id": "hotel-1", "type": "hotel", "title": "酒店"}],
            "room_allocation": [],
            "budget_per_person": "约¥800",
            "params": {"city": "西安", "group_size": "20人", "origin": "北京"},
        },
    )
    rid2 = db.save_plan_revision(
        plan["id"], 2, "改为两日行程",
        {
            "summary": "两日版",
            "timeline": [{"title": "兵马俑"}, {"title": "城墙"}],
            "budget": [],
            "action_items": [],
            "room_allocation": [],
            "budget_per_person": "约¥950",
            "params": {"city": "西安", "group_size": "20人", "days": "2"},
        },
    )
    return db, plan, rid1, rid2


class TestRevisionStore:
    def test_save_returns_id_and_list_orders_desc(self, seeded):
        db, plan, rid1, rid2 = seeded
        assert rid1 and rid2 and rid2 != rid1

        revisions = db.get_plan_revisions(plan["id"])
        assert [r["revision_number"] for r in revisions] == [2, 1]
        assert revisions[0]["message"] == "改为两日行程"
        # JSON 字段已反序列化回 list/dict
        assert revisions[0]["timeline"] == [{"title": "兵马俑"}, {"title": "城墙"}]
        assert revisions[0]["params"]["days"] == "2"
        assert revisions[1]["budget"][0]["estimated_cost"] == "¥4000"

    def test_get_plan_revisions_respects_limit(self, seeded):
        db, plan, _, _ = seeded
        assert len(db.get_plan_revisions(plan["id"], limit=1)) == 1

    def test_get_plan_revisions_empty_for_unknown_plan(self, seeded):
        db, *_ = seeded
        assert db.get_plan_revisions("nonexistent") == []

    def test_get_plan_revision_single_and_missing(self, seeded):
        db, plan, rid1, _ = seeded
        rev = db.get_plan_revision(rid1)
        assert rev["plan_id"] == plan["id"]
        assert rev["summary"] == "初版摘要"
        assert db.get_plan_revision(999999) is None

    def test_rollback_restores_revision_fields(self, seeded):
        db, plan, rid1, _ = seeded
        drifted = dict(plan)
        drifted["summary"] = "已漂移"
        drifted["timeline"] = [{"time_range": "Day 1", "title": "全新行程"}]
        db.save_plan(drifted)

        rolled = db.rollback_plan(plan["id"], rid1, user_id="owner-1")
        assert rolled["summary"] == "初版摘要"
        assert rolled["timeline"][0]["title"] == "兵马俑"
        assert rolled["budget"][0]["estimated_cost"] == "¥4000"
        assert rolled["params"]["city"] == "西安"
        assert rolled["updated_at"] != plan["updated_at"]

        persisted = db.get_plan(plan["id"], user_id="owner-1")
        assert persisted["summary"] == "初版摘要"

    def test_rollback_rejects_revision_of_other_plan(self, seeded):
        db, plan, _, _ = seeded
        other = _plan_payload("owner-2")
        db.save_plan(other)
        rid_foreign = db.save_plan_revision(other["id"], 1, "他人快照", {"summary": "s"})
        assert db.rollback_plan(plan["id"], rid_foreign, user_id="owner-1") is None

    def test_rollback_unknown_revision_returns_none(self, seeded):
        db, plan, _, _ = seeded
        assert db.rollback_plan(plan["id"], 999999, user_id="owner-1") is None

    def test_rollback_rejects_wrong_user(self, seeded):
        db, plan, rid1, _ = seeded
        assert db.rollback_plan(plan["id"], rid1, user_id="intruder") is None


class TestRevisionEndpoints:
    def test_list_revisions_requires_ownership(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _plan_payload(me["id"])
        temp_db.save_plan(plan)
        temp_db.save_plan_revision(plan["id"], 1, "首次生成", {"summary": plan["summary"]})

        r = api_client.get(f"/api/plan/{plan['id']}/revisions", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()[0]["message"] == "首次生成"

        other = api_client.post("/api/register", json={"username": "peeker", "password": "pass1234"})
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
        r = api_client.get(f"/api/plan/{plan['id']}/revisions", headers=other_headers)
        assert r.status_code == 404

    def test_rollback_endpoint_snapshots_current_and_restores(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _plan_payload(me["id"])
        temp_db.save_plan(plan)
        rid1 = temp_db.save_plan_revision(
            plan["id"], 1, "首次生成",
            {"summary": "初版摘要", "timeline": [{"title": "兵马俑"}], "budget": [],
             "action_items": [], "room_allocation": [], "budget_per_person": "",
             "params": plan["params"]},
        )
        drifted = dict(plan)
        drifted["summary"] = "漂移版"
        temp_db.save_plan(drifted)

        r = api_client.post(f"/api/plan/{plan['id']}/rollback/{rid1}", headers=auth_headers)
        assert r.status_code == 200
        assert "版本 1" in r.json()["message"]

        restored = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers).json()
        assert restored["summary"] == "初版摘要"

        # 回滚前快照已自动入历史（revision_number=2）
        revisions = api_client.get(f"/api/plan/{plan['id']}/revisions", headers=auth_headers).json()
        assert [x["revision_number"] for x in revisions] == [2, 1]
        assert "回滚前快照" in revisions[0]["message"]

    def test_rollback_unknown_revision_404(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _plan_payload(me["id"])
        temp_db.save_plan(plan)
        r = api_client.post(f"/api/plan/{plan['id']}/rollback/99999", headers=auth_headers)
        assert r.status_code == 404

    def test_revisions_require_login(self, api_client):
        r = api_client.get("/api/plan/whatever/revisions")
        assert r.status_code in (401, 403)
