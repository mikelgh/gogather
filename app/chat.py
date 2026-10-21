"""对话式规划：一句话输入 → 参数提取 → 智能追问 → 方案就绪。"""
import json
import re

from . import llm_client as _llm

EXTRACT_SYSTEM = """你是一个旅行规划参数提取助手。从用户的自然语言描述中提取以下参数：

必须提取的字段：
- city: 目标城市（必填）
- check_in: 入住日期，YYYY-MM-DD 格式
- check_out: 退房日期，YYYY-MM-DD 格式
- origin: 出发地
- group_size: 人数（如 "30" 或 "30人"）
- vibe: 氛围/偏好/主题
- budget_tier: 预算档次（"经济型"、"舒适型"、"豪华型"，默认"舒适型"）
- activity_type: 活动类型（如 "团建"、"同学聚会"、"婚礼"、"观赛"）
- children_count: 儿童数量（如 "3个小孩"、"带2个孩子"）
- children_ages: 儿童年龄描述（如 "5岁和8岁"、"都是小学生"）
- elderly_count: 老人数量（如 "2位老人"、"带父母"）
- accessibility: 无障碍需求（如 "有无障碍需求"、"有轮椅"、"行动不便"）
- transport_mode: 交通方式偏好（"flight" 飞机、"train" 高铁/动车、"auto" 自动推荐，默认 "auto"）
- preferences: 偏好标签列表（如 ["美食", "风景", "文化"]，可选值：美食、风景、休闲、夜生活、文化、户外、购物、亲子）

规则：
1. 今天是 {today}，所有相对日期（如"下周五"、"10月25日"）都转为 YYYY-MM-DD。
2. 如果某字段未提及，设为 null。
3. 从活动类型和上下文推断缺失信息（如"同学聚会"暗示 group_size ≥ 10）。
4. 判断信息是否足够生成方案。最少需要：city + (check_in 或 activity_type)。
5. 如果信息不足，生成 1-3 个简短追问，不要重复已提取的信息。
6. 如果 budget_tier 未提及，在追问中询问预算档次偏好。
7. 如果团体中有儿童或老人，追问年龄和无障碍需求以便推荐合适的活动。
8. 如果 origin 和 city 都已提取，但 check_in 未指定，必须追问出行日期（如"请问计划哪天出发？"）。
9. 如果 origin 和 city 都已提取，且用户未指定 transport_mode，根据距离判断是否追问交通方式：
   - 近距离（≤600km，如上海→杭州）：默认推荐高铁，追问中告知用户"已为您优先推荐高铁"
   - 中距离（600-1200km，如上海→北京）：追问"您偏好飞机还是高铁？"
   - 远距离（>1200km，如北京→广州）：默认推荐飞机，追问中告知用户"已为您优先推荐飞机"
   - 用户提到"高铁"、"动车"、"火车" → transport_mode="train"
   - 用户提到"飞机"、"机票"、"航班" → transport_mode="flight"

严格返回 JSON（不要 markdown 代码块）：
{{
    "extracted": {{"city": "...", "check_in": "...", "budget_tier": "...", "transport_mode": "auto", "children_count": "...", "preferences": [...], ...}},
    "sufficient": false,
    "follow_up": "追问内容（如果 sufficient=false）"
}}
"""


def _build_extract_system() -> str:
    from datetime import date
    return EXTRACT_SYSTEM.format(today=date.today().isoformat())


def _clean_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_params(user_message: str, conversation: list[dict] | None = None, user_id: str = None) -> dict:
    """从用户消息中提取参数。返回 {extracted, sufficient, follow_up}。"""
    system = _build_extract_system()

    history = ""
    if conversation:
        msgs = []
        for m in conversation[-6:]:
            role = "用户" if m.get("role") == "user" else "助手"
            msgs.append(f"{role}：{m.get('content', '')}")
        history = "\n".join(msgs) + "\n"

    user_prompt = (
        f"{history}用户最新消息：{user_message}\n\n"
        "请提取参数并判断是否足够生成方案。"
    )

    result = _llm.complete_json(system, user_prompt, use_fast_model=True,
                                user_id=user_id, action="extract_params")
    if not result:
        return {
            "extracted": _fallback_extract(user_message),
            "sufficient": False,
            "follow_up": "我需要确认一下：你的目标城市是哪里？计划什么时候出发？",
        }
    return result


def _fallback_extract(msg: str) -> dict:
    """LLM 失败时的简单正则兜底。"""
    params = {}
    city_match = re.search(r"去(\S{2,4})(?:玩|聚|旅|出)", msg)
    if city_match:
        params["city"] = city_match.group(1)
    num_match = re.search(r"(\d+)\s*人", msg)
    if num_match:
        params["group_size"] = num_match.group(1)
    return params


def merge_params(existing: dict, new_params: dict) -> dict:
    """合并参数，新值覆盖旧值（旧值为 null 时补入新值）。"""
    merged = dict(existing)
    for k, v in new_params.items():
        if v is not None and v != "":
            merged[k] = v
        elif k not in merged:
            merged[k] = None
    return merged


def is_sufficient(params: dict) -> bool:
    """判断参数是否足够生成方案。"""
    if not params.get("city"):
        return False
    if not params.get("check_in") and not params.get("activity_type"):
        return False
    # 若有出发地（需要交通），必须指定日期
    if params.get("origin") and params.get("origin") != params.get("city", ""):
        if not params.get("check_in"):
            return False
    return True


def params_to_plan_input(params: dict) -> dict:
    """将提取的参数转为 planner.plan() 需要的格式。"""
    return {
        "mode": "event" if params.get("activity_type") else "explore",
        "city": params.get("city", ""),
        "check_in": params.get("check_in", ""),
        "check_out": params.get("check_out", ""),
        "origin": params.get("origin", ""),
        "group_size": params.get("group_size", ""),
        "vibe": params.get("vibe", ""),
        "budget_tier": params.get("budget_tier", "舒适型"),
        "transport_mode": params.get("transport_mode", "auto"),
        "children_count": params.get("children_count"),
        "children_ages": params.get("children_ages"),
        "elderly_count": params.get("elderly_count"),
        "accessibility": params.get("accessibility"),
        "preferences": params.get("preferences", []),
    }
