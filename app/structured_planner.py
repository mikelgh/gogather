"""结构化方案生成：flyai 数据 → 时间线/预算/房间/行动项 JSON。"""
import json
import re
from datetime import datetime

from . import flyai_client as flyai
from . import llm_client as _llm

OUTLINE_SYSTEM = """你是团体出行行程大纲规划师。根据用户参数生成简要行程框架，供用户确认后再生成完整方案。

要求：
1. 只输出行程框架：每天的主题与上午/下午/晚上安排，不要价格、酒店品牌、航班号等落地细节。
2. 天数根据入住/退房日期推断；未提供日期时按 2 天 1 晚规划，并在 summary 注明"暂按2天1晚"。
3. 景点/活动必须真实存在，贴合城市、人群类型（团建/同学聚会/亲子等）与人数规模；餐饮写安排形式（如"特色欢迎晚宴""景区周边团餐"）。
4. 交通项写"出发地→目的地"形式即可，不写具体班次。
5. 每天约 3 条安排，强度均衡，同一天的活动地点尽量相近。
6. 若最近对话中已有此前的大纲且用户提出调整，基于原大纲修改，不要整体重写。
7. **地理可行性（硬约束）**：
   - 同一日的活动地点必须同城或邻近（相距<100km），禁止将超过200km的景点排为当日往返
   - 跨城市/远郊景点（如从成都去九寨沟、从杭州去黄山）必须占用完整的交通时段并安排当地住宿，不得当日往返
   - 每天纯交通时间（含接驳）不得超过8小时
8. **动线连贯性**：第 N 天结束地点应与第 N+1 天开始地点相近，禁止今天在成都、明天一早出现在600km外的乐山这种断裂动线。
9. **返程日**：最后一天上午或中午必须安排返程交通（返回出发地），当天下午不排需要深度游览的活动。

严格返回 JSON（不要 markdown 代码块）：
{
    "summary": "一句话概述（含人数、出发地→目的地、天数）",
    "days": [
        {
            "day": 1,
            "theme": "当天主题（如：抵达+城市文化）",
            "items": [
                {"slot": "上午", "title": "安排内容", "type": "交通"},
                {"slot": "下午", "title": "安排内容", "type": "活动"},
                {"slot": "晚上", "title": "安排内容", "type": "餐饮"}
            ]
        }
    ]
}
"""

STRUCTURED_PLAN_SYSTEM = """你是一个专业的团体出行方案结构化助手。基于飞猪 AI 搜索结果，生成结构化的 JSON 方案。

输出严格遵循以下 JSON 格式（不要 markdown 代码块）：
{{
    "summary": "一段话概述方案（80字内），必须包含：人数、出发地→目的地、日期、天数、交通方式、活动类型、预算档次等所有关键信息",
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
            "lat": 30.2889,
            "lng": 120.1019,
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
9. **餐饮估算**：若飞猪结果给出人均消费参考（如"人均 120-200 元/人"），取中间值×人数算出总额，description 注明「按飞猪人均参考估算」，is_per_person=false
10. **每日餐饮覆盖**：从入住到退房的每一天，时间线必须包含午餐和晚餐安排（至少 2 餐/天）。若飞猪结果中餐厅数量不足以覆盖所有餐次，剩余餐次在当天活动附近推荐「周边餐饮」，price 标注"按飞猪人均参考估算"或"点击预订链接查看实时价格"，notes 注明"建议提前预订团体餐"
11. **时间线完整性**：timeline 应覆盖从 Day 1 入住到 Day N 退房的全部天数，每天至少有：早餐（酒店/周边）、午餐、晚餐、当日主要活动
12. **禁止在 notes/description 中写原始链接**：所有预订链接必须放在 `booking_link` 字段，notes 和 description 中不得出现 `http://` 或 `https://` 开头的 URL。若需引用多个链接（如景点 + 寺庙），只保留主链接在 booking_link，notes 中用文字说明（如"含灵隐寺门票"）
13. **保留图片 URL**：若飞猪结果中包含「图片：https://...」，必须在 timeline 项中添加 `image` 字段，值为图片 URL。例如：`"image": "https://img.alicdn.com/..."`。若飞猪结果无图片，`image` 字段可省略
13. **景点门票必须使用飞猪数据**：若飞猪结果中景点包含「票价：门票¥XX；游船¥XX」等信息，必须在 timeline 中显示具体票价，并按实际行程安排（如"游览西湖+乘游船"）将对应费用计入预算。禁止忽略已有的票价数据。
14. **价格类型必须匹配**：酒店房价（如¥446/晚）只能挂在住宿类 action_item 上，禁止挂在自由活动、景点游览、餐饮等非住宿项。自由活动/无收费项目的 price 必须填"免费"或"无费用"，estimated_cost 留空。
15. **不可预订项目禁止附带链接**：booking_link 只能填飞猪结果中**同一个具体商家**的链接（指名道姓的酒店、餐厅、车次/航班、景点）。以下情况 booking_link 必须填 null，禁止从飞猪结果复制无关链接凑数：
   - 车上/自备早餐、集合签到、自由活动、酒店内用餐等没有真实预订对象的项目
   - 模糊推荐（如"周边中档餐厅""附近用餐""活动片区周边"），飞猪结果中没有对应的具体商家
   - 飞猪结果明确未返回该类数据（如餐厅数据缺失）
   此类项目 price 可填估算金额或"免费"。
16. **餐饮必须落到具体商家**：若飞猪美食结果列出了任何具体商家（餐厅或酒店宴会场地，**即使飞猪注明"数据有限""需自行核实"也必须使用**），timeline 的午餐/晚餐项必须在 notes 中列出 2-3 家候选并注明各自特色（有人均价格必须写出），booking_link 填其中主推荐商家的链接；仅当美食结果真的一个商家都没列出时才允许写"周边餐厅"模糊推荐（此时 booking_link 为 null，且 notes 不得写"飞猪未提供数据"以外的虚假理由）。**不同餐次应推荐不同组合**（如午餐主推 A、晚餐主推 B），备选优先选当日活动区域附近的商家，不要把跨城区的远距商家混进同一餐候选。
17. **收费项必须全部进预算**：timeline 中任何标了「¥X/人」的收费项（门票、餐饮、游船、演出）都必须有对应的 budget 条目，**逐条核对，一个都不能漏**。反之 budget 中出现的费用也必须在 timeline 有对应安排。
18. **budget_per_person 必须算出来**：= 所有 `is_per_person=false` 的 budget 条目金额之和 ÷ 人数。禁止凭印象取整或估算；先加总再相除，结果与各项明细必须自洽。
19. **每天都要有午餐**：Day 1 至 Day N 每一天都必须在 timeline 出现午餐项（Day 1 落地当天、Day N 返程当天同样需要，通常安排在活动/移动前后就近用餐）；晚餐可省略的只有"当天已乘机/高铁返程且在车用餐"的当晚。
20. **预算禁止重复行**：budget 数组中每个类目只出现一次（is_per_person=false），禁止同时写"住宿"和"住宿（人均）"两行。人均金额由 budget_per_person 统一展示，不要在 budget 里重复。
21. **地理可行性（硬约束）**：同一日的活动地点必须同城或邻近（相距<100km）。严禁将超过200km的景点排为当日往返（如从成都当日往返九寨沟约400km，不可能完成）。跨城市/远郊景点必须安排当地住宿，并占用完整交通时段。每天纯交通时间（含接驳）不得超过8小时。
22. **机场/车站名称必须与飞猪结果严格一致**：航班/车次的起降机场/车站名称必须照抄飞猪结果，禁止凭印象替换。例如飞猪显示"天府机场"就写天府机场，禁止写成双流机场。同城有多个机场/车站时尤其注意。
23. **返程完整性（硬约束）**：行程最后一天必须包含返程交通安排（航班/车次/大巴），从目的地返回出发地。若飞猪结果确实无返程数据，timeline 最后一项必须写"需自行预订返程交通"并在 notes 中说明，不允许完全遗漏返程。
24. **时间一致性**：景点开放时间描述必须与安排时间匹配。写"7:30开园即入"则安排时间必须是7:30左右，不能排15:30。同一天内的时间顺序必须单调递增（上午→下午→晚上）。
"""


def _conversation_text(conversation: list | None, limit: int = 6) -> str:
    if not conversation:
        return ""
    msgs = []
    for m in conversation[-limit:]:
        role = "用户" if m.get("role") == "user" else "助手"
        msgs.append(f"{role}：{m.get('content', '')}")
    return "\n".join(msgs)


def generate_outline(params: dict, conversation: list | None = None, user_id: str = None) -> dict | None:
    """用快速模型生成行程大纲（供用户确认；确认后作为硬约束生成正式方案）。失败返回 None。"""
    user_prompt = (
        f"用户参数：{json.dumps(params, ensure_ascii=False)}\n\n"
        + (_conversation_text(conversation) + "\n\n" if conversation else "")
        + "请生成行程大纲。"
    )
    result = _llm.complete_json(OUTLINE_SYSTEM, user_prompt, max_tokens=2000, use_fast_model=True,
                                user_id=user_id, action="generate_outline")
    if not result or not isinstance(result.get("days"), list) or not result["days"]:
        return None
    if not all(isinstance(d.get("items"), list) and d["items"] for d in result["days"]):
        return None
    return result


def outline_to_text(outline: dict) -> str:
    """大纲转简要文本（用于对话历史与前端消息）。"""
    lines = [outline.get("summary", "行程大纲")]
    for d in outline.get("days", []):
        items = "；".join(f"{it.get('slot', '')}{it.get('title', '')}" for it in d.get("items", []))
        lines.append(f"D{d.get('day', '')} {d.get('theme', '')}：{items}")
    return "\n".join(lines)


def generate_structured_plan(params: dict, flyai_sections: list, user_id: str = None, plan_id: str = None,
                             outline: dict | None = None) -> dict:
    """基于飞猪查询结果生成结构化方案。

    Args:
        params: 用户参数 {city, check_in, check_out, origin, group_size, vibe}
        flyai_sections: [(title, data), ...] 飞猪查询结果
        user_id: 用户ID（用于追踪 AI 使用量）
        plan_id: 方案ID（用于追踪 AI 使用量）
        outline: 用户确认的行程大纲（硬约束，可选）

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

    # 提取交通时间信息（用于约束时间线生成）
    transport_schedule = ""
    for title, data in flyai_sections:
        if "去程" in title or "返程" in title:
            # 尝试从标题和数据中提取时间
            import re
            # 匹配类似 "Day 1 23:00" 或 "2026-11-01 23:00" 的时间
            time_patterns = re.findall(r'(Day\s*\d+\s+\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})', data)
            if time_patterns:
                transport_schedule += f"{title}: {', '.join(time_patterns[:2])}\n"

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
    if transport_schedule:
        scenario += f"交通时间：\n{transport_schedule}"

    # 用户确认过的大纲作为硬约束
    outline_constraint = ""
    if outline and outline.get("days"):
        outline_constraint = (
            "==== 用户已确认的行程框架（硬约束） ====\n"
            + json.dumps(outline, ensure_ascii=False, indent=1)
            + "\n天数、每天主题与主要安排必须与该框架一致，禁止增删天数或更换主要景点/活动；"
              "你只需为各时段落实具体的酒店、餐厅、航班/车次、门票与价格等细节。\n\n"
        )

    user_prompt = f"""
请基于以下飞猪 AI 搜索结果，为{group_size}人的{city}聚会生成结构化方案。

==== 用户场景 ===={scenario}
{outline_constraint}==== 飞猪搜索结果（每条含真实价格与预订链接） ===={flyai_text}
==== 飞猪结果结束 ====

硬性要求：
0. **summary 必须包含所有关键信息**：{group_size}人、{origin}→{city}、{check_in}至{check_out}、交通方式（{origin}出发的交通类型）、活动类型（{params.get('activity_type', '聚会')}）、预算档次（{budget_tier}）
1. 只有飞猪结果中有对应具体商家的项目（酒店、餐厅、车次/航班、景点）才带该商家的「预订链接」原文；无对应数据的项目 booking_link 填 null
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
9. **时间线必须考虑交通时间**：
   - 若交通在 Day 1 晚上出发、Day 2 早上到达，则 Day 1 的活动应安排在出发地（{origin}）或省略
   - Day 2 及之后的活动才能安排在目的地（{city}）
   - 退房日（{check_out}）的活动必须在退房时间之后安排
10. **不可预订项目禁止附带链接**：车上/自备早餐、集合签到、自由活动、模糊推荐（如"周边中档餐厅"）等没有飞猪具体商家对应的项目，booking_link 必须填 null，禁止复制无关链接凑数
11. **餐饮必须落到具体商家**：若飞猪美食结果列出任何具体商家（餐厅或酒店宴会场地，即使注明"数据有限"也必须使用），午餐/晚餐项必须在 notes 列出 2-3 家候选并注明特色（有人均必须写出），booking_link 填主推荐商家；不同餐次推荐不同组合，备选优先选当日活动区域附近；仅当美食结果一个商家都没列出时才允许模糊推荐
12. **交通项目必须保留航班号/车次号**：若飞猪结果中包含航班号（如"川航 3U6710"）或车次号（如"G7502"），必须在 timeline 的 title 或 notes 中保留该信息。例如 title 应为"广州飞往成都（川航 3U6710）"或 notes 中注明"航班号：川航 3U6710"
13. **坐标透传（只搬运不生成）**：若飞猪结果中包含「坐标: 30.74,104.13」信息，且 timeline 项的 location 对应该商家，则在该项中添加 `"lat": 30.74, "lng": 104.13`（浮点数）。**严禁编造坐标**：飞猪结果中没有坐标信息的项目，不得添加 lat/lng 字段（省略即可，系统会另行解析）。交通路线类项目（机场→酒店等）不填坐标
请输出严格的 JSON 格式方案。
"""

    result = _llm.complete_json(STRUCTURED_PLAN_SYSTEM, user_prompt, max_tokens=4000,
                                user_id=user_id, plan_id=plan_id, action="generate_structured_plan")
    if not result:
        # 整体生成失败（超长方案输出截断/供应商故障）：按天分片兜底，替代垃圾 fallback
        result = _generate_plan_by_days(params, flyai_sections, outline, user_id, plan_id)
    if not result:
        return _fallback_structured(params, flyai_sections)

    # 分层修复：① 代码级确定性修复 → ② LLM 针对性修补 → ③ 整体重试兜底
    issues = _run_validators(result, params, flyai_sections)
    if issues:
        _apply_code_fixes(result, params)
        issues = _run_validators(result, params, flyai_sections)

    if issues:
        patched = _patch_plan_issues(result, issues, params, flyai_sections,
                                     user_id=user_id, plan_id=plan_id)
        if patched:
            _apply_code_fixes(patched, params)
            patch_issues = _run_validators(patched, params, flyai_sections)
            if len(patch_issues) < len(issues):
                result = patched
                issues = patch_issues

    if issues:
        retry_prompt = (
            user_prompt
            + "\n\n==== 上次生成方案存在的问题（必须在本次修正） ====\n"
            + "\n".join(f"- {i}" for i in issues)
            + "\n\n请重新生成完整 JSON，确保以上问题全部得到解决。"
        )
        retry_result = _llm.complete_json(STRUCTURED_PLAN_SYSTEM, retry_prompt, max_tokens=4000,
                                          user_id=user_id, plan_id=plan_id,
                                          action="generate_structured_plan_retry")
        if retry_result:
            _apply_code_fixes(retry_result, params)
            retry_issues = _run_validators(retry_result, params, flyai_sections)
            if len(retry_issues) < len(issues):
                result = retry_result

    _validate_coords(result, flyai_sections)
    _recompute_budget_per_person(result, params)
    _compute_days_nights(result, params)
    _validate_booking_links(result)
    return result


def _money(text) -> int | None:
    """从 "¥6000" / "¥321×30 = ¥9630" 中取最后一个金额。"""
    nums = re.findall(r"¥\s*([\d,]+(?:\.\d+)?)", str(text or ""))
    if not nums:
        return None
    try:
        return int(float(nums[-1].replace(",", "")))
    except ValueError:
        return None


def _recompute_budget_per_person(result: dict, params: dict) -> None:
    """人均预算按各明细实时重算，修正 LLM 取整/漏项导致的偏差。"""
    total = 0
    for item in result.get("budget", []):
        if item.get("is_per_person"):
            continue
        amount = _money(item.get("estimated_cost"))
        if amount:
            total += amount
    size_str = re.sub(r"\D", "", str(params.get("group_size", "")))
    size = int(size_str) if size_str else 0
    if not total or not size:
        return
    per_person = round(total / size)
    stated = _money(result.get("budget_per_person"))
    if stated is None or abs(stated - per_person) > 20:
        result["budget_per_person"] = f"约¥{per_person}"


def _compute_days_nights(result: dict, params: dict) -> None:
    """根据入住/退房日期自动推导 X天Y晚，修正 LLM 常见的 off-by-one 错误。"""
    from datetime import datetime
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")
    if not check_in or not check_out:
        return
    try:
        d_in = datetime.fromisoformat(check_in)
        d_out = datetime.fromisoformat(check_out)
    except (ValueError, TypeError):
        return
    nights = (d_out - d_in).days
    if nights <= 0:
        return
    days = nights + 1
    correct_label = f"{days}天{nights}晚"
    summary = result.get("summary", "")
    result["summary"] = re.sub(r"\d+天\d+晚", correct_label, summary) or summary


def _validate_booking_links(result: dict) -> None:
    """校验 timeline 中的 booking_link 必须是真实 http(s) URL，否则置为 null。"""
    for item in result.get("timeline", []):
        link = str(item.get("booking_link") or "")
        if not link:
            continue
        if not re.match(r"^https?://", link):
            item["booking_link"] = None
            continue
        # Detect feizhu router URLs with placeholder tokens (e.g. ?url=train_gz_cd)
        # Real links have URL-encoded nested URLs (?url=https%3A%2F%2F...) or long numeric IDs
        m = re.search(r"url=([^&]+)", link)
        if m and "router.feizhu.com" in link:
            token = m.group(1)
            # Placeholder: short lowercase token (e.g. train_gz_cd, jiuzhaigou, hotel_cd_1)
            if re.match(r"^[a-z][a-z0-9_]{2,25}$", token):
                item["booking_link"] = None


def _validate_coords(result: dict, flyai_sections: list) -> None:
    """坐标真实性校验：LLM 输出的 lat/lng 必须来自飞猪原文，否则删除（防止编造坐标）。

    允许少量精度误差（0.001°，约 100 米）——LLM 可能截断小数位。
    """
    import re as _re

    # 收集飞猪原文中的所有坐标
    known = set()
    for _, data in flyai_sections:
        for m in _re.finditer(r"坐标[:：]\s*([\d.]+)\s*[,，]\s*([\d.]+)", data or ""):
            known.add((round(float(m.group(1)), 3), round(float(m.group(2)), 3)))

    if not known:
        # 原文无任何坐标 → 全部移除 LLM 输出的坐标
        for item in result.get("timeline", []):
            item.pop("lat", None)
            item.pop("lng", None)
        return

    def _known_coord(lat, lng) -> bool:
        for klat, klng in known:
            if abs(klat - round(float(lat), 3)) <= 0.001 and abs(klng - round(float(lng), 3)) <= 0.001:
                return True
        return False

    for item in result.get("timeline", []):
        lat, lng = item.get("lat"), item.get("lng")
        if lat is None or lng is None:
            continue
        try:
            if not _known_coord(lat, lng):
                item.pop("lat", None)
                item.pop("lng", None)
        except (TypeError, ValueError):
            item.pop("lat", None)
            item.pop("lng", None)


# ── Post-generation validators ─────────────────────────────

_RETURN_KEYWORDS = [
    "返程", "返回", "回程", "送机", "送站", "归程",
    "机场", "航班", "高铁", "动车", "火车", "车次", "大巴", "包车",
]


def _extract_day(time_range: str) -> int | None:
    m = re.search(r"Day\s*(\d+)", str(time_range))
    return int(m.group(1)) if m else None


def _validate_return_transport(result: dict, params: dict) -> list[str]:
    """最后一天必须包含返程交通（从目的地返回出发地）。"""
    issues = []
    timeline = result.get("timeline", [])
    if not timeline:
        return issues

    last_day = 0
    for item in timeline:
        d = _extract_day(item.get("time_range", ""))
        if d:
            last_day = max(last_day, d)
    if last_day == 0:
        return issues

    origin = params.get("origin", "")
    has_return = False
    for item in timeline:
        d = _extract_day(item.get("time_range", ""))
        if d != last_day:
            continue
        text = str(item.get("title", "")) + str(item.get("notes", ""))
        if any(kw in text for kw in _RETURN_KEYWORDS):
            # 确认是"返回 origin"方向，而非其他用途的交通工具
            if origin and (origin in text or "返程" in text or "返回" in text or "送机" in text or "送站" in text):
                has_return = True
                break
            if not origin:
                has_return = True
                break

    if not has_return:
        issues.append(
            f"Day {last_day} 缺少返程交通：行程最后一天必须安排从目的地返回{origin or '出发地'}的"
            f"航班/车次/大巴。若飞猪无返程数据，应在 timeline 标注\"需自行预订返程交通\""
        )
    return issues


def _validate_budget_completeness(result: dict, params: dict) -> list[str]:
    """timeline 中带 ¥ 价格的收费项必须在 budget 中有对应条目。
    支持三种匹配：单价直接出现 / 单价×人数=总价 / 总价÷人数≈单价。"""
    issues = []
    timeline = result.get("timeline", [])
    budget = result.get("budget", [])

    budget_text = " ".join(
        str(b.get("category", "")) + " " + str(b.get("description", "")) + " " + str(b.get("estimated_cost", ""))
        for b in budget
    ).replace(",", "")

    # 收集 budget 各条目的金额数字
    budget_amounts = set()
    for b in budget:
        for m in re.finditer(r"¥\s*([\d,]+)", str(b.get("estimated_cost", ""))):
            budget_amounts.add(float(m.group(1).replace(",", "")))
        for m in re.finditer(r"¥\s*([\d,]+)", str(b.get("description", ""))):
            budget_amounts.add(float(m.group(1).replace(",", "")))

    # 人数
    size_m = re.search(r"\d+", str(params.get("group_size", "")))
    group_size = int(size_m.group()) if size_m else 0

    for item in timeline:
        price_str = str(item.get("price", ""))
        title = str(item.get("title", ""))

        if not price_str or "免费" in price_str or "无费用" in price_str:
            continue
        if "待确认" in price_str or "点击预订" in price_str:
            continue

        amounts = re.findall(r"¥\s*([\d,]+(?:\.\d+)?)", price_str)
        if not amounts:
            continue

        found = False
        for a_str in amounts:
            a = float(a_str.replace(",", ""))
            # 1. 单价直接出现在 budget 中
            if a_str.replace(",", "") in budget_text:
                found = True
                break
            # 2. 总价 = 单价 × 人数 出现在 budget 中
            if group_size and a * group_size in budget_amounts:
                found = True
                break
            # 3. budget 某金额 ÷ 人数 ≈ 单价（±5%）
            if group_size:
                for ba in budget_amounts:
                    if ba / group_size > 0 and abs(ba / group_size - a) / a < 0.05:
                        found = True
                        break
            if found:
                break

        if not found:
            issues.append(
                f"timeline「{title}」价格 {price_str} 未在 budget 中找到对应条目，"
                f"违反规则17（收费项必须全部进预算）"
            )
    return issues


def _calc_total_from_desc(desc: str) -> float | None:
    """从预算 description 的计算式（如 ¥400×15间×1晚）计算总额；无计算式返回 None。
    校验器与代码级修复共用，保证口径一致。"""
    calc_total = 0.0
    has_calc = False

    for group in re.split(r"[+＋]", desc):
        g = group.replace("×", "*").replace("x", "*").replace("X", "*")
        # ¥ 在开头: ¥100*30 或 ¥100*30*2
        m = re.search(r"¥\s*([\d,]+(?:\.\d+)?)\s*[^\d*¥]*\*\s*[^\d*¥]*([\d,]+(?:\.\d+)?)", g)
        if m:
            has_calc = True
            unit = float(m.group(1).replace(",", ""))
            q1 = float(m.group(2).replace(",", ""))
            subtotal = unit * q1
            # 继续匹配后续的 * qty（允许中文单位字符）
            rest = g[m.end():]
            for m2 in re.finditer(r"\*\s*[^\d*¥]*([\d,]+(?:\.\d+)?)", rest):
                subtotal *= float(m2.group(1).replace(",", ""))
            calc_total += subtotal
        else:
            # ¥ 在末尾: 30人*3天*5餐*¥100
            nums = re.findall(r"([\d,]+(?:\.\d+)?)\s*[^\d*+¥]*", g)
            nums = [v for v in nums if v.strip()]
            if len(nums) >= 4 and "¥" in g:
                has_calc = True
                subtotal = 1.0
                for v in nums:
                    subtotal *= float(v.replace(",", ""))
                calc_total += subtotal

    return calc_total if has_calc else None


def _validate_budget_arithmetic(result: dict, params: dict) -> list[str]:
    """校验 budget 单行计算式与结果是否匹配（如 ¥400×15间×1晚 应等于 ¥6000）。"""
    issues = []

    for item in result.get("budget", []):
        if item.get("is_per_person"):
            continue
        desc = str(item.get("description", ""))
        cost_str = str(item.get("estimated_cost", ""))
        stated = _money(cost_str)
        if not stated or not desc:
            continue
        if "待确认" in desc or "需通过预订链接确认" in desc:
            continue

        calc_total = _calc_total_from_desc(desc)
        if calc_total is not None and abs(stated - calc_total) > max(calc_total * 0.05, 10):
            issues.append(
                f"预算「{item.get('category', '')}」({desc}) 算术错误："
                f"按计算式应为 ¥{calc_total:.0f}，实际写 ¥{stated}"
            )
    return issues


def _validate_time_consistency(result: dict) -> list[str]:
    """检测 timeline 中明显的时间矛盾（如写"7:30开园"但排在15:30）。"""
    issues = []

    # 按天分组，检查每天内部时间是否单调递增
    day_items: dict[int, list] = {}
    for item in result.get("timeline", []):
        d = _extract_day(item.get("time_range", ""))
        if d:
            day_items.setdefault(d, []).append(item)

    for day, items in day_items.items():
        prev_hour = -1
        for item in items:
            tr = str(item.get("time_range", ""))
            m = re.search(r"(\d{1,2}):(\d{2})", tr)
            if not m:
                continue
            hour = int(m.group(1))
            if hour < prev_hour:
                issues.append(
                    f"Day {day}「{item.get('title', '')}」({tr}) 时间顺序异常："
                    f"出现在更早时间之后，违反规则24（同一天时间单调递增）"
                )
            prev_hour = max(prev_hour, hour)

    # 检查 notes 中的开放时间声明与实际安排是否矛盾
    for item in result.get("timeline", []):
        text = str(item.get("notes", "")) + str(item.get("title", ""))
        tr = str(item.get("time_range", ""))

        open_m = re.search(r"(\d{1,2})[:：.]?(\d{2})?\s*(?:开园|开门|开放|开始营业)", text)
        if open_m:
            open_hour = int(open_m.group(1))
            sched_m = re.search(r"(\d{1,2}):(\d{2})", tr)
            if sched_m:
                sched_hour = int(sched_m.group(1))
                if abs(sched_hour - open_hour) > 2:
                    issues.append(
                        f"「{item.get('title', '')}」时间矛盾："
                        f"notes 写{open_hour}点开放，但安排在 {sched_hour}点"
                    )
    return issues


def _validate_geographic_feasibility(result: dict) -> list[str]:
    """同一日有坐标的两个活动地点相距>300km 时报警（如成都→九寨沟当日往返）。"""
    from .transport import _haversine_km

    issues = []
    day_items: dict[int, list] = {}

    for item in result.get("timeline", []):
        d = _extract_day(item.get("time_range", ""))
        lat, lng = item.get("lat"), item.get("lng")
        if d and lat is not None and lng is not None:
            try:
                day_items.setdefault(d, []).append({
                    "title": str(item.get("title", "")),
                    "lat": float(lat),
                    "lng": float(lng),
                })
            except (TypeError, ValueError):
                continue

    for day, items in day_items.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                dist = _haversine_km(
                    items[i]["lat"], items[i]["lng"],
                    items[j]["lat"], items[j]["lng"],
                )
                if dist > 200:
                    issues.append(
                        f"Day {day} 地理不可行：「{items[i]['title']}」与"
                        f"「{items[j]['title']}」相距约{dist}km，当日往返不可能完成，"
                        f"违反规则21（禁止超200km景点当日往返）"
                    )
    return issues


def _run_validators(result: dict, params: dict, flyai_sections: list) -> list[str]:
    """运行全部后生成校验，返回问题描述列表。"""
    issues = []
    issues.extend(_validate_return_transport(result, params))
    issues.extend(_validate_budget_completeness(result, params))
    issues.extend(_validate_budget_arithmetic(result, params))
    issues.extend(_validate_time_consistency(result))
    issues.extend(_validate_geographic_feasibility(result))
    return issues


# ── Layered repair: ① code fixes → ② LLM patch → ③ full retry ──

def _apply_code_fixes(result: dict, params: dict) -> None:
    """代码级确定性修复（不调 LLM）：
    1. budget 计算式与结果不符 → 以计算式结果为准改写 estimated_cost
    2. 同一天时间顺序紊乱 → 按时间排序（仅当天所有项均可解析时间时才排，保守处理）
    修复后重算人均预算。原地修改 result。"""
    for item in result.get("budget", []):
        if item.get("is_per_person"):
            continue
        desc = str(item.get("description", ""))
        if not desc or "待确认" in desc or "需通过预订链接确认" in desc:
            continue
        calc = _calc_total_from_desc(desc)
        if calc is None or calc <= 0:
            continue
        stated = _money(item.get("estimated_cost"))
        if stated is None or abs(stated - calc) > max(calc * 0.05, 10):
            item["estimated_cost"] = f"¥{calc:.0f}"

    timeline = result.get("timeline", [])
    day_blocks: dict[int, list[int]] = {}
    for idx, item in enumerate(timeline):
        d = _extract_day(item.get("time_range", ""))
        if d:
            day_blocks.setdefault(d, []).append(idx)

    for idxs in day_blocks.values():
        times = []
        for idx in idxs:
            m = re.search(r"(\d{1,2}):(\d{2})", str(timeline[idx].get("time_range", "")))
            if not m:
                times = None
                break
            times.append((int(m.group(1)), int(m.group(2))))
        if not times:
            continue
        order = sorted(range(len(idxs)), key=lambda k: times[k])
        if order != list(range(len(idxs))):
            sorted_items = [timeline[idxs[k]] for k in order]
            for pos, idx in enumerate(idxs):
                timeline[idx] = sorted_items[pos]

    _recompute_budget_per_person(result, params)


_PATCHABLE_MARKERS = ("缺少返程", "未在 budget 中找到对应条目")

_PATCH_SYSTEM = """你是旅行方案修复助手。给定一个团体出行方案与若干已发现的问题，只修复列出的问题，不做其他任何改动。

输出严格遵循以下 JSON 格式（不要 markdown 代码块）：
{
    "budget": [修复后的完整预算数组：未涉及问题的原有条目原样保留，新增缺失的条目；同一类目只出现一行],
    "timeline_additions": [需要新增的时间线项数组（如缺失的返程交通项）；若无需要新增的项则为空数组]
}

规则：
1. 价格只能来自给定的飞猪搜索结果原文，禁止编造价格与链接
2. 新增预算项必须给出计算式（如"¥550×30人"）与计算结果 estimated_cost，is_per_person 填 false
3. 新增时间线项必须带 time_range（"Day N HH:MM-HH:MM"）、title、price、notes；booking_link 只能填飞猪结果中同一商家的链接，无对应则填 null；返程方向必须是从目的地返回出发地
4. 严禁删除或改写未涉及问题的原有条目；不要输出 summary/action_items/room_allocation/budget_per_person
"""


def _patch_plan_issues(result: dict, issues: list, params: dict, flyai_sections: list,
                       user_id: str = None, plan_id: str = None) -> dict | None:
    """LLM 针对性修补：仅处理可修补问题（缺返程/预算漏项），小上下文生成补丁并合并。

    返回修复后的新方案 dict（深拷贝），不可修补或 LLM 失败返回 None。"""
    if not any(mk in issue for issue in issues for mk in _PATCHABLE_MARKERS):
        return None

    group_size = params.get("group_size", "30")

    # 只带上受影响的时间线项，控制上下文
    affected_titles = []
    for issue in issues:
        m = re.search(r"「(.+?)」", issue)
        if m:
            affected_titles.append(m.group(1))

    affected_items = []
    last_day = 0
    for item in result.get("timeline", []):
        d = _extract_day(item.get("time_range", "")) or 0
        last_day = max(last_day, d)
        text = json.dumps(item, ensure_ascii=False)
        if any(t and t in text for t in affected_titles):
            affected_items.append(item)

    flyai_summary = "\n\n".join(f"【{t}】\n{(d or '')[:1200]}" for t, d in flyai_sections)

    user_prompt = f"""请修复以下问题：
{chr(10).join('- ' + i for i in issues)}

==== 当前 budget ====
{json.dumps(result.get('budget', []), ensure_ascii=False, indent=1)}

==== 受影响的时间线项 ====
{json.dumps(affected_items, ensure_ascii=False, indent=1) if affected_items else "（无）"}

行程共 {last_day} 天，人数 {group_size} 人，出发地 {params.get('origin', '')}。

==== 飞猪搜索结果 ====
{flyai_summary}

只输出修复补丁 JSON。
"""

    patched = _llm.complete_json(_PATCH_SYSTEM, user_prompt, max_tokens=3000,
                                 user_id=user_id, plan_id=plan_id, action="patch_plan_issues")
    if not patched:
        return None

    import copy
    patched_plan = copy.deepcopy(result)

    new_budget = patched.get("budget")
    if isinstance(new_budget, list):
        cleaned = [b for b in new_budget if isinstance(b, dict) and b.get("category")]
        if cleaned:
            patched_plan["budget"] = cleaned

    additions = patched.get("timeline_additions")
    if isinstance(additions, list):
        timeline = patched_plan.setdefault("timeline", [])
        for add in additions:
            if not isinstance(add, dict) or not add.get("title"):
                continue
            # 插入到同一天最后一项之后，保持按天分块的顺序
            d = _extract_day(str(add.get("time_range", "")))
            insert_at = len(timeline)
            if d:
                for idx in range(len(timeline) - 1, -1, -1):
                    if _extract_day(str(timeline[idx].get("time_range", ""))) == d:
                        insert_at = idx + 1
                        break
            timeline.insert(insert_at, add)

    return patched_plan


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
        "summary": f"{_group_label(group_size)}{city}聚会方案",
        "action_items": action_items,
        "timeline": timeline,
        "budget": budget,
        "room_allocation": [],
        "budget_per_person": "待估算",
    }


# ── 按天分片生成（整体生成失败时的兜底） ───────────────────

DAY_SLICE_SYSTEM = """你是团体出行方案结构化助手。基于飞猪搜索结果，为多天行程中的某一天生成结构化 JSON。

输出严格遵循以下 JSON 格式（不要 markdown 代码块）：
{
    "timeline": [
        {"time_range": "Day N HH:MM-HH:MM", "title": "安排内容", "location": "地点",
         "booking_link": "https://... 或 null", "price": "¥XX/人 或 免费 或 点击预订链接查看实时价格",
         "notes": "备注"}
    ],
    "budget": [
        {"category": "餐饮", "description": "¥100×30人", "estimated_cost": "¥3000", "is_per_person": false}
    ],
    "action_items": [
        {"id": "hotel-1", "type": "hotel", "title": "住宿：XX酒店", "description": "...",
         "recommendations": [], "estimated_cost": "约¥400/人/晚", "time_slot": "Day N",
         "assignee": "待分配", "priority": "high", "booking_link": "https://... 或 null"}
    ]
}

规则：
1. 价格必须来自飞猪搜索结果，禁止编造价格/航班号/车次号/链接；无价格填"点击预订链接查看实时价格"，该项不计入预算
2. 收费项（门票、餐饮、演出、交通）必须有对应 budget 条目，description 给出计算式（单价×人数），estimated_cost 给出结果
3. timeline 每天必须有午餐和晚餐安排；时间从早到晚排列，time_range 必须以指定的 "Day N " 开头
4. booking_link 只能填飞猪结果中同一具体商家的链接（指名道姓的酒店/餐厅/车次/航班/景点），否则填 null
5. 若是行程最后一天，必须包含从目的地返回出发地的返程交通；飞猪无返程数据时加一项"需自行预订返程交通"
6. 只输出该天的安排；type 取值：hotel/transport/activity/restaurant
"""


def _group_label(group_size) -> str:
    """人数展示标签：已含"人"则原样，否则补上（避免"12人人"）。"""
    s = str(group_size or "").strip()
    return s if "人" in s else f"{s}人"


def _infer_day_count(params: dict) -> int:
    """无大纲时从入住/退房日期推断天数，推断不出按 2 天。"""
    ci, co = params.get("check_in", ""), params.get("check_out", "")
    try:
        nights = (datetime.fromisoformat(co) - datetime.fromisoformat(ci)).days
        if nights > 0:
            return nights + 1
    except (ValueError, TypeError):
        pass
    return 2


def _merge_day_budgets(day_budgets: list) -> list:
    """按类目合并各天预算行：金额累加、计算式用 + 连接（校验器可逐组验算）。"""
    merged: dict[str, dict] = {}
    order = []
    for rows in day_budgets:
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("category") or row.get("is_per_person"):
                continue
            cat = str(row["category"])
            desc = str(row.get("description", "") or "").strip()
            amount = _money(row.get("estimated_cost"))
            if cat not in merged:
                merged[cat] = {"descs": [], "total": 0.0, "has_pending": False}
                order.append(cat)
            m = merged[cat]
            if desc and desc not in m["descs"]:
                m["descs"].append(desc)
            if amount is not None:
                m["total"] += amount
            else:
                m["has_pending"] = True
    result = []
    for cat in order:
        m = merged[cat]
        desc = " + ".join(m["descs"]) if m["descs"] else cat
        if m["total"] > 0:
            if m["has_pending"]:
                desc += "（另有待确认项）"
            result.append({"category": cat, "description": desc,
                           "estimated_cost": f"¥{m['total']:.0f}", "is_per_person": False})
        else:
            result.append({"category": cat, "description": desc,
                           "estimated_cost": "待确认", "is_per_person": False})
    return result


def _merge_day_action_items(day_items: list) -> list:
    """合并各天 action_items，按 id/标题去重，补齐缺失 id。"""
    seen = set()
    result = []
    for items in day_items:
        for it in items or []:
            if not isinstance(it, dict) or not it.get("title"):
                continue
            key = str(it.get("id") or it.get("title"))
            if key in seen:
                continue
            seen.add(key)
            result.append(it)
    for i, it in enumerate(result):
        if not it.get("id"):
            it["id"] = f"item-{i+1}"
    return result


def _generate_plan_by_days(params: dict, flyai_sections: list, outline: dict | None = None,
                           user_id: str = None, plan_id: str = None) -> dict | None:
    """按天分片生成方案（仅当整体生成失败时兜底，规避超长输出截断）。

    每天 1 次 LLM 调用（输出短），代码层聚合预算/action_items/房间分配。
    全部天都失败返回 None（调用方再走 _fallback_structured）。
    """
    city = params.get("city", "")
    group_size = params.get("group_size", "30")
    origin = params.get("origin", "")
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")

    outline_days = [d for d in ((outline or {}).get("days") or []) if isinstance(d, dict)]
    total = len(outline_days) if outline_days else _infer_day_count(params)
    if total <= 0:
        return None

    flyai_summary = "\n\n".join(f"【{t}】\n{(d or '')[:1500]}" for t, d in flyai_sections)

    timeline, day_budgets, day_actions, day_summaries = [], [], [], []
    for day in range(1, total + 1):
        frame = ""
        if day <= len(outline_days):
            frame = f"当天框架（硬约束）：{json.dumps(outline_days[day - 1], ensure_ascii=False)}\n"
        prev_tail = ""
        if timeline:
            prev_tail = "前一天末尾安排（动线衔接用）：" + "；".join(
                f"{it.get('time_range', '')} {it.get('title', '')}" for it in timeline[-2:]
            ) + "\n"
        return_note = (
            f"当天是行程最后一天，必须包含从{city}返回{origin or '出发地'}的返程交通。\n"
            if day == total else ""
        )
        user_prompt = f"""为以下行程的第 {day}/{total} 天生成结构化 JSON。

城市：{city}；出发地：{origin}；入住：{check_in}；退房：{check_out}；人数：{group_size}人
{frame}{prev_tail}{return_note}==== 飞猪搜索结果 ====
{flyai_summary}

time_range 必须以 "Day {day} " 开头（如 "Day {day} 09:00-11:00"）。只输出该天 JSON。
"""
        day_result = _llm.complete_json(DAY_SLICE_SYSTEM, user_prompt, max_tokens=2000,
                                        user_id=user_id, plan_id=plan_id,
                                        action="generate_plan_day_slice")
        if not day_result:
            day_summaries.append(f"Day {day} 生成失败")
            continue
        for it in day_result.get("timeline") or []:
            tr = str(it.get("time_range", "") or "")
            if f"Day {day}" not in tr:
                it["time_range"] = f"Day {day} {tr}".strip()
        timeline.extend(day_result.get("timeline") or [])
        day_budgets.append(day_result.get("budget") or [])
        day_actions.append(day_result.get("action_items") or [])
        day_summaries.append(f"Day {day} 完成")

    if not timeline:
        return None

    result = {
        "summary": f"{_group_label(group_size)}{city}行程（{total}天{max(total - 1, 0)}晚，按天分片生成："
                   + "、".join(day_summaries) + "）",
        "action_items": _merge_day_action_items(day_actions),
        "timeline": timeline,
        "budget": _merge_day_budgets(day_budgets),
        "room_allocation": [],
        "budget_per_person": "待估算",
        "generated_by": "day_slice",
    }

    # 房间分配用代码计算，不依赖 LLM
    size_m = re.search(r"\d+", str(group_size))
    size = int(size_m.group()) if size_m else 0
    if size > 0:
        rooms = calculate_room_allocation(size)
        hotel_item = next((a for a in result["action_items"] if a.get("type") == "hotel"), None)
        if hotel_item:
            rec = (hotel_item.get("recommendations") or [{}])[0]
            for r in rooms:
                r["hotel_name"] = rec.get("name", hotel_item.get("title", ""))
                r["hotel_link"] = rec.get("booking_link")
                if rec.get("price"):
                    r["price_per_night"] = rec["price"]
        result["room_allocation"] = rooms

    _recompute_budget_per_person(result, params)
    return result


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


def revise_plan(params: dict, current_plan: dict, revision_message: str, user_id: str | None = None, plan_id: str | None = None) -> dict | None:
    """基于用户反馈修改现有方案。

    Args:
        params: 原始用户参数
        current_plan: 当前结构化方案
        revision_message: 用户的修改要求
        user_id: 用户ID（用于追踪 AI 使用量）
        plan_id: 方案ID（用于追踪 AI 使用量）

    Returns:
        修改后的结构化方案；生成失败时返回 None。
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

    result = _llm.complete_json(REVISE_PLAN_SYSTEM, user_prompt, max_tokens=4000, use_fast_model=True,
                                user_id=user_id, plan_id=plan_id, action="revise_plan")
    if not result:
        return None

    _recompute_budget_per_person(result, {"group_size": group_size})
    _compute_days_nights(result, current_plan.get("params", {}))
    return result