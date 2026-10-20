"""配置与运行环境解析（API Key、模型、flyai 可执行文件探测）"""
import os
import sys
import shutil

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
