"""API 层基础流程测试：认证、方案 CRUD、分享、权限隔离、业务主路径。"""

import uuid

import pytest


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


# ── 业务主路径：chat / generate_plan / revise（LLM 与飞猪全部打桩） ──

def _mock_plan_result(city: str = "成都") -> dict:
    return {
        "scenario": "场景类型：活动/聚会出行\n目标城市：成都",
        "markdown": "# 成都 出行方案",
        "sections": [{"title": "🏨 住宿（含实时房价）", "data": "名称：成都希尔顿\n价格：¥600/晚"}],
        "warnings": [],
        "used_llm": False,
        "mode": "event",
        "city": city,
        "data_captured_at": "2026-10-22T12:00:00",
    }


def _mock_structured() -> dict:
    return {
        "summary": "50人成都3天团建",
        "timeline": [{"time_range": "Day 1 09:00-11:00", "title": "宽窄巷子"}],
        "budget": [{"category": "住宿", "estimated_cost": "¥6000", "is_per_person": False}],
        "action_items": [{"id": "hotel-1", "type": "hotel", "title": "酒店", "status": "pending"}],
        "room_allocation": [],
        "budget_per_person": "约¥120",
    }


@pytest.fixture()
def mock_llm_chain(monkeypatch):
    """打桩业务主链路，用返回的 state 控制各桩返回值、查看调用记录。"""
    import app.chat as chat_mod
    import app.planner as planner_mod
    import app.structured_planner as sp

    state = {
        "extract_result": {
            "extracted": {"city": "成都", "group_size": "50人", "budget_tier": "舒适型"},
            "sufficient": True,
            "follow_up": "请补充出发日期？",
        },
        "outline": None,
        "outline_raises": False,
        "structured": _mock_structured(),
        "revise_result": None,
    }

    def fake_extract(message, conversation=None, user_id=None, ui_selected=None):
        state["extract_message"] = message
        state["extract_ui_selected"] = ui_selected
        state["extract_conversation"] = conversation
        return state["extract_result"]

    def fake_outline(extracted, conversation=None, user_id=None):
        if state["outline_raises"]:
            raise RuntimeError("outline failed")
        return state["outline"]

    def fake_outline_text(outline):
        return "【行程大纲】Day1 抵达成都"

    def fake_plan(inp):
        state["plan_input"] = dict(inp)
        return _mock_plan_result(inp.get("city", ""))

    def fake_structured(extracted, sections, user_id=None, plan_id=None, outline=None):
        state["structured_args"] = {
            "extracted": extracted, "sections": sections,
            "plan_id": plan_id, "outline": outline,
        }
        return state["structured"]

    def fake_revise(params, current_structured, message, user_id=None, plan_id=None):
        state["revise_args"] = {"params": params, "current": current_structured, "message": message}
        return state["revise_result"]

    monkeypatch.setattr(chat_mod, "extract_params", fake_extract)
    monkeypatch.setattr(planner_mod, "plan", fake_plan)
    monkeypatch.setattr(sp, "generate_outline", fake_outline)
    monkeypatch.setattr(sp, "outline_to_text", fake_outline_text)
    monkeypatch.setattr(sp, "generate_structured_plan", fake_structured)
    monkeypatch.setattr(sp, "revise_plan", fake_revise)
    return state


class TestChatEndpoints:
    def test_chat_follow_up_with_transport_suggestion(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["extract_result"] = {
            "extracted": {"city": "深圳", "origin": "广州", "group_size": "20人"},
            "sufficient": False,
            "follow_up": "请问什么时候出发？",
        }
        r = api_client.post("/api/chat", headers=auth_headers,
                            json={"message": "20人从广州去深圳", "budget_tier": "舒适型",
                                  "transport_mode": "auto"})
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "follow_up"
        assert data["message"] == "请问什么时候出发？"
        assert data["sufficient"] is False
        assert data["extracted_params"]["city"] == "深圳"
        # 广州→深圳约 105km，短距离直接推荐高铁
        assert data["transport_suggested"] == "train"

    def test_chat_passes_ui_selection_to_extract(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["extract_result"] = {"extracted": {}, "sufficient": False, "follow_up": "去哪个城市？"}
        api_client.post("/api/chat", headers=auth_headers,
                        json={"message": "帮我安排团建", "budget_tier": "经济型",
                              "transport_mode": "train", "preferences": ["亲子"]})
        assert mock_llm_chain["extract_ui_selected"] == {
            "budget_tier": "经济型", "transport_mode": "train", "preferences": ["亲子"],
        }
        assert mock_llm_chain["extract_message"] == "帮我安排团建"

    def test_chat_budget_tier_regex_fallback(self, api_client, auth_headers, mock_llm_chain):
        """抽取漏掉预算档时，按消息原文关键词兜底。"""
        mock_llm_chain["extract_result"] = {
            "extracted": {"city": "成都"}, "sufficient": False, "follow_up": "何时出发？",
        }
        r = api_client.post("/api/chat", headers=auth_headers,
                            json={"message": "30人去成都豪华型团建"})
        assert r.json()["extracted_params"]["budget_tier"] == "豪华型"

    def test_chat_budget_tier_falls_back_to_ui_value(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["extract_result"] = {
            "extracted": {"city": "成都"}, "sufficient": False, "follow_up": "何时出发？",
        }
        r = api_client.post("/api/chat", headers=auth_headers,
                            json={"message": "30人去成都团建", "budget_tier": "经济型"})
        assert r.json()["extracted_params"]["budget_tier"] == "经济型"

    def test_chat_transport_mode_ui_fallback(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["extract_result"] = {
            "extracted": {"city": "成都", "transport_mode": "auto"},
            "sufficient": False, "follow_up": "何时出发？",
        }
        r = api_client.post("/api/chat", headers=auth_headers,
                            json={"message": "30人去成都团建", "transport_mode": "train"})
        assert r.json()["extracted_params"]["transport_mode"] == "train"

    def test_chat_outline_proposal(self, api_client, auth_headers, mock_llm_chain):
        outline = {"days": [{"day": 1, "title": "抵达成都"}]}
        mock_llm_chain["outline"] = outline
        r = api_client.post("/api/chat", headers=auth_headers,
                            json={"message": "50人去成都团建",
                                  "conversation": [{"role": "user", "content": "嗨"}]})
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "outline_proposal"
        assert data["outline"] == outline
        assert data["message"] == "【行程大纲】Day1 抵达成都"
        assert data["extracted_params"]["city"] == "成都"
        # 大纲确认路径不应触发生成
        assert "structured_args" not in mock_llm_chain

    def test_chat_outline_failure_falls_back_to_plan_ready(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["outline_raises"] = True
        r = api_client.post("/api/chat", headers=auth_headers, json={"message": "50人去成都团建"})
        data = r.json()
        assert data["type"] == "plan_ready"
        assert data["plan_id"]
        assert "structured_args" in mock_llm_chain

    def test_chat_saves_conversation(self, api_client, auth_headers, mock_llm_chain):
        mock_llm_chain["extract_result"] = {"extracted": {}, "sufficient": False, "follow_up": "去哪座城市？"}
        api_client.post("/api/chat", headers=auth_headers, json={"message": "公司团建"})
        convos = api_client.get("/api/conversations", headers=auth_headers).json()
        contents = [c["content"] for c in convos]
        assert "公司团建" in contents
        assert "去哪座城市？" in contents

    def test_chat_requires_login(self, api_client):
        r = api_client.post("/api/chat", json={"message": "hi"})
        assert r.status_code in (401, 403)


class TestGeneratePlanEndpoint:
    def test_generate_plan_persists_and_returns_plan_ready(self, api_client, auth_headers, mock_llm_chain):
        payload = {
            "message": "就按这个大纲生成",
            "params": {"city": "成都", "group_size": "50人", "origin": "广州",
                       "check_in": "2026-11-01", "check_out": "2026-11-03", "budget_tier": "舒适型"},
            "outline": {"days": [{"day": 1, "title": "抵达成都"}]},
            "conversation": [{"role": "user", "content": "50人去成都团建"}],
        }
        r = api_client.post("/api/generate_plan", headers=auth_headers, json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "plan_ready"
        assert data["plan_id"]
        assert data["structured"]["summary"] == "50人成都3天团建"
        assert data["extracted_params"]["city"] == "成都"

        # 生成链路收到确认的大纲与飞猪 sections
        args = mock_llm_chain["structured_args"]
        assert args["outline"] == payload["outline"]
        assert any("住宿" in title for title, _ in args["sections"])
        assert mock_llm_chain["plan_input"]["city"] == "成都"

        # 落库：原始诉求取会话首条用户消息，而非大纲确认语
        plan = api_client.get(f"/api/plan/{data['plan_id']}", headers=auth_headers).json()
        assert plan["summary"] == "50人成都3天团建"
        assert plan["original_input"] == "50人去成都团建"
        assert plan["data_captured_at"] == "2026-10-22T12:00:00"

    def test_generate_plan_defaults_missing_params(self, api_client, auth_headers, mock_llm_chain):
        r = api_client.post("/api/generate_plan", headers=auth_headers,
                            json={"message": "生成", "params": {"city": "成都"}})
        assert r.status_code == 200
        assert mock_llm_chain["plan_input"]["budget_tier"] == "舒适型"
        assert mock_llm_chain["plan_input"]["transport_mode"] == "auto"

    def test_generate_plan_requires_login(self, api_client):
        r = api_client.post("/api/generate_plan", json={"message": "x", "params": {}})
        assert r.status_code in (401, 403)


class TestReviseEndpoint:
    def test_revise_updates_persisted_plan(self, api_client, auth_headers, temp_db, mock_llm_chain):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)
        mock_llm_chain["revise_result"] = {
            "summary": "50人成都团建（Day3 改乐山）",
            "action_items": plan["action_items"],
            "timeline": [{"time_range": "Day 3 09:00-11:00", "title": "乐山大佛"}],
            "budget": plan["budget"],
            "room_allocation": [],
            "budget_per_person": "约¥1300",
        }
        r = api_client.post(f"/api/plan/{plan['id']}/revise", headers=auth_headers,
                            json={"message": "第三天换成乐山"})
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "revised"
        assert data["structured"]["summary"] == "50人成都团建（Day3 改乐山）"

        updated = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers).json()
        assert updated["summary"] == "50人成都团建（Day3 改乐山）"
        assert updated["timeline"][0]["title"] == "乐山大佛"
        assert updated["budget_per_person"] == "约¥1300"

        args = mock_llm_chain["revise_args"]
        assert args["message"] == "第三天换成乐山"
        assert args["params"]["city"] == "成都"
        assert args["current"]["timeline"] == plan["timeline"]

    def test_revise_returns_500_when_llm_fails(self, api_client, auth_headers, temp_db, mock_llm_chain):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)
        # revise_result 默认 None（LLM 修改失败）
        r = api_client.post(f"/api/plan/{plan['id']}/revise", headers=auth_headers,
                            json={"message": "改一下"})
        assert r.status_code == 500

    def test_revise_404_for_other_user(self, api_client, auth_headers, temp_db, mock_llm_chain):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)
        other = api_client.post("/api/register", json={"username": "reviser", "password": "pass1234"})
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
        r = api_client.post(f"/api/plan/{plan['id']}/revise", headers=other_headers,
                            json={"message": "偷改"})
        assert r.status_code == 404

    def test_revise_requires_login(self, api_client):
        r = api_client.post("/api/plan/whatever/revise", json={"message": "x"})
        assert r.status_code in (401, 403)
