# 聚行 · 活动出行 AI 助手

> 飞猪 × 阿里云百炼 **2026 AI 旅行创新大赛** 参赛项目
> 官网：https://opc.aliyun.com/feizhu ｜ 提交截止：**2026-10-25**

## 一句话介绍
**聚行**是一款面向「活动 / 聚会」场景的 AI 出行助手：用户说出一场活动（城市、日期、人数、氛围），AI 调用**飞猪 AI 开放平台**真实价库，自动拉取酒店、景点玩法与（外地）交通，并生成一份**可分享的出行方案**。
它把"办活动"和"安排出行"连成一条线——也是「聚行」与作者另一款产品 PartyStack（活动报名/签到小程序）的自然延伸。

## 与大赛命题的映射
| 大赛预设命题 | 本项目对应 |
|---|---|
| 目的地探索 | 「目的地探索」模式 |
| AI 私人讲解 / 酒店 Staycation | 可切换为对应查询；住宿、玩法皆由飞猪真实数据驱动 |
| 自由方向 | 「活动/聚会出行」即自由创新方向（活动场景 × 真实旅行供给）|

评审维度（创新性 40% / 完整度 30% / 市场前景 30%）：
- **创新性**：活动场景 × AI 出行编排，复用作者既有活动产品，切入点新。
- **完整度**：端到端跑通（输入 → 飞猪真实查询 → 方案），评审可直接在浏览器体验。
- **市场前景**：活动聚会是高频真实需求；飞猪推广者计划可让本应用持续分润（酒店/度假商品成交佣金）。

## 技术栈
- **后端**：Python + FastAPI
- **旅行数据**：飞猪 AI 开放平台 `flyai-cli`（官方 CLI，实测体验模式即可返回结构化结果 + 预订链接）
- **AI 润色（可选）**：阿里云百炼 / 通义千问（OpenAI 兼容接口），未配置 Key 时自动降级为模板拼接
- **前端**：单页原生 HTML（无构建步骤，评审直接开浏览器访问）

## 环境搭建
```bash
# 1) Python 依赖（用 uv，或 pip）
uv sync
#   或：pip install -e .

# 2) Node 依赖（提供本地 flyai 二进制，避免 npx 重复下载）
npm install        # 会安装 @fly-ai/flyai-cli 到 node_modules/.bin/flyai

# 3) 环境变量（可选）
cp .env.example .env
#   FLYAI_API_KEY      体验模式可不填；正式解锁完整结果去 https://flyai.open.fliggy.com/console
#   DASHSCOPE_API_KEY  不填则方案用模板拼接；大赛送百炼新用户 1 亿 Token
```

## 运行
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000
```
页面填城市/日期/人数/氛围 → 生成方案 → 可复制或导出 Markdown。

## 接口
- `GET  /`              页面
- `POST /api/plan`      参数 `{mode, city, check_in, check_out, origin, group_size, vibe}` → 产出方案 Markdown
- `POST /api/search`     原始飞猪查询，参数 `{command, options}`（`command` ∈ ai-search/keyword-search/search-hotel/search-poi/search-flight）

## 目录结构
```
feizhu-travel-ai/
├─ app/
│  ├─ config.py        运行环境 / flyai 二进制探测
│  ├─ flyai_client.py  飞猪 CLI 封装（子进程 + JSON 解析）
│  ├─ llm_client.py    百炼 / 通义千问封装（可选降级）
│  ├─ planner.py       编排：输入 → 飞猪查询 → 方案
│  └─ main.py          FastAPI 路由
├─ static/index.html   单页前端（含轻量 Markdown 渲染）
├─ pyproject.toml  package.json  .env.example  .gitignore
```

## 局限与后续
- 飞猪部分商品需跳转飞猪详情页查看最新报价（平台限制）。
- 体验模式部分结果受限；配置 `FLYAI_API_KEY` 解锁完整服务。
- 后续可加：用户账号体系、方案分享短链、接入推广者计划拿佣金、多日行程编排。
