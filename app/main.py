"""FastAPI 应用入口：页面、认证、规划、对话、实施跟踪接口。"""
import os
import uuid
import secrets
from datetime import datetime
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse, RedirectResponse
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


class SearchRequest(BaseModel):
    command: str = "ai-search"
    options: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    plan_id: str | None = None
    conversation: list[dict] = Field(default_factory=list)
    budget_tier: str = "舒适型"


class TaskUpdateRequest(BaseModel):
    status: str


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


# ── 页面 ──────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


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

    result = chat_mod.extract_params(req.message, req.conversation)
    extracted = result.get("extracted", {})
    sufficient = result.get("sufficient", False)
    follow_up = result.get("follow_up", "")

    if not extracted.get("budget_tier"):
        extracted["budget_tier"] = req.budget_tier

    if sufficient:
        from . import structured_planner
        plan_input = chat_mod.params_to_plan_input(extracted)
        plan_result = planner.plan(plan_input)

        sections = [(s["title"], s["data"]) for s in plan_result.get("sections", [])]
        structured = structured_planner.generate_structured_plan(extracted, sections)

        now = datetime.now().isoformat()
        plan_id = str(uuid.uuid4())[:12]
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
    return {
        "type": "follow_up",
        "message": follow_up,
        "extracted_params": extracted,
        "sufficient": False,
    }


# ── 方案检索与实施跟踪 ────────────────────────────────────

@app.get("/api/plan/{plan_id}")
def api_get_plan(plan_id: str, user: dict = Depends(get_current_user)):
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan


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

    revised = structured_planner.revise_plan(params, current_structured, req.message)
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
