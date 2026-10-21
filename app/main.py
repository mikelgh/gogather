"""FastAPI 应用入口：页面、认证、规划、对话、实施跟踪接口。"""
import os
import re
import uuid
import secrets
from datetime import datetime
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
import httpx

from . import config, planner, chat as chat_mod
from . import database as db
from . import auth

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

db.init_db()

app = FastAPI(title="聚行 · 飞猪 AI 旅行助手")


# ── Request Models ────────────────────────────────────────

class PlanRequest(BaseModel):
    mode: str = "event"
    city: str
    check_in: str = ""
    check_out: str = ""
    origin: str = ""
    group_size: str = ""
    vibe: str = ""
    budget_tier: str = "舒适型"
    transport_mode: str = "auto"
    children_count: str | None = None
    children_ages: str | None = None
    elderly_count: str | None = None
    accessibility: str | None = None
    preferences: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    command: str = "ai-search"
    options: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    plan_id: str | None = None
    conversation: list[dict] = Field(default_factory=list)
    budget_tier: str = "舒适型"
    transport_mode: str = "auto"
    preferences: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    status: str


class RestaurantRecommendRequest(BaseModel):
    item_index: int


class ReviseRequest(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ── Auth Dependency ───────────────────────────────────────

def get_current_user(authorization: str | None = Header(None)) -> dict:
    """从 Authorization header 解析 JWT，返回用户信息。"""
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    return {"id": payload["sub"], "username": payload.get("username", "")}


def get_optional_user(authorization: str | None = Header(None)) -> dict | None:
    """可选认证：有 token 就解析，没有就返回 None。"""
    if not authorization:
        return None
    token = authorization.removeprefix("Bearer ").strip()
    payload = auth.decode_access_token(token)
    if not payload:
        return None
    return {"id": payload["sub"], "username": payload.get("username", "")}


# Admin whitelist - only this GitHub username can access admin endpoints
ADMIN_GITHUB_USERNAMES = {"mikelgh"}


def get_admin_user(authorization: str | None = Header(None)) -> dict:
    """管理员认证：仅允许白名单内的 GitHub 用户。"""
    user = get_current_user(authorization)
    if user["username"] not in ADMIN_GITHUB_USERNAMES:
        raise HTTPException(status_code=403, detail="无管理员权限")
    return user


# ── 管理后台 API ───────────────────────────────────────────

@app.get("/api/admin/users")
def admin_list_users(admin: dict = Depends(get_admin_user)):
    """获取所有用户及其方案统计。"""
    return db.admin_get_all_users()


@app.get("/api/admin/plans")
def admin_list_plans(limit: int = 100, admin: dict = Depends(get_admin_user)):
    """获取所有方案（管理员视图）。"""
    return db.admin_get_all_plans(limit=limit)


@app.get("/api/admin/ai-usage")
def admin_ai_usage_summary(days: int = 30, admin: dict = Depends(get_admin_user)):
    """获取 AI 使用汇总统计。"""
    return db.admin_get_ai_usage_summary(days=days)


@app.get("/api/admin/plan/{plan_id}/ai-usage")
def admin_plan_ai_usage(plan_id: str, admin: dict = Depends(get_admin_user)):
    """获取指定方案的 AI 使用记录。"""
    return db.admin_get_plan_ai_usage(plan_id)


# ── 页面 ──────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/logo.svg")
def logo():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "logo.svg"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/share/{code}")
def share_page(code: str):
    return FileResponse(os.path.join(BASE_DIR, "static", "share.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "admin.html"))


# ── 诊断 ──────────────────────────────────────────────────

@app.get("/api/health")
def api_health():
    return {
        "flyai_key_loaded": bool(config.FLYAI_API_KEY),
        "dashscope_key_loaded": bool(config.DASHSCOPE_API_KEY),
        "flyai_prefix": config.resolve_flyai_prefix(),
    }


# ── 认证 ──────────────────────────────────────────────────

@app.post("/api/register")
def api_register(req: RegisterRequest):
    if len(req.username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少2个字符")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="密码至少4个字符")

    user_id = str(uuid.uuid4())[:12]
    password_hash = auth.hash_password(req.password)
    user = db.create_user(user_id, req.username, password_hash)
    if not user:
        raise HTTPException(status_code=409, detail="用户名已存在")

    token = auth.create_access_token(user["id"], user["username"])
    return {
        "token": token,
        "user": {"id": user["id"], "username": user["username"]},
    }


@app.post("/api/login")
def api_login(req: LoginRequest):
    user = db.get_user_by_username(req.username)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = auth.create_access_token(user["id"], user["username"])
    return {
        "token": token,
        "user": {"id": user["id"], "username": user["username"]},
    }


@app.get("/api/me")
def api_me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "username": user["username"]}


# ── GitHub OAuth ──────────────────────────────────────────

_oauth_states: dict[str, float] = {}

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


@app.get("/api/auth/github")
def github_login():
    if not config.GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GitHub OAuth 未配置")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = datetime.now().timestamp()
    params = {
        "client_id": config.GITHUB_CLIENT_ID,
        "redirect_uri": config.GITHUB_CALLBACK_URL,
        "scope": "read:user",
        "state": state,
    }
    return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/api/auth/github/callback")
async def github_callback(code: str, state: str):
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="无效的 state 参数")
    _oauth_states.pop(state)

    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": config.GITHUB_CLIENT_ID,
                "client_secret": config.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": config.GITHUB_CALLBACK_URL,
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="GitHub 授权失败")

        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        gh_user = user_resp.json()

    github_id = gh_user["id"]
    github_username = gh_user["login"]
    avatar_url = gh_user.get("avatar_url", "")

    existing = db.get_user_by_github_id(github_id)
    if existing:
        token = auth.create_access_token(existing["id"], existing["username"])
        frontend_url = f"/?token={token}&username={existing['username']}"
        return RedirectResponse(frontend_url)

    existing_by_name = db.get_user_by_username(github_username)
    if existing_by_name:
        db.link_github_account(existing_by_name["id"], github_id, avatar_url)
        token = auth.create_access_token(existing_by_name["id"], existing_by_name["username"])
        frontend_url = f"/?token={token}&username={existing_by_name['username']}"
        return RedirectResponse(frontend_url)

    user_id = str(uuid.uuid4())[:12]
    new_user = db.create_github_user(user_id, github_username, github_id, avatar_url)
    if not new_user:
        raise HTTPException(status_code=500, detail="创建用户失败")

    token = auth.create_access_token(new_user["id"], new_user["username"])
    frontend_url = f"/?token={token}&username={new_user['username']}"
    return RedirectResponse(frontend_url)


# ── Alipay OAuth ──────────────────────────────────────────

@app.get("/api/auth/alipay")
def alipay_login(request: Request):
    if not config.ALIPAY_APP_ID:
        raise HTTPException(status_code=500, detail="支付宝 OAuth 未配置")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = datetime.now().timestamp()
    from . import alipay_oauth
    url = alipay_oauth.build_auth_url(
        config.ALIPAY_APP_ID, config.ALIPAY_CALLBACK_URL, state
    )
    ua = request.headers.get("user-agent", "").lower()
    is_mobile = any(k in ua for k in ("mobile", "android", "iphone", "ipad"))
    if is_mobile:
        import qrcode, io, base64
        buf = io.BytesIO()
        qrcode.make(url, box_size=8, border=2).save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>支付宝登录 - GoGather</title>
<style>
body{{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;
background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.card{{background:#fff;border-radius:16px;padding:32px 24px;text-align:center;
box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:340px;width:90%}}
h2{{margin:0 0 8px;font-size:18px;color:#1a1a1a}}
p{{margin:0 0 20px;font-size:14px;color:#888}}
img{{width:220px;height:220px;border-radius:8px}}
.tip{{margin-top:16px;font-size:13px;color:#1677ff}}
</style></head><body>
<div class="card">
<h2>支付宝扫码登录</h2>
<p>请打开支付宝扫描二维码</p>
<img src="data:image/png;base64,{qr_b64}" alt="支付宝登录二维码">
<div class="tip">扫码后在支付宝内完成授权</div>
</div></body></html>""")
    return RedirectResponse(url)


@app.get("/api/auth/alipay/callback")
async def alipay_callback(auth_code: str = "", code: str = "", state: str = ""):
    """Alipay callback: auth_code or code param depending on API version."""
    auth_code = auth_code or code
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="无效的 state 参数")
    _oauth_states.pop(state)

    from . import alipay_oauth
    import httpx as _httpx

    # Exchange auth_code for access_token
    token_params = alipay_oauth.build_token_params(
        config.ALIPAY_APP_ID, auth_code, config.ALIPAY_PRIVATE_KEY
    )
    async with _httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.get(
            "https://openapi.alipay.com/gateway.do",
            params=token_params,
        )
        token_data = token_resp.json()

    alipay_response = token_data.get("alipay_system_oauth_token_response", {})
    access_token = alipay_response.get("access_token")
    alipay_user_id = alipay_response.get("user_id")
    if not access_token:
        error_resp = token_data.get("error_response", {})
        raise HTTPException(
            status_code=400,
            detail=f"支付宝授权失败: {error_resp.get('sub_msg', '未知错误')}"
        )

    # Get user info
    userinfo_params = alipay_oauth.build_userinfo_params(
        config.ALIPAY_APP_ID, access_token, config.ALIPAY_PRIVATE_KEY
    )
    async with _httpx.AsyncClient(timeout=30) as client:
        info_resp = await client.get(
            "https://openapi.alipay.com/gateway.do",
            params=userinfo_params,
        )
        info_data = info_resp.json()

    user_info = info_data.get("alipay_user_info_share_response", {})
    alipay_user_id = user_info.get("user_id") or alipay_user_id
    nick_name = user_info.get("nick_name") or user_info.get("nick") or f"alipay_{alipay_user_id[:6]}"
    avatar_url = user_info.get("avatar", "")

    # Find or create user
    existing = db.get_user_by_alipay_id(alipay_user_id)
    if existing:
        token = auth.create_access_token(existing["id"], existing["username"])
        return RedirectResponse(f"/?token={token}&username={existing['username']}")

    existing_by_name = db.get_user_by_username(nick_name)
    if existing_by_name:
        db.link_alipay_account(existing_by_name["id"], alipay_user_id, avatar_url)
        token = auth.create_access_token(existing_by_name["id"], existing_by_name["username"])
        return RedirectResponse(f"/?token={token}&username={existing_by_name['username']}")

    user_id = str(uuid.uuid4())[:12]
    new_user = db.create_alipay_user(user_id, nick_name, alipay_user_id, avatar_url)
    if not new_user:
        raise HTTPException(status_code=500, detail="创建用户失败")

    token = auth.create_access_token(new_user["id"], new_user["username"])
    return RedirectResponse(f"/?token={token}&username={new_user['username']}")


# ── Taobao OAuth ──────────────────────────────────────────

TAOBAO_AUTHORIZE_URL = "https://oauth.taobao.com/authorize"
TAOBAO_TOKEN_URL = "https://oauth.taobao.com/token"


@app.get("/api/auth/taobao")
def taobao_login():
    if not config.TAOBAO_APP_KEY:
        raise HTTPException(status_code=500, detail="淘宝 OAuth 未配置")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = datetime.now().timestamp()
    params = {
        "client_id": config.TAOBAO_APP_KEY,
        "response_type": "code",
        "redirect_uri": config.TAOBAO_CALLBACK_URL,
        "state": state,
        "view": "web",
    }
    return RedirectResponse(f"{TAOBAO_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/api/auth/taobao/callback")
async def taobao_callback(code: str, state: str):
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="无效的 state 参数")
    _oauth_states.pop(state)

    import httpx as _httpx

    # Exchange code for access_token
    async with _httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            TAOBAO_TOKEN_URL,
            data={
                "client_id": config.TAOBAO_APP_KEY,
                "client_secret": config.TAOBAO_APP_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.TAOBAO_CALLBACK_URL,
            },
        )
        token_data = token_resp.json()

    access_token = token_data.get("access_token")
    taobao_user_id = str(token_data.get("taobao_user_id", ""))
    taobao_nick = token_data.get("taobao_nick", "")
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=f"淘宝授权失败: {token_data.get('error_description', '未知错误')}"
        )

    # Decode nick (Taobao returns URL-encoded Chinese)
    from urllib.parse import unquote
    nick_name = unquote(taobao_nick) if taobao_nick else f"taobao_{taobao_user_id[:6]}"

    # Find or create user
    existing = db.get_user_by_taobao_id(taobao_user_id)
    if existing:
        token = auth.create_access_token(existing["id"], existing["username"])
        return RedirectResponse(f"/?token={token}&username={existing['username']}")

    existing_by_name = db.get_user_by_username(nick_name)
    if existing_by_name:
        db.link_taobao_account(existing_by_name["id"], taobao_user_id)
        token = auth.create_access_token(existing_by_name["id"], existing_by_name["username"])
        return RedirectResponse(f"/?token={token}&username={existing_by_name['username']}")

    user_id = str(uuid.uuid4())[:12]
    new_user = db.create_taobao_user(user_id, nick_name, taobao_user_id)
    if not new_user:
        raise HTTPException(status_code=500, detail="创建用户失败")

    token = auth.create_access_token(new_user["id"], new_user["username"])
    return RedirectResponse(f"/?token={token}&username={new_user['username']}")


# ── 旧版规划接口（保留向后兼容） ──────────────────────────

@app.post("/api/plan")
def api_plan(req: PlanRequest, user: dict = Depends(get_current_user)):
    if not req.city:
        raise HTTPException(status_code=400, detail="city 必填")
    return planner.plan(req.dict())


# ── 对话式规划 ────────────────────────────────────────────

@app.post("/api/chat")
def api_chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    # Save user message to conversation history
    db.save_conversation(user["id"], "user", req.message)

    # 传递 UI 已选择的参数给 LLM，避免重复追问（预算始终传递，即使是默认值舒适型）
    ui_selected = {
        "budget_tier": req.budget_tier,
        "transport_mode": req.transport_mode if req.transport_mode != "auto" else None,
        "preferences": req.preferences if req.preferences else None,
    }
    result = chat_mod.extract_params(req.message, req.conversation, user_id=user["id"],
                                     ui_selected=ui_selected)
    extracted = result.get("extracted", {})
    sufficient = result.get("sufficient", False)
    follow_up = result.get("follow_up", "")

    if not extracted.get("budget_tier"):
        # 抽取漏掉时，先按消息原文关键词兜底，再用 UI 选择值
        tier_match = re.search(r"(经济型|舒适型|豪华型)", req.message)
        extracted["budget_tier"] = tier_match.group(1) if tier_match else req.budget_tier
    if not extracted.get("transport_mode") or extracted.get("transport_mode") == "auto":
        extracted["transport_mode"] = req.transport_mode
    if not extracted.get("preferences") and req.preferences:
        extracted["preferences"] = req.preferences

    if sufficient:
        from . import structured_planner
        plan_input = chat_mod.params_to_plan_input(extracted)
        plan_result = planner.plan(plan_input)

        sections = [(s["title"], s["data"]) for s in plan_result.get("sections", [])]
        now = datetime.now().isoformat()
        plan_id = str(uuid.uuid4())[:12]
        structured = structured_planner.generate_structured_plan(extracted, sections, user_id=user["id"], plan_id=plan_id)
        plan_dict = {
            "id": plan_id,
            "user_id": user["id"],
            "created_at": now,
            "updated_at": now,
            "phase": "planning",
            "original_input": req.message,
            "conversation": req.conversation + [
                {"role": "user", "content": req.message},
                {"role": "assistant", "content": "方案已生成"},
            ],
            "params": extracted,
            "summary": structured.get("summary", plan_result.get("markdown", "")[:200]),
            "action_items": structured.get("action_items", []),
            "timeline": structured.get("timeline", []),
            "budget": structured.get("budget", []),
            "room_allocation": structured.get("room_allocation", []),
            "budget_per_person": structured.get("budget_per_person"),
            "share_code": None,
            "members": [],
            "markdown": plan_result.get("markdown", ""),
            "warnings": plan_result.get("warnings", []),
            "used_llm": plan_result.get("used_llm", False),
        }
        db.save_plan(plan_dict)
        # Save assistant response
        db.save_conversation(user["id"], "assistant", "方案已生成")
        return {
            "type": "plan_ready",
            "message": "方案已生成！",
            "extracted_params": extracted,
            "plan_id": plan_id,
            "plan": plan_result,
            "structured": structured,
        }

    # Save follow-up question
    db.save_conversation(user["id"], "assistant", follow_up)
    # 根据距离给出交通推荐：近距离直接推荐火车/飞机，中距离才需要用户选择
    transport_suggested = None
    _origin = extracted.get("origin")
    _city = extracted.get("city")
    if _origin and _city and _origin != _city:
        from . import transport as transport_mod
        transport_suggested = transport_mod.recommend_transport(_origin, _city)["suggested"]
    return {
        "type": "follow_up",
        "message": follow_up,
        "extracted_params": extracted,
        "sufficient": False,
        "transport_suggested": transport_suggested,
    }


# ── 方案检索与实施跟踪 ────────────────────────────────────

@app.get("/api/plan/{plan_id}")
def api_get_plan(plan_id: str, user: dict = Depends(get_current_user)):
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan


@app.delete("/api/plan/{plan_id}")
def api_delete_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """删除方案。"""
    success = db.delete_plan(plan_id, user_id=user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"message": "方案已删除"}


@app.put("/api/plan/{plan_id}/task/{task_id}")
def api_update_task(plan_id: str, task_id: str, req: TaskUpdateRequest, user: dict = Depends(get_current_user)):
    plan = db.update_task_status(plan_id, task_id, req.status, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案或任务不存在")
    return plan


@app.post("/api/plan/{plan_id}/revise")
def api_revise_plan(plan_id: str, req: ReviseRequest, user: dict = Depends(get_current_user)):
    """对话式修改方案：基于用户反馈更新结构化方案。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")

    from . import structured_planner

    current_structured = {
        "summary": plan.get("summary", ""),
        "action_items": plan.get("action_items", []),
        "timeline": plan.get("timeline", []),
        "budget": plan.get("budget", []),
        "room_allocation": plan.get("room_allocation", []),
        "budget_per_person": plan.get("budget_per_person", ""),
    }
    params = plan.get("params", {})

    revised = structured_planner.revise_plan(params, current_structured, req.message, user_id=user["id"], plan_id=plan_id)
    if not revised:
        raise HTTPException(status_code=500, detail="修改失败，请重试")

    now = datetime.now().isoformat()
    plan["summary"] = revised.get("summary", plan.get("summary", ""))
    plan["action_items"] = revised.get("action_items", plan.get("action_items", []))
    plan["timeline"] = revised.get("timeline", plan.get("timeline", []))
    plan["budget"] = revised.get("budget", plan.get("budget", []))
    plan["room_allocation"] = revised.get("room_allocation", plan.get("room_allocation", []))
    plan["budget_per_person"] = revised.get("budget_per_person", plan.get("budget_per_person", ""))
    plan["updated_at"] = now
    db.save_plan(plan)

    db.save_conversation(user["id"], "user", req.message)
    db.save_conversation(user["id"], "assistant", f"已修改方案：{revised.get('summary', '')[:100]}")

    return {
        "type": "revised",
        "message": "方案已修改",
        "plan_id": plan_id,
        "structured": revised,
    }


# ── 修订历史 ──────────────────────────────────────────────

@app.get("/api/plan/{plan_id}/revisions")
def api_list_revisions(plan_id: str, user: dict = Depends(get_current_user)):
    """获取方案的修订历史。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return db.get_plan_revisions(plan_id)


@app.post("/api/plan/{plan_id}/rollback/{revision_id}")
def api_rollback_plan(plan_id: str, revision_id: int, user: dict = Depends(get_current_user)):
    """回滚到指定修订版本。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    
    revision = db.get_plan_revision(revision_id)
    if not revision or revision["plan_id"] != plan_id:
        raise HTTPException(status_code=404, detail="修订版本不存在")
    
    # Save current version as a revision before rollback
    current_revision_count = len(db.get_plan_revisions(plan_id))
    db.save_plan_revision(
        plan_id, 
        current_revision_count + 1, 
        f"回滚前快照 (回滚到版本 {revision['revision_number']})",
        plan
    )
    
    # Perform rollback
    updated_plan = db.rollback_plan(plan_id, revision_id, user_id=user["id"])
    if not updated_plan:
        raise HTTPException(status_code=500, detail="回滚失败")
    
    db.save_conversation(user["id"], "assistant", f"已回滚到版本 {revision['revision_number']}")
    
    return {
        "message": f"已回滚到版本 {revision['revision_number']}",
        "plan": updated_plan,
    }


@app.post("/api/plan/{plan_id}/share")
def api_share_plan(plan_id: str, user: dict = Depends(get_current_user)):
    code = db.generate_share_code(plan_id, user_id=user["id"])
    if not code:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"share_code": code, "share_url": f"/share/{code}"}


@app.get("/api/share/{code}")
def api_get_shared(code: str):
    """分享页面：无需登录，通过分享码访问。"""
    plan = db.get_plan_by_share(code)
    if not plan:
        raise HTTPException(status_code=404, detail="分享码无效")
    return plan


def _recommend_restaurants(plan: dict, item_index: int) -> dict:
    """为时间线中的用餐项推荐 2-3 家附近餐厅，结果持久化到 timeline 项里。"""
    from . import flyai_client

    timeline = plan.get("timeline") or []
    if item_index < 0 or item_index >= len(timeline):
        raise HTTPException(status_code=404, detail="时间线项目不存在")
    item = timeline[item_index]

    params = plan.get("params") or {}
    city = params.get("city", "")
    group = re.sub(r"[^\d]", "", str(params.get("group_size", ""))) or "团体"
    budget_hint = {
        "经济型": "人均100元以内",
        "舒适型": "人均100-200元",
        "豪华型": "人均200元以上",
    }.get(params.get("budget_tier"), "人均100-200元")

    # 从用餐项的地点/标题提取商圈关键词（多商圈只取第一个，斜杠会干扰飞猪检索）
    raw = item.get("location") or item.get("title") or ""
    area = re.sub(r"午餐|晚餐|早餐|用餐|：|:|团体|集中|聚餐|建议.*$|，.*$", "", raw)
    area = re.sub(r"(餐厅|中档|高档|经济型|周边|附近|片区)+", "", area)
    area = area.split("/")[0].strip(" /、，")

    query = f"{city}{area}附近适合{group}人团体聚餐的热门餐厅 {budget_hint}"
    options = []
    for _attempt in range(2):  # flyai 餐厅数据偶发为空，取两次中成功的一次
        result = flyai_client.ai_search(query)
        text = result.get("data", "") if isinstance(result, dict) else str(result)
        options = flyai_client.parse_restaurants(text)
        if options:
            break

    item["restaurant_options"] = options
    item["restaurant_options_note"] = None if options else "飞猪暂未找到符合条件的餐厅，建议扩大商圈或直接电话预订"
    plan["timeline"] = timeline
    plan["updated_at"] = datetime.now().isoformat()
    db.save_plan(plan)
    return {"options": options, "note": item["restaurant_options_note"], "query": query}


@app.post("/api/plan/{plan_id}/recommend_restaurants")
def api_recommend_restaurants(plan_id: str, req: RestaurantRecommendRequest,
                              user: dict = Depends(get_current_user)):
    """为方案中某个用餐项推荐附近餐厅（需登录，仅方案所有者）。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return _recommend_restaurants(plan, req.item_index)


@app.post("/api/share/{code}/recommend_restaurants")
def api_share_recommend_restaurants(code: str, req: RestaurantRecommendRequest):
    """分享页访客也可触发餐厅推荐（只能通过分享码操作该方案）。"""
    plan = db.get_plan_by_share(code)
    if not plan:
        raise HTTPException(status_code=404, detail="分享码无效")
    return _recommend_restaurants(plan, req.item_index)


@app.get("/api/plans")
def api_list_plans(user: dict = Depends(get_current_user)):
    return db.list_plans(user["id"])


@app.get("/api/conversations")
def api_get_conversations(user: dict = Depends(get_current_user)):
    return db.get_conversations(user["id"])


@app.delete("/api/conversations")
def api_clear_conversations(user: dict = Depends(get_current_user)):
    db.clear_conversations(user["id"])
    return {"ok": True}


# ── 飞猪原始查询 ──────────────────────────────────────────

@app.post("/api/search")
def api_search(req: SearchRequest, user: dict = Depends(get_current_user)):
    from . import flyai_client

    fn = getattr(flyai_client, req.command.replace("-", "_"), None)
    if fn is None or not callable(fn):
        raise HTTPException(status_code=400, detail=f"不支持的 command: {req.command}")
    try:
        return fn(**req.options)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"参数错误: {e}")
