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
4. **价格必须来自飞猪结果**：
   - 若飞猪返回「价格: ¥XXX」，直接引用，禁止标"待确认"或¥0
   - 若飞猪返回「票价：门票¥10；游船¥69」等多票种，**必须全部列出**，并根据行程安排选择对应票种计入预算
   - 若安排了收费活动（如游船、索道、演出），必须从飞猪结果中找到对应票价并计入预算
5. **预算必须计算**：
   - 住宿总费 = 房间单价 × 房间数（人数÷2 向上取整）× 晚数
   - 交通总费 = 去程单价×人数 + 返程单价×人数（若两段都有价格）；若只有去程，仅算去程
   - 门票总费 = 单价 × 人数（若有多种票，按实际安排的票种累加）
   - 活动费用 = 单价 × 人数（如游船、索道等额外收费项目）
   - budget 数组每项给出计算式（如"¥400×15间×1晚"、"¥10×30人门票+¥69×30人游船"）和结果数字
6. 房间分配根据人数计算（30人→15间双人间）
7. budget_per_person = 所有预算项总和 ÷ 人数，必须是具体数字（如"约¥1200"），禁止"待估算"
8. **禁止¥0 或空价格**：若飞猪结果无价格数据，price 必须填"点击预订链接查看实时价格"或"待确认"，**绝不允许写¥0、0、留空或 null**。该项不计入预算总额，description 注明"需通过预订链接确认价格"，但其余有价格的项照常计算
9. **餐饮估算**：若飞猪结果给出人均消费参考（如"人均 120-200 元/人"），取中间值估算餐饮预算，description 注明「按飞猪人均参考估算」，is_per_person=true
10. **每日餐饮覆盖**：从入住到退房的每一天，时间线必须包含午餐和晚餐安排（至少 2 餐/天）。若飞猪结果中餐厅数量不足以覆盖所有餐次，剩余餐次在当天活动附近推荐「周边餐饮」，price 标注"按飞猪人均参考估算"或"点击预订链接查看实时价格"，notes 注明"建议提前预订团体餐"
11. **时间线完整性**：timeline 应覆盖从 Day 1 入住到 Day N 退房的全部天数，每天至少有：早餐（酒店/周边）、午餐、晚餐、当日主要活动
12. **禁止在 notes/description 中写原始链接**：所有预订链接必须放在 `booking_link` 字段，notes 和 description 中不得出现 `http://` 或 `https://` 开头的 URL。若需引用多个链接（如景点 + 寺庙），只保留主链接在 booking_link，notes 中用文字说明（如"含灵隐寺门票"）
13. **景点门票必须使用飞猪数据**：若飞猪结果中景点包含「票价：门票¥XX；游船¥XX」等信息，必须在 timeline 中显示具体票价，并按实际行程安排（如"游览西湖+乘游船"）将对应费用计入预算。禁止忽略已有的票价数据。
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
    budget_tier = params.get("budget_tier", "舒适型")
    children_count = params.get("children_count")
    children_ages = params.get("children_ages")
    elderly_count = params.get("elderly_count")
    accessibility = params.get("accessibility")
    preferences = params.get("preferences", [])

    # 构建飞猪数据摘要（结构化数据含价格+链接，放宽截断以保留完整价格信息）
    flyai_summary = []
    for title, data in flyai_sections:
        flyai_summary.append(f"【{title}】\n{data[:2500]}")

    flyai_text = "\n\n".join(flyai_summary)

    # 预算档次对应的价格指导
    budget_guidance = {
        "经济型": "优先选择价格最低的选项，住宿选经济型酒店/民宿（200元以下/晚），餐饮选平价餐厅",
        "舒适型": "选择中等价位选项，住宿选中档酒店（300-500元/晚），餐饮选中档餐厅",
        "豪华型": "优先选择品质高的选项，住宿选高档酒店/度假村（600元以上/晚），餐饮选特色高端餐厅",
    }
    budget_hint = budget_guidance.get(budget_tier, budget_guidance["舒适型"])

    # 团体构成信息
    group_composition = []
    if children_count:
        age_info = f"（{children_ages}）" if children_ages else ""
        group_composition.append(f"儿童：{children_count}人{age_info}")
    if elderly_count:
        group_composition.append(f"老人：{elderly_count}人")
    if accessibility:
        group_composition.append(f"无障碍需求：{accessibility}")
    composition_text = "；".join(group_composition) if group_composition else "无特殊需求"

    # 偏好标签
    preferences_text = "、".join(preferences) if preferences else "无特定偏好"

    # 构建用户场景描述
    scenario = f"""
城市：{city}
入住：{check_in}，退房：{check_out}
出发地：{origin}
人数：{group_size}人
偏好：{vibe}
预算档次：{budget_tier}（{budget_hint}）
团体构成：{composition_text}
兴趣偏好：{preferences_text}
"""

    user_prompt = f"""
请基于以下飞猪 AI 搜索结果，为{group_size}人的{city}聚会生成结构化方案。

==== 用户场景 ===={scenario}
==== 飞猪搜索结果（每条含真实价格与预订链接） ===={flyai_text}
==== 飞猪结果结束 ====

硬性要求：
1. action_items 与 timeline 中的每个项目都必须带上飞猪结果里的「预订链接」原文
2. price / estimated_cost 必须优先使用飞猪结果中的真实价格（如「价格: ¥554」）
3. **若飞猪结果无价格**：timeline.price 填"点击预订链接查看实时价格"，budget 中该项不计入总额但 description 注明"需通过预订链接确认价格"
4. 预算必须计算：住宿 = 房价×房间数×晚数（房间数=人数÷2 向上取整）、去程交通 = 去程单价×{group_size}人、返程交通 = 返程单价×{group_size}人（若两段都有价格）、门票 = 单价×{group_size}人；餐饮若无价格，按飞猪结果描述定性并标注
5. budget_per_person = 总预算 ÷ {group_size}，必须给出具体数字
6. **预算档次指导**：{budget_hint}。在飞猪结果中优先选择符合该档次的选项
7. **团体构成适配**：
   - 若有儿童（{children_count or '无'}），优先推荐亲子友好的景点和活动（如主题乐园、动物园、博物馆），避免高强度徒步
   - 若有老人（{elderly_count or '无'}），选择交通便利、步行距离短的景点，避免爬山等高强度活动
   - 若有无障碍需求（{accessibility or '无'}），确保推荐的酒店和景点有无障碍设施
8. **兴趣偏好匹配**：优先推荐符合用户兴趣标签（{preferences_text}）的活动和餐厅
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
        # 生成基本时间线项
        timeline.append({
            "time_range": f"Day {i+1}",
            "title": title,
            "action_item_id": f"{item_type}-{i+1}",
            "location": city,
            "booking_link": None,
            "price": "点击预订链接查看实时价格",
            "notes": "LLM 生成失败，显示原始数据",
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


REVISE_PLAN_SYSTEM = """你是一个旅行方案修改助手。根据用户的修改要求，更新现有的结构化方案。

输出严格遵循以下 JSON 格式（不要 markdown 代码块）：
{
    "summary": "更新后的方案概述（50字内）",
    "action_items": [...],
    "timeline": [...],
    "budget": [...],
    "room_allocation": [...],
    "budget_per_person": "约¥XXX"
}

规则：
1. 仅修改用户要求的部分，其余保持不变
2. 如果修改涉及价格变化，重新计算相关预算项
3. 保留所有飞猪预订链接
4. budget_per_person 必须重新计算为具体数字
"""


def revise_plan(params: dict, current_plan: dict, revision_message: str) -> dict:
    """基于用户反馈修改现有方案。

    Args:
        params: 原始用户参数
        current_plan: 当前结构化方案
        revision_message: 用户的修改要求

    Returns:
        修改后的结构化方案
    """
    import json

    group_size = params.get("group_size", "30")

    current_json = json.dumps(current_plan, ensure_ascii=False, indent=2)

    user_prompt = f"""
当前方案：
{current_json}

用户修改要求：{revision_message}

人数：{group_size}人

请根据用户要求修改方案，输出完整的更新后 JSON。
"""

    result = _llm.complete_json(REVISE_PLAN_SYSTEM, user_prompt, max_tokens=6000, use_fast_model=True)
    if not result:
        return current_plan

    return result