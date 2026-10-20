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
            "booking_link": "https://router.feizhu.com/...",
            "price": "¥400/间/晚（飞猪实时价）",
            "notes": "统一办理入住，领取房卡"
        }}
    ],
    "budget": [
        {{
            "category": "住宿",
            "description": "双人间¥400×15间×1晚",
            "estimated_cost": "¥6000",
            "is_per_person": false,
            "action_item_id": "hotel-1"
        }},
        {{
            "category": "住宿（人均）",
            "description": "¥6000÷30人",
            "estimated_cost": "¥200",
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
3. 时间线要具体到小时，按 Day 1/Day 2 组织；timeline 每项必须带 booking_link 和 price
4. **价格必须来自飞猪结果中的「价格: ¥XXX」字段**，直接引用，禁止标"待确认"后什么都不给
5. **预算必须计算**：
   - 住宿总费 = 房间单价 × 房间数（人数÷2 向上取整）× 晚数
   - 门票/交通总费 = 单价 × 人数
   - budget 数组每项给出计算式（如"¥400×15间×1晚"）和结果数字
6. 房间分配根据人数计算（30人→15间双人间）
7. budget_per_person = 所有预算项总和 ÷ 人数，必须是具体数字（如"约¥1200"），禁止"待估算"
8. 若某项确实无价格数据，description 说明"点击预订链接查看实时价格"，该项不计入预算总额，但其余项照常计算
9. **餐饮估算**：若飞猪结果给出人均消费参考（如"人均 120-200 元/人"），取中间值估算餐饮预算，description 注明「按飞猪人均参考估算」，is_per_person=true
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

    # 构建飞猪数据摘要（结构化数据含价格+链接，放宽截断以保留完整价格信息）
    flyai_summary = []
    for title, data in flyai_sections:
        flyai_summary.append(f"【{title}】\n{data[:2500]}")

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
==== 飞猪搜索结果（每条含真实价格与预订链接） ===={flyai_text}
==== 飞猪结果结束 ====

硬性要求：
1. action_items 与 timeline 中的每个项目都必须带上飞猪结果里的「预订链接」原文
2. price / estimated_cost 必须优先使用飞猪结果中的真实价格（如「价格: ¥554」）
3. 预算必须计算：住宿 = 房价×房间数×晚数（房间数=人数÷2 向上取整）、门票/交通 = 单价×{group_size}人；餐饮若无价格，按飞猪结果描述定性并标注
4. budget_per_person = 总预算 ÷ {group_size}，必须给出具体数字
请输出严格的 JSON 格式方案。
"""

    result = _llm.complete_json(STRUCTURED_PLAN_SYSTEM, user_prompt, max_tokens=6000)
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
