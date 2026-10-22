"""API 层基础流程测试：认证、方案 CRUD、分享、权限隔离。"""

import uuid


def _make_plan_payload(user_id: str) -> dict:
    pid = str(uuid.uuid4())[:12]
    return {
        "id": pid,
        "user_id": user_id,
        "created_at": "2026-10-22T12:00:00",
        "updated_at": "2026-10-22T12:00:00",
        "phase": "planning",
        "original_input": "50人去成都团建",
        "conversation": [],
        "params": {"city": "成都", "group_size": "50人", "origin": "广州"},
        "summary": "50人成都团建方案",
        "action_items": [],
        "timeline": [{"time_range": "Day 1 09:00-11:00", "title": "宽窄巷子"}],
        "budget": [{"category": "住宿", "estimated_cost": "¥6000", "is_per_person": False}],
        "room_allocation": [],
        "budget_per_person": "约¥1200",
        "share_code": None,
        "members": [],
        "markdown": "# 方案",
        "warnings": [],
        "used_llm": False,
    }


class TestAuth:
    def test_register_login_me(self, api_client):
        r = api_client.post("/api/register", json={"username": "alice", "password": "pass1234"})
        assert r.status_code == 200
        token = r.json()["token"]

        r = api_client.post("/api/login", json={"username": "alice", "password": "pass1234"})
        assert r.status_code == 200

        r = api_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_register_rejects_short_password(self, api_client):
        r = api_client.post("/api/register", json={"username": "bob", "password": "12"})
        assert r.status_code == 400

    def test_duplicate_username_409(self, api_client):
        api_client.post("/api/register", json={"username": "carol", "password": "pass1234"})
        r = api_client.post("/api/register", json={"username": "carol", "password": "pass1234"})
        assert r.status_code == 409

    def test_me_requires_token(self, api_client):
        r = api_client.get("/api/me")
        assert r.status_code in (401, 403)


class TestPlanCrud:
    def test_save_get_list_delete(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)

        r = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["summary"] == "50人成都团建方案"
        assert isinstance(r.json()["timeline"], list)

        r = api_client.get("/api/plans", headers=auth_headers)
        assert r.status_code == 200
        assert any(p["id"] == plan["id"] for p in r.json())

        r = api_client.delete(f"/api/plan/{plan['id']}", headers=auth_headers)
        assert r.status_code == 200
        r = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers)
        assert r.status_code == 404

    def test_owner_isolation(self, api_client, auth_headers, temp_db):
        """他人不能读别人的方案。"""
        me = api_client.get("/api/me", headers=auth_headers).json()
        other = api_client.post("/api/register", json={"username": "intruder", "password": "pass1234"})
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}

        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)

        r = api_client.get(f"/api/plan/{plan['id']}", headers=other_headers)
        assert r.status_code == 404

    def test_share_code_flow(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)

        r = api_client.post(f"/api/plan/{plan['id']}/share", headers=auth_headers)
        assert r.status_code == 200
        code = r.json()["share_code"]
        assert len(code) == 6

        # 公开访问分享，无需 token
        r = api_client.get(f"/api/share/{code}")
        assert r.status_code == 200
        assert r.json()["summary"] == "50人成都团建方案"

    def test_data_captured_at_roundtrip(self, api_client, auth_headers, temp_db):
        """价格时效字段落库后可读回。"""
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        plan["data_captured_at"] = "2026-10-22T19:43:55"
        temp_db.save_plan(plan)

        r = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers)
        assert r.json()["data_captured_at"] == "2026-10-22T19:43:55"

    def test_task_status_update(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        plan["action_items"] = [{"id": "hotel-1", "type": "hotel", "title": "酒店", "status": "pending"}]
        temp_db.save_plan(plan)

        r = api_client.put(
            f"/api/plan/{plan['id']}/task/hotel-1",
            json={"status": "confirmed"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["action_items"][0]["status"] == "confirmed"


class TestVerifyPrices:
    def test_verify_prices_endpoint(self, api_client, auth_headers, temp_db, monkeypatch):
        """验价端点：返回对比报告且不修改方案。"""
        from app import flyai_client

        def fake_search_flight(**kwargs):
            return {"data": {"itemList": [
                {"ticketPrice": 551, "journeys": [{"segments": [{"marketingTransportNo": "AQ1113"}]}]},
            ]}}

        def fake_search_train(**kwargs):
            return {"data": {"itemList": []}}

        monkeypatch.setattr(flyai_client, "search_flight", fake_search_flight)
        monkeypatch.setattr(flyai_client, "search_train", fake_search_train)

        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        plan["params"]["check_in"] = "2026-11-01"
        plan["params"]["check_out"] = "2026-11-03"
        plan["timeline"] = [
            {"time_range": "Day 1 23:00-01:35", "title": "广州飞往成都（AQ1113）", "price": "¥549/人"},
        ]
        temp_db.save_plan(plan)

        r = api_client.post(f"/api/plan/{plan['id']}/verify_prices", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "verify_prices"
        assert body["summary"]["checked"] == 1
        item = body["items"][0]
        assert item["transport_no"] == "AQ1113"
        assert item["status"] in ("unchanged", "changed")
        assert item["new_price"] == 551

    def test_verify_prices_requires_owner(self, api_client, auth_headers, temp_db):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)

        api_client.post("/api/register", json={"username": "stranger", "password": "pass1234"})
        r = api_client.post("/api/register", json={"username": "stranger2", "password": "pass1234"})
        other_headers = {"Authorization": f"Bearer {r.json()['token']}"}

        r = api_client.post(f"/api/plan/{plan['id']}/verify_prices", headers=other_headers)
        assert r.status_code == 404

    def test_verify_prices_requires_login(self, api_client, temp_db):
        r = api_client.post("/api/plan/whatever/verify_prices")
        assert r.status_code in (401, 403)
