"""高德地理编码 API：飞猪坐标缺失时的备用方案。

使用 Web 服务 API（restapi.amap.com），个人认证免费 5000 次/天。
配置 AMAP_API_KEY 环境变量后启用；key 缺失时所有调用静默返回 None，
由 geocode.py 回退到硬编码表。
"""
import logging
from functools import lru_cache

import httpx

from . import config

log = logging.getLogger(__name__)

API_URL = "https://restapi.amap.com/v3/geocode/geo"


def is_enabled() -> bool:
    """高德 API key 是否已配置。"""
    return bool(getattr(config, "AMAP_API_KEY", None))


@lru_cache(maxsize=512)
def _geocode_cached(address: str, city: str) -> tuple[float, float] | None:
    """带缓存的地理编码调用（同地址只查一次）。"""
    key = config.AMAP_API_KEY
    if not key:
        return None
    try:
        resp = httpx.get(
            API_URL,
            params={
                "key": key,
                "address": address,
                "city": city or "",
                "output": "json",
            },
            timeout=5,
        )
        data = resp.json()
    except Exception as e:
        log.warning("AMap geocode failed for %r: %s", address, e)
        return None

    geocodes = data.get("geocodes") or []
    if data.get("status") != "1" or not geocodes:
        return None
    loc = geocodes[0].get("location", "")  # "104.0668,30.5728"（高德顺序是 lng,lat）
    try:
        lng_s, lat_s = loc.split(",")
        return (float(lat_s), float(lng_s))
    except (ValueError, AttributeError):
        return None


def geocode(address: str, city: str = "") -> tuple[float, float] | None:
    """把地址文本转成 (lat, lng)。失败或未配置 key 返回 None。

    address 应尽量是可查询的具体地名（如"成都大熊猫繁育研究基地"），
    过长或含箭头的交通路线文本需先拆分再查。
    """
    if not address or "→" in address:
        return None
    # 去掉括号备注（如"漫花庄园（新都区）"→"漫花庄园"），提高命中率
    import re
    cleaned = re.sub(r"[（(].*?[)）]", "", address).strip()
    if not cleaned:
        return None
    return _geocode_cached(cleaned, city)
