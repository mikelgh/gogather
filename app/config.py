"""配置与运行环境解析（API Key、模型、flyai 可执行文件探测）"""
import glob
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 自动加载项目根目录 .env（FLYAI_API_KEY / DASHSCOPE_API_KEY 等），
# 明确指向 BASE_DIR/.env，避免「从别的目录启动 uvicorn 导致读不到 Key」。
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except Exception:
    pass


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


DASHSCOPE_API_KEY = get_env("DASHSCOPE_API_KEY", "")
FLYAI_API_KEY = get_env("FLYAI_API_KEY", "")
QWEN_MODEL = get_env("QWEN_MODEL", "qwen-plus")
QWEN_BASE_URL = get_env(
    "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# GitHub OAuth
GITHUB_CLIENT_ID = get_env("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = get_env("GITHUB_CLIENT_SECRET", "")
GITHUB_CALLBACK_URL = get_env(
    "GITHUB_CALLBACK_URL", "https://gogather.partystack.cn/api/auth/github/callback"
)

# MiniMax 配置（可选，LLM_PROVIDER=minimax 时启用）
MINIMAX_API_KEY = get_env("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = get_env("MINIMAX_BASE_URL", "https://api.minimax.cn/v1")
MINIMAX_MODEL = get_env("MINIMAX_MODEL", "MiniMax-M3")

# LLM 供应商选择：qwen（默认）或 minimax
LLM_PROVIDER = get_env("LLM_PROVIDER", "qwen")


def resolve_flyai_prefix() -> list:
    """返回调用 flyai 的命令前缀（列表形式，便于 subprocess 安全传参）。"""
    override = get_env("FLYAI_BIN", "")
    if override:
        return override.split()
    local = os.path.join(BASE_DIR, "node_modules", ".bin", "flyai")
    if sys.platform.startswith("win"):
        local_win = local + ".cmd"
        if os.path.exists(local_win):
            return [local_win]
    else:
        if os.path.exists(local):
            return [local]
    # 回退：通过 npx 拉取（首次会下载，略慢）
    if sys.platform.startswith("win"):
        npx = shutil.which("npx.cmd") or shutil.which("npx")
    else:
        npx = shutil.which("npx")
    if npx:
        return [npx, "-y", "@fly-ai/flyai-cli"]
    return ["npx", "-y", "@fly-ai/flyai-cli"]


def _node_major(exe: str) -> int:
    """探测某个 node 可执行文件的主版本号；失败返回 0。"""
    try:
        p = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        v = (p.stdout or "").strip().lstrip("v")
        return int(v.split(".")[0])
    except Exception:
        return 0


def resolve_node_dir() -> str:
    """返回一个含 Node>=18 的目录（flyai-cli 依赖全局 fetch，需 Node 18+）。

    flyai.cmd 用 PATH 上的 `node`；若服务进程的 PATH 上是旧 Node（<18），
    flyai-cli 会报 `ReferenceError: fetch is not defined` 而崩。这里优先挑
    WorkBuddy 托管的最新 Node，其次 PATH 上的 node，取到第一个 >=18 的目录。
    """
    if sys.platform.startswith("win"):
        node_name = "node.exe"
    else:
        node_name = "node"

    candidates = []
    # 1) WorkBuddy 托管 Node（按版本目录名倒序，尽量取最新）
    managed = sorted(
        glob.glob(
            os.path.join(
                os.path.expanduser("~"),
                ".workbuddy",
                "binaries",
                "node",
                "versions",
                "*",
            )
        ),
        reverse=True,
    )
    for d in managed:
        if os.path.exists(os.path.join(d, node_name)):
            candidates.append(d)
    # 2) PATH 上的 node
    w = shutil.which("node")
    if w:
        candidates.append(os.path.dirname(w))

    for d in candidates:
        if _node_major(os.path.join(d, node_name)) >= 18:
            return d
    return ""
