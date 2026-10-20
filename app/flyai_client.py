"""FlyAI（飞猪 AI 开放平台）CLI 封装。

通过子进程调用官方 `flyai-cli`，把自然语言/结构化查询转成飞猪真实旅行数据。
体验模式（无 API Key）即可返回结构化结果与预订链接；配置 FLYAI_API_KEY 解锁完整服务。
"""
import json
import os
import subprocess
import sys

from . import config

# flyai 子命令 -> 支持的参数白名单（仅透传已知参数，避免注入）
_ALLOWED_OPTIONS = {
    "ai-search": {"query"},
    "keyword-search": {"query"},
    "search-hotel": {
        "dest_name", "key_words", "poi_name", "hotel_types", "sort",
        "check_in_date", "check_out_date", "hotel_stars", "hotel_bed_types", "max_price",
    },
    "search-poi": {"city_name", "poi_level", "keyword", "category"},
    "search-flight": {
        "origin", "destination", "dep_date", "dep_date_start", "dep_date_end",
        "back_date", "back_date_start", "back_date_end", "journey_type",
        "seat_class_name", "transport_no", "transfer_city", "max_price", "sort_type",
    },
}


def _run(subcommand: str, options: dict) -> dict:
    allowed = _ALLOWED_OPTIONS.get(subcommand, set())
    cmd = config.resolve_flyai_prefix() + [subcommand]
    for key, value in (options or {}).items():
        if value is None or value == "":
            continue
        if key not in allowed:
            continue
        cmd += [f"--{key.replace('_', '-')}", str(value)]

    env = os.environ.copy()
    if config.FLYAI_API_KEY:
        env["FLYAI_API_KEY"] = config.FLYAI_API_KEY

    cmd_str = (
        subprocess.list2cmdline(cmd)
        if sys.platform.startswith("win")
        else " ".join(cmd)
    )

    try:
        if sys.platform.startswith("win"):
            # Windows 下 npx / flyai 均为 .cmd，需用 cmd /c 执行。
            # 关键：显式 UTF-8 解码，否则中文 Windows 默认 GBK 会把 flyai 的 UTF-8
            # 输出解崩（UnicodeDecodeError），stdout 被吞成空 -> status=-1 no output。
            proc = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                env=env,
                shell=True,
            )
        else:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return {
            "data": "",
            "status": -2,
            "message": "timeout (90s)，flyai 调用超时",
            "systemMessage": "",
        }
    except FileNotFoundError:
        return {
            "data": "",
            "status": -3,
            "message": "flyai 未找到：请先 `npm install` 或配置 FLYAI_BIN",
            "systemMessage": "",
        }

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if not out:
        # 把真实报错带回（脱敏 Key），避免只显示 "no output" 无从排查
        detail = err or "no output"
        if config.FLYAI_API_KEY:
            detail = detail.replace(config.FLYAI_API_KEY, "***")
        return {
            "data": "",
            "status": -1,
            "message": f"{detail}（rc={proc.returncode}）",
            "systemMessage": "",
        }
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {
            "data": out,
            "status": -1,
            "message": "non-json output",
            "systemMessage": "",
        }


def extract_data(result: dict) -> str:
    """从 flyai 返回里取出正文 text。"""
    return (result.get("data") or "").strip()


# ── 高层封装 ───────────────────────────────────────────────
def ai_search(query: str) -> dict:
    return _run("ai-search", {"query": query})


def keyword_search(query: str) -> dict:
    return _run("keyword-search", {"query": query})


def search_hotel(
    dest_name=None, key_words=None, poi_name=None, hotel_types=None,
    sort=None, check_in_date=None, check_out_date=None,
    hotel_stars=None, hotel_bed_types=None, max_price=None,
) -> dict:
    return _run("search-hotel", {k: v for k, v in locals().items() if v is not None})


def search_poi(city_name=None, poi_level=None, keyword=None, category=None) -> dict:
    return _run("search-poi", {k: v for k, v in locals().items() if v is not None})


def search_flight(
    origin=None, destination=None, dep_date=None, back_date=None,
    seat_class_name=None, max_price=None, sort_type=None,
) -> dict:
    return _run("search-flight", {k: v for k, v in locals().items() if v is not None})
