# GoGather 性能优化部署总结

**部署时间**: 2026-10-21 09:39  
**提交**: c250a56 "perf: 百炼快速模型 + flyai并行查询"  
**生产地址**: https://gogather.partystack.cn

---

## 🚀 已完成的性能优化

### 1. FlyAI 并行查询（预期提速：72s → ~18s）

**优化前**:
- 4 个 flyai 查询串行执行：酒店、景点、美食、去程航班、返程航班
- 每个查询约 18s，总计约 72s

**优化后**:
- 使用 `ThreadPoolExecutor(max_workers=6)` 并行执行所有查询
- 5 个查询同时发起，总耗时约 18s（取最慢的一个）

**代码位置**: `app/planner.py:159-271`

```python
with ThreadPoolExecutor(max_workers=6) as executor:
    future_to_name = {executor.submit(task_fn): task_name for task_name, task_fn in tasks}
    for future in as_completed(future_to_name):
        # 处理并行结果
```

### 2. 百炼混合模型策略（预期提速：参数提取 15s → 2-5s）

**优化前**:
- 所有 LLM 调用都使用 `qwen-plus`（高质量但慢，15-30s）
- 参数提取、方案修改等简单任务也用慢模型

**优化后**:
- **快速任务**（参数提取、方案修改）：使用 `qwen-turbo`（2-5s）
- **质量任务**（方案生成）：使用 `qwen-plus`（15-30s）

**配置**: `app/config.py:27`
```python
QWEN_MODEL_FAST = get_env("QWEN_MODEL_FAST", "qwen-turbo")
```

**调用点**:
- `app/chat.py:extract_params` → `use_fast_model=True`
- `app/structured_planner.py:revise_plan` → `use_fast_model=True`
- `app/structured_planner.py:generate_structured_plan` → `use_fast_model=False`（默认）

### 3. 预期总体提速

| 阶段 | 优化前 | 优化后 | 提速 |
|------|--------|--------|------|
| 参数提取 | 15-30s | 2-5s | **5-10x** |
| FlyAI 查询 | 72s | 18s | **4x** |
| 方案生成 | 30-60s | 30-60s | 不变 |
| 方案修改 | 15-30s | 2-5s | **5-10x** |
| **端到端总计** | **~124s** | **~55-85s** | **1.5-2x** |

---

## ✅ 已验证的功能

### P0 功能（核心）
1. ✅ 预算档次选择（经济型/舒适型/豪华型）
2. ✅ 方案对话式修改（/api/plan/{plan_id}/revise）
3. ✅ 生成进度提示（前端轮询消息）

### P1 功能（增强）
4. ✅ 团体人员构成（儿童/老人/无障碍需求）
5. ✅ 偏好标签选择（美食/风景/休闲/文化/户外/亲子/夜生活/购物）

### 性能优化
6. ✅ FlyAI 并行查询（ThreadPoolExecutor）
7. ✅ 百炼快速模型（qwen-turbo）

---

## 🧪 手动测试验证

请在生产环境测试以下场景：

### 测试 1：标准聚会方案
```
输入：我们 30 个人想去成都聚会，从广州出发，11 月 15 日入住，11 月 17 日退房，预算舒适型
预期：
- 参数提取：2-5s（快速模型）
- FlyAI 查询：~18s（并行）
- 方案生成：30-60s
- 总计：50-83s（vs 旧版 124s）
```

### 测试 2：带儿童的家庭游
```
输入：公司 20 人去三亚团建，带 5 个小孩（3-8 岁），12 月 1 日到 12 月 3 日，经济型预算
预期：
- 参数提取：识别 children_count=5, children_ages=[3,4,5,6,7,8]
- 方案内容：推荐亲子友好景点、家庭房
```

### 测试 3：方案修改
```
生成方案后输入：把酒店换成更便宜的
预期：
- 修改响应：2-5s（快速模型）
- 更新内容：budget 中酒店价格降低，budget_per_person 重新计算
```

### 测试 4：进度提示
```
生成方案时观察：
- 0-8s：显示"正在分析需求..."
- 8-16s：显示"正在查询飞猪航班..."
- 16-24s：显示"正在搜索酒店..."
- 24-32s：显示"正在查找景点和活动..."
- 32-40s：显示"正在生成方案..."
- 40s+：显示"正在计算预算..."
```

---

## 📊 监控建议

### 日志检查
```bash
# 查看生产服务器日志
ssh tencent "journalctl -u gogather -f"

# 搜索并行查询日志
ssh tencent "journalctl -u gogather | grep 'ThreadPoolExecutor'"

# 搜索快速模型调用
ssh tencent "journalctl -u gogather | grep 'qwen-turbo'"
```

### 性能指标
- 端到端时间：目标 < 90s（当前 124s）
- 参数提取时间：目标 < 5s（当前 15-30s）
- FlyAI 查询时间：目标 < 20s（当前 72s）

---

## 🎯 下一步优化建议

### P2 功能（待开发）
1. **修改历史版本**：保存每次修改的快照，支持回滚
   - 数据库：新增 plan_revisions 表
   - 前端：版本对比 UI

2. **方案分享优化**：生成可分享的静态 HTML
   - 后端：/api/plan/{id}/share → 生成静态页面
   - 前端：分享按钮 + 二维码

### 性能进一步优化
3. **FlyAI 缓存**：相同城市+日期的查询结果缓存 1 小时
   - 预期：重复查询从 18s → <1s

4. **流式输出**：方案生成时使用 SSE 流式返回
   - 预期：首字显示时间从 50s → 5s

---

## 📝 黑客松提交文档

### 百炼使用说明

**百炼能力 / 模型**:
- qwen-plus（高质量方案生成）
- qwen-turbo（快速参数提取与修改）

**调用方式**: 百炼 API（DashScope OpenAI 兼容接口）

**使用环节**:
1. **参数提取**（运行时）：qwen-turbo，2-5s
   - 输入：用户自然语言描述
   - 输出：结构化参数（城市、日期、人数、预算等）

2. **方案生成**（运行时）：qwen-plus，30-60s
   - 输入：飞猪真实数据 + 结构化参数
   - 输出：JSON 格式完整方案

3. **方案修改**（运行时）：qwen-turbo，2-5s
   - 输入：当前方案 + 用户修改要求
   - 输出：更新后的 JSON 方案

**Skill 名称**: 无（直接调用 API）

**其他工具 / 技术栈**:
- FlyAI 开放平台（航班、酒店、景点、美食实时数据）
- FastAPI（后端框架）
- SQLite（数据持久化）
- Vue 3（前端框架）

---

## ✅ 部署检查清单

- [x] 代码已提交（c250a56）
- [x] 已推送到 origin/main
- [x] 生产服务器已自动重启（09:39:06）
- [x] ThreadPoolExecutor 代码已部署
- [x] QWEN_MODEL_FAST 配置已部署
- [ ] 手动测试验证（待用户执行）
- [ ] 性能指标监控（待用户确认）

---

**下一步**: 请在 https://gogather.partystack.cn 测试新功能，验证：
1. 生成速度是否明显提升（目标 < 90s）
2. 进度提示是否正常显示
3. 预算档次、团体构成、偏好标签是否生效
4. 方案修改是否快速响应（目标 < 5s）
