> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# M2-M4 第一版(End-to-End)执行 Plan

> 生成日期:2026-09-06。基线:**v2 @ PR5(T1-T6)+ PR6(P1/P2)+ PR7(参考源归一化)全部合并之后**(2026-09-06 修订:PR7 拍板先行于 E1,见 PR7_参考源归一化Plan.md)。
> 定位:第一版 = 功能完整 + WebUI 完整的可演示产品,先跑通闭环,优化(性能/样式/边界)后置。
> 借鉴来源:REFERENCE.md(2026-09-05 实测调研版);架构依据:ARCHITECTURE.md §0-§9。

---

## 0. 执行形态(重要,先读)

用户期望「一个大任务让 agent 直接写跑」。现实约束:单个 agent 会话上下文有限,M2-M4 全量约 20-30 个模块文件 + 前端整包,一次跑完必然出现后半程质量塌陷且无法中途验收。

**采用形态:1 份本计划 + 4 个串行「大任务」**。每个大任务 = 粘一段公共提示词 + 一段任务提示词,agent 在 worktree 中自主长跑(内部自主拆步骤、自主 TDD),完成后主会话合并验收。体验上接近「一个任务写完」,但每阶段有门禁兜底。

```text
PR7 参考源归一化(先行:M1 匹配层 → M2/M3 → V1 回归)
        ↓
E1 M2 收尾:指标 + 合批 + CLI 报表     ← 1 个 agent 长跑
        ↓ 合并验收
E2 M3 后端:FastAPI + SSE 全量 API     ← 1 个 agent 长跑
        ↓ 合并验收
E3 M3 前端:新建 8 页面 WebUI       ← 1 个 agent 长跑(可与 E4 部分并行)
        ↓ 合并验收
E4 M4 闭环:RSS 调度 + 下载网关 + 洗版 + 通知 + docker-compose ← 1 个 agent 长跑
        ↓ 合并验收
第一版完成 → README + demo 素材(另行小任务)
```

## 0.1 拍板记录(2026-09-06 用户逐项确认,冻结)

| # | 决策 | 结论 |
|---|------|------|
| D1 | LangGraph | **不引入**;叙事改「评估过,单次结构化转换场景框架是负收益,SDK 直调 + 结构化输出 + 确定性管线」 |
| D2 | 前端落位 | 进 `AutoAnime/frontend/`;**UI 全部新建,不复用 AnimeAgent/AutoAnime 旧 UI 代码**(栈保 React 19 + Tailwind 4 + @xyflow/react;Soft Ink 设计纪律照搬;AnimeAgent 仓库冻结归档) |
| D3 | v1 通知 | webhook + Telegram 双做最小版 |
| D4 | Chat 页 | 丢弃,Pipeline 页顶替 |
| D5 | 下载器实测 | 维持:aria2 只接口+离线测试;qBittorrent 真连等 E4 完成后当次确认(L2) |
| D6 | API 认证 | simple token(AUTOANIME_API_TOKEN env,默认空);README 写「勿暴露公网」 |
| D7 | 开源协议 | **MIT** |
| D8 | v2 转正时机 | 第一版验收后一次性转正(main 归档、README 指向 v2、MIT 落定、demo 素材齐) |
| D9 | 洗版跨盘降级 | 默认 **copy**;单文件 >20GB 跳过记 audit;strict 永不 copy 可配 |
| D10 | 第一版验收线 | 按验收剧本:docker-compose 一键起 → 在放番剧订阅全自动闭环 1h 内归档 → 2608 库存导入出指标报告 → xyflow 页实时流动(人工介入率是运营指标不进验收) |
| D11 | L3 模型 | v1 单模型可配(llm_model 填便宜模型);小→大级联进 backlog |
| D12 | Discovery 页 | 丢弃;「发现」并入 Subscriptions(Mikan 选番);规则化发现进 backlog |
| D13 | 订阅上下文传递 | 订阅匹配结果 = expected context,权威载体 release_record(episode_id/torrent_hash);下载完成扫描时逐文件附带传入管线;对齐一致 → HIGH 快路径(跳过 L2 查找与 API 匹配);手动导入路径 expected=None,两路共用同一解析代码 |
| D14 | 订阅错配恢复 | 整理时发现错配:不归档,隔离 + rejected;优先改挂(内容正确仅归属错,零重下);否则 episode 回 MISSING 立即回补重下;配套 torrent_hash/来源拉黑 + 单集回补预算(默认 2 次,超限转人工) |
| D15 | 回补/洗版来源(审核 B1) | **v1 诚实降级**:缺集检测产出缺口报告+通知,回补 = 等 RSS 新条目自然命中(去重后走订阅快路径);评分 seeders/size 未知 → 0 分参与不剔除;主动搜索 indexer(Nyaa API 等)进 backlog(v2 第二下载飞轮) |
| D16 | 进程模型(审核 B2) | **v1 单 backend 进程**:compose 单容器,lifespan 同时起 FastAPI + AsyncIOScheduler;内存事件总线仅进程内,SSE 重放基于落库数据;CLI 只做库操作不承载事件 |
| D17 | 归档命名模板 | Sonarr 兼容:`{title_cn}/Season {SS}/{title_cn} - S{SS}E{EE}.{quality}.mkv`;标题语言 title_cn → romaji 回退,Settings 可配(Jellyfin/Plex 零配置) |
| D18 | 字幕文件策略 | v1 只做同包字幕自动跟随:视频归档时同目录同名字幕跟随改名(.zh.ass 等后缀)一起移动;不做字幕站下载、不处理内封字幕提取 |
| D19 | LLM 默认 | `llm_model` 默认 `deepseek-chat`;配置仅 OPENAI_BASE_URL + OPENAI_API_KEY 两个 env;不配 key → L3 关闭,LOW 全进人工(验证优雅降级路径) |
| D20 | 放送时区纪律 | air_date 判定一律用 JST,界面展示转本地时区;防日本凌晨放送番的假缺口 |
| D21 | 洗版旧种处理 | v1 完全不动下载器:hardlink 下归档侧替换不影响做种原件,旧种继续做种;仅 upgraded_count + audit 记录;暂停/删除旧种进 v2 策略 |

> 2026-09-06 联网核实补充(来源:PyPI/npm 实测 + AutoBangumi 官方文档 + V2EX):
> - 依赖钉版确认:APScheduler 停在 3.x(3.11.3,2026-06;4.0 重写线勿用)、feedparser 6.0.14(2026-07 仍发布,
>   非「弃维护」旧情报)、qbittorrent-api 2026.8.1(calver)、sse-starlette 3.4.11、
>   @xyflow/react 12.11.6(peer react>=17,与 React 19 兼容无坑)
> - Mikan RSS 实操坑(已写进 E4 任务提示词):主站被墙须可配备用域名 mikanime.tv、RSS token 按密钥处理、
>   每番单字幕组最佳实践、OVA/剧场版订阅不支持走散装导入

## 1. 借鉴映射(从 REFERENCE.md 提炼,任务内直接引用)

| 主题 | 借谁 | 借什么 | 纪律 |
|------|------|--------|------|
| 洗版/整理职责切分 | MoviePilot `app/chain/transfer/` | plan/execution/checkpoint/retry/history 的文件级职责划分,正对位 organize/ 四件套 | GPL-3.0,只学结构不抄代码 |
| 做种中替换文件 | MoviePilot filemanager / Sonarr | **hardlink 策略**:下载目录原件不动,归档目录硬链接后改名;跨盘降级 copy | 思想,自实现 |
| 洗版触发判定 | Sonarr Custom Formats | 候选评分制 + 「新分 ≥ 现分 + 阈值才升级」;评分公式用 ARCHITECTURE §3 现成定义 | 只学判定逻辑 |
| RSS 订阅最小闭环 | BGmi(MIT) | 订阅→拉取→过滤→下载的最小状态机,仓库小可通读 | MIT,可代码级参考 |
| RSS 条目语义 | FlexGet(MIT) | series/parsing/seen/rejected/estimate_release 的 accept/reject 语义 | MIT,可代码级参考 |
| SSE 模式 | Auto_Bangumi(MIT) | SSE 事件流驱动 WebUI(它已验证此栈在此类产品可行) | MIT |
| UI 设计纪律 | Auto_Bangumi DESIGN.md | Soft Ink:CSS token 明暗双主题 + 反模式清单(拒绝粉彩 pill/发光点/彩色大底警报框) | 照抄规范本身(MIT 仓库内文档) |
| 避坑 | 全部 | 不做 LLM-primary(AutoBangumi 反面);不抄 n8n 代码(fair-code);ani-rss GPL-2.0 同 MoviePilot 纪律 | — |

## 2. 公共提示词(每个 E 任务开跑前先粘贴)

```text
你在独立 worktree 中工作,不要切换主仓库分支。

仓库:C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\AutoAnime
基线分支:v2(worktree 路径在任务提示词中给出;开工前先 git log --oneline -3 v2 确认基线)

必读文档(动工前全部读完):
- ARCHITECTURE.md(架构真源;本任务涉及的章节)
- REFERENCE.md(借鉴映射与协议纪律:GPL 项目只学思想不抄代码,MIT 可参考实现)
- notes/M2_M4_第一版Plan.md(本计划,契约决策不得自行更改)

项目铁律:
1. 单用户本地工具(决议 #1);SQLite 单库(决议 #4);所有状态进库,不引入文件态缓存。
2. Python 3.12 + uv + async SQLAlchemy 2.0 + pydantic 配置;pytest/ruff/pyright 三件套每次收工全绿。
3. 不引入模块级可变全局状态;DB 会话只在 store 层;网络调用只在 provider/gateway 层。
4. AI 边界(9.2):LLM 只做非结构化转换;调度/评分/洗版触发/缺集检测全部确定性代码。
5. 每完成一个逻辑单元 commit(中文 message,说清改了什么与影响范围;禁止 Co-Authored-By 或任何 Claude 归属 trailer)。
6. 只提交推送自己的 task/* 分支;GitHub 超时用环境变量 HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 重试(不改 git config)。
7. 单元测试一律离线(下载器/RSS/API 均用 fake + 录制 fixture);真实外联只在任务提示词明确允许时手动做。
8. 发现契约决策不可行:停下写入最终报告,不静默偏离;发现前任任务 bug 只记录不越界修。
9. 第一版纪律:先跑通再优化;不做计划外重构;范围外想法记进报告 backlog,不实现。
10. 进程模型(D16):FastAPI + APScheduler 同一 backend 进程(compose 单容器);内存事件总线仅进程内有效,
    SSE 重放基于落库数据(audit/daily_metrics);CLI 只做库操作,不承载事件。
```

## 3. E1:M2 收尾——指标、合批、CLI 报表

```text
任务:E1 M2 收尾(指标产出 + L3 机会主义合批 + CLI 报表)

分支:task/e1-m2-metrics
worktree:git worktree add "..\worktrees\e1-m2-metrics" -b task/e1-m2-metrics v2

只允许修改:
- autoanime/pipeline/**(合批器;orchestrator 仅增量接线合批入口)
- autoanime/cli.py(report 子命令扩展)
- scripts/validate_metrics.py(新建,离线快照跑批产出指标 JSON)
- autoanime/core/events.py(如需新增事件类型,增量)
- tests/**(对应新增)

实现内容:
1. 机会主义合批(ARCHITECTURE 9.3b):纯函数 batch_organizer——队列自然堆积 ≥5 个
   「同目录+同字幕组」才打包,上限 20,逐项校验失败不连坐;单文件快路径不变(订阅场景永不凑批)。
   订阅/库存两种入口共用。
2. 指标产出(validate_metrics.py,参考 validate_l3_corpus.py 模式):
   输入外部快照 --snapshot,离线全跑 L1→L2→L3(fake)→合批,输出 JSON:
   total / l1_high / l2_hit / l3_entered / llm_calls(fake 计数) / p50_p95_ms /
   记忆命中率 / 合批批次统计 / canonical_hit / alias_hit(PR7 前置消歧与 title_aliases 命中)。
   无快照环境单测 skip。
3. CLI report 扩展:autoanime report --json 汇总库内 daily_metrics + audit,
   增加人工介入率口径 = (audit 中 manual 纠正事件数) / (总归档事件数)。
4. 边界:合批上限/阈值进 config(增量字段,默认 ≥5/20),不硬编码。

验收:pytest/ruff/pyright 全绿;快照实测产出指标 JSON 报告(路径写入最终报告);
     git status 干净;推送 task/e1-m2-metrics。

最终报告:分支、commit、指标 JSON 全文、合批决策流说明、发现的 bug 清单。
```

## 4. E2:M3 后端——FastAPI + SSE 全量 API

```text
任务:E2 M3 后端(FastAPI + SSE,覆盖前端 8 页面所需全部资源)

分支:task/e2-api
worktree:git worktree add "..\worktrees\e2-api" -b task/e2-api v2

只允许修改:
- autoanime/web/**(新建 FastAPI 应用;routers/schemas/deps 分层)
- autoanime/core/events.py(事件总线接 SSE 的 adapter,增量)
- autoanime/core/models.py + autoanime/alembic/versions/**(B3 增量:rss_sources 表,
  id/url/token SecretStr/season_id/enabled/last_polled_at,alembic revision)
- autoanime/config.py(增量:api_token/api_port/cors 等字段)
- tests/unit/test_web_*.py、tests/integration/**(API 集成测试)

实现内容:
1. FastAPI 应用(web/app.py):lifespan 内创建 SqliteStorage;依赖注入式拿会话;
   autoanime/api serve 启动(uvicorn);?--dev 模式 CORS 放开 localhost:5173。
2. REST 资源(与前端 8 页面一一对应,schemas 用 pydantic,分页 limit/offset):
   - GET /api/metrics        → Dashboard:人工介入率/各级命中率/LLM 调用率周曲线/待确认队列趋势
   - /api/series             → Episodes 页:series 列表 + season/episode 树 + 状态机字段 + quality_score
   - /api/pending            → Pending 页:待确认队列;POST /{id}/confirm、POST /{id}/correct(字段纠正,
                               触发 5.2 学习三件套:parse_memory+alias+bypass)、POST /{id}/reject
   - POST /api/organize/{id}/rollback → Logs 页的撤销整理(执行 reverse instruction + 学习流程)
   - /api/audit              → Logs 页:audit_log 分页 + 按 operation_id 分组
   - /api/subscriptions、/api/rss_sources → 订阅/RSS 源 CRUD(数据落 series/schedule_state 相应表)
   - GET/PUT /api/settings   → Settings 页:quality 阈值/自主权限档位/LLM 开关等运行时项
3. SSE:GET /api/events(sse-starlette),桥接既有事件总线,事件分类(events.py)原样透传;
   客户端断线自动清理;无消息 30s 心跳注释防代理超时。
4. 认证(拍板 #6):AUTOANIME_API_TOKEN 非空时校验 X-API-Token 头,空则跳过;中间件一处实现。
5. 事件回放:SSE 连接建立时可选 Last-Event-ID 重放最近 N 条(daily_metrics/audit 查询实现,防漏报)。
6. 所有 handler 不写业务逻辑:识别/归档/学习走既有 pipeline 与 store 方法;路由层只做参数校验与组装。

验收:pytest/ruff/pyright 全绿;uvicorn 起服后用 httpx 脚本跑一遍资源冒烟(写进报告);
     SSE 用 curl -N 实测收到心跳与至少一类事件;git status 干净;推送。

最终报告:分支、commit、端点清单表(方法/路径/请求响应 schema 摘要)、SSE 事件类型表、冒烟输出摘录。
```

## 5. E3:M3 前端——新建 8 页面 WebUI

```text
任务:E3 M3 前端(新建 WebUI:8 页面 + @xyflow/react 管线页)

分支:task/e3-webui
worktree:git worktree add "..\worktrees\e3-webui" -b task/e3-webui v2
(前置:E2 已合并;/api 端点清单以 E2 报告为准)

⚠️ D2 拍板:UI 全部新建,不复用 AnimeAgent/AutoAnime 任何旧 UI 代码(组件/页面/样式/文案都不搬);
栈 = React 19 + Tailwind 4 + TypeScript(strict) + Vite + @xyflow/react。

只允许修改/新建:
- frontend/**(v2 仓库内新目录)

实现内容:
1. 脚手架:Vite + React 19 + Tailwind 4 + tsconfig strict;目录 api/ components/ pages/ hooks/;
   文案中文单语,集中在一个 strings 模块(i18n 结构预留,不引库)。
2. 自建最小组件库(用到才写,先于页面):Layout(sidebar 桌面/折叠移动)、数据表格(分页)、
   状态徽标(7-10px 小色标)、抽屉(Drawer)、表单基础件、卡片、SSE hook(useEvents,断线重连
   + Last-Event-ID)、useApi(loading/error 态统一)。
3. 页面映射(8 页):
   Dashboard(指标卡:人工介入率/本周归档数/LLM 调用率)
   Library(series 卡片网格 + season/episode 明细抽屉 + quality_score 徽标)
   Subscriptions(追番管理:列表 + 放送进度条 + 降频状态标;Mikan 选番入口文案「每番只订一个字幕组」)
   RSSSources(源管理:增删启停)
   Pending(待确认队列:逐条 diff 视图——各字段 evidence 来源标注 name/folder/memory/llm,
           纠正表单提交 /correct;人工介入率主战场,交互要顺)
   Pipeline(@xyflow/react:L1→L2→L3→arbiter→organize 节点图,每节点实时命中率徽标,
           SSE 驱动文件流动画;demo 招牌,优先做)
   Logs(audit 时间线 + operation_id 分组展开 + 撤销整理按钮)
   Settings(下载器/LLM 开关/自主权限三档/质量阈值表单,PUT /api/settings)
4. Soft Ink 设计(照搬 AutoBangumi DESIGN.md 规范,代码自写):
   src/styles/tokens.css(CSS 变量明暗双主题);数据文本 mono + tabular-nums;
   状态只用小色标,彩色不上正文;反模式自查(拒绝粉彩 pill/圆角方块图标空状态/发光点/彩色大底警报框)。
5. 数据层:api/ 客户端全部指向 E2 端点;E2 未合并期间用 vite proxy + mock 开发,合并后关。
6. 质量门:tsc -b 零错、eslint 零错、每页一个渲染冒烟测试(vitest);
   npm run build 产物可静态预览。

验收:全部门禁绿;npm run dev + uvicorn 联调截图 8 页各一张(存 notes/screenshots/,
      不进 git);git status 干净;推送。

最终报告:分支、commit、页面-端点对照表、SSE 重连策略、反模式自查表、截图路径。
```

## 6. E4:M4 闭环——RSS 调度 + 网关 + 洗版 + 通知 + 部署

```text
任务:E4 M4 闭环(订阅调度 + 缺集回补 + 下载网关 + 洗版引擎 + 通知 + docker-compose)

分支:task/e4-loop
worktree:git worktree add "..\worktrees\e4-loop" -b task/e4-loop v2
(前置:E1 已合并;E2/E3 建议已合并,至少 E2 已合并)

只允许修改:
- autoanime/scheduler/**(占位转实现,APScheduler)
- autoanime/gateway/**(qbittorrent.py 占位转实现;aria2.py 接口+测试)
- autoanime/organize/**(mover/upgrade/rollback/naming 占位转实现)
- autoanime/providers/notify.py(新建;通知注册进 Registry)
- autoanime/config.py(增量:scheduler/qbittorrent/notify/upgrade 阈值字段)
- autoanime/core/models.py + autoanime/alembic/versions/**(B4/B5 增量:release_record
  加 status/picked_at/finished_at;episode 状态枚举加 FLAGGED)
- autoanime/cli.py(subscribe/rerun 子命令增量)
- autoanime/pipeline/orchestrator.py(仅 expected 上下文接线增量,细则 §6.1)
- docker-compose.yml、docker/**、tests/**

实现内容:
1. RSS 拉取(feedparser==6.0.*,2026-07 仍活跃发布):Mikan/自配源 → 新条目 → 三级识别管线;
   feedparser 同步库一律 asyncio.to_thread 包裹(B6,否则阻塞事件循环)。
   条目语义参照 FlexGet accept/reject/seen(去重),订阅模式 RSS 携带 bangumi subject_id 直接
   GET 详情零模糊搜索(ARCHITECTURE 5.6)。
   **Mikan 实操坑(2026-09-06 联网核实,来源 AutoBangumi 官方文档 + V2EX)**:
   - 主站 mikanani.me 部分地区被墙:RSS base_url 必须可配(备用域名 mikanime.tv),
     拉取器走用户代理环境(复用 HTTPS_PROXY);网络失败重试后跳过本轮,不 crash 不告警风暴
   - RSS URL 带 ?token=xxx:token 是密钥,SecretStr,不进 git/日志/报告
   - 最佳实践「每番只订阅一个字幕组」:写进订阅页 UI 提示文案与 docs/DEPLOY.md
   - Mikan 不支持 OVA/剧场版订阅:这两类走散装导入路径(L1 管线已有 segment=MOVIE/SEASON_PACK)
   - 轮询间隔可配,默认 30min,抖动 ±10%(整点齐射打源站,也拉不齐分布式 flock)
2. 订阅调度(APScheduler **3.x 线**,用 AsyncIOScheduler;4.0 是重写版非默认主线,勿用):
   订阅时预生成全季 Episode 行(含 air_date,数据来自 PR6 参考源;air_date 判定一律用 JST,
   展示层转本地时区,D20);
   AIRING 每周轮询 + 缺集 diff(期望集数 vs 非 MISSING)→ 缺口报告 + 通知(D15:v1 无主动搜索,
   回补靠后续 RSS 新条目自然命中,去重后走订阅快路径);COLLECTED 降频每月(仅洗版机会检查);
   状态全部落 schedule_state,重启恢复(ARCHITECTURE §2)。
3. 下载网关(qbittorrent-api==2026.*,calver 活跃包,持续跟 qBittorrent 5.x):添加种子/查询进度/
   下载完成检测用轮询比对 state(qBittorrent 无 webhook,qbittorrent-api 同步库走 to_thread/B6),
   按 torrent_hash 幂等去重,启动时补扫「下载完成但未归档」悬挂任务(B4:按 release_record.status 判断);完成回调 → organize(细则 §6.1);失败重试 ≤2;aria2 实现接口 + fake 测试不实测(拍板 #5)。
4. 洗版引擎(organize/upgrade.py,借鉴 MoviePilot transfer 职责切分 + Sonarr 评分判定):
   - 评分公式按 ARCHITECTURE §3 原文实现(分辨率4/来源3/编码2/字幕组2/做种封顶2),纯函数 + 参数化单测;
     RSS 场景 seeders/size 未知 → 0 分参与、不剔除(D15 降级契约)
   - 触发:新候选 score ≥ 现有 + upgrade_threshold(默认 2,可配);单集上限 upgraded_count ≤ 2
   - 安全方案(硬需求):下载到临时目录 → 校验完整性 → **hardlink 优先**:若源与目标同文件系统,
     下载原件保留做种,归档侧硬链接后原子改名替换;跨盘/不支持时**默认降级 copy**(D9 拍板;
     单文件 >20GB 跳过并记 audit;strict=永不 copy 可配 upgrade_copy_policy);
     失败回滚保留旧文件,decision=rejected 落 release_record;
     旧种处理(D21):完全不动下载器,做种原件保留,仅 upgraded_count + audit 记录
   - mover/rollback:归档/撤销走 audit reverse instruction(5.4),与 E2 的 rollback 端点共用
5. 通知(拍板 #3):providers/notify.py 实现 Notifier Protocol 的 webhook + telegram 最小版,
   注册 Registry;事件(新集归档/缺集回补/洗版完成/待确认积压告警)→ 可配置订阅;密钥 SecretStr+env。
6. docker-compose:backend(uv 容器,挂载媒体目录与数据卷)+ frontend(build 产物 nginx 托管)+
   一份 .env.example(不含真实密钥);compose config 校验通过;README 部署段落另行任务,此处只写
   docs/DEPLOY.md 草稿(DEPLOY.md 含:每番只订一个字幕组提示、「勿暴露公网」安全提示)。
7. 真实闭环实测(qbittorrent 真连 + Mikan 真源)属 L2 外发,单独列在报告末尾「待用户当次确认执行」,
   不自动跑。
8. 订阅快路径与错配恢复(§6.1/D13/D14):expected 传递 + 对齐校验 + 错配 A/B/C 三分支 +
   回补预算;A/B/C 决策表参数化单测钉死。
9. 并发幂等:CLI 手动触发与调度器共用 store 入口;torrent_hash 唯一约束兜底;非法状态机转移
   直接拒绝;调度器进程内单例,防重复下载/重复归档。
10. 归档命名(D17):Sonarr 兼容模板 {title_cn}/Season {SS}/{title_cn} - S{SS}E{EE}.{quality};
    标题语言 title_cn → romaji 回退,Settings 可配;剧场版/OVA segment 按 media_type 分支。
11. 字幕跟随(D18):mover 归档视频时,同包同名字幕(.ass/.srt)跟随改名(.zh 等语言后缀保留)
    一起移动;不做字幕站下载、不提取内封字幕。

验收:pytest/ruff/pyright 全绿;调度器用注入 clock 的单测验证降频与缺集 diff;
     洗版决策表(评分×阈值×上限×回滚×降级)参数化单测钉死;快路径对齐与错配 A/B/C 决策表单测钉死;
     git status 干净;推送。

最终报告:分支、commit、订阅状态机图、订阅快路径命中率/错配统计、洗版决策矩阵、降级策略表、
     docker 冒烟输出、待实测清单、**D10 验收剧本自检表**(四条逐项打勾或说明未达原因,供主会话转正决策)。
```

## 6.1 订阅快路径与错配恢复(E4 细则,2026-09-06 拷问补充;对应拍板 D13/D14)

**大流程定性(拷问结论,写死)**:订阅→下载→整理的长流程 = SQLite 状态机 + APScheduler + 事件总线,
**不用 LangGraph**。expected 是数据库里的持久记忆,不是运行时图状态;跨天、跨重启、跨进程,
正是决议 #4 的职责域。

数据流:

```text
RSS 条目 → 匹配 Episode(MISSING) → release_record(episode_id, torrent_hash)  ← expected 权威载体
        → 下载任务
下载完成事件 → 扫描包内文件,逐文件 payload 附 expected(订阅路径)
             → 手动导入路径 expected=None,两路共用同一套解析代码
L1 解析 → 与 expected 对齐校验:
  ├─ 一致(剧名命中 + 季集对上) → HIGH 快路径:跳过 L2 查找、跳过 API 匹配,
  │     命中 episode_id 直接归档;audit 记 subscribed_fast_path
  ├─ 同番但集数不同/双集/SP → 确定性规则处理(拆条/特典规则),免 LLM
  └─ 冲突(指向另一部番/解析失败) → 文件名优先且降档进仲裁,expected 作为证据之一;
        标记该 release 疑似错标
```

错配恢复三分支(整理时发现 expected 与文件不符,先隔离 + rejected 落库,再诊断):

- **A 改挂优先**:文件解析出有效归属且内容正确、仅匹配错 → 改挂 release_record 到正确
  episode_id,重新归档命名,原 episode 保持 MISSING 等 RSS;**零重下**。
- **B 人工**:两边都救不动(解析失败/陌生番/证据矛盾) → 隔离目录 + pending_queue,附完整证据链。
- **C 回补重下**:文件不可救 → 隔离待清理;episode 保持/回 MISSING → 立即触发回补搜索
  (复用缺集检测路径,不等下轮轮询);rejected 候选与 torrent_hash 排除出评分池;
  同字幕组+同发布模式连续错标进 bypass_list/降信任分;**单集自动回补预算默认 2 次,超限转人工**
  (防「错标源霸榜→下载→再错标」死循环烧流量)。

实现落点:E4 allowed list 增 pipeline/orchestrator.py 接线;对齐校验与 A/B/C 决策纯函数化,
决策表进参数化单测。

## 7. 主会话合并纪律(每个 E 任务后)

1. 核对 diff 越界(只含允许清单)→ 合并进 v2 → 全量联动(pytest/ruff/pyright)→ push。
2. E2 合并后先审端点清单与前端需求是否对齐,再开 E3;E3 可与 E4 后半并行,但合并串行。
3. 任何「契约不可行」报告 → 停下 renegotiate,不静默放行。
4. E4 报告末尾的真实闭环实测(qBittorrent/Mikan/通知外发)→ 用户当次确认后另行执行(L2)。

## 8. 第一版明确不做(写进各任务提示词的反范围)

多用户/认证体系、插件市场、i18n 新语言、移动端 bottom nav、LangGraph、aria2 真实实测、
自动更新与签名校验、Redis/Postgres、K8s、性能调优(仅保证 P50 可测)、旧仓库清理(AutoAnimeMv.py 等,另行小任务)。

## 9. 第一版之后(backlog,不进本计划)

**v2 转正包(D8,第一版验收通过后执行)**:LICENSE(MIT)落库、main 归档、README 指向 v2、
README + 指标对比表 + demo GIF、发布公告(攒真实用户起点)。

指标面板告警规则、洗版 copy_policy 细化、TG 交互式确认、L3 小→大模型级联、规则化发现(Discovery)、
ans 索引站接入、AnimeAgent 仓库归档。

## 10. 计划审核记录(2026-09-06,目标:第一版完整方案)

> 2026-09-06 追记:PR7(参考源归一化)已拍板先行于 E1 执行;E1 基线相应更新为 PR5+PR6+PR7;
> PR7 的前置消歧插入点在 L2 miss → L3 之间,与本计划 §6.1 订阅快路径(L1 对齐,先于 L2)
> 与 E1 合批(识别后)无阶段冲突,仅需 E1 指标补 canonical/alias 命中口径(已改)。

**总评**:骨架成立——E1→E4 串行门禁、拍板冻结、离线优先测试、D10 验收剧本齐备;主要缺口是
「订阅上下文传递/错配恢复」未进 E4(本次已补:D13/D14 + §6.1 + E4 增项 8/9/A4 语义)。
其余风险与处置:

| # | 发现 | 影响 | 处置 |
|---|------|------|------|
| A1 | expected 传递/错配恢复原计划缺失 | 订阅场景退化为从零识别,错标源反复烧流量 | 已补 D13/D14/§6.1 + E4 增项(本次修订) |
| A2 | D10 验收依赖「在放番剧」,换季执行会卡住 | 验收剧本与放送时效耦合 | 执行细则:非在放季时用本地静态 RSS fixture 源(自建 HTTP 服务 + 真下载器真闭环)走 D10;真 Mikan 实测仍按 L2 用户确认执行 |
| A3 | E4 体量最大(调度+网关+洗版+错配+通知+compose+闭环),单次长跑塌陷风险最高 | E4 质量与工期 | 允许拆两次合并:E4a(RSS 拉取+调度+缺集回补+网关)/ E4b(洗版+错配恢复+通知+compose);契约与验收线不变,拆分在 E4 报告声明 |
| A4 | qBittorrent 无 webhook,完成事件靠轮询 | 事件丢失 = 整理悬挂 | 已写进 E4:轮询比对 state + hash 幂等 + 启动补扫悬挂任务 |
| A5 | 快路径/错配收益无指标口径 | 订阅优化不可见 | E4 最终报告增加 subscribed_fast_path 命中率与错配率统计(E1 离线快照无 expected,不计) |
| A6 | 媒体库(Jellyfin/Plex)联动未排入 v1 | 演示完整度 | 维持不做,providers/media 占位即第一版边界;进 backlog |
| A7 | CLI 手动触发与常驻调度并发写库 | 重复处理风险 | E4 增项 9 已覆盖(hash 唯一约束 + 状态机守卫 + 调度单例) |

**审核结论**:按上述处置修订后,本计划覆盖第一版完整方案
(订阅 → 下载 → 识别 → 归档 → 洗版 → 错配恢复 → 通知 → 部署 → D10 验收),可执行。

### 10.1 第二轮架构审核(2026-09-06,ARCHITECTURE.md 全文对照 + v2 现有代码核对)

**总评**:排除下列问题后,「状态机 + 调度器 + 事件总线 + 确定性边界」的架构方向无根本性错误;
P0-1 是唯一触及卖点语义的问题,需要拍板;其余为落地契约补全。

**P0(2026-09-06 已拍板:B1 → D15、B2 → D16,按建议处置执行,已修订进任务提示词)**:

| # | 发现 | 影响 | 建议处置 |
|---|------|------|------|
| B1 | **回补/洗版候选来源断裂**:RSS 是推送流只发新条目,E4 无任何搜索 provider;历史缺集等不到种子,COLLECTED 每月检查永远无新候选。连带:评分公式的 seeders/size 在 Mikan RSS 中拿不到 | 「回补搜索」「洗版发现更优旧种」两个卖点在 v1 机制上跑不通 | v1 诚实降级:缺集检测产出缺口报告+通知,回补 = 等 RSS 自然命中(去重后走快路径);评分输入契约「seeders/size 未知 → 0 分参与,不剔除」;主动搜索 indexer(Nyaa API 等)进 backlog,作为 v2 第二下载飞轮 |
| B2 | **进程模型未定义**:FastAPI / APScheduler / CLI 是否同进程未写死;内存事件总线跨进程不通(CLI 触发的事件 SSE 收不到);多进程引发 alembic 迁移竞争与 SQLite 写并发 | 事件可见性、迁移安全、并发正确性全部悬空 | v1 钉死单 backend 进程:compose 一个容器,lifespan 同时起 FastAPI + AsyncIOScheduler;SSE 重放必须基于落库数据(audit/daily_metrics);CLI 只做库操作不承载事件;写进公共提示词铁律 |

**P1(需补进对应任务契约)**:

| # | 发现 | 影响 | 建议处置 |
|---|------|------|------|
| B3 | rss_sources 表不在 11 张表清单;Mikan 订阅粒度是季度 subject,RSS 应挂 season 而非 series(多季番剧 = 多条 RSS);E2/E4 允许清单无 alembic/versions/** | E2 的 /api/rss_sources 无处落库,多季订阅建模错误 | E2 新增 rss_sources(id, url, token SecretStr, season_id, enabled, last_polled_at)+ alembic revision;允许清单补 alembic/versions/** |
| B4 | 下载任务生命周期缺状态:release_record 有 torrent_hash 无 status(候选/已选/下载中/完成/失败) | 「启动补扫悬挂任务」无法判断哪些 hash 已处理 | release_record 增 status + picked_at/finished_at(v1 不另建 download_task 表) |
| B5 | 文件↔库对账只有单向:仅覆盖「下载完成未归档」;反向(ORGANIZED 但文件被挪走/删除)无机制,UI 静默说谎 | 归档可信度受损 | v1 最小版:启动对账 stat ORGANIZED 文件存在性,不一致标 FLAGGED + 通知,不自动修;进 E4b |

**P2(写进对应任务提示词即可)**:

- B6:feedparser / qbittorrent-api 均为同步库,在 AsyncIOScheduler / asyncio 中必须 asyncio.to_thread 包裹,否则阻塞事件循环(SSE 心跳停摆)。
- B7:SSE + 认证:EventSource 无法自定义 header,/api/events 需支持 token 经 query param 传递(默认空 token 时无感)。
- B8:hardlink 生效前提 = 下载目录与媒体库同文件系统;compose 必须同卷挂载设计,DEPLOY.md 明确「两个挂载点须同一盘/同一卷」,否则 D9 的 hardlink 优先全量降级 copy。

**执行纪律**:B1/B2 由用户拍板后,处置内容修订进 E4 任务提示词与公共提示词;B3-B5 在 E2/E4 开工时按建议落实;B6-B8 直接写进任务提示词。
