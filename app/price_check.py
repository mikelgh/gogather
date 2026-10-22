"""票价验价：对方案中的航班/车次重新发起飞猪查询，比对价格变化。

只做对比报告，不修改方案价格——下单价以飞猪实时页为准。
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .planner import _FLIGHT_RE, _TRAIN_RE

# 判定为"价格未变"的容差（5%）
UNCHANGED_TOLERANCE = 0.05


def _extract_transport_nos(text: str) -> list[str]:
    """从文本中提取航班号（如 3U6710/AQ1112）与车次号（如 G7502），保序去重。"""
    found = []
    for pat in (_TRAIN_RE, _FLIGHT_RE):
        for m in pat.findall(str(text or "")):
            if m not in found:
                found.append(m)
    return found


def _is_train_no(no: str) -> bool:
    return bool(no) and no[0].upper() in "GDCKZ"


def _first_price(text: str) -> float | None:
    m = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)", str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_offers(data) -> dict[str, dict]:
    """从飞猪结构化返回（itemList）中提取 车次/航班号 → 价格/摘要 的映射。"""
    offers: dict[str, dict] = {}
    if not isinstance(data, dict):
        return offers
    for it in (data.get("itemList") or []):
        price = it.get("ticketPrice") or it.get("price")
        try:
            price = float(str(price).replace(",", "")) if price else None
        except (TypeError, ValueError):
            price = None
        for j in (it.get("journeys") or [])[:1]:
            for seg in (j.get("segments") or []):
                no = str(seg.get("marketingTransportNo") or "").strip()
                if no:
                    offers[no] = {
                        "price": price,
                        "dep": seg.get("depDateTime") or "",
                        "arr": seg.get("arrDateTime") or "",
                        "seat": seg.get("seatClassName") or "",
                    }
    return offers


def verify_plan_prices(plan: dict, flyai_module) -> dict:
    """对 plan timeline 中带航班/车次号的项重新验价。

    按（交通类型 × 去程/返程）聚合发起查询，每个方向最多 2 条（飞机/火车各一），
    并行执行。返回对比报告，不修改方案。
    """
    params = plan.get("params") or {}
    city = params.get("city", "")
    origin = params.get("origin", "")
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")

    # 收集 timeline 中带编号的交通项
    checked_items = []
    for idx, item in enumerate(plan.get("timeline") or []):
        text = str(item.get("title", "")) + str(item.get("notes", ""))
        nos = _extract_transport_nos(text)
        if not nos:
            continue
        day_m = re.search(r"Day\s*(\d+)", str(item.get("time_range", "")))
        old_price = _first_price(str(item.get("price", "")))
        checked_items.append({
            "index": idx,
            "day": int(day_m.group(1)) if day_m else None,
            "title": item.get("title", ""),
            "transport_no": nos[0],
            "old_price": old_price,
        })

    # 需要的查询：(kind, direction)
    train_nos = {it["transport_no"] for it in checked_items if _is_train_no(it["transport_no"])}
    flight_nos = {it["transport_no"] for it in checked_items if not _is_train_no(it["transport_no"])}
    queries = []
    if train_nos:
        queries.append(("train", "outbound", origin, city, check_in))
        queries.append(("train", "return", city, origin, check_out))
    if flight_nos:
        queries.append(("flight", "outbound", origin, city, check_in))
        queries.append(("flight", "return", city, origin, check_out))

    def _run_query(kind, _direction, dep, arr, date):
        fn = flyai_module.search_train if kind == "train" else flyai_module.search_flight
        return kind, parse_offers(fn(origin=dep, destination=arr, dep_date=date or None).get("data"))

    offers: dict[str, dict] = {}
    query_failed = False
    if queries and checked_items:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_run_query, *q) for q in queries]
            for fut in as_completed(futures):
                try:
                    _, result = fut.result()
                    offers.update(result)
                except Exception:
                    query_failed = True

    report = []
    for it in checked_items:
        no = it["transport_no"]
        offer = offers.get(no)
        entry = {
            "index": it["index"],
            "day": it["day"],
            "title": it["title"],
            "transport_no": no,
            "kind": "train" if _is_train_no(no) else "flight",
            "old_price": it["old_price"],
            "new_price": None,
            "diff": None,
            "diff_pct": None,
            "status": "not_found",
            "note": "",
        }
        if offer is None:
            entry["note"] = "本次验价未查到该车次/航班，可能已售罄或时刻调整，请以预订链接实时信息为准"
        elif offer.get("price") is None:
            entry["status"] = "no_price"
            entry["note"] = "查询到班次但未返回价格，请以预订链接实时价格为准"
        else:
            new_price = offer["price"]
            entry["new_price"] = new_price
            if it["old_price"] is None:
                entry["status"] = "no_old_price"
                entry["note"] = f"方案中该项未记录价格，当前查到 ¥{new_price:.0f}"
            else:
                diff = new_price - it["old_price"]
                entry["diff"] = round(diff, 2)
                entry["diff_pct"] = round(abs(diff) / it["old_price"] * 100, 1) if it["old_price"] else None
                if abs(diff) <= max(it["old_price"] * UNCHANGED_TOLERANCE, 1):
                    entry["status"] = "unchanged"
                    entry["note"] = "价格基本一致"
                else:
                    entry["status"] = "changed"
                    arrow = "上涨" if diff > 0 else "下降"
                    entry["note"] = f"较方案生成时{arrow} ¥{abs(diff):.0f}（{entry['diff_pct']}%），以 ¥{new_price:.0f} 为参考"
        report.append(entry)

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "items": report,
        "summary": {
            "checked": len(report),
            "changed": sum(1 for r in report if r["status"] == "changed"),
            "not_found": sum(1 for r in report if r["status"] == "not_found"),
        },
        "query_failed": query_failed,
        "note": "验价结果仅供参考，实际价格以飞猪预订页实时报价为准",
    }
