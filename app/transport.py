"""交通方式推荐：基于城市间距离自动判断飞机/高铁。

规则：
- ≤600km → 优先高铁（2-3h，比飞机更便捷）
- 600-1200km → 两者均可，询问用户
- >1200km → 优先飞机
"""

# 主要城市经纬度（近似中心点）
_CITY_COORDS = {
    "北京": (39.90, 116.40),
    "上海": (31.23, 121.47),
    "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06),
    "杭州": (30.27, 120.15),
    "南京": (32.06, 118.80),
    "成都": (30.57, 104.07),
    "重庆": (29.56, 106.55),
    "武汉": (30.59, 114.30),
    "西安": (34.26, 108.94),
    "长沙": (28.23, 112.94),
    "郑州": (34.75, 113.65),
    "天津": (39.13, 117.20),
    "苏州": (31.30, 120.62),
    "无锡": (31.57, 120.30),
    "青岛": (36.07, 120.38),
    "大连": (38.91, 121.60),
    "厦门": (24.48, 118.09),
    "福州": (26.07, 119.30),
    "昆明": (25.04, 102.71),
    "贵阳": (26.65, 106.63),
    "南宁": (22.82, 108.37),
    "海口": (20.02, 110.35),
    "三亚": (18.25, 109.50),
    "哈尔滨": (45.75, 126.65),
    "长春": (43.88, 125.32),
    "沈阳": (41.80, 123.43),
    "济南": (36.65, 116.99),
    "石家庄": (38.04, 114.51),
    "太原": (37.87, 112.55),
    "合肥": (31.82, 117.23),
    "南昌": (28.68, 115.86),
    "兰州": (36.06, 103.83),
    "乌鲁木齐": (43.83, 87.60),
    "呼和浩特": (40.84, 111.75),
    "拉萨": (29.65, 91.17),
    "银川": (38.49, 106.27),
    "西宁": (36.62, 101.78),
    "珠海": (22.27, 113.58),
    "佛山": (23.02, 113.12),
    "东莞": (23.04, 113.75),
    "宁波": (29.87, 121.55),
    "温州": (28.00, 120.67),
    "常州": (31.77, 119.97),
    "徐州": (34.26, 117.18),
    "烟台": (37.54, 121.39),
    "桂林": (25.27, 110.29),
    "丽江": (26.88, 100.23),
    "洛阳": (34.62, 112.45),
    "开封": (34.80, 114.31),
}

# 城市名标准化：去掉"市"后缀
def _normalize(name: str) -> str:
    return name.rstrip("市").strip()


def _get_coords(city: str) -> tuple | None:
    city = _normalize(city)
    return _CITY_COORDS.get(city)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Haversine 公式计算两坐标间近似直线距离（km）。"""
    import math
    r = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return int(r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def estimate_distance_km(origin: str, destination: str) -> int | None:
    """返回两城市间近似直线距离（km），无法估算时返回 None。"""
    c1 = _get_coords(origin)
    c2 = _get_coords(destination)
    if not c1 or not c2:
        return None
    return _haversine_km(c1[0], c1[1], c2[0], c2[1])


def recommend_transport(origin: str, destination: str) -> dict:
    """基于距离推荐交通方式。

    返回:
        suggested: "train" | "flight" | "ask"
        reason: 推荐原因文本
        distance_km: 估算距离或 None
    """
    dist = estimate_distance_km(origin, destination)
    if dist is None:
        return {
            "suggested": "ask",
            "reason": "无法估算距离，建议询问用户偏好",
            "distance_km": None,
        }
    if dist <= 600:
        hours = dist / 300
        return {
            "suggested": "train",
            "reason": f"两地相距约{dist}km，高铁约{hours:.1f}小时，比飞机更便捷",
            "distance_km": dist,
        }
    if dist <= 1200:
        return {
            "suggested": "ask",
            "reason": f"两地相距约{dist}km，高铁和飞机均可，建议询问用户偏好",
            "distance_km": dist,
        }
    return {
        "suggested": "flight",
        "reason": f"两地相距约{dist}km，飞机更合适",
        "distance_km": dist,
    }
