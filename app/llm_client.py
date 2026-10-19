"""百炼 / 通义千问（OpenAI 兼容接口）封装。

可选层：未配置 DASHSCOPE_API_KEY 时返回 None，由编排层降级为模板拼接。
"""
import httpx

from . import config

_SYSTEM_PROMPT = """你是一个专业的旅行规划助手，服务于「聚行」——一款面向活动/聚会场景的 AI 出行助手。
你将拿到飞猪 AI 搜索返回的真实结果（含可点击的预订链接），需要为用户产出一份**结构化、可分享**的出行方案。

要求：
1. 使用 Markdown，条理清晰、分段小标题。
2. 必须包含：方案概览、推荐住宿（务必保留原始预订链接）、推荐玩法/景点、餐饮与实用 Tips。
3. 若涉及外地出行（有出发地），补一段交通建议。
4. 语气亲切，像真人导游，不要堆砌技术术语。
5. 严格基于提供的结果，不要编造价格、评分或不存在的链接；票价/房态以飞猪实时为准。
"""


def complete(user_prompt: str, max_tokens: int = 1600):
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
            {"role": "system", "content": _SYSTEM_PROMPT},
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
