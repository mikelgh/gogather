"""规划编排：把用户输入 -> 飞猪查询 -> （可选）百炼润色 -> 可分享方案。

聚行的差异化：不是"去哪玩"的通用旅游规划，而是"围绕一场活动/聚会来规划出行"——
替团体操心统一预订、集体签到接驳、分组活动、聚餐，因此 event 与 explore 两套结构不同。

数据真实性（硬约束）：
- 所有事实性信息均来自飞猪 AI 搜索的真实返回；LLM 仅作组织与呈现。
- 任何来自模型自身知识（等同"其他渠道"）的补充，必须逐条标注【建议·非飞猪数据】。
- 严禁编造航班号/车次号/价格/评分/预订链接。
- 兜底机制：若润色结果仍出现飞猪原始数据中没有的具体数据，先重试一次（把违规项
  反馈给模型要求其删除）；若仍不合规，则直接回退到飞猪原始数据直出（零编造保证）。
"""
import re

from . import flyai_client as flyai
from . import llm_client as llm

# 数据来源说明（追加到每个方案末尾，明确哪些是飞猪实时数据、哪些是通用建议）
_DATA_NOTE = """\
## 📋 数据说明

- 本方案中的**酒店、景点、餐厅、交通等事实信息，均来自飞猪 AI 实时搜索结果**（含原文预订链接）；房态 / 票价 / 余票以飞猪实时为准。
- 文中标注 **【建议·非飞猪数据，仅供参考，使用前请核实】** 的内容为通用出行建议，非飞猪实时数据，使用前请自行核实。
- 若某板块显示「飞猪未返回数据」，说明该次查询未命中，请重试或更换关键词。

> 聚行 · 数据真实性承诺：不编造航班号 / 车次号 / 价格 / 评分 / 预订链接。
"""

# 疑似编造检测：扫描润色文本里"飞猪原始数据中没有"的具体价格/航班/车次。
# 用环视 (?<![A-Za-z0-9...]) / (?![0-9...]) 而非 \b——\b 在中日韩文字与 ASCII
# 交界处会失效（如"如G7502"匹配不到）。同时兼容全角字母/数字（模型偶尔输出全角）。
_PRICE_RE = re.compile(r"[¥￥]\s*[0-9０-９][0-9０-９,，]*")
_FLIGHT_RE = re.compile(r"(?<![A-Za-z0-9Ａ-Ｚａ-ｚ])[A-Za-zＡ-Ｚａ-ｚ]{2}[0-9０-９]{3,4}(?![0-9０-９])")
_TRAIN_RE = re.compile(r"(?<![A-Za-z0-9Ａ-Ｚａ-ｚ])[GDCKZgdckzＧＤＣＫＺｇｄｃｋｚ][0-9０-９]{2,4}(?![0-9０-９])")


def detect_fabrication(composed: str, raw: str) -> list:
    """返回润色文本中、飞猪原始数据里【没有】的具体价格/航班号/车次号（去重列表）。

    若某数字/编号确实来自飞猪原文，则它会出现在 raw 中，不会被误报。
    """
    suspects: list = []
    # 把 raw 中的数字（含全角）压成连续串，便于"价格数字是否出现在原文"的判断
    raw_digits = re.sub(r"[^\d]", "", raw)
    for m in _PRICE_RE.findall(composed):
        num = re.sub(r"\D", "", m)  # \D 也会保留全角数字（Python re 默认 Unicode）
        if num and num not in raw_digits:
            suspects.append(m.strip())
    for pat in (_FLIGHT_RE, _TRAIN_RE):
        for m in pat.findall(composed):
            if m not in raw:
                suspects.append(m)
    seen = []
    for s in suspects:
        if s not in seen:
            seen.append(s)
    return seen


def flag_suspect_fabrication(composed: str, raw: str) -> list:
    """detect_fabrication 的告警包装，便于直接 extend 进 warnings。"""
    toks = detect_fabrication(composed, raw)
    if toks:
        return [
            "⚠️ 润色文本中出现飞猪原始数据中未包含的具体数据（"
            + "、".join(toks)
            + "），可能为模型补充，请以飞猪实时查询核实或仅作【建议】参考。"
        ]
    return []


def _build_user_prompt(scenario: str, raw: str) -> str:
    return (
        f"{scenario}\n\n"
        "==== 飞猪 AI 搜索结果（这是唯一的事实来源，含可点击预订链接）====\n"
        f"{raw}\n"
        "==== 飞猪结果结束 ====\n\n"
        "请严格基于上方「飞猪 AI 搜索结果」产出方案：事实性内容只能来自该结果并保留原始链接；"
        "任何模型自身知识的补充都必须逐条标注【建议·非飞猪数据，仅供参考，使用前请核实】，"
        "且不得混入具体价格、航班/车次号或预订链接。不要编造任何飞猪结果中没有的信息。"
    )


def _raw_markdown(inp: dict, sections: list) -> str:
    """纯飞猪原始数据直出（零编造保证）：只拼装真实返回，绝不添加任何编造。"""
    mode = inp.get("mode", "event")
    header = f"# {inp.get('city', '')} 出行方案\n\n> {build_scenario_text(inp)}\n\n"
    body = "\n\n".join(f"## {title}\n\n{data}" for title, data in sections)
    if mode == "event":
        body += (
            "\n\n## 🤝 团体出行贴士\n\n- 建议统一预订住宿与聚餐，便于签到与接驳；"
            "人数较多时优先选择含会议室/宴会厅的酒店。\n"
            "- 外地来宾可统一安排接站/接机，减少分散到达的协调成本。"
            "（以上为通用建议，非飞猪实时数据，使用前请核实）"
        )
    return header + body


def _polish(inp: dict, scenario: str, raw: str, mode: str, sections: list):
    """调用百炼润色，并做"零编造"兜底。

    返回 (markdown, warnings, used_llm)。
    - 首次润色若干净 -> 直接返回。
    - 若检测到疑似编造 -> 重试一次（把违规项反馈给模型，要求仅删除违规部分）。
    - 重试仍不干净 -> 回退到 _raw_markdown（飞猪原始数据直出，确保零编造）。
    """
    base = _build_user_prompt(scenario, raw)
    composed = llm.complete(base, mode=mode)
    if not composed:
        return None, [], False

    toks = detect_fabrication(composed, raw)
    if not toks:
        return composed, [], True

    # 重试一次：把违规项明确反馈，要求只删除这些具体数字/编号，其余保持不变
    retry = (
        base
        + "\n\n【系统校验·必须修正】你上一版输出含有飞猪结果中不存在的具体数据（"
        + "、".join(toks)
        + "），属疑似编造。请只删除这些具体数字 / 航班号 / 车次号，其余内容保持不变重新输出；"
        "若需要预算类建议，仅用定性描述（如'人均从数十元到百余元不等'），不要出现具体金额或编号。"
    )
    composed2 = llm.complete(retry, mode=mode)
    if composed2 and not detect_fabrication(composed2, raw):
        return composed2, [], True

    # 仍不合规 -> 兜底：飞猪原始数据直出（零编造保证）
    return (
        _raw_markdown(inp, sections),
        ["⚠️ 润色两次仍含疑似编造内容，已切换为飞猪原始数据直出（确保零编造）。"],
        False,
    )


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

    if raw:
        markdown, fab_w, used_llm = _polish(inp, scenario, raw, mode, sections)
    else:
        markdown, fab_w, used_llm = None, [], False

    # 润色失败（无 Key / 超时）或回退时，用纯飞猪原始数据直出
    if not markdown:
        markdown = _raw_markdown(inp, sections)
        used_llm = False

    if fab_w:
        warnings.extend(fab_w)

    # 数据来源说明：无论走哪条路径，都明确标注数据出处（真实性承诺）
    markdown = markdown.rstrip() + "\n\n" + _DATA_NOTE

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
