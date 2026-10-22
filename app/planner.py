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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
        "若飞猪美食结果包含具体餐厅/宴会商家，聚餐推荐板块必须列出 2-3 家候选（含各自特色与链接），"
        "不要只挑一家；确实只有一家时才写一家。"
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
    transport_mode = inp.get("transport_mode", "auto")
    transport_labels = {"flight": "飞机", "train": "高铁/动车", "auto": "自动推荐"}
    if inp.get("origin") and inp.get("origin") != inp.get("city", ""):
        lines.append(f"交通偏好：{transport_labels.get(transport_mode, '自动推荐')}")
    return "\n".join(lines)


def build_sections(inp: dict) -> tuple:
    """并行发起飞猪查询（优先结构化接口，带出真实价格与预订链接）。

    返回 (sections, warnings)：
    - sections: [(标题, 正文), ...]（仅含非空结果）
    - warnings: 查询失败/返回空时的可读提示，便于前端明确报错而非静默装死
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    city = inp.get("city", "")
    check_in = inp.get("check_in", "")
    check_out = inp.get("check_out", "")
    origin = inp.get("origin", "")
    vibe = inp.get("vibe", "")
    group = inp.get("group_size", "")
    mode = inp.get("mode", "event")

    sections = []
    warnings = []

    def _fmt(result, limit: int = 8) -> str:
        """结构化 dict → 带价格链接的文本；纯文本 → 原样。"""
        data = flyai.extract_data(result)
        if isinstance(data, dict):
            return flyai.format_structured(data, limit=limit)
        return (data or "").strip()

    def _query_hotel():
        """查询住宿"""
        hotel_opts = {"dest_name": city}
        if check_in:
            hotel_opts["check_in_date"] = check_in
        if check_out:
            hotel_opts["check_out_date"] = check_out
        result = flyai.search_hotel(**hotel_opts)
        text = _fmt(result, limit=10)
        
        if not text:
            # fallback to ai-search
            q_hotel = f"{city} 适合{group or '团体'}举办活动聚会的酒店"
            if group:
                q_hotel += f"（可容纳{group}）"
            q_hotel += " 含会议室或宴会厅"
            if check_in:
                q_hotel += f" {check_in}入住"
            if vibe:
                q_hotel += f" 偏好{vibe}"
            result = flyai.ai_search(q_hotel)
            text = _fmt(result, limit=10)
            title = "🏨 住宿（备选）"
        else:
            title = "🏨 住宿（含实时房价）"
        
        return (title, text, result)

    def _query_poi():
        """查询景点"""
        result = flyai.search_poi(city_name=city)
        text = _fmt(result, limit=10)
        
        if not text:
            q_poi = f"{city} 必玩景点与本地特色玩法"
            if vibe:
                q_poi += f" 偏好{vibe}"
            if mode == "event":
                q_poi += " 适合团体打卡"
            result = flyai.ai_search(q_poi)
            text = _fmt(result, limit=10)
            title = "🎡 玩法（备选）"
        else:
            title = "🎡 玩法（含门票信息）"
        
        return (title, text, result)

    def _query_food(area_hints: list | None = None):
        """查询美食：优先按活动商圈精准检索（飞猪对带商圈的查询命中率远高于全市泛查）"""
        group_num = re.sub(r"[^\d]", "", str(group))
        budget_hint = {
            "经济型": "人均100元以内",
            "舒适型": "人均100-200元",
            "豪华型": "人均200元以上",
        }.get(inp.get("budget_tier"), "人均100-200元")
        base_q = f"{city} 适合{group_num or '团体'}人团体聚餐的热门餐厅 {budget_hint}"
        if vibe:
            base_q += f" 偏好{vibe}"
        queries = [
            f"{city}{area}附近适合{group_num or '团体'}人团体聚餐的热门餐厅 {budget_hint}"
            for area in (area_hints or [])[:2]
        ]
        queries.append(base_q)
        texts = []
        first_result = None
        for q in queries:
            result = flyai.ai_search(q)
            if first_result is None:
                first_result = result
            text = _fmt(result, limit=8)
            if text:
                texts.append(text)
        merged = "\n\n---\n\n".join(texts)
        return ("🍜 美食聚餐", merged, first_result)

    def _query_flight(direction, dep_date):
        """查询航班"""
        if direction == "outbound":
            result = flyai.search_flight(origin=origin, destination=city, dep_date=dep_date)
            title = f"✈️ 去程（{origin}→{city}，含实时票价）"
            fallback_q = f"{origin} 到 {city} 机票"
            if dep_date:
                fallback_q += f" {dep_date}出发"
        else:
            result = flyai.search_flight(origin=city, destination=origin, dep_date=dep_date)
            title = f"✈️ 返程（{city}→{origin}，含实时票价）"
            fallback_q = f"{city} 到 {origin} 机票"
            if dep_date:
                fallback_q += f" {dep_date}出发"

        text = _fmt(result, limit=6)
        if not text:
            result = flyai.ai_search(fallback_q)
            text = _fmt(result, limit=6)
            title += "（备选）"

        return (title, text, result)

    def _query_train(direction, dep_date):
        """查询火车/高铁"""
        if direction == "outbound":
            result = flyai.search_train(origin=origin, destination=city, dep_date=dep_date)
            title = f"🚄 去程（{origin}→{city}，含实时票价）"
            fallback_q = f"{origin} 到 {city} 高铁 动车"
            if dep_date:
                fallback_q += f" {dep_date}出发"
        else:
            result = flyai.search_train(origin=city, destination=origin, dep_date=dep_date)
            title = f"🚄 返程（{city}→{origin}，含实时票价）"
            fallback_q = f"{city} 到 {origin} 高铁 动车"
            if dep_date:
                fallback_q += f" {dep_date}出发"

        text = _fmt(result, limit=6)
        if not text:
            result = flyai.ai_search(fallback_q)
            text = _fmt(result, limit=6)
            title += "（备选）"

        return (title, text, result)

    def _query_transport(direction, dep_date, transport_mode):
        """根据交通方式偏好查询航班或火车。

        transport_mode: "flight" / "train" / "auto"
        - "auto" 时根据距离自动决定
        - 中距离（600-1200km）且 auto 时，同时查询两种并给出对比
        """
        from . import transport as transport_mod

        rec = transport_mod.recommend_transport(origin, city)
        suggested = rec["suggested"]

        if transport_mode == "flight":
            return _query_flight(direction, dep_date)
        elif transport_mode == "train":
            return _query_train(direction, dep_date)
        else:
            # auto 模式
            if suggested == "train":
                return _query_train(direction, dep_date)
            elif suggested == "flight":
                return _query_flight(direction, dep_date)
            else:
                # 中距离：同时查两种，给用户对比
                flight_result = _query_flight(direction, dep_date)
                train_result = _query_train(direction, dep_date)
                f_title, f_text, f_raw = flight_result
                t_title, t_text, t_raw = train_result
                dir_label = "去程" if direction == "outbound" else "返程"
                if f_text and t_text:
                    combined_title = f"✈️🚄 {dir_label}（{origin}→{city}，飞机/高铁对比）"
                    combined_text = f"【飞机方案】\n{f_text}\n\n【高铁方案】\n{t_text}"
                    combined_raw = {"data": f"{f_raw}\n---\n{t_raw}", "status": 0, "message": "both"}
                    return (combined_title, combined_text, combined_raw)
                elif f_text:
                    return flight_result
                elif t_text:
                    return train_result
                else:
                    return (f_title + " / " + t_title, "", f_raw)

    # 准备所有查询任务（food 依赖 poi 结果提取商圈，稍后单独执行）
    tasks = [
        ("hotel", _query_hotel),
        ("poi", _query_poi),
    ]
    
    if origin and origin != city and check_in:
        transport_mode = inp.get("transport_mode", "auto")
        tasks.append(("outbound", lambda: _query_transport("outbound", check_in, transport_mode)))
        if check_out:
            tasks.append(("return", lambda: _query_transport("return", check_out, transport_mode)))
    elif origin and origin != city and not check_in:
        warnings.append(f"⚠️ 未指定出行日期，无法查询{origin}→{city}的交通信息。请在对话中提供出发日期。")

    # 并行执行所有查询
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_name = {executor.submit(task_fn): task_name for task_name, task_fn in tasks}
        
        for future in as_completed(future_to_name):
            task_name = future_to_name[future]
            try:
                title, text, result = future.result()
                if text:
                    sections.append((title, text))
                else:
                    status = result.get("status")
                    msg = (result.get("message") or "").strip()
                    warnings.append(
                        f"{title}：飞猪未返回数据（status={status}"
                        + (f"，{msg}" if msg else "")
                        + "）"
                    )
            except Exception as e:
                warnings.append(f"{task_name}：查询异常 - {str(e)}")

    # 美食查询：从景点结果提取活动商圈，带商圈精准查餐厅
    poi_text = next((data for t, data in sections if "玩法" in t or "景点" in t), "")
    area_hints = []
    for name in re.findall(r"名称[：:]\s*([^\n；;，,|]+)", poi_text)[:4]:
        area = name.strip()
        if area.startswith(city):
            area = area[len(city):]
        if 2 <= len(area) <= 10 and area not in area_hints:
            area_hints.append(area)
    try:
        title, text, result = _query_food(area_hints)
        if text:
            sections.append((title, text))
        else:
            warnings.append(f"{title}：飞猪未返回数据（status={result.get('status')}）")
    except Exception as e:
        warnings.append(f"food：查询异常 - {str(e)}")

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

    # 价格时效基线：全部查询完成的时间点（验价/过期预警以此为基准）
    data_captured_at = datetime.now().isoformat(timespec="seconds")

    return {
        "scenario": scenario,
        "markdown": markdown,
        "sections": [{"title": t, "data": d} for t, d in sections],
        "warnings": warnings,
        "used_llm": used_llm,
        "mode": mode,
        "city": inp.get("city", ""),
        "data_captured_at": data_captured_at,
    }
