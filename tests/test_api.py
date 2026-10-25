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

    def test_token_for_missing_account_cannot_access_or_create_data(self, api_client, temp_db):
        from app import auth, main

        token = auth.create_access_token("deleted-account", "former-user")
        headers = {"Authorization": f"Bearer {token}"}
        assert api_client.get("/api/plans", headers=headers).status_code == 401
        assert api_client.post("/api/chat", headers=headers, json={"message": "计划旅行"}).status_code == 401
        assert main.get_optional_user(headers["Authorization"]) is None
        assert temp_db.get_conversations("deleted-account") == []


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


class TestAdminAccess:
    @pytest.mark.parametrize("path", [
        "/api/admin/users", "/api/admin/plans", "/api/admin/ai-usage",
        "/api/admin/plan/test-plan/ai-usage",
    ])
    def test_admin_endpoints_enforce_permissions(self, api_client, auth_headers, monkeypatch, path):
        from app import main

        assert api_client.get(path).status_code == 401
        assert api_client.get(path, headers=auth_headers).status_code == 403
        owner = api_client.get("/api/me", headers=auth_headers).json()["id"]
        monkeypatch.setattr(main, "ADMIN_USER_IDS", {owner})
        assert api_client.get(path, headers=auth_headers).status_code == 200


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

    def test_revise_saves_before_snapshot(self, api_client, auth_headers, temp_db, mock_llm_chain):
        me = api_client.get("/api/me", headers=auth_headers).json()
        plan = _make_plan_payload(me["id"])
        temp_db.save_plan(plan)
        mock_llm_chain["revise_result"] = {
            "summary": "修改后摘要",
            "action_items": plan["action_items"],
            "timeline": plan["timeline"],
            "budget": plan["budget"],
            "room_allocation": [],
            "budget_per_person": plan["budget_per_person"],
        }
        r = api_client.post(f"/api/plan/{plan['id']}/revise", headers=auth_headers,
                            json={"message": "换家酒店"})
        assert r.status_code == 200
        revs = api_client.get(f"/api/plan/{plan['id']}/revisions", headers=auth_headers).json()
        assert len(revs) == 1
        assert revs[0]["revision_number"] == 1
        assert "换家酒店" in revs[0]["message"]
        assert revs[0]["summary"] == plan["summary"]

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


class TestGithubOAuth:
    """GitHub 回调网络异常时优雅降级（服务器侧 github.com 间歇性被阻断）。"""

    def test_callback_network_error_redirects_home(self, api_client, monkeypatch):
        import httpx

        from app import main as app_main

        class ExplodingClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                raise httpx.ConnectTimeout("github.com unreachable")

        async def _no_sleep(_):
            return

        monkeypatch.setattr(app_main.httpx, "AsyncClient", ExplodingClient)
        monkeypatch.setattr(app_main.asyncio, "sleep", _no_sleep)
        state = "test-oauth-state"
        app_main._oauth_states[state] = 0

        resp = api_client.get(
            "/api/auth/github/callback",
            params={"code": "abc123", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 307
        assert resp.headers["location"] == "/?auth_error=github"
        assert state not in app_main._oauth_states

    def test_callback_bad_state_rejected(self, api_client):
        resp = api_client.get(
            "/api/auth/github/callback",
            params={"code": "abc", "state": "not-exist"},
            follow_redirects=False,
        )
        assert resp.status_code == 400


def test_me_returns_bound_identity(api_client, auth_headers, temp_db):
    from app import database

    me = api_client.get("/api/me", headers=auth_headers).json()
    assert me["github_bound"] is False
    assert me["alipay_bound"] is False

    user = database.get_user_by_username("testuser")
    database.link_github_account(user["id"], 123456, "http://avatar/u.png")
    database.link_alipay_account(user["id"], "2088xxx", "")

    me = api_client.get("/api/me", headers=auth_headers).json()
    assert me["github_bound"] is True
    assert me["alipay_bound"] is True


@pytest.fixture()
def oauth_callback(api_client, monkeypatch):
    """只模拟三方 HTTP 响应与支付宝签名，账号查询和创建使用 temp_db。"""
    import httpx
    from urllib.parse import parse_qs, quote, urlsplit
    from app import main, alipay_oauth, auth

    identity = {}

    class OAuthClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if url == main.GITHUB_TOKEN_URL:
                return httpx.Response(200, json={"access_token": "test-token"})
            assert url == main.TAOBAO_TOKEN_URL
            return httpx.Response(200, json={
                "access_token": "test-token", "taobao_user_id": identity["id"],
                "taobao_nick": quote(identity["name"], safe=""),
            })

        async def get(self, url, **kwargs):
            if url == main.GITHUB_USER_URL:
                return httpx.Response(200, json={"id": int(identity["id"]), "login": identity["name"]})
            if kwargs["params"]["stage"] == "token":
                return httpx.Response(200, json={"alipay_system_oauth_token_response": {
                    "access_token": "test-token", "user_id": identity["id"],
                }})
            return httpx.Response(200, json={"alipay_user_info_share_response": {
                "user_id": identity["id"], "nick_name": identity["name"],
            }})

    monkeypatch.setattr(main.httpx, "AsyncClient", OAuthClient)
    monkeypatch.setattr(alipay_oauth, "build_token_params", lambda *args: {"stage": "token"})
    monkeypatch.setattr(alipay_oauth, "build_userinfo_params", lambda *args: {"stage": "info"})

    def login(provider, provider_id, name):
        identity.update(id=provider_id, name=name)
        state = str(uuid.uuid4())
        monkeypatch.setitem(main._oauth_states, state, 0)
        response = api_client.get(
            f"/api/auth/{provider}/callback", params={"code": "test-code", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 307
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert set(query) == {"token", "username"}
        payload = auth.decode_access_token(query["token"][0])
        assert query["username"] == [payload["username"]]
        return payload, {"Authorization": f"Bearer {query['token'][0]}"}

    return login


@pytest.mark.parametrize("provider", ["github", "alipay", "taobao"])
@pytest.mark.parametrize("already_bound", [False, True])
def test_oauth_same_name_never_takes_over(provider, already_bound, oauth_callback, api_client, temp_db):
    name = "同名&user=other+#"
    original = temp_db.create_user("original-user", name, "test-hash")
    if already_bound:
        assert getattr(temp_db, f"link_{provider}_account")(original["id"], "111111", "")
    before = temp_db.get_user_by_id(original["id"])
    plan = _make_plan_payload(original["id"])
    temp_db.save_plan(plan)

    newcomer, headers = oauth_callback(provider, "222222", name)
    assert newcomer["sub"] != original["id"]
    assert newcomer["username"].startswith(name + "_")
    assert len(newcomer["username"]) == len(name) + 7
    assert temp_db.get_user_by_id(original["id"]) == before
    assert getattr(temp_db, f"get_user_by_{provider}_id")("222222")["id"] == newcomer["sub"]
    assert api_client.get(f"/api/plan/{plan['id']}", headers=headers).status_code == 404

    # provider 昵称变动不会改变账号归属或本地昵称。
    again, _ = oauth_callback(provider, "222222", "不同昵称")
    assert again["sub"] == newcomer["sub"]
    assert again["username"] == newcomer["username"]
    if already_bound:
        old_login, old_headers = oauth_callback(provider, "111111", "不同昵称")
        assert old_login["sub"] == original["id"]
        assert old_login["username"] == name
        assert api_client.get(f"/api/plan/{plan['id']}", headers=old_headers).status_code == 200
    assert temp_db.get_user_by_id(original["id"]) == before


@pytest.mark.parametrize("provider", ["github", "alipay", "taobao"])
def test_oauth_unique_name_preserved(provider, oauth_callback, temp_db):
    name = "新用户&name=other+#"
    user, _ = oauth_callback(provider, "333333", name)
    assert user["username"] == name
    assert temp_db.get_user_by_username(name)["id"] == user["sub"]


@pytest.mark.parametrize("insert_new", [False, True])
def test_save_plan_share_collision_keeps_both_plans(temp_db, monkeypatch, insert_new):
    import sqlite3

    for uid in ("owner-a", "owner-b"):
        temp_db.create_user(uid, uid, "test-hash")
    first = _make_plan_payload("owner-a")
    second = _make_plan_payload("owner-b")
    first["share_code"] = "aaaaaa"
    temp_db.save_plan(first)
    temp_db.save_plan(second)
    before = [temp_db.get_plan(p["id"]) for p in (first, second)]
    conflicting = dict(second, share_code="aaaaaa", summary="不得写入")
    if insert_new:
        conflicting["id"] = "third-plan"

    get_conn = temp_db._get_conn
    connections = []

    def tracked_conn():
        conn = get_conn()
        connections.append(conn)
        return conn

    monkeypatch.setattr(temp_db, "_get_conn", tracked_conn)
    with pytest.raises(sqlite3.IntegrityError):
        temp_db.save_plan(conflicting)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert [temp_db.get_plan(p["id"]) for p in (first, second)] == before
    assert temp_db.get_plan_by_share("aaaaaa")["id"] == first["id"]
    if insert_new:
        assert temp_db.get_plan("third-plan") is None
    temp_db.save_plan(dict(second, summary="正常更新"))
    assert temp_db.get_plan(second["id"])["summary"] == "正常更新"


def test_generate_share_collision_retries_and_is_idempotent(api_client, auth_headers, temp_db, monkeypatch):
    owner = api_client.get("/api/me", headers=auth_headers).json()["id"]
    temp_db.create_user("other-owner", "other-owner", "test-hash")
    first = _make_plan_payload("other-owner")
    second = _make_plan_payload(owner)
    first["share_code"] = "aaaaaa"
    temp_db.save_plan(first)
    temp_db.save_plan(second)
    first_before = temp_db.get_plan(first["id"])
    second_before = temp_db.get_plan(second["id"])
    chars = iter("aaaaaabbbbbb")
    monkeypatch.setattr(temp_db.secrets, "choice", lambda alphabet: next(chars))

    url = f"/api/plan/{second['id']}/share"
    for _ in range(2):
        response = api_client.post(url, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["share_code"] == "bbbbbb"
    assert temp_db.get_plan(first["id"]) == first_before
    assert temp_db.get_plan(second["id"]) == dict(second_before, share_code="bbbbbb")
    assert temp_db.generate_share_code(first["id"], user_id=owner) is None
    assert temp_db.generate_share_code("missing", user_id=owner) is None


def test_generate_share_does_not_overwrite_concurrent_edit(temp_db, monkeypatch):
    plan = _make_plan_payload("owner")
    temp_db.save_plan(plan)
    updated = dict(plan, summary="并发新摘要", updated_at="2026-11-01T12:00:00",
                   timeline=[{"title": "并发新行程"}])
    edited = False

    def choice(alphabet):
        nonlocal edited
        if not edited:
            edited = True
            temp_db.save_plan(updated)
        return "c"

    monkeypatch.setattr(temp_db.secrets, "choice", choice)
    assert temp_db.generate_share_code(plan["id"], "owner") == "cccccc"
    saved = temp_db.get_plan(plan["id"])
    for key in ("summary", "updated_at", "timeline"):
        assert saved[key] == updated[key]


def test_concurrent_share_returns_same_code(temp_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    plan = _make_plan_payload("owner")
    temp_db.save_plan(plan)
    barrier = Barrier(2)
    state = local()

    def choice(alphabet):
        if not getattr(state, "started", False):
            state.started = True
            barrier.wait(timeout=10)
        return state.char

    def share(char):
        state.char = char
        return temp_db.generate_share_code(plan["id"], "owner")

    monkeypatch.setattr(temp_db.secrets, "choice", choice)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(share, ("a", "b")))
    assert codes[0] == codes[1] == temp_db.get_plan(plan["id"])["share_code"]
    assert codes[0] in ("aaaaaa", "bbbbbb")


@pytest.mark.parametrize("description_source", ["summary", "group_size", "dates"])
def test_share_page_escapes_dynamic_html(api_client, temp_db, description_source):
    from html import escape
    from html.parser import HTMLParser
    from urllib.parse import quote

    class Tags(HTMLParser):
        def __init__(self, text):
            super().__init__()
            self.tags = []
            self.metas = {}
            self.feed(text)

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == "meta":
                attrs = dict(attrs)
                if "property" in attrs:
                    self.metas[attrs["property"]] = attrs["content"]

    plan = _make_plan_payload("owner")
    plan["share_code"] = "safe01"
    temp_db.save_plan(plan)
    baseline = Tags(api_client.get("/share/safe01").text)
    attack = '\"><script id="injected">alert(1)</script><b title=\'x\'>&'
    code = '\"><script id="injected">'
    plan["share_code"] = code
    plan["params"] = {"city": attack, "origin": attack}
    if description_source == "summary":
        plan["summary"] = attack
    elif description_source == "group_size":
        plan["params"]["group_size"] = attack
    else:
        plan["params"].update(check_in="2026-" + attack, check_out="2026-" + attack)
    temp_db.save_plan(plan)
    response = api_client.get("/share/" + quote(code, safe=""))
    assert response.status_code == 200
    html = response.text
    parsed = Tags(html)
    assert parsed.tags == baseline.tags  # 模板原有脚本不变，输入不能增加任何标签。
    assert '<script id="injected">' not in html
    assert escape(attack, quote=True) in html
    assert escape(code, quote=True) in html
    assert parsed.metas["og:title"] == f"GoGather 聚行 · {attack}→{attack} 出行方案"
    assert attack in parsed.metas["og:description"]
    assert parsed.metas["og:image"] == f"/share/{code}/cover.png"


def test_public_share_allowlist_preserves_display_fields(api_client, auth_headers, temp_db):
    owner = api_client.get("/api/me", headers=auth_headers).json()["id"]
    plan = _make_plan_payload(owner)
    plan.update(
        share_code="public", conversation=[{"role": "user", "content": "私密会话"}],
        original_input="私密原始输入", members=[{"name": "私密成员"}],
        data_captured_at="2026-10-22T11:00:00",
        room_allocation=[{"notes": "私密分房"}], markdown="私密内部文本",
        warnings=["内部告警"],
    )
    plan["params"].update(check_in="2026-11-01", check_out="2026-11-03", internal_note="私密参数")
    plan["timeline"][0].update(
        price="¥50", notes="游览", booking_link="https://example.com/book", image="/static/test.jpg",
        lat=30.67, lng=104.06, action_item_id="activity-1", location="宽窄巷子",
        restaurant_options=[{"name": "餐厅", "highlights": "本地菜", "url": "https://example.com/meal"}],
        restaurant_options_note=None,
    )
    plan["action_items"] = [{"id": "activity-1", "type": "activity", "title": "预订门票",
                             "description": "提前预订", "estimated_cost": "¥50", "status": "pending"}]
    temp_db.save_plan(plan)
    response = api_client.get("/api/share/public")
    assert response.status_code == 200
    public = response.json()
    display_fields = {"summary", "timeline", "budget", "action_items", "budget_per_person",
                      "created_at", "data_captured_at"}
    assert set(public) == display_fields | {"params"}
    for key in display_fields:
        assert public[key] == plan[key]
    assert public["params"] == {key: plan["params"][key] for key in (
        "city", "origin", "check_in", "check_out", "group_size",
    )}
    assert "私密" not in response.text
    owned = api_client.get(f"/api/plan/{plan['id']}", headers=auth_headers).json()
    assert owned == temp_db.get_plan(plan["id"])
    for key in ("conversation", "original_input", "user_id", "members", "markdown", "params"):
        assert owned[key] == plan[key]
    # 地图和提醒为独立公开接口，完整保留其展示数据；已有坐标无需访问外部服务。
    map_response = api_client.get("/api/share/public/map")
    assert map_response.status_code == 200
    assert map_response.json()["title"] == "广州 → 成都"
    assert map_response.json()["timeline"][0]["lat"] == 30.67
    assert map_response.json()["timeline"][0]["lng"] == 104.06
    assert api_client.get("/map/public").status_code == 200
    assert api_client.get("/api/share/public/reminders").json()["reminders"]
    assert api_client.get("/api/share/missing").status_code == 404


@pytest.mark.parametrize("llm_result", [None, {}])
def test_real_revise_failure_leaves_all_state_unchanged(api_client, auth_headers, temp_db, monkeypatch, llm_result):
    from unittest.mock import Mock
    from app import structured_planner

    owner = api_client.get("/api/me", headers=auth_headers).json()["id"]
    plan = _make_plan_payload(owner)
    plan["conversation"] = [{"role": "user", "content": "原始会话"}]
    temp_db.save_plan(plan)
    temp_db.save_plan_revision(plan["id"], 1, "已有修订", plan)
    temp_db.save_conversation(owner, "user", "已有用户会话")
    temp_db.save_conversation(owner, "assistant", "已有助手会话")
    before = (temp_db.get_plan(plan["id"]), temp_db.get_plan_revisions(plan["id"]),
              temp_db.get_conversations(owner))
    complete = Mock(return_value=llm_result)
    monkeypatch.setattr(structured_planner._llm, "complete_json", complete)

    response = api_client.post(f"/api/plan/{plan['id']}/revise", headers=auth_headers,
                               json={"message": "第三天换个景点"})
    assert response.status_code == 500
    assert response.json()["detail"] == "修改失败，请重试"
    complete.assert_called_once()
    assert complete.call_args.kwargs["action"] == "revise_plan"
    assert complete.call_args.kwargs["plan_id"] == plan["id"]
    assert complete.call_args.kwargs["user_id"] == owner
    assert (temp_db.get_plan(plan["id"]), temp_db.get_plan_revisions(plan["id"]),
            temp_db.get_conversations(owner)) == before
