"""FlyAI（飞猪 AI 开放平台）CLI 封装。

通过子进程调用官方 `flyai-cli`，把自然语言/结构化查询转成飞猪真实旅行数据。
体验模式（无 API Key）即可返回结构化结果与预订链接；配置 FLYAI_API_KEY 解锁完整服务。
"""
import json
import os
import subprocess
import sys
import time

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
    "search-train": {
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
    # 保证 flyai-cli 用 Node>=18（否则全局 fetch 未定义会直接崩）。
    # flyai.cmd 调 PATH 上的 node，把含现代 Node 的目录怼到 PATH 最前。
    node_dir = config.resolve_node_dir()
    if node_dir:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

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


def _run_with_retry(subcommand: str, options: dict, max_retries: int = 2) -> dict:
    """带自动重试的 flyai 调用，遇到 429 限频时自动重试。"""
    for attempt in range(max_retries + 1):
        result = _run(subcommand, options)
        # 检查是否限频错误
        message = result.get("message", "")
        if "429" in message or "Rate limit" in message:
            if attempt < max_retries:
                wait_time = 2 * (attempt + 1)  # 递增等待时间：2s, 4s
                time.sleep(wait_time)
                continue
        # 成功或非限频错误，直接返回
        return result
    return result


def extract_data(result: dict):
    """从 flyai 返回里取出正文。结构化查询返回 dict，ai-search 返回 str。"""
    return result.get("data") or ""


def format_structured(data, limit: int = 8) -> str:
    """把结构化查询（itemList dict）格式化为含价格+预订链接的文本，供 LLM 直接引用。

    只保留价格/链接/名称等事实字段，绝不添油加醋；超出的条目截断以控制 token。
    支持酒店/景点（name+price 字段）与机票（ticketPrice+journeys 嵌套）两种结构。
    """
    if not isinstance(data, dict):
        return str(data or "").strip()
    items = data.get("itemList") or []
    if not items:
        return ""
    lines = []
    for it in items[:limit]:
        # 机票/火车票结构：ticketPrice（机票）或 price（火车票）+ journeys[].segments[]
        if "journeys" in it:
            price = it.get("ticketPrice") or it.get("price")
            journeys = it.get("journeys") or []
            seg_parts = []
            is_train = False
            for j in journeys[:1]:
                for seg in (j.get("segments") or []):
                    transport_no = seg.get("marketingTransportNo") or ""
                    transport_name = seg.get("marketingTransportName") or ""
                    dep = f"{seg.get('depCityName','')} {seg.get('depDateTime','')}"
                    arr = f"{seg.get('arrCityName','')} {seg.get('arrDateTime','')}"
                    seat = seg.get("seatClassName") or ""
                    # 判断是火车还是飞机：车次号以 G/D/C/K/Z 开头
                    if transport_no and transport_no[0].upper() in "GDCKZ":
                        is_train = True
                    seg_parts.append(f"{transport_name}{transport_no} {dep}→{arr} {seat}")
            label = "车次" if is_train else "航班"
            parts = [f"{label}: " + " / ".join(seg_parts)] if seg_parts else []
            if price:
                parts.append(f"价格: ¥{price}")
            if it.get("jumpUrl"):
                parts.append(f"预订链接: {it['jumpUrl']}")
            if parts:
                lines.append("- " + "；".join(parts))
            continue

        # 酒店/景点结构
        parts = []
        name = it.get("name") or ""
        if name:
            parts.append(f"名称: {name}")
        price = it.get("price")
        # 处理 ticketInfo 可能是 dict 或 list（多个票种）
        ticket_info = it.get("ticketInfo")
        if ticket_info:
            if isinstance(ticket_info, list):
                # 多个票种：如门票 10 元、游船 69 元
                ticket_prices = [f"{t.get('name', '')}{t.get('price', '')}" for t in ticket_info if t.get('price')]
                if ticket_prices:
                    parts.append(f"票价：{'；'.join(ticket_prices)}")
            elif isinstance(ticket_info, dict):
                price = ticket_info.get("price") or price
        if price:
            parts.append(f"价格: {price}")
        star = it.get("star") or it.get("poiLevel")
        if star:
            parts.append(f"星级/等级: {star}")
        if it.get("address"):
            parts.append(f"地址: {it['address']}")
        # 坐标（真实数据，供 LLM 透传到 timeline 的 lat/lng 字段）
        lat, lng = it.get("latitude"), it.get("longitude")
        if lat and lng:
            parts.append(f"坐标: {lat},{lng}")
        if it.get("interestsPoi"):
            parts.append(f"位置: {it['interestsPoi']}")
        desc = (it.get("description") or "").strip()
        if desc:
            parts.append(f"简介: {desc[:120]}")
        link = it.get("detailUrl") or it.get("jumpUrl")
        if link:
            parts.append(f"预订链接: {link}")
        pic = it.get("mainPic") or it.get("image") or it.get("pic")
        if pic:
            parts.append(f"图片：{pic}")
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)


# ── 高层封装 ───────────────────────────────────────────────
def ai_search(query: str) -> dict:
    return _run_with_retry("ai-search", {"query": query})


def parse_restaurants(text: str, limit: int = 3) -> list:
    """从 ai-search 返回的餐厅推荐文本中解析结构化餐厅列表。

    识别格式：**[餐厅名](预订链接)** 后跟 - **亮点**：... 等要点行。
    返回 [{name, url, highlights}]，highlights 为要点拼接的简短描述。
    """
    import re
    results = []
    if not text:
        return results
    # 按 "**[名称](链接)**" 切分块
    pattern = re.compile(r"\*\*\[([^\]]+)\]\((https?://[^)]+)\)\*\*")
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches[:limit]):
        name = m.group(1).strip()
        url = m.group(2).strip()
        block_start = m.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[block_start:block_end]
        points = re.findall(r"-\s*(?:\*\*)?([^*：:\n]{1,12})(?:\*\*)?[：:]\s*([^\n]+)", block)
        points = [(k, v) for k, v in points if k.strip() not in ("地址",)]
        highlights = "；".join(f"{k.strip()}：{v.strip().replace('**', '')}" for k, v in points[:3])
        results.append({"name": name, "url": url, "highlights": highlights})
    return results


def keyword_search(query: str) -> dict:
    return _run("keyword-search", {"query": query})


def search_hotel(
    dest_name=None, key_words=None, poi_name=None, hotel_types=None,
    sort=None, check_in_date=None, check_out_date=None,
    hotel_stars=None, hotel_bed_types=None, max_price=None,
) -> dict:
    return _run_with_retry("search-hotel", {k: v for k, v in locals().items() if v is not None})


def search_poi(city_name=None, poi_level=None, keyword=None, category=None) -> dict:
    return _run_with_retry("search-poi", {k: v for k, v in locals().items() if v is not None})


def search_flight(
    origin=None, destination=None, dep_date=None, back_date=None,
    seat_class_name=None, max_price=None, sort_type=None,
) -> dict:
    return _run_with_retry("search-flight", {k: v for k, v in locals().items() if v is not None})


def search_train(
    origin=None, destination=None, dep_date=None, back_date=None,
    seat_class_name=None, max_price=None, sort_type=None,
) -> dict:
    return _run_with_retry("search-train", {k: v for k, v in locals().items() if v is not None})
