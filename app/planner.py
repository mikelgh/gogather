"""规划编排：把用户输入 -> 飞猪查询 -> （可选）百炼润色 -> 可分享方案。

聚行的差异化：不是"去哪玩"的通用旅游规划，而是"围绕一场活动/聚会来规划出行"——
替团体操心统一预订、集体签到接驳、分组活动、聚餐，因此 event 与 explore 两套结构不同。
"""
from . import flyai_client as flyai
from . import llm_client as llm


def build_scenario_text(inp: dict) -> str:
    mode = inp.get("mode", "event")
    label = "活动/聚会出行" if mode == "event" else "目的地探索"
    lines = [f"场景类型：{label}", f"目标城市：{inp.get('city', '')}"]
    if inp.get("check_in"):
        lines.append(f"入住：{inp.get('check_in')}　退房：{inp.get('check_out', '')}")
    if inp.get("origin"):
        lines.append(f"出发地：{inp.get('origin')}")
    if inp.get("group_size"):
        lines.append(f"参与人数：{inp.get('group_size')}")
    if inp.get("vibe"):
        lines.append(f"氛围/偏好：{inp.get('vibe')}")
    return "\n".join(lines)


def build_sections(inp: dict) -> tuple:
    """按场景发起若干飞猪查询。

    返回 (sections, warnings)：
    - sections: [(标题, 正文), ...]（仅含非空结果）
    - warnings: 查询失败/返回空时的可读提示，便于前端明确报错而非静默装死
    """
    city = inp.get("city", "")
    check_in = inp.get("check_in", "")
    check_out = inp.get("check_out", "")
    origin = inp.get("origin", "")
    vibe = inp.get("vibe", "")
    group = inp.get("group_size", "")
    mode = inp.get("mode", "event")

    sections = []
    warnings = []

    def _call(title: str, query: str):
        r = flyai.ai_search(query)
        data = flyai.extract_data(r)
        if data:
            sections.append((title, data))
        else:
            status = r.get("status")
            msg = (r.get("message") or "").strip()
            warnings.append(
                f"{title}：飞猪未返回数据（status={status}"
                + (f"，{msg}" if msg else "")
                + "）"
            )
        return r

    # 1) 住宿
    if mode == "event":
        q_hotel = f"{city} 适合{group or '团体'}举办活动聚会的酒店"
        if group:
            q_hotel += f"（可容纳{group}）"
        q_hotel += " 含会议室或宴会厅"
    else:
        q_hotel = f"{city} 高性价比酒店推荐"
    if check_in:
        q_hotel += f" {check_in}入住"
    if vibe:
        q_hotel += f" 偏好{vibe}"
    _call("🏨 住宿", q_hotel)

    # 2) 玩法 / 景点
    q_poi = f"{city} 必玩景点与本地特色玩法"
    if vibe:
        q_poi += f" 偏好{vibe}"
    if mode == "event":
        q_poi += " 适合团体打卡"
    _call("🎡 玩法", q_poi)

    # 3) 美食聚餐 —— 聚会场景的核心环节
    q_food = f"{city} 适合{group or '团体'}聚餐的特色餐厅 本地必吃榜"
    if vibe:
        q_food += f" 偏好{vibe}"
    _call("🍜 美食聚餐", q_food)

    # 4) 外地出行 -> 交通
    if origin and origin != city:
        q_f = f"{origin} 到 {city} 机票/高铁"
        if check_in:
            q_f += f" {check_in}出发"
        _call("✈️ 交通", q_f)

    return sections, warnings


def plan(inp: dict) -> dict:
    mode = inp.get("mode", "event")
    scenario = build_scenario_text(inp)
    sections, warnings = build_sections(inp)

    raw = "\n\n".join(f"【{title}】\n{data}" for title, data in sections)

    user_prompt = (
        f"{scenario}\n\n飞猪 AI 搜索结果（含可点击预订链接）：\n{raw}\n\n"
        f"请基于以上真实结果，产出一份完整、可分享的出行方案。"
    )
    composed = llm.complete(user_prompt, mode=mode) if raw else None

    if composed:
        markdown = composed
        used_llm = True
    else:
        # 降级：直接拼装飞猪原始结果（仍保持清晰结构）
        header = f"# {inp.get('city', '')} 出行方案\n\n> {scenario}\n\n"
        body = "\n\n".join(
            f"## {title}\n\n{data}" for title, data in sections
        )
        if mode == "event":
            body += (
                "\n\n## 🤝 团体出行贴士\n\n- 建议统一预订住宿与聚餐，便于签到与接驳；"
                "人数较多时优先选择含会议室/宴会厅的酒店。\n"
                "- 外地来宾可统一安排接站/接机，减少分散到达的协调成本。"
            )
        markdown = header + body
        used_llm = False

    # 没有任何飞猪数据时，给出明确的可排查告警（而非渲染空方案）
    if not sections:
        warnings.append(
            "⚠️ 全部飞猪查询均无返回。常见原因：① 服务进程未加载 .env（请重启服务）；"
            "② 未配置 FLYAI_API_KEY，命中公共体验额度且已限流；③ 网络/CLI 异常。"
        )

    return {
        "scenario": scenario,
        "markdown": markdown,
        "sections": [{"title": t, "data": d} for t, d in sections],
        "warnings": warnings,
        "used_llm": used_llm,
        "mode": mode,
        "city": inp.get("city", ""),
    }
