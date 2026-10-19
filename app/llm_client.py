"""百炼 / 通义千问（OpenAI 兼容接口）封装。

可选层：未配置 DASHSCOPE_API_KEY 时返回 None，由编排层降级为模板拼接。
按场景（event 聚会 / explore 探索）给出不同结构的润色提示，强化聚行的差异化。
"""
import httpx

from . import config

_BASE_EVENT = """你是一个专业的「活动/聚会出行」规划助手，服务于「聚行」——一款面向活动与聚会场景的 AI 出行助手。
你将拿到飞猪 AI 搜索返回的真实结果（含可点击的预订链接），需要为用户产出一份**结构化、可分享**的聚会出行方案。

要求：
1. 使用 Markdown，条理清晰、分段小标题，语气亲切像真人统筹。
2. 必须包含以下板块（按序）：
   - 方案概览（一句话定调 + 关键信息：城市、日期、人数、氛围）
   - 住宿建议（务必保留原始预订链接；聚会场景优先推荐含会议室/宴会厅、可统一预订的酒店）
   - 集体交通与签到（若有外地来宾，给出接站/接机与统一签到建议）
   - 分组活动玩法（把景点拆成可分组打卡的半日/一日动线）
   - 聚餐推荐（保留餐厅预订链接，给出包间/拼桌建议）
   - 实用 Tips（预算、天气、注意事项）
3. 严格基于提供的结果，不要编造价格、评分或不存在的链接；房态/票价以飞猪实时为准。
4. 突出"替团体省心"的价值：统一预订、集体动线、签到接驳。
"""

_BASE_EXPLORE = """你是一个专业的旅行规划助手，服务于「聚行」——一款 AI 出行助手。
你将拿到飞猪 AI 搜索返回的真实结果（含可点击的预订链接），需要为用户产出一份**结构化、可分享**的出行方案。

要求：
1. 使用 Markdown，条理清晰、分段小标题，语气亲切像真人导游。
2. 必须包含以下板块（按序）：
   - 方案概览（城市、日期、偏好一句话定调）
   - 推荐住宿（务必保留原始预订链接）
   - 必玩玩法（景点与本地特色，给出一日/半日动线）
   - 美食推荐（保留餐厅预订链接）
   - 交通建议（若有外地出发）
   - 实用 Tips
3. 严格基于提供的结果，不要编造价格、评分或不存在的链接；房态/票价以飞猪实时为准。
"""

_SYSTEM_PROMPTS = {
    "event": _BASE_EVENT,
    "explore": _BASE_EXPLORE,
}


def complete(user_prompt: str, mode: str = "event", max_tokens: int = 1800):
    if not config.DASHSCOPE_API_KEY:
        return None
    system = _SYSTEM_PROMPTS.get(mode, _BASE_EXPLORE)
    url = config.QWEN_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
