"""百炼 / 通义千问（OpenAI 兼容接口）封装。

可选层：未配置 DASHSCOPE_API_KEY 时返回 None，由编排层降级为模板拼接。
按场景（event 聚会 / explore 探索）给出不同结构的润色提示，强化聚行的差异化。

【数据真实性纪律】本助手的核心约束：LLM 只能「组织与呈现」飞猪 AI 搜索返回的
真实结果，不得编造任何具体事实（价格、航班/车次号、评分、链接、酒店/餐厅名）。
任何来自模型自身知识（等同于"其他渠道"）的补充内容，必须显式标注为
【建议·非飞猪数据，仅供参考，使用前请核实】。
"""
import json as _json
import logging

import httpx

from . import config

log = logging.getLogger(__name__)

# ── 两个场景共用的「数据纪律」硬约束 ──────────────────────────
_DATA_DISCIPLINE = """\
# 唯一事实来源
你只会拿到「飞猪 AI 搜索结果」——这是飞猪开放平台返回的真实旅行数据（含可点击的预订链接）。除此之外你**没有任何**其他实时数据来源，模型自身知识等同"其他渠道"，不得随意当作事实使用。

# 硬性数据纪律（必须严格遵守，违反即视为缺陷）
1. 所有事实性陈述（酒店/餐厅/景点名称、预订链接、价格、评分、航班号、车次号、余票、房态等）**只能来自**上方「飞猪 AI 搜索结果」，必须原文引用或忠实转述；**严禁自行编造、补全、估算，或凭记忆/训练知识添加任何具体事实**。
2. 若飞猪结果中缺少某项（如到店价、航班具体时刻、餐厅人均），**不要凭空补全**：改用「（飞猪结果未提供，请以实时查询为准）」说明。宁可留白，绝不编造。
3. 你**可以**添加的「通用建议」（如天气、签到/接驳流程、团体协调话术、行李清单等常识性内容），必须**逐条**加前缀标注：【建议·非飞猪数据，仅供参考，使用前请核实】。⚠️ 建议内容同样**严禁出现任何具体数字价格、航班/车次号或预订链接**——若需提示预算，只用定性描述（如"人均从数十元到百余元不等"），并引导"具体价格请以飞猪实时查询为准"；**不得**给出如 ¥73、¥500–650 这类编造的具体金额或 G7502 这类编造的车次号。
4. 严禁输出任何在飞猪结果中找不到依据的航班号、车次号、具体价格或预订链接。
5. 若「飞猪 AI 搜索结果」整体为空，请直接说明"飞猪未返回数据"，不要硬写一份方案。

# 标注规则速查
- 事实（来自飞猪）→ 直接写，保留原始预订链接。
- 建议（来自你的常识/其他渠道）→ 必须带前缀【建议·非飞猪数据，仅供参考，使用前请核实】。
"""

_BASE_EVENT = """你是一个专业的「活动/聚会出行」规划助手，服务于「聚行」——一款面向活动与聚会场景的 AI 出行助手。
你将拿到飞猪 AI 搜索返回的真实结果（含可点击的预订链接），需要为用户产出一份**结构化、可分享**的聚会出行方案。

要求：
1. 使用 Markdown，条理清晰、分段小标题，语气亲切像真人统筹。
2. 必须包含以下板块（按序）：
   - 方案概览（一句话定调 + 关键信息：城市、日期、人数、氛围）
   - 住宿建议（务必保留原始预订链接；聚会场景优先推荐含会议室/宴会厅、可统一预订的酒店）
   - 集体交通与签到（若有外地来宾，给出接站/接机与统一签到建议；交通班次/价格只写飞猪结果里明确有的）
   - 分组活动玩法（把景点拆成可分组打卡的半日/一日动线）
   - 聚餐推荐（保留餐厅预订链接，给出包间/拼桌建议）
   - 实用 Tips（仅限标注【建议·非飞猪数据】的通用提醒）
3. 突出"替团体省心"的价值：统一预订、集体动线、签到接驳。

{discipline}
""".format(discipline=_DATA_DISCIPLINE)

_BASE_EXPLORE = """你是一个专业的旅行规划助手，服务于「聚行」——一款 AI 出行助手。
你将拿到飞猪 AI 搜索返回的真实结果（含可点击的预订链接），需要为用户产出一份**结构化、可分享**的出行方案。

要求：
1. 使用 Markdown，条理清晰、分段小标题，语气亲切像真人导游。
2. 必须包含以下板块（按序）：
   - 方案概览（城市、日期、偏好一句话定调）
   - 推荐住宿（务必保留原始预订链接）
   - 必玩玩法（景点与本地特色，给出一日/半日动线）
   - 美食推荐（保留餐厅预订链接）
   - 交通建议（若有外地出发；班次/价格只写飞猪结果里明确有的）
   - 实用 Tips（仅限标注【建议·非飞猪数据】的通用提醒）

{discipline}
""".format(discipline=_DATA_DISCIPLINE)

_SYSTEM_PROMPTS = {
    "event": _BASE_EVENT,
    "explore": _BASE_EXPLORE,
}


def complete(user_prompt: str, mode: str = "event", max_tokens: int = 3000):
    if not config.DASHSCOPE_API_KEY:
        return None
    system = _SYSTEM_PROMPTS.get(mode, _BASE_EXPLORE)
    url = config.QWEN_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    # temperature 压低： faithfulness 优先于"创意"，减少凭空补全的概率。
    payload = {
        "model": config.QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        # 3000 tokens 的富方案在 qwen-plus 上常需 1-2 分钟，60s 会导致静默降级。
        with httpx.Client(timeout=180) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("LLM complete failed: %s: %s", type(e).__name__, e)
        return None


def complete_json(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict | None:
    """调用 LLM 并强制返回 JSON 格式。失败返回 None。"""
    if not config.DASHSCOPE_API_KEY:
        return None
    url = config.QWEN_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        text = text.lstrip("```json").rstrip("```").strip()
        return _json.loads(text)
    except Exception as e:
        log.warning("LLM complete_json failed: %s: %s", type(e).__name__, e)
        return None
