# 聚行 · 活动出行 AI 助手

> 飞猪 × 阿里云百炼 **2026 AI 旅行创新大赛** 参赛项目  
> 官网：https://opc.aliyun.com/feizhu ｜ 提交截止：**2026-10-25**

## 一句话介绍

**聚行**是一款面向「活动/聚会」场景的 **策划+实施一体化** AI 出行助手：用户用一句话描述活动需求，AI 智能追问补全参数，调用飞猪真实数据生成结构化方案（时间线+预算+待办+预订链接），并提供实施跟踪、进度看板和一键分享功能。

**核心差异化**：所有竞品只做"方案生成"，聚行是唯一覆盖"策划→实施→分享"全流程的产品，独占团体/活动出行蓝海场景。

## 核心功能

### Phase 1: 策划
- **一句话对话**：自然语言输入，AI 智能追问补全参数（城市、日期、人数、预算、偏好）
- **飞猪真实数据**：自动调用 flyai CLI 查询酒店、景点、美食、交通，零编造
- **结构化方案**：生成时间线、预算明细、待办清单，每个 item 带预订链接
- **房间分配**：根据人数自动计算房间分配方案
- **人均预算**：自动计算人均费用，支持分类展示

### Phase 2: 实施
- **待办跟踪**：可勾选任务列表，状态循环（待处理→预订中→已确认→已完成）
- **进度看板**：可视化进度条 + 状态统计 + 下一步行动提示
- **一键分享**：生成分享码和只读链接，方便团队成员查看
- **实施视图**：独立的实施跟踪页面，专注执行

## 技术架构

```
用户一句话 → AI追问补全 → flyai查真实数据 → 结构化方案(JSON)
                                                    ↓
                                            ┌───────────────┐
                                            │  Phase 1: 策划  │
                                            │  时间线/预算/房间 │
                                            │  每个item带预订链接│
                                            └───────┬───────┘
                                                    ↓
                                            ┌───────────────┐
                                            │  Phase 2: 实施  │
                                            │  待办清单/状态跟踪│
                                            │  进度看板/分享   │
                                            └───────────────┘
```

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLite
- **旅行数据**：飞猪 AI 开放平台 `flyai-cli`（官方 CLI）
- **AI 引擎**：阿里云百炼 / 通义千问（qwen-plus，JSON mode）
- **前端**：原生 HTML/CSS/JS（无构建步骤，零依赖）
- **部署**：Docker / Docker Compose

## 快速开始

### 本地运行

```bash
# 1) Python 依赖
uv sync
# 或：pip install -e .

# 2) Node 依赖（提供本地 flyai 二进制）
npm install

# 3) 环境变量
cp .env.example .env
# 编辑 .env 填入 API Key：
#   FLYAI_API_KEY      飞猪 AI 开放平台 Key（体验模式可不填）
#   DASHSCOPE_API_KEY  阿里云百炼 Key（不填则降级到模板拼接）
#   JWT_SECRET         JWT 签名密钥（公网部署务必修改）

# 4) 启动
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000
```

### Docker 部署

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

### 生产部署（腾讯云 · systemd + Caddy）

线上地址：https://gogather.partystack.cn （Caddy 自动 HTTPS，反代 `127.0.0.1:8010`）

仓库：https://github.com/mikelgh/gogather （公开，服务器使用只读 Deploy Key）

生产环境采用原生部署（Python venv + `gogather.service` + Caddy），未使用 Docker；
`Dockerfile` / `docker-compose.yml` 保留用于本地或容器化场景。

自动更新（push-to-deploy）：

- `gogather-update.timer` 每分钟检测 `origin/main`
- 有新提交时 `git reset --hard origin/main`
- `pyproject.toml` / `uv.lock` 或 `package*.json` 变更时自动重装依赖
- 自动 `systemctl restart gogather`
- `.env` 与 `data/` 未被 git 跟踪，更新不会覆盖

```bash
# 服务器一次性启用
sudo systemctl enable --now gogather-update.timer
```

## API 接口

### 认证
- `POST /api/register` — 注册  
  请求：`{ username, password }`，响应：`{ token, user }`
- `POST /api/login` — 登录  
  请求：`{ username, password }`，响应：`{ token, user }`
- `GET /api/me` — 当前用户信息

除 `/api/register`、`/api/login`、`/api/share/{code}` 外，所有 API 需携带请求头 `Authorization: Bearer <token>`。
方案数据按 `user_id` 隔离：每个用户只能读取/修改/分享自己的方案；他人访问一律返回 404。

### 对话式规划
- `POST /api/chat`  
  请求：`{ message, conversation, plan_id? }`  
  响应：`{ type: "follow_up"|"plan_ready", extracted_params, plan_id?, plan?, structured? }`

### 方案管理
- `GET /api/plan/{plan_id}` — 获取方案详情（仅本人）
- `GET /api/plans` — 列出当前用户的方案
- `PUT /api/plan/{plan_id}/task/{task_id}` — 更新任务状态（仅本人）  
  请求：`{ status: "pending"|"booking"|"confirmed"|"completed" }`

### 分享功能
- `POST /api/plan/{plan_id}/share` — 生成分享码（仅本人）  
  响应：`{ share_code, share_url }`
- `GET /api/share/{code}` — 获取分享的方案（公开，无需登录）
- `GET /share/{code}` — 分享页面（公开只读 HTML）

### 飞猪原始查询
- `POST /api/search` — 原始飞猪查询  
  请求：`{ command: "ai-search"|"search-hotel"|..., options: {...} }`

## 数据模型

```python
ActionItem:
  id, type(hotel/transport/activity/restaurant),
  title, description,
  recommendations: [{name, price, booking_link, rating}],
  estimated_cost, time_slot, assignee,
  status: pending → booking → confirmed → completed

Plan:
  id, original_input, conversation[], params{},
  summary, action_items[], timeline[], budget[],
  room_allocation[], budget_per_person,
  share_code, members[]
```

## UI 特色

- **现代设计**：紫色渐变主题，Inter + Noto Sans SC 字体
- **流畅动画**：页面切换淡入淡出、卡片入场动画、状态切换微交互
- **打字指示器**：等待 AI 响应时显示动态加载动画
- **Toast 通知**：操作反馈（分享成功、状态更新等）
- **响应式布局**：完美适配桌面和移动端
- **空状态设计**：友好的空数据提示

## 目录结构

```
gogather/
├─ app/
│  ├─ config.py            运行环境 / flyai 二进制探测
│  ├─ flyai_client.py      飞猪 CLI 封装（子进程 + JSON 解析）
│  ├─ llm_client.py        百炼 / 通义千问封装（JSON mode）
│  ├─ planner.py           编排：输入 → 飞猪查询 → 方案
│  ├─ chat.py              对话式参数提取（LLM JSON mode）
│  ├─ structured_planner.py 结构化方案生成
│  ├─ models.py            Pydantic 数据模型
│  ├─ database.py          SQLite 持久化
│  └─ main.py              FastAPI 路由
├─ static/
│  ├─ index.html           主页面（对话+方案+实施）
│  └─ share.html           分享页面（只读）
├─ data/                   SQLite 数据库目录
├─ Dockerfile              Docker 构建文件
├─ docker-compose.yml      Docker Compose 配置
├─ pyproject.toml          Python 依赖
├─ package.json            Node 依赖（flyai CLI）
└─ .env.example            环境变量示例
```

## 数据真实性承诺（硬约束）

聚行对"编造数据"零容忍，所有事实性内容**只**来自飞猪 AI 搜索的真实返回：

- **唯一事实来源**：酒店/景点/餐厅/交通的名称、预订链接、价格、评分、航班号、车次号等信息，只能来自飞猪结果并保留原始链接
- **模型补充必须标注**：百炼/通义千问仅做"组织与呈现"；任何来自模型自身知识的通用建议，必须逐条加前缀 **【建议·非飞猪数据，仅供参考，使用前请核实】**
- **兜底防编造**：润色结果会经"疑似编造检测"（正则扫描飞猪原文中不存在的价格/航班/车次）。若检出，先重试一次；若仍不合规，则**直接回退到飞猪原始数据直出**
- **数据说明**：每个方案末尾附 **📋 数据说明**，明示哪些是飞猪实时数据、哪些是通用建议

## 与大赛命题的映射

| 大赛预设命题 | 本项目对应 |
|---|---|
| 目的地探索 | 「目的地探索」模式 |
| AI 私人讲解 / 酒店 Staycation | 可切换为对应查询；住宿、玩法皆由飞猪真实数据驱动 |
| 自由方向 | 「活动/聚会出行」即自由创新方向（活动场景 × 真实旅行供给）|

**评审维度**（创新性 40% / 完整度 30% / 市场前景 30%）：

- **创新性**：活动场景 × AI 出行编排，复用作者既有活动产品，切入点新；event 模式自带"团体统筹"逻辑（统一预订/集体签到接驳/分组动线/聚餐），与通用旅游规划拉开差异
- **完整度**：端到端跑通（输入→追问→飞猪查询→结构化方案→实施跟踪→分享），评审可直接在浏览器体验完整流程
- **市场前景**：活动聚会是高频真实需求；飞猪推广者计划可让本应用持续分润（酒店/度假商品成交佣金）

## 竞品对比

| 维度 | 竞品最强 | 聚行 |
|------|---------|------|
| 输入方式 | 表单/语音 | **一句话对话 + 智能追问** |
| 输出格式 | Markdown | **结构化方案（时间线+预算+待办+预订链接）** |
| 后续跟踪 | 无 | **实施看板 + 状态管理 + 分享** |
| 团体支持 | 无 | **房间分配 + 分组 + 人均预算** |
| 闭环程度 | 到推荐为止 | **推荐→预订链接→跟踪确认** |
| 活动场景 | 无 | **独占蓝海** |

## 局限与后续

- 飞猪部分商品需跳转飞猪详情页查看最新报价（平台限制）
- 体验模式部分结果受限；配置 `FLYAI_API_KEY` 解锁完整服务
- 后续可加：用户账号体系、多人协作编辑、接入推广者计划拿佣金、多日行程编排、地图可视化

## 开发日志

- **Day 1**（10/20）：数据模型 + 对话接口 + 对话 UI ✅
- **Day 2**（10/21）：结构化方案生成 + 方案展示 ✅
- **Day 3**（10/22）：实施跟踪 + 分享功能 ✅
- **Day 4**（10/23）：UI 视觉升级 + 部署准备 ✅
- **Day 5**（10/24）：GitHub Issue 提交 + 最终打磨

详细进度见 [SPR.md](./SPR.md)

## License

MIT
