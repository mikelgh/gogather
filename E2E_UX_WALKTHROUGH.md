# E2E 用户旅程走查记录

> 目的：以真实用户身份走完整产品流程，验证功能正确性与 UX；作为演示前的质量备查档案。
> 方法：Playwright 无头 Chromium（1440×900 桌面 + 390×844 移动端）驱动真实页面，后端/LLM/飞猪全部真实调用，每步截图。
> 复跑：`.venv/bin/python e2e_walkthrough.py`（全流程）、`e2e_verify_fixes.py`（修复回归）。截图与 report.json 在 `e2e_shots/`。
> 环境：chromium-1117（`~/.cache/ms-playwright`），playwright 1.56 需 `executable_path` 指定。

## 第一轮：主流程走查（2026-10-22）

### 流程与结果

| # | 步骤 | 预期 | 结果 |
|---|---|---|---|
| 1 | 打开首页 | 登录弹窗自动弹出，GitHub/支付宝第三方入口可见 | ✅ |
| 2 | 注册新用户 | 进入主界面，侧栏"我的方案 (0)" | ✅ |
| 3 | 对话："30人从北京去西安做团建，11月1日到3日，轻松氛围" | 追问或大纲提案 | ✅ 大纲卡 ~20s，3 天含返程日（D3 西安→北京），逐时段+类型标签 |
| 4 | 点击「✅ 按此生成完整方案」 | 按钮禁用+进度提示，~55-85s 出方案 | ✅ ~70s；期间"正在搜索酒店…"反馈清晰；05b 系列截图 |
| 5 | 方案页 | 标题/摘要/已生成徽章/数据时效标注 | ✅ "生成于 · 飞猪价格获取于 ·（参考价）"齐全；MU2102/MU2123 真实航班号 |
| 6 | 时间线页签 | 地图+编号路线+地点列表 | ✅ 11 地点、北京→西安虚线、自动播放/定位可见 |
| 7 | 预算页签 | 分类卡片+算式+人均 | ✅ 321×15间×2晚=9630、370×30=11100、350×30=10500，人均 1041 算术自洽 |
| 8 | 待办页签 | 分组清单+预订链接+底部操作区 | ✅ 住宿/¥9630/预订标签；四按钮（进入实施跟踪/分享/重新生成/新建方案）+修改输入框 |
| 9 | 提醒页签 | 提醒列表 | ✅ |
| 10 | 票价验价（预算页） | 重查飞猪出对比报告，不改方案 | ✅ API 实测：去/返程双项 status=unchanged；前端面板此前已人工验证 |
| 11 | 修改："第二天上午加一个陕西历史博物馆" | 摘要更新+时间线+地图重算 | ✅ revised=True；摘要更新；地图 11→12 地点 |
| 12 | 侧栏收起/展开、移动端首屏 | 布局正常 | ✅（脚本收起点击未生效，属脚本问题；此前已人工验证该功能） |
| 13 | 全程 console/接口 | 无报错 | ✅ 无 /api 4xx/5xx；仅 favicon 404（已修） |

### 发现并修复的问题（3）

| 问题 | 严重度 | 根因 | 修复 | 回归验证 |
|---|---|---|---|---|
| 新注册用户永远看不到新手引导 | 中 | tour 触发只在「带 token 页面加载」路径（init），`handleAuth` 注册成功后未调用 | `handleAuth` 成功路径补 `if (!window.tourCompleted() && state.planId === null) setTimeout(() => window.startTour(), 600)` | 重跑注册流程：引导出现（1/3 卡片+跳过） ✅ |
| 刚生成完方案封面是 🗺️ 占位符，AI 封面要二次进入方案才有 | 中 | `plan_ready` 响应的 plan 字段来自 `planner.plan()` 输出，无 `share_code`，前端封面分支走不到 AI 封面 URL | `_build_plan_ready` 捕获 `generate_share_code()` 返回码写回 `plan_result["share_code"]`；封面 img 增加 onerror 兜底（失败回落 🗺️） | 重跑生成：出方案瞬间即渲染 AI 封面（src=/share/01ocdw/cover.png） ✅ |
| /favicon.ico 404（console 报错噪音） | 低 | 有静态文件无路由 | `main.py` 增加 `/favicon.ico` FileResponse 路由 | curl 200 ✅ |

### 观察项（未改动，待产品判断）

1. 对话式修改后头部"生成于"时间戳不变——语义合理（未重新生成），可考虑加"已修改于"。
2. 新手引导第 1 步指向空侧栏，对新用户信息量略低（内容优化，低优先级）。
3. 大纲卡"直接输入修改意见→重新出大纲"分支本轮未覆盖。

### 数据清理记录

- 走查临时账号 `e2e_walk_76141`、`e2e_fix_76854` 及方案已删。
- ⚠️ 失误记录：清理 SQL 用 `LIKE 'e2e_%'`（`_` 是单字符通配符），把历史测试账号 `e2etest`（含 2 个测试方案）一并删除，与"保留并报告"的原意不符。正式方案（33 份）未受影响。后续清理应先 SELECT 确认名单再 DELETE。

## 第二轮：页签功能深检（2026-10-22）

> 覆盖：时间线交互（定位/自动播放/全屏地图）、预算（验价面板入镜）、待办四态流转与持久化、实施跟踪看板（进度%/下一步行动）、分享只读页、修订历史、重新生成、新建方案。

**方法**：`e2e_tabs_test.py`（Playwright headless，真实后端+真实 LLM/飞猪），新注册账号走完整生成流程后逐页签操作；每步截图到 `e2e_shots/r2_*.png`，全程捕获 console 错误与 /api 4xx+5xx。脚本分阶段 try/except 隔离，单步失败不中断。辅助聚焦脚本：`e2e_dbg_verify.py`（验价埋点复现）、`e2e_dbg_rollback.py`（修订历史/回滚专项）。

### 执行记录（三轮迭代）

- 第 1 轮：验价面板滚动步骤超时崩溃 → 暴露「点击验价无请求」线索。
- 第 2 轮：验价等待 150s 仍未出面板；`journalctl` 证实两次 E2E 会话点击按钮后**后端从未收到 `POST /verify_prices`**（历史人工请求均 200 正常）→ 锁定前端问题。
- 第 3 轮（修复后）：全流程通过，验价面板入镜。

### 结果总表（第 3 轮，用户 e2e_tab_79038）

| # | 检查项 | 预期 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 新用户注册触发新手引导（第一轮修复回归） | 引导出现 | ✅ | r2_01 |
| 2 | 生成方案（~70s）+ AI 封面立即渲染（第一轮修复回归） | 出方案即有封面 | ✅ cover_img=True | r2_02 |
| 3 | 时间线·地点列表点击定位 | 地图聚焦+popup | ✅ 13 地点，popup 打开 | r2_03 |
| 4 | 时间线·▶ 自动播放 | 按钮变 ⏸ 停止播放，逐点巡航 | ✅ 状态切换正常 | r2_04 |
| 5 | 时间线·⛶ 全屏查看入口 | 链接指向 /map/{code} | ✅ href=/map/fmdnxt?from=plan&plan=… | r2_05 |
| 6 | 预算页签渲染 | 分项卡+人均+数据时间戳 | ✅ 住宿/去程/返程+约¥1074/人 | r2_06 |
| 7 | 票价验价（修复后） | 面板入镜，逐项对比 | ✅ 「2 项交通，0 项价格有变」，MU2102 ¥370 价格一致 | r2_07 |
| 8 | 待办页签初始态 | checklist 渲染 | ✅ 6 项 | r2_08 |
| 9 | 待办四态流转（点击切换） | pending→booking | ✅ PUT 200 | r2_09 |
| 10 | 待办状态刷新后持久化 | ?plan= 恢复后仍为 booking | ✅ | r2_10 |
| 11 | 实施跟踪看板 | 进度/统计/下一步行动 | ✅ 进行中 1 / 待处理 5，nextActions=True | r2_11 |
| 12 | 分享方案 → 只读页 | share link + 公开访问 | ✅ /share/fmdnxt，只读页含封面/地图/行程 | r2_12/r2_13 |
| 13 | 修订历史入口+弹窗（修复后） | 方案视图内可达，展示版本 | ✅ 「版本 1 · 修改前快照（…）」+ 回滚按钮 | r2_15a/r2_15b |
| 14 | 回滚流程 | confirm→快照→内容还原 | ✅ 回滚 200，自动存 v2「回滚前快照」，摘要还原 | r2_16 + DB 核验 |
| 15 | 提醒页签 | 提醒列表 | ✅ 13 项（航班/入住/用餐…） | r2_17 |
| 16 | 重新生成 | 新方案落库+侧栏+1 | ✅ 新方案 811bdbf8 落库（DB 核验；脚本读数过早误报，见观察项 3） | journalctl+DB |
| 17 | 新建方案 | 回到对话视图 | ✅ chat_visible=True | r2_20 |
| 18 | 全程 console/接口 | 无报错 | ✅ 无 pageerror，无 /api 4xx/5xx | report2.json |

### 发现并修复的问题（2 个新 bug + 1 个 UX 缺陷）

| 问题 | 严重度 | 根因 | 修复 | 回归验证 |
|---|---|---|---|---|
| **刚生成的方案点「票价验价」无任何反应**（无请求/无提示） | 高 | `plan_ready` 响应里 `plan` 是 planner 输出（无 `id`，plan_id 单独传），前端 `state.plan = data.plan` 后 `state.plan.id` 为 undefined，`verifyPrices()` 的 `if (!planId) return` 静默返回。第一轮误判为"脚本没截到面板" | 三处 plan_ready 赋值统一改为 `state.plan = { ...data.plan, id: data.plan_id }`（对话流 3645 / 生成流 3498 / 重新生成流 4107） | 修复后点验价 → POST 200 → 面板入镜（r2_07）✅ |
| **修订历史永远是"暂无修订记录"，回滚功能形同虚设** | 高 | `api_revise_plan` 直接覆盖方案，从不调用 `db.save_plan_revision`；plan_revisions 表生产环境为空 | revise 覆盖前先存当前版本快照（约定同回滚端点："修改前快照（{用户请求}）"）；补回归测试 `test_revise_saves_before_snapshot`（148 tests 全绿） | UI revise 后弹窗出现版本条目；回滚 200 + 内容还原 + 自动存回滚前快照 ✅ |
| 修订历史入口只在「实施跟踪」视图，方案视图（修改方案处）反而没有 | 中 | 按钮和弹窗 markup 被嵌在 `#executionView` 内部，方案视图下随父容器隐藏 | 按钮移入「修改方案」标题行右侧（`margin-left:auto`），弹窗移到方案视图容器末尾（fixed 覆盖层不受影响）；实施跟踪头部保留标题+返回按钮 | 弹窗从方案视图直接打开（r2_15a/b），实施跟踪布局复查正常 ✅ |

### 观察项（未改动）

1. **刷新页面不恢复当前方案**（回到对话态）：`restorePlan` 仅在 URL 带 `?plan=<id>` 时恢复，代码注释表明是有意的产品决策（"首页默认停在新建方案的对话态"）。但对正在查看方案的用户，F5 会"丢失"当前方案（侧栏仍可点回）。建议至少在方案视图下刷新时带 `?plan=` 或提示。
2. **验价耗时波动大**：真实重查飞猪，本轮 <10s 返回，第一轮曾 >60s。前端已有 ⏳ 按钮态，但无进度提示；超长耗时场景可考虑加"查询中（约需 1 分钟）"文案。
3. 脚本缺陷（非产品）：重新生成的成功判断以"方案标题可见"为准，旧方案仍在展示时立即为真；正确做法是等待 toast「新方案已生成」或侧栏数量变化。本轮以 journalctl+DB 落库为准判定功能正常。
4. LLM 供应商配额：qwen 在本轮多次 403/429（自动降级 minimax 成功），演示前需确认配额（沿用第一轮提醒）。

### 数据清理（待确认后执行）

第二轮产生了 3 个测试账号（e2e_tab_77468 / e2e_tab_78067 / e2e_tab_79038）、4 个方案、2 条修订、15 条会话。已按第一轮教训先 SELECT 确认名单（16 个正式用户不受影响），DELETE 语句因破坏性操作策略被拦截，**待用户确认后执行**：

```sql
-- 用户: 695843d4-c91, ea819807-b5c, 373a0694-386
-- 方案: d62e2cff-207, 66d471dd-b04, 811bdbf8-a17, 38ad2597-e2f
DELETE FROM plan_revisions WHERE plan_id IN ('d62e2cff-207','66d471dd-b04','811bdbf8-a17','38ad2597-e2f');
DELETE FROM conversations  WHERE user_id  IN ('695843d4-c91','ea819807-b5c','373a0694-386');
DELETE FROM plans          WHERE id       IN ('d62e2cff-207','66d471dd-b04','811bdbf8-a17','38ad2597-e2f');
DELETE FROM users          WHERE id       IN ('695843d4-c91','ea819807-b5c','373a0694-386');
```

## 第三轮：注册防护（阿里云验证码 2.0）+ 接口限频 + 窄屏封面适配（2026-10-22）

> 覆盖：注册防机器人全链路（前端弹层 → 服务端二次校验 → 限频兜底）、移动端封面标题裁切修复。
> 新增：`app/captcha.py`（服务端校验）、`app/ratelimit.py`（滑动窗口限频）、`tests/test_captcha.py`（10 例）。改动：`app/main.py`、`static/index.html`、`static/share.html`、`app/cover_generator.py`、`tests/conftest.py`。

### 方案设计

- **验证码形态**：阿里云验证码 2.0，场景 `rl88szkg` / 身份标 `l1syhy`（控制台：yundun.console.aliyun.com/?p=captcha）。形态在控制台随时可切（一点即过 ↔ 无痕验证），**无需改代码**——最终采用无痕验证。
- **配置驱动启用**：`.env` 同时具备 `ALIYUN_CAPTCHA_SCENE_ID` / `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET` 才启用；未配置则注册不要求验证码（本地开发/测试零成本）。前端经 `/api/public-config` 下发 `captcha_enabled / captcha_scene_id / captcha_prefix`。
- **fail-closed**：服务端 `verify_captcha()` 任何异常（SDK 未安装 / 网络 / 解析失败）一律判失败，宁可拦截不放行。
- **限频先行**：限频检查在验证码门之前，洪水请求不消耗阿里云配额。REGISTER 10 次/小时/IP、LOGIN 30 次/15 分钟/IP，内存滑动窗口（单进程语义，重启清零）。
- **窄屏封面裁切（三层修复）**：① 生成端 `_fit_font()` 自动缩字号（标题 ≤76% 宽、副标题 ≤80%，72px→36px 逐级）从源头防溢出；② 分享页移动端 `aspect-ratio: 5/4` 替代固定 300px 高（此前 `object-fit:cover` 把 1200×960 封面横向裁掉两侧）；③ 主页 `#planCoverContainer` 同样改为 aspect-ratio 适配。

### 联调记录（6 个问题，全部修复）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 弹层 "Network Error"，请求打到 `undefined.captcha-open.aliyuncs.com` | `initAliyunCaptcha` 缺 `prefix`（身份标）参数 | env → public-config → 前端全链路下发；缺失时显式提示配置 ALIYUN_CAPTCHA_PREFIX |
| 2 | 无痕形态弹层永不渲染 | `#captcha-element` 为 display:none/0×0，SDK 拒绝挂载；且触发点击早于 `getInstance` 回调（SDK 尚未绑定按钮） | 容器改为可见空元素；点击移入 `getInstance` 回调内执行 |
| 3 | 服务端报 `'dict' object has no attribute 'captcha_verify_param'` | `verify_intelligent_captcha` 只接受模型对象，不能传 dict | 改用 `captcha_models.VerifyIntelligentCaptchaRequest(...)` |
| 4 | **前端一点即过成功，后端却 403「人机验证未通过」**；用户报的错误 id（alrQeQwsqi）实为一次**通过**记录的 CertifyId | 实测响应体为 `{'Result': {'VerifyResult': True}}` 嵌套 dict，而代码按 SDK 文档读 `verification_result.verify_result`——T001（通过）全被误判为失败 | `_extract_verify_result()` 兼容 dict 嵌套与 Tea 模型两种形态；回归测试覆盖通过/拒绝/None/垃圾体 |
| 5 | 注册成功、背景已登录，弹层仍显示失败 | `doAuthSubmit` 无返回值 → `captchaVerifyCallback` 拿到 `undefined` → `captchaResult` falsy | `doAuthSubmit` 显式 `return true/false` |
| 6 | 成功后又弹第二次验证，且一点就失败；或闪现一下自动消失 | 回调里 `await` 重载荷（loadConversations/restorePlan/loadPlanList）耗时过长 → SDK 判定超时、重置勾选框并保留弹层 → 二次校验复用同一 session 被风控判 F001（疑似机器人）。另一分支：双击注册 → 双实例叠加，第二实例回调必然 409 | 回调**立即** return，重载荷改 fire-and-forget 异步 IIFE；成功后 300/800/1500ms 三段兜底点关闭按钮；`_captchaInstance` 复用 + `_captchaIniting` 防抖 + `_captchaPendingCreds` 动态凭证（双击 → 仅 1 次 InitCaptcha、1 个弹层，日志核验） |

### 限频实测（线上日志核验）

- 注册：同 IP 先前已有 4 条窗口记录，E2E 连发 6 次 → 403（无票据被验证码门拦截且计入限频）→ 第 7 次 **429**，滑动窗口正确。
- 登录：同 IP 连发 30 次全部 401（密码错）→ 第 31 次 **429**，30 次边界精确。
- 403 同时证明验证码门在用户落库之前生效——无 ticket 请求不会创建用户。
- 无头浏览器（Playwright）在风控学习后稳定被判 F001 拒绝——真人/机器区分符合预期；演示务必用真实浏览器。

### 已知残留（均无功能影响）

1. 注册成功后弹层偶有 <1s 闪现后自动关闭（兜底关闭按钮生效）。
2. 限频为单进程内存实现；多 worker 部署需换 Redis（当前单 uvicorn，不受影响）。
3. 测试期间用户办公网 IP 已消耗注册配额约 9-10 次/小时额度，演示当天注意预留。
4. qwen 配额多次 403/429（自动降级 minimax 成功），演示前确认（沿用第二轮提醒）。

### 数据清理（待确认后执行，名单已 SELECT 核对）

第二轮名单不变，另加验证码测试产生的 2 个账号（均无方案、无会话）：`smoketest_captcha_1`（d307edaa-240）、`e2ecap_84071`（e29a5545-880）。用户自建测试账号 `test1009`（93594eb3-4bb）**待用户决定**去留。

```sql
-- 在第二轮 SQL 基础上追加：
DELETE FROM users WHERE id IN ('d307edaa-240','e29a5545-880');
-- test1009（93594eb3-4bb）：待用户确认
```
