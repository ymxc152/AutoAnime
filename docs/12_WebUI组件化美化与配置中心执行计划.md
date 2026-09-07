# WebUI 组件化、美化与配置中心执行计划（12）

> - 状态：**已拍板，实施中**
> - 拍板记录（2026-09-07）：组件库 = **方案 A shadcn/ui**；美化幅度 = **方案乙 视觉重设计**（重定调色板/字体/圆角/阴影，12-C 相应上调为重设计走查）
> - 基线：`v2 @ 667cf59`（含一键启动器）；摸排时后端 1099 tests 绿，前端 105 tests 绿
> - 依据：2026-09-07 三路只读审计 + Playwright 8 页截图实测（截图在 `<工作区>/scripts/ui-review/`）
> - 前置文档：`docs/11_WebUI产品化缺口执行计划.md`（A–G 已完成，本计划是其后续迭代）
> - 目标：解决用户四项抱怨——界面不美观、不实用、配置无法全在 WebUI 完成、文字显示不全

## 0. 摸排结论摘要（详见审计，此处只留结论）

1. **配置覆盖极低**：后端 `config.py` 约 45 字段，Settings 页仅 6 项可编辑；PUT 白名单仅这 6 个（`routers/settings.py:21-23`）；仅内存 `setattr` 不持久化，重启回 `.env`；调度参数启动时固化，运行时改了不生效。qBittorrent 全套 / LLM base_url+key / TMDB key / 通知全套 / RSS 轮询间隔 / 洗版阈值均无 WebUI 入口。`.env.example` 缺 `AUTOANIME_TMDB_API_KEY` 条目。
2. **功能入口缺口**：Library 全只读；`reject` 不传 reason、`confirm` 不传覆写字段；`parse-preview` 缺 folder/parent；scheduler scope 写死 `all`；行级 rollback 无入口；CLI 独有 `confirm`（库外学习）、`rerun --source-id`（单源轮询）、`report` 无 WebUI 入口；错配 A/B/C / 手动洗版 / 手动回补 / 通知测试发送——后端无端点。
3. **显示缺陷**：Pending `raw_name`(max-w-sm)/`reason`(max-w-xs)、RssSources URL(max-w-md)、Logs operation_id、Pipeline message 均 `truncate` 硬截断；Settings 路径在 `md:w-64` 固定容器溢出；`SseStatusLine.tsx:17` 占位符 `{attempt}` 字面直出；枚举英文直出与中文映射两页不一致。
4. **交互/视觉**：缺 Toast / 居中 Modal / Tabs / Tooltip / DropdownMenu / Textarea / Checkbox 组件 / 图标体系 / 表头排序 / 分页跳转；`window.confirm` 原生弹窗；**暗色模式不跟随系统**（首帧脚本只认 localStorage，实测截图证实）；保存 Token 后强制 reload 丢弃未保存编辑；列表 `limit:100/200` 拉全量。
5. **正面事实（保留不推翻）**：自建 Soft Ink token 体系（`tokens.css` + `@theme inline`）一致性良好；暗色 token 全覆盖；响应式 md 断点 + 移动降级齐全；mock 有开关不进生产包。

---

## ⚠️→✅ 拍板 1：组件库引入方式（已定：方案 A shadcn/ui）

候选（React 19 + Tailwind 4 + Vite 约束下）：

| 方案 | 形态 | 优点 | 缺点 |
|---|---|---|---|
| **A. shadcn/ui（推荐）** | 复制源码进仓 + Radix 无头基元 + `lucide-react` 图标 + `sonner` Toast | 代码归我们所有，深度定制不改依赖；Radix 提供 a11y（焦点圈禁/Esc/aria）；与 Tailwind 4 + CSS var token 体系天然契合；社区模板最多 | 引入 `radix-ui`/`cva`/`clsx`/`tailwind-merge` 等若干轻依赖；首次接入约 1-2 天 |
| B. Radix UI Themes | npm 运行时组件库 | 开箱即用、主题变量化 | 主题体系与 Soft Ink 冲突，需整体换肤；运行时锁定 |
| C. Ant Design 5 | npm 重库 | 组件最全 | 体积大、自带强设计语言（与美化目标背道而驰）、CSS-in-JS 与 Tailwind 混用乱 |
| D. 继续自建补齐 | 纯手写 | 零依赖 | Toast/焦点管理/a11y 细节成本高，质量上限低 |

**推荐 A**。理由：本项目是 local-first 单用户工具，组件总量需求约 15 个；shadcn/ui 的「代码进仓、无运行时库」模式与现有 token 架构无缝（把 shadcn 的 `--background` 等桥接到 `--ink-*` 即可保留 Soft Ink 配色）；需要的关键件（Dialog/Toast/Tabs/Tooltip/DropdownMenu/Select/Checkbox/Textarea/Command）全部有现成实现。

## ⚠️→✅ 拍板 2：美化幅度（已定：方案乙 视觉重设计）

- **重设计范围**：重定调色板（token 层换新值，保留 CSS var 架构）、字体栈、圆角、阴影、组件默认密度；暗色两套 + 响应式全部重验；预估比精修多 2-3 天
- **12-B 引入 shadcn/ui 时直接以新 token 为准**（桥接层不需要兼容旧配色，只需保持 `--ink-*` 命名不换，值全换）

---

## Phase 划分

| 顺序 | Phase | 主题 | 依赖 | 预估 |
|---|---|---|---|---|
| 1 | 12-A | P0 缺陷修复（截断/文案/暗色/reload） | 无 | 0.5-1 天 |
| 2 | 12-B | 组件底座（shadcn/ui 引入 + token 桥接 + 基础件替换） | 拍板 1 | 1-2 天 |
| 3 | 12-C | 全站美化走查（8 页逐页） | 12-B + 拍板 2 | 2-3 天 |
| 4 | 12-D | 配置中心后端（持久化 + 白名单 + 热生效 + 通知测试） | 无（可与 12-B 并行） | 2-3 天 |
| 5 | 12-E | 配置中心前端（Settings 分组表单） | 12-D（+12-B 完成体验最佳） | 1-2 天 |
| 6 | 12-F | 功能入口补齐 | 12-D 部分依赖 | 2-3 天 |

---

## 12-A：P0 缺陷修复（纯前端，先跑通门禁热身）

### 任务清单

1. 截断治理（保留 `truncate` 但加自适应宽度 + `title` + 点击展开复制）：
   - `Pending.tsx:358,372`：`max-w-sm`/`max-w-xs` 移除，改表格列宽自适应 + 单行省略 + 悬停 title + 点击 Drawer 内看全文
   - `RssSources.tsx:317`：URL 同上
   - `Logs.tsx:108`、`Pipeline.tsx:394`：同上
   - `Settings.tsx:237,240` + `form.tsx:131`：SettingRow 值容器去掉固定 `md:w-64`，改 `min-w-0 flex-1 break-all`
2. `SseStatusLine.tsx:17`：改走 strings 的 `t()` 插值，消除 `{attempt}` 字面直出
3. 暗色跟随系统：
   - `index.html` 首帧脚本：`localStorage` 未设置时读 `matchMedia('(prefers-color-scheme: dark)')`
   - `useTheme.ts`：toggle 写回 localStorage；可选监听系统偏好变化
4. 枚举中文化统一：抽 `lib/labels.ts`，`Subscriptions.tsx:253-254`、`Pending.tsx:366`、`Dashboard.tsx:202-205` 全部走映射
5. Settings 保存 Token：去掉 `window.location.reload()`，改为重新拉 settings + 行内提示
6. `.env.example` 补 `AUTOANIME_TMDB_API_KEY=` 条目

### 验收

- [ ] 待确认页长文件名在 1280px 宽下可见主体，悬停/点击可见全文
- [ ] SSE 断连重连提示显示真实次数
- [ ] 系统暗色偏好 + 未手动切换过主题 → 首屏即暗色（Playwright `colorScheme: dark` 实测）
- [ ] 追番/待确认/总览三页枚举全部中文
- [ ] Settings 保存 Token 不再丢失未保存编辑
- [ ] `npm run typecheck` / `lint` / `test` 全绿

---

## 12-B：组件底座（方案 A 为例，拍板后细化）

### 任务清单

1. 基建：`components.json` + `cn()`（clsx + tailwind-merge）+ `cva`；shadcn 的 `--background`/`--foreground` 等 CSS var 桥接到现有 `--ink-*`（`styles/tokens.css` 增加别名段，`.dark` 同步）
2. 引入组件（复制进 `components/ui/`，与现有 `components/` 手写件并存，逐个替换）：button / input / textarea / select / checkbox / switch / dialog / dropdown-menu / tabs / tooltip / badge / table / sonner(toast) / skeleton / separator
3. 图标：`lucide-react`（tree-shaking，仅按需 import）
4. 替换点第一批（行为等价重构，不改布局）：
   - 裸 `<input type=checkbox>`（Pending/RssSources）→ checkbox
   - `window.confirm`（Settings.tsx:70）→ AlertDialog（居中 Modal）
   - 行内文字成功提示 → toast
   - Drawer 保留（右侧抽屉场景仍成立），Dialog 用于确认/短表单
5. Button 注释宣称的 active 态补上（或由 shadcn button 取代）
6. Select 自绘箭头（shadcn select 原生解决暗色箭头问题）

### 验收

- [ ] 新旧组件并存期无样式冲突；8 页截图对比无回归
- [ ] Dialog 有焦点圈禁 / Esc / 遮罩关闭（复用现有 useFocusTrap 或 Radix 内建）
- [ ] toast 在暗色下配色正确
- [ ] bundle 增量 < 80KB gzip（`npm run build` 前后对比）
- [ ] 前端三件套全绿 + 105 个既有测试全绿（替换组件的测试同步迁移）

---

## 12-C：全站美化走查（视觉重设计）

### 原则（方案乙，已拍板）

token 层重定调色板/字体/圆角/阴影（`--ink-*` 命名不动、值全换，明暗两套同步重定）；shadcn/ui 组件以新 token 为默认样式；每页「改前/改后」截图存 `scripts/ui-review/12c/`；暗色 + 响应式全量重验。

### 逐页要点

| 页面 | 改动 |
|---|---|
| Layout | 侧栏加 Logo 区块 + 当前项左侧指示条 + 图标；SSE banner 改为顶部细条 + toast 化重连提示 |
| Dashboard | 三卡加图标与趋势语义色；两个「暂无数据」图补迷你图（复用 SSE 计数，不引图表库，SVG 手绘 sparkline） |
| Subscriptions | 行密度收紧；status Badge 语义色；空态加引导按钮 |
| RssSources | URL 列展示域名主体 + 完整值进 Drawer；上次拉取结果/错误列（消费后端已有字段） |
| Pending | 操作按钮组右对齐固定；纠正抽屉表单分区 |
| Library | 卡片网格补海报占位图、进度条（已有集/总集数）；集行密度 |
| Pipeline | 手动操作卡片图标化；节点卡片配色统一 token |
| Logs | operation_id 可复制按钮；rollback 按钮仅 rollbackable 显示（已做，保持） |
| Settings | 分组导航锚点（12-E 前先视觉分区） |

### 验收

- [ ] 8 页 × 明暗两套截图走查通过
- [ ] 窄窗口（md 768px）与宽屏（1920px）两档无溢出/无横向滚动（表格横滚除外）
- [ ] Lighthouse 可用性抽检无明显可访问性回归

---

## 12-D：配置中心后端

### 设计

1. **持久化**：新增 `app_settings` 表（key TEXT PRIMARY KEY, value TEXT, updated_at）；只存「与默认值不同的覆盖项」，启动时 env/toml → DB 覆盖合并出运行时 Settings
2. **PUT 白名单扩展**（分三档）：
   - 立即生效（进程内 setattr，同现有 6 项）：`llm_timeout_s`、`llm_max_retries`、`reference_qps`、`pending_backlog_alert_threshold`、`log_level`
   - 调度类（重建 loop 后生效）：`scheduler_enabled`、`rss_poll_interval_minutes`、`rss_poll_jitter_pct`、`download_poll_interval_s`、`collected_check_days`——PUT 后触发 scheduler 重建（`routers/scheduler.py` 的 build_loop 需支持重入）
   - 重启生效（连接/密钥类，写 DB + 提示需重启）：`llm_base_url`、`llm_api_key`（写库加密或明文？→ 明文 + 不回显）、`tmdb_api_key`、`downloader`、`qbittorrent_*`、`notify_*`、`upgrade_threshold`、`upgrade_max_per_episode`、`upgrade_copy_policy`、`mismatch_backfill_budget`、`naming_title_language`
3. **密钥纪律**：GET 永不回显密钥值，只回 `has_*` 布尔；PUT 空串 = 不修改，显式 `null` = 清除（沿用 RSS token 交互惯例）
4. **新增端点**：
   - `POST /api/settings/notify-test`：发送测试通知（webhook + telegram 各一发），返回逐通道结果
   - `POST /api/settings/qbit-test`：qBittorrent 连接测试（登录 + 返回版本）
5. **审计**：settings PUT / 测试动作落 audit_log
6. `.env.example` 与 `config.py` 的差异收口（TMDB key 注释、WEB_PORT 标注为部署层变量）

### 任务清单

1. SQLAlchemy model + alembic migration（`app_settings`）
2. `load_settings` 合并逻辑（env → db 覆盖）+ 单测
3. PUT 路由改造：白名单分组 + 落库 + 调度重建钩子
4. scheduler 重建支持（旧 loop 取消、锁释放语义复用 review 收口经验）
5. notify-test / qbit-test 端点 + 测试（mock 外呼）
6. audit 事件：`settings.updated` / `settings.notify_test` / `settings.qbit_test`
7. API tests：白名单外 422、密钥不回显、重启生效档提示字段、token 空串/null 语义

### 验收

- [ ] 改 RSS 轮询间隔 → 不重启，下一轮按新间隔跑（日志/事件验证）
- [ ] 改 qBittorrent 密码 → 提示重启生效；重启后生效
- [ ] GET /api/settings 任何密钥字段只有 `has_*`
- [ ] 通知测试：webhook 与 telegram 分别返回成功/失败明细
- [ ] 全部配置动作落 audit
- [ ] 后端三件套全绿（pytest/ruff/pyright）

---

## 12-E：配置中心前端

### UI（Settings 页重排为 Tabs：运行 | 识别 | 下载器 | 洗版 | 调度 | 通知 | 环境）

| 分组 | 内容 | 控件 |
|---|---|---|
| 运行 | dry_run / l2 / llm_enabled / log_level | Switch / Select |
| 识别 | llm_model / llm_base_url / llm_api_key / llm_timeout / reference_order / reference_qps | Input / Password / Number |
| 下载器 | downloader / qbit host/port/user/password / qbit-test 按钮 | Input + 测试按钮 |
| 洗版 | upgrade_threshold / max_per_episode / copy_policy / mismatch_backfill_budget | Number / Select |
| 调度 | scheduler_enabled / rss_interval / jitter / download_poll / collected_check_days | Switch / Number（标注「保存后自动生效」） |
| 通知 | notify_enabled / webhook_url / telegram token / chat_id / notify_events / notify-test 按钮 | Switch / Input / Password + 测试按钮 |
| 环境 | 只读展示（路径/SSE/token has_*）保持现状 | — |

### 交互纪律

- 每组独立保存按钮 + dirty 提示；密钥字段 `password` 型，占位显示 `已配置/未配置`
- 调度组保存成功 toast「已生效」；重启生效组 toast「重启后生效」
- `useBlocker` 未保存拦截保留（换 AlertDialog）

### 验收

- [ ] WebUI 可完成 qBittorrent / LLM / TMDB / 通知 / 洗版 / 调度全量配置（不再需要改 .env 的场景 = 除首次路径配置外）
- [ ] 密钥任何界面不回显明文
- [ ] 与 12-D 验收联动（改间隔不重启生效实测）
- [ ] 前端三件套全绿 + 新增 Vitest（分组渲染、dirty、测试按钮态）

---

## 12-F：功能入口补齐（小 EP 集，可按序拆多个小 PR）

1. **前端收窄恢复**（后端已支持）：reject 带 reason 输入；confirm/correct 覆写 season/episode/segment/fansub；parse-preview 补 folder/parent；run-once scope 三选（all/rss/download）
2. **行级 rollback**：Logs 行操作（端点已支持任意 audit_id）
3. **单源立即轮询**：后端 `POST /api/rss_sources/{id}/poll`（复用 rerun --source-id 逻辑）+ RSS 行按钮
4. **库外学习**：Pipeline 页新增「人工确认命名」表单（等价 CLI confirm），落 parse_memory + audit
5. **Library 操作首版**：集行「重新识别」按钮（后端 `POST /api/episodes/{id}/reparse`，进 L1-L3 管线，错配走 A/B/C）——错配干预的入口式第一步
6. **report 报表页**：Dashboard 加「识别指标」区块消费 metrics 扩展（或独立小节）

### 验收

- [ ] 每项对应 SSE 事件 + audit 事件
- [ ] reparse 不与 scheduler 并发冲突（互斥锁）
- [ ] 前后端三件套全绿 + 真服务联动（VITE_USE_MOCK=0）

---

## 执行顺序与门禁

- 顺序：12-A → 12-B → 12-C ⇄（12-D → 12-E）→ 12-F；12-D 可与 12-B 并行（前后端不同人/不同分支互斥路径）
- 每 Phase 合并前门禁沿用 `docs/11` 第 10 节：后端 pytest/ruff/pyright + 前端 typecheck/lint/test + 真服务联动 + SSE 事件验证 + audit 落库 + 密钥不回显
- 新依赖（radix-ui / cva / clsx / tailwind-merge / lucide-react / sonner）进 lockfile 前按 `generated-artifacts` 纪律评审：来源 npm 官方、版本锁死、bundle 增量报告

## 风险与注意点

1. **12-B 新旧组件并存**：替换必须逐个组件 + 逐页截图回归，避免一次性换皮造成 105 个测试大面积红
2. **12-D 调度重建**：loop 重入与互斥锁释放是 review 高危区（参照收口记录「scheduler 构建失败释放互斥锁」）
3. **12-D 写库密钥**：SQLite 明文存 LLM/TMDB/qbit/telegram 密钥——单机本地工具可接受（与 `.env` 同级安全），但必须：不进日志、不进 GET 响应、不进 audit 详情
4. **12-F reparse**：重新识别会覆盖既有归档判定，必须走 dry-run 预览 + 确认两步，避免误伤已归档集
5. 暗色系统偏好在 12-A 修复后，12-B 引入的组件必须两套主题都验
