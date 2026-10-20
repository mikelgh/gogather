"""结构化方案生成：flyai 数据 → 时间线/预算/房间/行动项 JSON。"""
import json
import re

from . import flyai_client as flyai
from . import llm_client as _llm

STRUCTURED_PLAN_SYSTEM = """你是一个专业的团体出行方案结构化助手。基于飞猪 AI 搜索结果，生成结构化的 JSON 方案。

输出严格遵循以下 JSON 格式（不要 markdown 代码块）：
{{
    "summary": "一段话概述方案（50字内）",
    "action_items": [
        {{
            "id": "hotel-1",
            "type": "hotel",
            "title": "住宿：杭州白金汉爵大酒店",
            "description": "16个宴会厅，可承接30人聚会，含KTV和棋牌",
            "recommendations": [
                {{
                    "name": "杭州白金汉爵大酒店",
                    "description": "西湖区，1170间客房，2023年装修",
                    "price": "约¥400/晚",
                    "booking_link": "https://router.feizhu.com/...",
                    "rating": "4.8分"
                }}
            ],
            "estimated_cost": "约¥400/人/晚",
            "time_slot": "Day 1 15:00 入住",
            "assignee": "待分配",
            "priority": "high",
            "booking_link": "https://router.feizhu.com/..."
        }}
    ],
    "timeline": [
        {{
            "time_range": "Day 1 15:00-16:00",
            "title": "酒店入住",
            "action_item_id": "hotel-1",
            "location": "杭州白金汉爵大酒店",
            "notes": "统一办理入住，领取房卡"
        }}
    ],
    "budget": [
        {{
            "category": "住宿",
            "description": "双人间，15间×1晚",
            "estimated_cost": "¥400",
            "is_per_person": true,
            "action_item_id": "hotel-1"
        }}
    ],
    "room_allocation": [
        {{
            "room_type": "双人间",
            "count": 15,
            "price_per_night": "¥400",
            "hotel_name": "杭州白金汉爵大酒店",
            "hotel_link": "https://router.feizhu.com/...",
            "notes": "建议同性别配对"
        }}
    ],
    "budget_per_person": "约¥1200"
}}

规则：
1. 所有事实信息必须来自飞猪搜索结果，保留原始预订链接
2. 为每个可预订项目创建 action_item（酒店/交通/餐厅/活动）
3. 时间线要具体到小时，按 Day 1/Day 2 组织
4. 预算按类别拆分，给出人均估算
5. 房间分配根据人数计算（30人→15间双人间）
6. 如果飞猪数据缺少某项，标注"待确认"而非编造
"""


def generate_structured_plan(params: dict, flyai_sections: list) -> dict:
    """基于飞猪查询结果生成结构化方案。

    Args:
        params: 用户参数 {city, check_in, check_out, origin, group_size, vibe}
        flyai_sections: [(title, data), ...] 飞猪查询结果

    Returns:
        结构化方案 dict
    """
    city = params.get("city", "")
    group_size = params.get("group_size", "30")
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")
    origin = params.get("origin", "")
    vibe = params.get("vibe", "")

    # 构建飞猪数据摘要
    flyai_summary = []
    for title, data in flyai_sections:
        flyai_summary.append(f"【{title}】\n{data[:500]}")

    flyai_text = "\n\n".join(flyai_summary)

    # 构建用户场景描述
    scenario = f"""
城市：{city}
入住：{check_in}，退房：{check_out}
出发地：{origin}
人数：{group_size}人
偏好：{vibe}
"""

    user_prompt = f"""
请基于以下飞猪 AI 搜索结果，为{group_size}人的{city}聚会生成结构化方案。

==== 用户场景 ===={scenario}
==== 飞猪搜索结果 ===={flyai_text}
==== 飞猪结果结束 ====

请输出严格的 JSON 格式方案。
"""

    result = _llm.complete_json(STRUCTURED_PLAN_SYSTEM, user_prompt, max_tokens=4000)
    if not result:
        return _fallback_structured(params, flyai_sections)

    return result


def _fallback_structured(params: dict, flyai_sections: list) -> dict:
    """LLM 失败时的兜底结构化方案。"""
    city = params.get("city", "")
    group_size = params.get("group_size", "30")
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")

    action_items = []
    timeline = []
    budget = []

    for i, (title, data) in enumerate(flyai_sections):
        item_type = ["hotel", "activity", "restaurant", "transport"][i] if i < 4 else "activity"
        action_items.append({
            "id": f"{item_type}-{i+1}",
            "type": item_type,
            "title": title,
            "description": data[:100],
            "recommendations": [],
            "estimated_cost": "待确认",
            "time_slot": f"Day 1",
            "assignee": "待分配",
            "priority": "medium",
            "booking_link": None,
        })

    return {
        "summary": f"{group_size}人{city}聚会方案",
        "action_items": action_items,
        "timeline": timeline,
        "budget": budget,
        "room_allocation": [],
        "budget_per_person": "待估算",
    }


def calculate_room_allocation(group_size: int, hotel_info: str = "") -> list:
    """根据人数计算房间分配建议。"""
    if not group_size or group_size <= 0:
        return []

    # 默认双人间
    double_rooms = group_size // 2
    remaining = group_size % 2

    rooms = []
    if double_rooms > 0:
        rooms.append({
            "room_type": "双人间",
            "count": double_rooms,
            "price_per_night": None,
            "hotel_name": "",
            "hotel_link": None,
            "notes": "建议同性别配对",
        })

    if remaining > 0:
        rooms.append({
            "room_type": "单人间",
            "count": 1,
            "price_per_night": None,
            "hotel_name": "",
            "hotel_link": None,
            "notes": "剩余1人",
        })

    return rooms
