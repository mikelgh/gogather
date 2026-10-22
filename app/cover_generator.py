"""动态封面图生成：根据方案信息生成微信分享用的 og:image。"""

import os
import hashlib
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont


# 封面图尺寸（微信推荐 5:4 比例）
COVER_WIDTH = 1200
COVER_HEIGHT = 960

# AI 背景/成品封面缓存目录：放 /tmp 会在重启后丢失，导致所有封面回落渐变底
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "covers")

# 字体路径（优先使用系统中文字体）
FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/PingFang.ttc",  # macOS
    "C:\\Windows\\Fonts\\msyh.ttc",  # Windows 微软雅黑
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取中文字体，失败时回退到默认字体。"""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, base_size: int, max_ratio: float = 0.76, min_size: int = 36) -> ImageFont.FreeTypeFont:
    """按文字宽度自适应字号：长标题自动缩小，保证两侧留白不被裁切。

    封面在窄屏（分享页/手机）按高度缩放显示时左右会被 object-fit: cover 裁掉，
    文字控制在画布宽度的 max_ratio 以内，任何端都能完整显示。
    """
    max_width = int(COVER_WIDTH * max_ratio)
    size = base_size
    while size > min_size:
        font = get_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 4
    return get_font(min_size)


def generate_cover(
    city: str = "",
    date_range: str = "",
    group_size: str = "",
    summary: str = "",
    origin: str = "",
) -> bytes:
    """生成动态封面图，返回 PNG 字节数据。
    
    参数:
        city: 目的地城市
        date_range: 日期范围（如 "11-01 ~ 11-05"）
        group_size: 人数（如 "2 人" 或 "30 人"）
        summary: 方案摘要
        origin: 出发地
    """
    # 创建渐变背景（RGBA 模式以支持透明度）
    img = Image.new("RGBA", (COVER_WIDTH, COVER_HEIGHT))
    draw = ImageDraw.Draw(img)
    
    # 绘制紫色渐变背景（从 #667eea 到 #764ba2）
    for y in range(COVER_HEIGHT):
        ratio = y / COVER_HEIGHT
        r = int(102 + (118 - 102) * ratio)
        g = int(126 + (75 - 126) * ratio)
        b = int(234 + (162 - 234) * ratio)
        draw.line([(0, y), (COVER_WIDTH, y)], fill=(r, g, b, 255))
    
    # 绘制装饰圆形（模拟 logo 中的三个圆点）
    circle_y = COVER_HEIGHT // 2 - 100
    circle_spacing = 80
    start_x = COVER_WIDTH // 2 - circle_spacing
    colors = ["#f472b6", "#38bdf8", "#8b5cf6"]  # 粉、蓝、紫
    
    for i, color in enumerate(colors):
        cx = start_x + i * circle_spacing
        cy = circle_y
        radius = 35
        # 绘制圆形
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color,
        )
    
    # 绘制标题文字（title_font 在下方按文字长度自适应）
    subtitle_font = get_font(42)

    # 标题：城市 + 出行方案
    if origin and city:
        title = f"{origin} → {city}"
    elif city:
        title = f"{city}"
    else:
        title = "出行方案"
    
    title_text = f"{title} 出行方案"
    
    # 计算标题位置（居中）；长标题自动缩字号，避免窄屏显示时被裁切
    title_font = _fit_font(draw, title_text, 72)
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (COVER_WIDTH - title_width) // 2
    title_y = COVER_HEIGHT // 2 + 50
    
    # 绘制标题阴影
    shadow_offset = 3
    draw.text(
        (title_x + shadow_offset, title_y + shadow_offset),
        title_text,
        fill=(0, 0, 0, 77),  # rgba(0,0,0,0.3) = (0,0,0,77)
        font=title_font,
    )
    # 绘制标题
    draw.text((title_x, title_y), title_text, fill=(255, 255, 255), font=title_font)
    
    # 绘制副标题（日期 + 人数）
    info_parts = []
    if date_range:
        info_parts.append(date_range)
    if group_size:
        # 确保 group_size 是字符串
        group_size = str(group_size)
        # 确保格式正确
        if "人" not in group_size:
            group_size = f"{group_size}人"
        info_parts.append(group_size)
    
    if info_parts:
        subtitle = " · ".join(info_parts)
        subtitle_font = _fit_font(draw, subtitle, 42, max_ratio=0.8)
        subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_x = (COVER_WIDTH - subtitle_width) // 2
        subtitle_y = title_y + 100
        
        draw.text((subtitle_x, subtitle_y), subtitle, fill=(255, 255, 255, 230), font=subtitle_font)
    
    # 绘制品牌名称（底部）
    brand_text = "GoGather 聚行"
    brand_font = get_font(32)
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_width = brand_bbox[2] - brand_bbox[0]
    brand_x = (COVER_WIDTH - brand_width) // 2
    brand_y = COVER_HEIGHT - 120
    
    draw.text((brand_x, brand_y), brand_text, fill=(255, 255, 255, 179), font=brand_font)
    
    # 绘制底部装饰线
    line_y = brand_y - 30
    line_width = 200
    line_x = (COVER_WIDTH - line_width) // 2
    draw.line(
        [(line_x, line_y), (line_x + line_width, line_y)],
        fill=(255, 255, 255, 128),
        width=2,
    )
    
    # 转换为 PNG 字节
    from io import BytesIO
    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95)
    return buffer.getvalue()


def generate_cover_cached(
    city: str = "",
    date_range: str = "",
    group_size: str = "",
    summary: str = "",
    origin: str = "",
    cache_dir: str = CACHE_DIR,
) -> str:
    """生成封面图并缓存到文件，返回文件路径。"""
    # 创建缓存目录
    os.makedirs(cache_dir, exist_ok=True)
    
    # 生成缓存键（v2：标题自适应字号，防止窄屏裁切）
    cache_key = f"{city}_{date_range}_{group_size}_{origin}"
    cache_hash = hashlib.md5(cache_key.encode()).hexdigest()[:12]
    cache_path = os.path.join(cache_dir, f"cover_v2_{cache_hash}.png")
    
    # 如果缓存存在，直接返回
    if os.path.exists(cache_path):
        return cache_path
    
    # 生成封面图
    png_data = generate_cover(city, date_range, group_size, summary, origin)
    
    # 保存到缓存
    with open(cache_path, "wb") as f:
        f.write(png_data)
    
    return cache_path


def extract_primary_city(city: str) -> str:
    """从多城市名称中提取主城市（用于匹配 AI 封面缓存）。
    
    例如：
    - "成都 + 九寨沟 + 乐山" -> "成都"
    - "广州→成都" -> "成都"
    - "成都" -> "成都"
    """
    if not city:
        return ""
    
    # 处理 "城市 A + 城市 B + 城市 C" 格式
    if "+" in city:
        return city.split("+")[0].strip()
    
    # 处理 "城市 A→城市 B" 格式（取目的地）
    if "→" in city:
        parts = city.split("→")
        return parts[-1].strip() if len(parts) > 1 else parts[0].strip()
    
    # 处理 "城市 A-城市 B" 格式
    if "-" in city and len(city) > 10:
        return city.split("-")[0].strip()
    
    return city.strip()


def generate_ai_background(city: str, cache_dir: str = CACHE_DIR) -> str:
    """使用 ImageGen 为城市生成 AI 背景图，返回文件路径。"""
    os.makedirs(cache_dir, exist_ok=True)
    
    # 提取主城市（处理多城市名称）
    primary_city = extract_primary_city(city)
    
    # 缓存键基于主城市名
    cache_hash = hashlib.md5(primary_city.encode()).hexdigest()[:12]
    bg_path = os.path.join(cache_dir, f"bg_{cache_hash}.png")
    
    if os.path.exists(bg_path):
        return bg_path
    
    # 构建 ImageGen prompt
    prompt = f"""A stunning travel poster style background image for {city}, China. 
    Wide landscape view of iconic landmarks or scenic beauty of {city}. 
    Soft gradient overlay at bottom for text readability. 
    Professional photography style, vibrant colors, clear sky.
    No text, no watermarks, no people in foreground.
    Aspect ratio 5:4, high quality."""
    
    # 调用 ImageGen（通过 qoder CLI）
    try:
        result = subprocess.run(
            ["qoder", "image-gen", "--prompt", prompt, "--size", "1536x1024", "--output", bg_path],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0 and os.path.exists(bg_path):
            return bg_path
    except Exception as e:
        print(f"ImageGen failed for {city}: {e}")
    
    return None


def generate_cover_with_ai(
    city: str = "",
    date_range: str = "",
    group_size: str = "",
    summary: str = "",
    origin: str = "",
    use_ai: bool = True,
) -> bytes:
    """生成封面图，可选使用 AI 背景。"""
    if use_ai and city:
        bg_path = generate_ai_background(city)
        if bg_path:
            return _generate_cover_with_background(bg_path, city, date_range, group_size, origin)
    
    # 回退到程序化渐变背景
    return generate_cover(city, date_range, group_size, summary, origin)


def _generate_cover_with_background(
    bg_path: str,
    city: str,
    date_range: str,
    group_size: str,
    origin: str,
) -> bytes:
    """使用背景图生成封面，叠加半透明遮罩和文字。"""
    # 加载背景图并调整尺寸
    bg = Image.open(bg_path).convert("RGBA")
    bg = bg.resize((COVER_WIDTH, COVER_HEIGHT), Image.Resampling.LANCZOS)
    
    # 创建半透明渐变遮罩（底部更深，便于文字阅读）
    overlay = Image.new("RGBA", (COVER_WIDTH, COVER_HEIGHT))
    overlay_draw = ImageDraw.Draw(overlay)
    
    for y in range(COVER_HEIGHT):
        ratio = y / COVER_HEIGHT
        # 从顶部 30% 透明到底部 70% 透明
        alpha = int(77 + (179 - 77) * ratio)
        overlay_draw.line([(0, y), (COVER_WIDTH, y)], fill=(0, 0, 0, alpha))
    
    # 合成背景 + 遮罩
    img = Image.alpha_composite(bg, overlay)
    draw = ImageDraw.Draw(img)
    
    # 绘制文字（title_font 按文字长度自适应，防止窄屏裁切）
    brand_font = get_font(32)

    # 标题
    if origin and city:
        title = f"{origin} → {city}"
    elif city:
        title = city
    else:
        title = "出行方案"

    title_text = f"{title} 出行方案"

    title_font = _fit_font(draw, title_text, 72)
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (COVER_WIDTH - title_width) // 2
    title_y = COVER_HEIGHT // 2 + 50
    
    # 文字阴影
    shadow_offset = 3
    draw.text(
        (title_x + shadow_offset, title_y + shadow_offset),
        title_text,
        fill=(0, 0, 0, 128),
        font=title_font,
    )
    draw.text((title_x, title_y), title_text, fill=(255, 255, 255), font=title_font)
    
    # 副标题（日期 + 人数）
    info_parts = []
    if date_range:
        info_parts.append(date_range)
    if group_size:
        # 确保 group_size 是字符串
        group_size = str(group_size)
        if "人" not in group_size:
            group_size = f"{group_size}人"
        info_parts.append(group_size)
    
    if info_parts:
        subtitle = " · ".join(info_parts)
        subtitle_font = _fit_font(draw, subtitle, 42, max_ratio=0.8)
        subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_x = (COVER_WIDTH - subtitle_width) // 2
        subtitle_y = title_y + 100
        
        draw.text(
            (subtitle_x + 2, subtitle_y + 2),
            subtitle,
            fill=(0, 0, 0, 102),
            font=subtitle_font,
        )
        draw.text((subtitle_x, subtitle_y), subtitle, fill=(255, 255, 255, 242), font=subtitle_font)
    
    # 品牌名称
    brand_text = "GoGather 聚行"
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_width = brand_bbox[2] - brand_bbox[0]
    brand_x = (COVER_WIDTH - brand_width) // 2
    brand_y = COVER_HEIGHT - 120
    
    draw.text(
        (brand_x + 2, brand_y + 2),
        brand_text,
        fill=(0, 0, 0, 102),
        font=brand_font,
    )
    draw.text((brand_x, brand_y), brand_text, fill=(255, 255, 255, 204), font=brand_font)
    
    # 转换为 PNG
    from io import BytesIO
    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95)
    return buffer.getvalue()
