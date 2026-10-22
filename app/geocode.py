"""简易地理编码：把地点文本转成经纬度。

MVP 用硬编码表覆盖主要城市的热门 POI，未命中时回退到城市中心坐标。
"""

# 城市中心坐标
CITY_COORDS = {
    "杭州": (30.2741, 120.1551),
    "深圳": (22.5431, 114.0579),
    "上海": (31.2304, 121.4737),
    "北京": (39.9042, 116.4074),
    "广州": (23.1291, 113.2644),
    "成都": (30.5728, 104.0668),
    "九寨沟": (33.2667, 103.9167),
    "乐山": (29.5467, 103.7767),
    "昆明": (25.0389, 102.7183),
    "大理": (25.6065, 100.2676),
    "丽江": (26.8552, 100.2259),
    "南京": (32.0603, 118.7969),
    "武汉": (30.5928, 114.3055),
    "西安": (34.3416, 108.9398),
    "重庆": (29.4316, 106.9123),
}

# 热门 POI 坐标（按城市分组）
POI_COORDS = {
    "杭州": {
        "西湖": (30.2424, 120.1489),
        "灵隐寺": (30.2409, 120.1009),
        "灵隐飞来峰": (30.2409, 120.1009),
        "雷峰塔": (30.2307, 120.1495),
        "西溪湿地": (30.2677, 120.0777),
        "宋城": (30.1719, 120.1006),
        "河坊街": (30.2377, 120.1677),
        "南宋御街": (30.2377, 120.1677),
        "龙井村": (30.2167, 120.1167),
        "九溪烟树": (30.2067, 120.1267),
        "杭州东站": (30.2889, 120.2119),
        "杭州站": (30.2431, 120.1819),
        "杭州西溪紫金港希尔顿欢朋酒店": (30.2889, 120.1019),
        "杭州庆华饭店": (30.2531, 120.1719),
        "德悦海鲜": (30.2019, 120.2119),
        "乾堂里": (30.2377, 120.1677),
    },
    "深圳": {
        "欢乐谷": (22.5431, 113.9779),
        "世界之窗": (22.5331, 113.9779),
        "锦绣中华": (22.5331, 113.9679),
        "野生动物园": (22.5931, 113.9979),
        "大梅沙": (22.5931, 114.3079),
        "小梅沙": (22.6031, 114.3179),
        "华侨城": (22.5431, 113.9779),
        "深圳站": (22.5431, 114.1179),
        "深圳北站": (22.6131, 114.0279),
        "深圳东站": (22.5931, 114.1279),
        "深圳湾": (22.4931, 113.9479),
        "东门": (22.5531, 114.1279),
        "福田": (22.5331, 114.0579),
        "罗湖": (22.5431, 114.1179),
    },
    "上海": {
        "外滩": (31.2404, 121.4904),
        "东方明珠": (31.2404, 121.4974),
        "迪士尼": (31.1444, 121.6604),
        "南京路": (31.2354, 121.4754),
        "豫园": (31.2274, 121.4914),
        "上海虹桥站": (31.1944, 121.3244),
        "上海站": (31.2504, 121.4554),
    },
    "北京": {
        "故宫": (39.9163, 116.3972),
        "天安门": (39.9087, 116.3975),
        "长城": (40.4319, 116.5704),
        "颐和园": (39.9999, 116.2755),
        "北京南站": (39.8650, 116.3789),
        "北京站": (39.9027, 116.4272),
        "北京西站": (39.8944, 116.3250),
        "北京北站": (39.9472, 116.3589),
        "北京首都国际机场": (40.0802, 116.6025),
        "北京首都机场": (40.0802, 116.6025),
        "首都国际机场": (40.0802, 116.6025),
        "北京大兴国际机场": (39.5106, 116.4292),
        "大兴国际机场": (39.5106, 116.4292),
    },
    "广州": {
        "广州塔": (23.1067, 113.3211),
        "长隆": (23.0017, 113.3278),
        "白云山": (23.1967, 113.3067),
        "广州南站": (22.9867, 113.2667),
        "广州站": (23.1467, 113.2567),
        "广州东站": (23.1567, 113.3267),
        "白云国际机场": (23.3924, 113.2988),
        "广州白云国际机场": (23.3924, 113.2988),
    },
    "成都": {
        "武侯祠": (30.6467, 104.0467),
        "锦里": (30.6467, 104.0467),
        "大熊猫基地": (30.7367, 104.1467),
        "宽窄巷子": (30.6767, 104.0367),
        "成都东站": (30.6167, 104.1467),
        "成都站": (30.6967, 104.0767),
        "大熊猫繁育研究基地": (30.7367, 104.1467),
        "东郊记忆": (30.6567, 104.1167),
        "川菜博物馆": (30.7067, 103.9567),
        "蜀风雅韵": (30.6667, 104.0567),
        "漫花庄园": (30.7867, 104.0867),
        "飞越丛林": (30.7267, 104.0267),
        "双流国际机场": (30.5728, 103.9467),
        "成都双流国际机场": (30.5728, 103.9467),
        "天府国际机场": (30.3167, 104.4467),
        "成都天府国际机场": (30.3167, 104.4467),
    },
    "九寨沟": {
        "九寨沟风景区": (33.2667, 103.9167),
        "九寨沟": (33.2667, 103.9167),
        "九寨千古情": (33.2617, 103.9217),
        "九寨千古情景区": (33.2617, 103.9217),
    },
    "乐山": {
        "乐山大佛": (29.5467, 103.7767),
        "乐山大佛景区": (29.5467, 103.7767),
        "峨眉山": (29.5467, 103.3367),
        "峨眉山景区": (29.5467, 103.3367),
        "报国寺": (29.5567, 103.3567),
        "上中顺": (29.5567, 103.7717),
        "上中顺特色街区": (29.5567, 103.7717),
    },
    "南京": {
        "中山陵": (32.0667, 118.8567),
        "夫子庙": (32.0267, 118.7967),
        "南京南站": (31.9767, 118.8067),
        "南京站": (32.0867, 118.8067),
    },
    "武汉": {
        "黄鹤楼": (30.5467, 114.3067),
        "东湖": (30.5567, 114.3767),
        "武汉站": (30.6067, 114.4267),
        "汉口站": (30.6167, 114.2567),
        "武昌站": (30.5267, 114.3167),
    },
    "西安": {
        "兵马俑": (34.3867, 109.2767),
        "大雁塔": (34.2267, 108.9667),
        "西安北站": (34.3767, 108.9467),
        "西安站": (34.2767, 108.9667),
    },
    "重庆": {
        "洪崖洞": (29.5667, 106.5767),
        "解放碑": (29.5567, 106.5767),
        "重庆北站": (29.6067, 106.5467),
        "重庆站": (29.5467, 106.5667),
    },
    "昆明": {
        "昆明长水国际机场": (25.0996, 102.9358),
        "昆明长水机场": (25.0996, 102.9358),
        "长水机场": (25.0996, 102.9358),
        "长水国际机场": (25.0996, 102.9358),
        "昆明南站": (24.9570, 102.8236),
    },
    "大理": {
        "大理凤仪机场": (25.6470, 100.3210),
        "大理机场": (25.6470, 100.3210),
    },
    "丽江": {
        "丽江三义机场": (26.6661, 100.2488),
        "丽江三义国际机场": (26.6661, 100.2488),
        "三义机场": (26.6661, 100.2488),
    },
}


def _extract_cities(city: str) -> list:
    """从多城市名称中提取所有城市名（如"成都 + 九寨沟 + 乐山" → ["成都", "九寨沟", "乐山"]）。"""
    import re
    # 按 +、,、，、/、→ 等分隔符拆分
    parts = re.split(r'[+,，/、→\s]+', city)
    return [p.strip() for p in parts if p.strip()]


def _detect_city_in_location(location: str, cities: list) -> str | None:
    """从地点文本中检测城市名（如"广州白云国际机场" → "广州"）。"""
    for c in cities:
        if c in location:
            return c
    # 尝试常见城市名（即使不在 cities 列表中）
    for city_name in CITY_COORDS.keys():
        if city_name in location:
            return city_name
    # 模糊匹配：地点中包含城市名的一部分（如"九寨千古情" → "九寨沟"）
    for city_name in list(CITY_COORDS.keys()) + list(POI_COORDS.keys()):
        # 取城市名的前 2 个字符进行匹配（如"九寨沟" → "九寨"）
        if len(city_name) >= 2 and city_name[:2] in location:
            return city_name
    return None


def geocode(location: str, city: str, fallback_center: bool = True) -> tuple[float, float] | None:
    """把地点文本转成 (lat, lng)。未命中返回 None。

    fallback_center=False 时跳过城市中心兜底（供 geocode_timeline 分层：
    精确 POI → 高德 API → 城市中心），避免粗略中心坐标抢先于精确结果。
    """
    if not location:
        return None

    import re

    # 支持多城市名称（如"成都 + 九寨沟 + 乐山"）
    cities = _extract_cities(city)
    
    # 优先处理 "城市 A→城市 B" 格式的交通路线（返回出发地坐标）
    route_match = re.search(r"(.+)\s*→\s*(.+)", location)
    if route_match:
        dep_full = route_match.group(1).strip()
        arr_full = route_match.group(2).strip()
        # 尝试从地点名中提取城市名（查找所有城市名）
        dep_city = None
        arr_city = None
        for city_name in list(CITY_COORDS.keys()) + list(POI_COORDS.keys()):
            if city_name in dep_full:
                dep_city = city_name
                break
        for city_name in list(CITY_COORDS.keys()) + list(POI_COORDS.keys()):
            if city_name in arr_full:
                arr_city = city_name
                break
        # 出发侧：优先按文本精确匹配 POI（如"成都东站→双流机场"应标成都东站），再退回机场/车站/中心
        if dep_city:
            dep_pois = POI_COORDS.get(dep_city, {})
            for poi_key, coords in dep_pois.items():
                if poi_key in dep_full:
                    return coords
            # 优先找机场
            for poi_key in dep_pois:
                if "机场" in poi_key:
                    return dep_pois[poi_key]
            # 其次找火车站
            for suffix in ["东", "北", "南", "西", ""]:
                station_name = f"{dep_city}{suffix}站"
                if station_name in dep_pois:
                    return dep_pois[station_name]
            # 回退到出发城市中心
            if dep_city in CITY_COORDS:
                return CITY_COORDS[dep_city]
        # 如果出发城市不在 POI 列表中，尝试到达城市
        if arr_city:
            arr_pois = POI_COORDS.get(arr_city, {})
            for poi_key, coords in arr_pois.items():
                if poi_key in arr_full:
                    return coords
            for poi_key in arr_pois:
                if "机场" in poi_key:
                    return arr_pois[poi_key]
            if arr_city in CITY_COORDS:
                return CITY_COORDS[arr_city]
    
    # 从地点文本中检测城市名
    detected_city = _detect_city_in_location(location, cities)
    
    # 如果检测到城市名，优先使用该城市的 POI
    if detected_city:
        city_pois = POI_COORDS.get(detected_city, {})
        # 精确匹配 POI
        for poi_name, coords in city_pois.items():
            if poi_name in location:
                return coords
        # 回退到该城市中心
        if fallback_center and detected_city in CITY_COORDS:
            return CITY_COORDS[detected_city]

    # 尝试所有城市的 POI（模糊匹配）
    for c in cities:
        city_pois = POI_COORDS.get(c, {})
        for poi_name, coords in city_pois.items():
            if poi_name in location:
                return coords

    # 回退到第一个城市的中心坐标
    if fallback_center:
        for c in cities:
            if c in CITY_COORDS:
                return CITY_COORDS[c]
    return None


def geocode_timeline(timeline: list, city: str) -> list:
    """给 timeline 每项加上 lat/lng 字段。

    坐标来源优先级：
    1. timeline 项自带的 lat/lng（飞猪坐标，LLM 透传 + 校验）
    2. 硬编码 POI 表 / 路线规则（不含城市中心兜底）
    3. 高德地理编码 API（需配置 AMAP_API_KEY）
    4. 城市中心兜底（保证地图总能展示）
    """
    result = []
    for item in timeline:
        item_copy = dict(item)
        if item_copy.get("lat") and item_copy.get("lng"):
            result.append(item_copy)
            continue
        loc = item.get("location", "") or item.get("title", "")
        # 硬编码精确匹配（POI 表 / 路线规则）
        coords = geocode(loc, city, fallback_center=False)
        if not coords:
            from . import amap_geocode
            # 地址文本通常已含城市语境，高德可全文匹配；只有能明确检测到城市时才加约束，
            # 多城市行程传首个城市会导致跨城市地点（如 city=昆明 查 丽江古城）编码失败
            hint = _detect_city_in_location(loc, _extract_cities(city)) or ""
            coords = amap_geocode.geocode(loc, hint)
            if not coords and hint:
                coords = amap_geocode.geocode(loc, "")
        # 城市中心兜底（含路线出发/到达城市中心）
        if not coords:
            coords = geocode(loc, city)
        if coords:
            item_copy["lat"] = coords[0]
            item_copy["lng"] = coords[1]
        result.append(item_copy)
    return result
