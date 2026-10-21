# 【2026飞猪AI旅行创新大赛】GoGather 聚行 - AI 驱动的团体出行方案生成平台

## 项目名称
GoGather 聚行

## 团队信息
- **开发者**: mikelgh
- **GitHub**: https://github.com/mikelgh
- **项目仓库**: https://github.com/mikelgh/gogather
- **在线演示**: https://gogather.partystack.cn

## 出行场景
**50人企业团建·7天6晚·多城市长途行程**

广州→成都→九寨沟→乐山→成都→广州

## 核心功能

### 1. AI 可执行方案生成
- 一句话描述活动，AI 自动生成完整行程
- 拉取飞猪实时数据：交通/酒店/景点/餐厅全部带真实价格和预订链接
- 支持多种出行类型：团建、聚会、家庭游、情侣游
- 智能交通方式选择：高铁/飞机/自驾
- 预算档次控制：经济型/舒适型/豪华型

### 2. 地图时间线
- 行程按天着色显示在地图上，空间感知一目了然
- 点击 marker 显示详情（价格、预订链接、备注）
- 支持交通路线可视化
- 方案页和分享页均已接入

### 3. 主动提醒服务
- 解析 time_range 自动生成带日期的提醒
- 方案页"提醒"tab 展示
- 分享页待办后显示提醒区块
- 支持 29+ 项行程提醒（到点该退房该集合）

### 4. 多模态封面
- AI 生成城市专属封面图（14 个热门城市）
- 智能文字叠加：城市、日期、人数、品牌名称
- 服务端渲染 OpenGraph meta tags
- 微信/社交平台完美显示（PNG 格式，5:4 比例）

### 5. 餐厅推荐
- 基于飞猪 AI 实时数据
- 智能推荐附近餐厅
- 带价格和预订链接

### 6. 分享协作
- 一键生成分享链接
- 无需注册即可查看
- 团员可实时查看行程、预算、提醒

## 百炼（通义千问）使用情况

### 使用的模型
- **主模型**: qwen-plus（通义千问 Plus）
- **快速模型**: qwen-turbo（通义千问 Turbo，用于快速响应）

### 使用场景
1. **行程规划**: 调用 qwen-plus 生成多日行程编排
2. **预算分析**: 调用 qwen-plus 分析预算合理性
3. **餐厅推荐**: 调用 qwen-turbo 快速推荐附近餐厅
4. **提醒生成**: 调用 qwen-turbo 解析时间线生成提醒文本

### 调用方式
```python
# 通过 DashScope API 调用
from openai import OpenAI

client = OpenAI(
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

response = client.chat.completions.create(
    model="qwen-plus",
    messages=[
        {"role": "system", "content": "你是专业的旅行规划师..."},
        {"role": "user", "content": user_input}
    ]
)
```

### 使用量统计
- 总方案数：25
- AI 调用次数：约 100+ 次（每个方案生成约 4-5 次调用）
- 主要消耗：行程规划（长文本生成）

## 飞猪 Skill 使用情况

### 使用的 Skill
通过 `flyai-cli`（飞猪 AI 开放平台官方 CLI）调用以下 6 个 skill：

| Skill | 用途 | 调用场景 |
|-------|------|----------|
| `ai-search` | AI 智能搜索 | 自然语言查询旅行产品 |
| `keyword-search` | 关键词搜索 | 精确搜索特定产品 |
| `search-hotel` | 酒店搜索 | 按城市/日期/价格搜索酒店 |
| `search-poi` | POI 搜索 | 搜索景点/地标 |
| `search-flight` | 机票搜索 | 按航线/日期搜索航班 |
| `search-train` | 火车票搜索 | 按城市/日期搜索高铁 |

### 返回数据
- ✅ 真实价格（实时报价）
- ✅ 预订链接（可跳转到飞猪下单）
- ✅ 产品详情（图片、描述、规格）

### 在作品中的体现
- **AI 方案生成**: 调用 `ai-search` + `search-hotel` + `search-flight` + `search-train`
- **餐厅推荐**: 调用 `ai-search` 搜索附近餐厅
- **地图 POI**: 调用 `search-poi` 获取景点坐标

## 项目截图

### 1. 方案生成页
![方案生成](https://gogather.partystack.cn/)

### 2. 分享页（地图时间线）
![分享页](https://gogather.partystack.cn/share/h5ydji)

### 3. AI 封面图
![AI 封面](https://gogather.partystack.cn/share/h5ydji/cover.png)

## 项目链接

- **GitHub 仓库**: https://github.com/mikelgh/gogather
- **在线演示**: https://gogather.partystack.cn
- **演示方案**: https://gogather.partystack.cn/share/h5ydji
- **提交文档**: https://github.com/mikelgh/gogather/blob/main/SUBMISSION.md

## 演示方案详情

**分享码**: `h5ydji`
- **城市**: 成都 + 九寨沟 + 乐山
- **人数**: 50 人团队
- **行程**: 广州→成都→九寨沟→乐山→成都→广州 7 日游
- **行程节点**: 43 个（含早午晚餐、转场、休整）
- **总预算**: ¥180,125（人均 ¥3,602）
- **亮点**: 
  - AI 自动生成，43 个详细行程节点
  - 团队拓展活动（越野车、民俗游戏）
  - 九寨沟深度游（树正沟、日则沟、则查洼沟）
  - 乐山大佛文化之旅
  - 成都熊猫基地 + 轻松团建
  - 完整预算明细（14 项）

## 创新点

### 1. AI 可执行闭环
不是"写攻略"，而是"生成可执行的待办"。每个方案都包含真实车次/航班信息、实时酒店价格、景点门票链接、餐厅预订入口。

### 2. 空间感知地图
将时间线映射到地理空间，按天着色显示，让团队对行程有直观的空间感知。

### 3. 主动服务
不是被动查询，而是主动提醒。自动生成 29 项行程提醒，到点推送。

### 4. 多模态封面
AI 生成城市专属封面图，每个城市都有独特的视觉识别，提升分享吸引力。

## 技术架构

### 后端
- **框架**: FastAPI + Python 3.11
- **数据库**: SQLite (用户、方案、对话、AI 使用记录)
- **AI 服务**: 
  - 飞猪 AI 开放平台 (实时价格 + 预订链接)
  - DashScope/百炼 (通义千问 LLM)
- **地图**: Leaflet + 高德地图瓦片
- **部署**: 单服务器 uvicorn 生产模式

### 前端
- **技术栈**: 原生 HTML/CSS/JS (无框架，轻量)
- **地图库**: Leaflet.js
- **字体**: Inter + Noto Sans SC

## 自评打分

| 维度 | 权重 | 自评分 | 说明 |
|------|------|--------|------|
| 创新性 | 40% | 8.5 | AI 可执行闭环 + 地图时间线 + 主动提醒 |
| 完整度 | 30% | 8.5 | 核心路径跑通；地图/提醒/封面已接入 |
| 市场前景 | 30% | 8.0 | 团建/聚会高频刚需；CPS+企业 B 端路径清晰 |

**综合 8.4/10**

## 遇到的坑与解决方案

### 1. 地图瓦片在中国大陆加载失败
**问题**: OpenStreetMap 在中国大陆被墙，地图显示灰色背景
**解决**: 切换为高德地图瓦片 (`https://wprd01.is.autonavi.com/appmaptile`)

### 2. 返程标记点位置不准确
**问题**: "杭州→上海" 这样的路线格式无法匹配 POI 坐标
**解决**: 在 geocode.py 中添加路线模式匹配，识别 "城市 A→城市 B" 格式

### 3. 微信分享不显示自定义标题和图片
**问题**: 微信爬虫不执行 JavaScript，客户端渲染的 meta tags 无效
**解决**: 服务端渲染 OpenGraph meta tags，在 `/share/{code}` 路由中替换 HTML

### 4. group_size 类型不一致导致 500 错误
**问题**: 部分方案的 group_size 存储为整数 (10) 而非字符串 ("10人")
**解决**: 在 cover_generator.py 和 main.py 中添加 `str()` 类型转换

### 5. 预算数据字段名不匹配
**问题**: 数据库存储的预算字段名与前端期望不一致
**解决**: 统一数据结构，使用 `category`/`estimated_cost`/`description` 字段

## 后续演进

### 短期 (1-2 个月)
- [ ] 提醒订阅 (短信/邮件/微信模板消息)
- [ ] 更多城市 AI 封面 (目标 50 城)
- [ ] 方案版本对比 UI

### 中期 (3-6 个月)
- [ ] 企业 B 端 (团建 SaaS，按人头收费)
- [ ] 多人协作编辑
- [ ] 接入推广者计划拿佣金

### 长期 (6-12 个月)
- [ ] 更丰富的 POI 坐标库 (接入高德/百度 geocoding)
- [ ] 智能行程优化算法
- [ ] 用户画像与个性化推荐
