"""FastAPI 应用入口：提供页面与规划接口。"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, planner

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="聚行 · 飞猪 AI 旅行助手")


class PlanRequest(BaseModel):
    mode: str = "event"          # event（活动/聚会出行） | explore（目的地探索）
    city: str
    check_in: str = ""
    check_out: str = ""
    origin: str = ""
    group_size: str = ""
    vibe: str = ""


class SearchRequest(BaseModel):
    command: str = "ai-search"   # ai-search | keyword-search | search-hotel | search-poi | search-flight
    options: dict = Field(default_factory=dict)


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/api/health")
def api_health():
    """诊断用：确认运行中的进程是否加载到 Key（不返回明文）。"""
    from . import flyai_client

    return {
        "flyai_key_loaded": bool(config.FLYAI_API_KEY),
        "dashscope_key_loaded": bool(config.DASHSCOPE_API_KEY),
        "flyai_prefix": config.resolve_flyai_prefix(),
    }


@app.post("/api/plan")
def api_plan(req: PlanRequest):
    if not req.city:
        raise HTTPException(status_code=400, detail="city 必填")
    return planner.plan(req.dict())


@app.post("/api/search")
def api_search(req: SearchRequest):
    from . import flyai_client

    fn = getattr(flyai_client, req.command.replace("-", "_"), None)
    if fn is None or not callable(fn):
        raise HTTPException(status_code=400, detail=f"不支持的 command: {req.command}")
    try:
        return fn(**req.options)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"参数错误: {e}")
