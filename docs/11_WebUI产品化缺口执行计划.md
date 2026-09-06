# WebUI 产品化缺口执行计划（待执行）

> 状态：**草案 / 等待执行**  
> 基线：`task/ui-fixes @ ec2498d`，后端 1074 tests 绿，前端 85 tests 绿  
> 目标：把 WebUI 从“只读观察 + 少量 CRUD”推进到“可日常运维的本地控制台”

## 0. 已拍板事项

### RSS URL 内嵌 token

- 不在当前版本隐藏 RSS URL。
- 如果用户直接粘贴 `https://...?token=...`，WebUI 会按 URL 原样展示。
- 独立 token 字段仍按密钥处理：不回显、只返回 `has_token`。
- 使用约定：包含 `?token=` 的 URL 页面不要截图或共享。
- 文档已补充该约定。

## 1. 范围

### In Scope

1. 补齐 WebUI 对已有后端接口的消费：
   - 订阅更新 `PATCH /api/subscriptions/{id}`
   - RSS 源更新 `PATCH /api/rss_sources/{id}`
2. 补齐产品化操作入口：
   - WebUI 手动触发订阅循环 / 导入 / 单文件试跑
3. 降低本地工具的配置摩擦：
   - WebUI 内设置 API Token
4. 性能与规模化：
   - 媒体库从“一次拉 200 条 + 前端过滤”演进到后端搜索 / 分页
5. 状态一致性：
   - 操作完成后自动刷新相关页面数据
   - SSE 事件与页面数据刷新联动

### Out of Scope

- 不做多用户 / 权限系统
- 不做公网部署能力
- 不做 HTTPS
- 不在当前阶段重做设计系统

---

## 2. Phase A：Logs 撤销入口契约修复

> 状态：本计划创建前已修复

### 问题

原实现中所有 audit operation group 都显示“撤销整理”，但只有组内最新 audit 行带 `reverse` 指令时才真正可回滚。其他组点击后会得到 409。

### 修复方案

- 后端 `GET /api/audit/operations` 返回新增字段：

```ts
interface OperationGroupDto {
  operation_id: string
  rows: number
  entities: string[]
  actions: string[]
  first_audit_id: number
  last_audit_id: number
  rollbackable: boolean
}
```

- `rollbackable` 判定口径：**组内最新一行 audit 是否有 reverse 指令**。
- 前端只在 `group.rollbackable === true` 时渲染“撤销整理”。
- 非 rollbackable 组不再提供入口，避免无意义 409。
- 后端 409 契约保留，仍然作为兜底语义。

### 验收

- [ ] `pending_confirm` / `pending_reject` / `subscription_created` / `rss_source_created` 组不显示撤销按钮
- [ ] `episode.organized` / `upgrade.completed` / 带 reverse 的记忆状态操作显示撤销按钮
- [ ] 点击撤销仍保留二次确认
- [ ] 撤销成功后 Logs 自动刷新
- [ ] 后端 rollback 404 / 409 语义测试保留

---

## 3. Phase B：订阅编辑

### 目标

让用户可以在 WebUI 修改订阅偏好，而不是只能删除重建。

### API 现状

已有：

```http
PATCH /api/subscriptions/{id}
Content-Type: application/json

{
  "status": "active",
  "fansub_pref": "Kamigakari",
  "quality_pref": "1080p"
}
```

后端当前 `SubscriptionUpdateIn` 只支持：

- `status`
- `fansub_pref`
- `quality_pref`

### UI 设计

在订阅行加“编辑”按钮，打开 Drawer 或小型 Modal。

#### 第一版字段

| 字段 | 控件 | 说明 |
|---|---|---|
| 状态 | Select | `active` / `paused` / `finished` 等后端允许值 |
| 字幕组偏好 | Input | 可清空 |
| 质量偏好 | Input | 可清空 |

#### 暂不做

- 不允许编辑标题，避免影响识别记忆与外键语义。
- 不允许编辑季号 / 集数，集表已生成后直接改会引发复杂状态迁移。

### 任务清单

1. `frontend/src/api/endpoints.ts` 增加 `subscriptions.update`
2. `frontend/src/api/types.ts` 增加 `SubscriptionUpdateBody`
3. `Subscriptions.tsx` 增加编辑 Drawer
4. 保存成功后刷新订阅列表
5. 增加 mock handler：PATCH 成功 / 404 / 422
6. 增加 Vitest：
   - 编辑字段后保存
   - 保存后行内数据更新
   - API 失败时错误可见
   - Dirty 状态保存按钮启用
7. 联动测试：
   - UI PATCH 后重新查询后端确认字段变更
   - SSE 收到 `subscription.updated`

### 验收

- [ ] 可修改状态、字幕组偏好、质量偏好
- [ ] 保存后无需手动刷新页面
- [ ] 404 / 422 错误对用户可见
- [ ] Audit Log 出现 `subscription_updated`
- [ ] Pipeline / Logs 页可收到 SSE 事件

---

## 4. Phase C：RSS 源编辑

### 目标

允许用户在 WebUI 修改 RSS 地址、token、启停状态，而不必删除重建。

### API 现状

已有：

```http
PATCH /api/rss_sources/{id}
Content-Type: application/json

{
  "url": "https://mikanani.me/RSS/MyBangumi",
  "token": "new-token",
  "enabled": true
}
```

前端当前只消费了 `enabled`。

### UI 设计

在 RSS 源行加“编辑”按钮，打开 Drawer。

| 字段 | 控件 | 说明 |
|---|---|---|
| 地址 | Input | 必填 |
| Token | Password Input | 留空 = 不修改；显式“清除 token” = 传 `null` |
| 启停 | Switch | 与现有开关一致 |

### Token 交互

第一版建议：

- UI 不显示已有 token
- 只显示 `已配置 / 未配置`
- 编辑表单中 token 输入框留空表示“不修改”
- 单独提供“清除 token”按钮或 Switch
- 如果 URL 内嵌 token，则按已拍板约定原样展示，文档提醒不要截图共享

### 任务清单

1. `rssSources.update` 已存在，确认类型覆盖 `token: string | null`
2. RSS 行增加“编辑”按钮
3. 编辑 Drawer 支持局部 PATCH
4. 提交时区分：
   - `token === undefined`：不修改
   - `token === ''`：建议 UI 层拦截，避免误清空
   - 显式清除：提交 `token: null`
5. mock handler 覆盖：
   - URL 更新
   - token 更新为已配置
   - token 清除为未配置
   - 404
6. 增加 Vitest：
   - 修改 URL 成功
   - 清除 token 后 `has_token=false`
   - URL 为空展示校验错误
   - 404 错误展示
7. 联动测试：
   - UI PATCH 后重新查询后端
   - 确认独立 token 不回显

### 验收

- [ ] 可修改 RSS URL
- [ ] 可设置 / 清除独立 token
- [ ] 修改后列表自动刷新
- [ ] token 字段永远不回显明文
- [ ] Audit Log 出现 `rss_source_updated`

---

## 5. Phase D：WebUI 管线操作入口

### 目标

让 WebUI 不只是观察器，也能承担常用运维动作。

### 第一版入口

建议放在 Pipeline 页右上角或新增“运维”分组。

| 动作 | 后端能力 | 第一版方案 |
|---|---|---|
| 手动跑一轮订阅循环 | CLI 已有 `rerun`；调度器已有同一批入口 | 新增 REST API |
| 手动导入目录 | CLI 已有 `import` | 新增异步任务 API |
| 单文件试跑解析 | CLI 已有 `parse` | 新增 REST API，只解析，不落库 |
| 手动触发 RSS 轮询 | Scheduler 内已有逻辑 | 可复用 rerun 或单独 API |

### 新 API 建议

#### 1. 单文件试跑

```http
POST /api/pipeline/parse-preview
Content-Type: application/json

{
  "name": "[SubGroup] Title - 01 [1080p].mkv"
}
```

响应建议复用 CLI `parse --json` 的结构。

风险低，优先做。

#### 2. 手动订阅循环

```http
POST /api/scheduler/run-once
Content-Type: application/json

{
  "scope": "subscriptions"
}
```

行为：

- 执行下载对账 + RSS 轮询
- 返回任务 ID 或同步返回 summary
- SSE 发布开始 / 结束事件

#### 3. 目录导入

目录导入可能耗时长，建议异步任务：

```http
POST /api/pipeline/import
Content-Type: application/json

{
  "path": "D:/downloads/anime",
  "dry_run": false
}
```

```http
GET /api/tasks/{task_id}
```

第一版可以先限制：

- 单任务运行
- 不并发
- 不支持取消
- 后台执行，SSE 推送进度

### UI 设计

1. Pipeline 页新增“手动操作”卡片：
   - 单文件解析试跑
   - 手动订阅循环
   - 目录导入
2. Directory import 增加 dry-run Switch
3. 所有动作显示 running / success / failed 状态
4. Pipeline 侧栏显示 SSE 事件
5. Logs 页展示对应 audit / operation

### 任务清单

1. 后端设计任务模型与任务 API
2. 后端新增 parse-preview API
3. 后端新增 run-once API
4. 后端新增 import API
5. 增加并发保护：同类任务运行中不允许重复触发
6. 前端新增 Pipeline 操作区
7. 前端新增任务状态展示
8. SSE 增加 pipeline / system 事件
9. 增加权限 / token 测试
10. 增加路径合法性、空目录、不存在目录、Windows 保留名测试
11. 前端增加 Vitest
12. 联动测试真实 CLI 同语义

### 验收

- [ ] 单文件解析试跑成功
- [ ] 手动订阅循环成功
- [ ] dry-run 导入不移动文件
- [ ] 长任务不会阻塞 UI
- [ ] 重复触发有明确提示
- [ ] 失败原因可见
- [ ] Logs / Pipeline / SSE 状态一致

---

## 6. Phase E：API Token 设置入口

### 目标

用户无需手工 localStorage，即可在 WebUI 配置 API token。

### 边界

- API token 本身不能由后端 GET 返回
- 只能通过环境变量设置后端期望值
- WebUI 只保存本地 localStorage token
- 忘记 token 时仍需用户查看本地 `.env`

### UI 设计

Settings 页新增“本机连接”卡片：

| 字段 | 控件 | 说明 |
|---|---|---|
| API Token | Password Input | 只写 localStorage |
| 保存 | Button | 保存后立即调用一次 `/api/health` 验证 |
| 清除 | Button | 删除 localStorage token |

### 验收流程

1. 后端设置 `AUTOANIME_API_TOKEN=test-token`
2. WebUI 未配置 token，API 返回 401
3. Settings 输入正确 token
4. 点击保存
5. 页面自动重试 API
6. Settings 页 `API Token` 环境信息显示“已配置”

### 任务清单

1. Settings 增加本机连接卡片
2. 复用 `getApiToken()` / `setApiToken()`
3. 保存后触发全局 API retry
4. 处理 401 错误统一提示
5. 增加 Vitest
6. 联动测试无 token / 正确 token / 错误 token

### 验收

- [ ] 正确 token 后 API 正常
- [ ] 错误 token 显示 401
- [ ] 清除 token 后 401 恢复
- [ ] token 不出现在日志 / API GET 响应

---

## 7. Phase F：媒体库规模化

### 目标

超过 200 个 series 时仍保持可用，并降低前端一次性渲染压力。

### API 现状

- `GET /api/series?limit=200` 已对齐后端 `limit <= 200`
- 前端一次拉全量后做标题过滤

### 第一版增强：后端搜索

新增 query 参数：

```http
GET /api/series?limit=100&offset=0&q=芙莉莲
```

过滤字段：

- `title_cn`
- `title_jp`
- `title_romaji`

实现建议：

- SQL 层 `ILIKE` / SQLite `LIKE`
- 后端统一大小写与 trim
- 返回 `Page[SeriesOut]`
- 前端 debounce 250ms

### 第二版增强：分页

- Library 使用标准 Pagination
- 每页 24 / 48 / 100 可选
- 保留筛选 / 排序状态到 URL query

### 任务清单

1. 后端 `SeriesQuery` 增加 `q`
2. `ApiStore.list_series_page` 支持 keyword filter
3. 增加 API tests：
   - 中文标题
   - 罗马音
   - 日文标题
   - 空结果
   - 分页 total 正确
4. 前端 `series.list` 支持 `q`
5. Library 搜索改为 debounce 后端搜索
6. 增加分页
7. 加载态 / 空态 / 错误态补齐
8. 大数据量手工验收

### 验收

- [ ] `limit > 200` 仍返回 422，前端不再发送
- [ ] 后端搜索命中三种标题
- [ ] 分页 total 正确
- [ ] 搜索不再一次渲染全部 series
- [ ] 输入过程中无请求风暴

---

## 8. Phase G：SSE 与数据刷新联动

### 目标

操作和后台任务发生后，相关页面自动更新，减少手动刷新。

### 事件映射

| SSE 事件 | 需要刷新 |
|---|---|
| `subscription.created` | Subscriptions / Dashboard |
| `subscription.updated` | Subscriptions / Library |
| `subscription.deleted` | Subscriptions / Library / Dashboard |
| `rss_source.created` | RSS Sources |
| `rss_source.updated` | RSS Sources |
| `rss_source.deleted` | RSS Sources / Subscriptions |
| `pending_confirm` | Pending / Dashboard / Logs |
| `pending_correct` | Pending / Dashboard / Logs |
| `pending_reject` | Pending / Dashboard / Logs |
| `episode.organized` | Library / Dashboard / Pipeline / Logs |
| `upgrade.completed` | Library / Dashboard / Pipeline / Logs |
| `episode.gap` | Subscriptions / Dashboard |
| `pipeline.import.started` | Pipeline / Logs |
| `pipeline.import.progress` | Pipeline |
| `pipeline.import.completed` | Pipeline / Library / Logs |
| `pipeline.import.failed` | Pipeline / Logs |

### 方案

1. 在 EventStreamProvider 上暴露 `lastEventAt(category, message)`
2. 页面 hook 订阅相关事件
3. 命中后调用 `reload()`
4. 相同事件短窗口内去重，避免循环内多次刷新
5. 页面不可见时延迟刷新，回到可见时合并刷新

### 任务清单

1. 扩展 EventStreamProvider
2. 新增 `useReloadOnEvent()`
3. 各页面接入
4. 增加 Vitest
5. 真后端联动测试

### 验收

- [ ] Pending 操作后 Dashboard 自动更新
- [ ] RSS 操作后 Subscriptions 页 RSS 数量自动更新
- [ ] 导入完成后 Library 自动出现新集
- [ ] 不会因连续事件造成请求风暴

---

## 9. 执行顺序与依赖

| 顺序 | Phase | 原因 | 预估 |
|---|---|---|---|
| 1 | A Logs rollbackable | 小改动，先稳定既有语义 | 0.5 天 |
| 2 | B 订阅编辑 | 后端已有接口，收益高 | 1-2 天 |
| 3 | C RSS 编辑 | 后端已有接口，收益高 | 1-2 天 |
| 4 | E API Token 设置 | 独立，风险低 | 0.5-1 天 |
| 5 | G SSE 刷新联动 | 为后续长任务打基础 | 2 天 |
| 6 | D 管线操作入口 | 需要新增后端任务 API，改动较大 | 3-5 天 |
| 7 | F 媒体库规模化 | 可后置，但数据量上来前应做 | 2-3 天 |

---

## 10. 每阶段统一验收门禁

每个 Phase 合并前必须满足：

1. 后端：
   - `uv run pytest -q`
   - `uv run ruff check .`
   - `uv run pyright`
2. 前端：
   - `npm run typecheck`
   - `npm run lint`
   - `npm test`
3. 真服务联动：
   - `VITE_USE_MOCK=0`
   - 真后端 + Vite dev server
   - 至少覆盖新增主路径 + 失败路径
4. SSE：
   - 新增写操作必须验证对应 SSE 事件
5. Audit：
   - 新增写操作必须落 audit
6. 安全：
   - 新增接口不得回显密钥
   - 路径类输入必须校验
   - Windows 路径行为显式测试

---

## 11. 风险与注意点

### 订阅编辑

- 不要在第一版允许编辑标题，可能破坏 alias / parse_memory 归属。
- `status` 枚举变化必须和 DB 约束对齐。

### RSS 编辑

- token 需要区分“不修改”和“清空”。
- URL 中内嵌 token 按当前约定明文展示，但独立 token 字段必须保持不回显。

### Pipeline 触发

- import 可能长时间运行，不能阻塞事件循环。
- 必须避免同一目录并发导入。
- dry-run / 正式 run 必须有明确审计区分。
- Windows 路径与保留名要进测试。

### SSE 刷新

- 不建议每个事件都全页 reload。
- 短时间多次事件要 debounce / coalesce。
- 页面不可见时应合并刷新。

---

## 12. 建议第一轮执行包

如果一次只做一个最小可合并包：

1. 修 Logs rollbackable
2. 增加订阅编辑
3. 增加 RSS 编辑
4. 更新计划文档

可以不做：

- 管线触发
- 媒体库后端搜索
- SSE 全量刷新

这样风险最小，且能立刻补齐 WebUI 日常运维短板。
