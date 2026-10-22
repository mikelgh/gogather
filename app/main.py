"""FastAPI 应用入口：页面、认证、规划、对话、实施跟踪接口。"""
import os
import re
import uuid
import secrets
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx

from . import config, planner, chat as chat_mod
from . import database as db
from . import auth

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

db.init_db()

log = logging.getLogger(__name__)

app = FastAPI(title="聚行 · 飞猪 AI 旅行助手")

# 静态资源统一挂载（brand.css / copy.js / logo / 新增图片等）
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)


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


class GeneratePlanRequest(BaseModel):
    """用户确认行程大纲后，按确认框架生成正式方案的请求。"""
    message: str
    params: dict = Field(default_factory=dict)
    outline: dict | None = None
    conversation: list[dict] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    status: str


class RestaurantRecommendRequest(BaseModel):
    item_index: int


class ReviseRequest(BaseModel):
    message: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    captcha_verify_param: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class UsernameRequest(BaseModel):
    username: str


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


# Admin whitelist - only this account can access admin endpoints（按 user id 判定，改昵称不会掉管理员）
ADMIN_USER_IDS = {"32798410-ccb"}


def get_admin_user(authorization: str | None = Header(None)) -> dict:
    """管理员认证：仅允许白名单内的账号。"""
    user = get_current_user(authorization)
    if user["id"] not in ADMIN_USER_IDS:
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

@app.exception_handler(404)
async def not_found(request: Request, exc):
    """未匹配的路径：接口与静态资源仍返回 404，页面走品牌 404。"""
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/static/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(
        os.path.join(BASE_DIR, "static", "404.html"),
        status_code=404,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/")
def index():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/favicon.ico")
def favicon():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "favicon.ico"),
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/logo.svg")
def logo():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "logo.svg"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/og-cover.png")
def og_cover():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "og-cover.png"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/map_component.js")
def map_component_js():
    return FileResponse(
        os.path.join(BASE_DIR, "static", "map_component.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/share/{code}/cover.png")
def share_cover(code: str):
    """动态生成分享封面图。"""
    from fastapi.responses import Response
    from . import cover_generator
    
    plan = db.get_plan_by_share(code)
    if not plan:
        # 返回默认封面
        return FileResponse(
            os.path.join(BASE_DIR, "static", "og-cover.png"),
            media_type="image/png",
        )
    
    params = plan.get("params") or {}
    city = params.get("city", "")
    origin = params.get("origin", "")
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")
    group_size = params.get("group_size", "")
    summary = plan.get("summary", "")
    
    date_range = ""
    if check_in and check_out:
        date_range = f"{check_in[5:]} ~ {check_out[5:]}"
    
    # 生成封面图（优先使用 AI 背景）
    png_data = cover_generator.generate_cover_with_ai(
        city=city,
        date_range=date_range,
        group_size=group_size,
        summary=summary,
        origin=origin,
        use_ai=True,
    )
    
    return Response(
        content=png_data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/share/{code}")
def share_page(code: str):
    """分享页：服务端渲染 OpenGraph meta 标签，供微信等社交平台抓取。"""
    from fastapi.responses import HTMLResponse
    
    plan = db.get_plan_by_share(code)
    html_path = os.path.join(BASE_DIR, "static", "share.html")
    
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    if plan:
        params = plan.get("params") or {}
        city = params.get("city", "")
        origin = params.get("origin", "")
        check_in = params.get("check_in", "")
        check_out = params.get("check_out", "")
        group_size = params.get("group_size", "")
        summary = plan.get("summary", "")
        
        date_range = ""
        if check_in and check_out:
            date_range = f"{check_in[5:]} ~ {check_out[5:]}"
        
        title = f"{origin}→{city} 出行方案" if origin and city else f"{city} 出行方案" if city else "出行方案"
        og_title = f"GoGather 聚行 · {title}"
        # 确保 group_size 是字符串
        group_size_str = str(group_size) if group_size else ""
        group_label = group_size_str if group_size_str and "人" in group_size_str else f"{group_size_str}人" if group_size_str else ""
        desc_parts = [date_range, group_label]
        desc = " · ".join(filter(None, desc_parts)) or summary or "AI 生成的出行方案"
        # 替换 meta 标签
        html = html.replace(
            '<meta property="og:title" content="GoGather 聚行 · 出行方案">',
            f'<meta property="og:title" content="{og_title}">'
        )
        html = html.replace(
            '<meta property="og:description" content="AI 生成的出行方案，含实时价格与预订链接">',
            f'<meta property="og:description" content="{desc}">'
        )
        html = html.replace(
            '<meta property="og:image" content="/og-cover.png">',
            f'<meta property="og:image" content="/share/{code}/cover.png">'
        )
        html = html.replace(
            '<title>GoGather 聚行 · 方案分享</title>',
            f'<title>GoGather 聚行 · {title}</title>'
        )
    
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})


@app.get("/map/{code}")
def fullscreen_map_page(code: str):
    """全屏地图页：左侧地图，右侧地点列表。"""
    plan = db.get_plan_by_share(code)
    if not plan:
        raise HTTPException(status_code=404, detail="分享码无效")
    return FileResponse(
        os.path.join(BASE_DIR, "static", "map.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "admin.html"))


@app.get("/guide")
def guide_page():
    """功能说明页：公开可访问，未登录也能看。"""
    return FileResponse(
        os.path.join(BASE_DIR, "static", "guide.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── 诊断 ──────────────────────────────────────────────────

@app.get("/api/health")
def api_health():
    return {
        "flyai_key_loaded": bool(config.FLYAI_API_KEY),
        "dashscope_key_loaded": bool(config.DASHSCOPE_API_KEY),
        "flyai_prefix": config.resolve_flyai_prefix(),
    }


# ── 认证 ──────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/api/public-config")
def api_public_config():
    """前端公开配置（是否启用验证码及场景 ID，均非敏感信息）。"""
    from . import captcha as captcha_mod

    return {
        "captcha_enabled": captcha_mod.captcha_enabled(),
        "captcha_scene_id": config.get_env("ALIYUN_CAPTCHA_SCENE_ID"),
        "captcha_prefix": config.get_env("ALIYUN_CAPTCHA_PREFIX"),
    }


@app.post("/api/register")
def api_register(req: RegisterRequest, request: Request):
    from . import captcha as captcha_mod
    from . import ratelimit

    ip = _client_ip(request)
    if not ratelimit.hit(f"register:{ip}", ratelimit.REGISTER_LIMIT, ratelimit.REGISTER_WINDOW_S):
        raise HTTPException(status_code=429, detail="注册过于频繁，请一小时后再试")
    if captcha_mod.captcha_enabled() and not captcha_mod.verify_captcha(req.captcha_verify_param):
        raise HTTPException(status_code=403, detail="人机验证未通过，请重试")

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
def api_login(req: LoginRequest, request: Request):
    from . import ratelimit

    ip = _client_ip(request)
    if not ratelimit.hit(f"login:{ip}", ratelimit.LOGIN_LIMIT, ratelimit.LOGIN_WINDOW_S):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请15分钟后再试")
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
    row = db.get_user_by_id(user["id"]) or {}
    return {
        "id": user["id"],
        "username": user["username"],
        "github_bound": bool(row.get("github_id")),
        "alipay_bound": bool(row.get("alipay_id")),
        "avatar_url": row.get("avatar_url") or "",
    }


# \w 已覆盖中英文/数字/下划线；昵称会出现在 OAuth 回跳 URL 与管理后台表格里，限制字符集可避免注入
USERNAME_RE = re.compile(r"^[\w.-]{2,20}$")


@app.patch("/api/me/username")
def api_update_username(req: UsernameRequest, user: dict = Depends(get_current_user)):
    name = (req.username or "").strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(status_code=400, detail="昵称需为 2-20 位中英文、数字、下划线、点或减号")
    if name == user["username"]:
        return {"id": user["id"], "username": name, "token": auth.create_access_token(user["id"], name)}
    if db.get_user_by_username(name):
        raise HTTPException(status_code=409, detail="该昵称已被占用，换一个试试")
    if not db.update_username(user["id"], name):
        raise HTTPException(status_code=409, detail="昵称修改失败，该昵称可能已被占用")
    # 令牌里带旧昵称，重新签发让前端立刻生效
    return {"id": user["id"], "username": name, "token": auth.create_access_token(user["id"], name)}


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

    # 服务器侧访问 github.com 间歇性被阻断（浏览器侧是通的），换 token 失败时
    # 重试几次，仍失败则带错误参数跳回首页，避免用户直接看到 500。
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            token_resp = None
            for attempt in range(3):
                try:
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
                    break
                except httpx.RequestError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1)
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
    except httpx.RequestError as e:
        log.warning("GitHub OAuth 回调网络异常: %s", e)
        return RedirectResponse("/?auth_error=github")

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
        raise HTTPException(status_code=400, detail="无效的 state 参数（登录会话已过期，请重新扫码）")
    _oauth_states.pop(state)

    from . import alipay_oauth
    import httpx as _httpx

    def _fail(status: int, stage: str, data: dict) -> None:
        err = (data or {}).get("error_response", {}) or {}
        detail = err.get("sub_msg") or err.get("msg") or "未知错误"
        log.warning("支付宝登录失败[%s]: %s | resp=%s", stage, detail, str(data)[:300])
        raise HTTPException(status_code=status, detail=f"{stage}失败: {detail}")

    # Exchange auth_code for access_token
    token_params = alipay_oauth.build_token_params(
        config.ALIPAY_APP_ID, auth_code, config.ALIPAY_PRIVATE_KEY
    )
    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.get(
                "https://openapi.alipay.com/gateway.do",
                params=token_params,
            )
            token_data = token_resp.json()
    except Exception as e:
        log.warning("支付宝 token 接口调用异常: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail="支付宝接口调用失败，请稍后重试")

    alipay_response = token_data.get("alipay_system_oauth_token_response", {})
    access_token = alipay_response.get("access_token")
    alipay_user_id = alipay_response.get("user_id")
    if not access_token:
        _fail(400, "支付宝授权", token_data)

    # Get user info
    userinfo_params = alipay_oauth.build_userinfo_params(
        config.ALIPAY_APP_ID, access_token, config.ALIPAY_PRIVATE_KEY
    )
    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            info_resp = await client.get(
                "https://openapi.alipay.com/gateway.do",
                params=userinfo_params,
            )
            info_data = info_resp.json()
    except Exception as e:
        log.warning("支付宝用户信息接口调用异常: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail="支付宝接口调用失败，请稍后重试")

    user_info = info_data.get("alipay_user_info_share_response", {})
    if not user_info:
        _fail(400, "获取支付宝用户信息", info_data)
    # 新版 OAuth 2.0（开放平台应用选择 open_id 作为用户标识）只返回 open_id，没有 user_id
    alipay_open_id = alipay_response.get("open_id") or user_info.get("open_id") or ""
    alipay_user_id = user_info.get("user_id") or alipay_user_id or alipay_open_id
    if not alipay_user_id:
        # 缺 user_id 时旧代码会对 None 切片抛 TypeError（前端只看到 Internal Server Error）
        log.warning(
            "支付宝登录失败[用户标识缺失]: token_resp=%s userinfo_resp=%s",
            str(alipay_response)[:300], str(user_info)[:300],
        )
        reason = user_info.get("sub_msg") or user_info.get("msg") or "接口未返回 user_id"
        raise HTTPException(
            status_code=400,
            detail=f"支付宝未返回用户标识（{reason}），请确认应用已开通 auth_user 授权及用户信息接口权限",
        )
    nick_name = (user_info.get("nick_name") or user_info.get("nick") or "").strip()
    if not nick_name:
        nick_name = f"alipay_{alipay_user_id[-6:]}"
    avatar_url = user_info.get("avatar") or ""

    # Find or create user
    existing = db.get_user_by_alipay_id(alipay_user_id)
    if existing:
        token = auth.create_access_token(existing["id"], existing["username"])
        return RedirectResponse(f"/?token={token}&username={existing['username']}")

    existing_by_name = db.get_user_by_username(nick_name)
    if existing_by_name:
        # 绑定失败说明该支付宝已绑到别的账号，此时不能再登录进同名账号
        if not db.link_alipay_account(existing_by_name["id"], alipay_user_id, avatar_url):
            raise HTTPException(
                status_code=409,
                detail=f"该支付宝账号已绑定到其他用户，无法合并到「{nick_name}」",
            )
        token = auth.create_access_token(existing_by_name["id"], existing_by_name["username"])
        return RedirectResponse(f"/?token={token}&username={existing_by_name['username']}")

    user_id = str(uuid.uuid4())[:12]
    new_user = db.create_alipay_user(user_id, nick_name, alipay_user_id, avatar_url)
    if not new_user:
        raise HTTPException(status_code=409, detail="创建用户失败（用户名或支付宝账号已被占用）")

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

def _first_user_message(conversation: list, default: str) -> str:
    """取会话中第一条用户消息，作为方案的原始诉求。"""
    for m in conversation or []:
        if m.get("role") == "user" and m.get("content"):
            return m["content"]
    return default


def _build_plan_ready(extracted: dict, original_message: str, conversation: list,
                      user_id: str, outline: dict | None = None) -> dict:
    """跑完整生成链路（flyai → 结构化方案）并落库，返回 plan_ready 响应。"""
    from . import structured_planner
    plan_input = chat_mod.params_to_plan_input(extracted)
    plan_result = planner.plan(plan_input)

    sections = [(s["title"], s["data"]) for s in plan_result.get("sections", [])]
    now = datetime.now().isoformat()
    plan_id = str(uuid.uuid4())[:12]
    structured = structured_planner.generate_structured_plan(
        extracted, sections, user_id=user_id, plan_id=plan_id, outline=outline)
    plan_dict = {
        "id": plan_id,
        "user_id": user_id,
        "created_at": now,
        "updated_at": now,
        "phase": "planning",
        # 存用户最初的诉求，而不是大纲调整轮里的最后一句（如"第三天换个景点"）
        "original_input": _first_user_message(conversation, original_message),
        "conversation": conversation + [
            {"role": "user", "content": original_message},
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
        "data_captured_at": plan_result.get("data_captured_at", now),
    }
    db.save_plan(plan_dict)
    share_code = db.generate_share_code(plan_id, user_id=user_id)
    # plan_ready 立即带出分享码，前端可直接渲染 AI 封面（否则首次展示只有占位图）
    if share_code:
        plan_result["share_code"] = share_code
    db.save_conversation(user_id, "assistant", "方案已生成")
    return {
        "type": "plan_ready",
        "message": "方案已生成！",
        "extracted_params": extracted,
        "plan_id": plan_id,
        "plan": plan_result,
        "structured": structured,
    }


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
        # 先用快速模型出行程大纲供用户确认；确认后由 /api/generate_plan 正式生成。
        # 大纲生成失败时回退为直接生成（不阻塞主流程）。
        from . import structured_planner
        try:
            outline = structured_planner.generate_outline(
                extracted, conversation=req.conversation, user_id=user["id"])
        except Exception:
            outline = None
        if outline:
            outline_text = structured_planner.outline_to_text(outline)
            db.save_conversation(user["id"], "assistant", outline_text,
                                 meta={"type": "outline", "outline": outline})
            return {
                "type": "outline_proposal",
                "message": outline_text,
                "outline": outline,
                "extracted_params": extracted,
            }
        return _build_plan_ready(
            extracted, req.message,
            req.conversation + [{"role": "user", "content": req.message}],
            user["id"],
        )

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


@app.post("/api/generate_plan")
def api_generate_plan(req: GeneratePlanRequest, user: dict = Depends(get_current_user)):
    """用户确认行程大纲后，按确认的框架生成正式方案。"""
    extracted = dict(req.params)
    if not extracted.get("budget_tier"):
        extracted["budget_tier"] = "舒适型"
    if not extracted.get("transport_mode"):
        extracted["transport_mode"] = "auto"
    return _build_plan_ready(
        extracted, req.message,
        req.conversation + [{"role": "user", "content": req.message}],
        user["id"], outline=req.outline,
    )


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


@app.post("/api/plan/{plan_id}/regenerate")
def api_regenerate_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """按原输入条件重新生成方案（不沿用旧大纲，产出新方案）。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    params = plan.get("params") or {}
    if not params.get("city"):
        raise HTTPException(status_code=400, detail="该方案缺少出行参数，无法重新生成")
    return _build_plan_ready(
        params,
        plan.get("original_input") or "",
        plan.get("conversation") or [],
        user["id"],
    )


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

    # 修改前保存当前版本快照（与回滚端点的"回滚前快照"同一约定，修订历史/回滚依赖此记录）
    revision_count = len(db.get_plan_revisions(plan_id))
    db.save_plan_revision(
        plan_id,
        revision_count + 1,
        f"修改前快照（{req.message}）",
        plan,
    )

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


# ── 票价验价 ──────────────────────────────────────────────

@app.post("/api/plan/{plan_id}/verify_prices")
def api_verify_prices(plan_id: str, user: dict = Depends(get_current_user)):
    """对方案中的航班/车次重新发起飞猪查询，返回价格对比报告（不修改方案）。"""
    from . import price_check
    from . import flyai_client

    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    report = price_check.verify_plan_prices(plan, flyai_client)
    if not report["items"]:
        return {"type": "verify_prices", "message": "方案中没有可验价的航班/车次项", **report}
    return {"type": "verify_prices", "message": "验价完成", **report}


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


def _map_payload(plan: dict) -> dict:
    """方案地图数据：带坐标时间线 + 标题（供方案页/分享页/全屏地图页共用）。"""
    from . import geocode as geocode_mod

    timeline = plan.get("timeline", [])
    params = plan.get("params") or {}
    city = params.get("city", "")
    origin = params.get("origin", "")
    title = f"{origin} → {city}" if origin and city else (city or "出行方案")
    return {
        "city": city,
        "title": title,
        "timeline": geocode_mod.geocode_timeline(timeline, city),
    }


@app.get("/api/plan/{plan_id}/map")
def api_plan_map(plan_id: str, user: dict = Depends(get_current_user)):
    """返回带地理坐标的时间线，用于地图渲染。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return _map_payload(plan)


@app.get("/api/share/{code}/map")
def api_share_map(code: str):
    """分享页地图数据（无需登录）。"""
    plan = db.get_plan_by_share(code)
    if not plan:
        raise HTTPException(status_code=404, detail="分享码无效")
    return _map_payload(plan)


@app.get("/api/plan/{plan_id}/reminders")
def api_plan_reminders(plan_id: str, user: dict = Depends(get_current_user)):
    """返回方案的提醒列表（基于时间线生成）。"""
    plan = db.get_plan(plan_id, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return _build_reminders(plan)


@app.get("/api/share/{code}/reminders")
def api_share_reminders(code: str):
    """分享页提醒列表（无需登录）。"""
    plan = db.get_plan_by_share(code)
    if not plan:
        raise HTTPException(status_code=404, detail="分享码无效")
    return _build_reminders(plan)


def _build_reminders(plan: dict) -> dict:
    """从时间线生成提醒列表。"""
    import re
    from datetime import datetime, timedelta

    timeline = plan.get("timeline", [])
    params = plan.get("params", {})
    check_in = params.get("check_in", "")
    reminders = []

    for item in timeline:
        time_range = item.get("time_range", "")
        title = item.get("title", "")
        # 解析 Day N HH:MM
        m = re.match(r"Day\s*(\d+)\s+(\d{1,2}:\d{2})", time_range, re.IGNORECASE)
        if not m:
            continue
        day_num = int(m.group(1))
        time_str = m.group(2)
        # 计算实际日期
        if check_in:
            try:
                base = datetime.fromisoformat(check_in)
                actual_date = base + timedelta(days=day_num - 1)
                date_str = actual_date.strftime("%m-%d")
            except:
                date_str = f"Day{day_num}"
        else:
            date_str = f"Day{day_num}"

        # 生成提醒文本
        reminder_text = f"{date_str} {time_str} {title}"
        reminders.append({
            "time": f"{date_str} {time_str}",
            "title": title,
            "text": reminder_text,
            "action_item_id": item.get("action_item_id"),
        })

    return {"reminders": reminders, "total": len(reminders)}


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
