> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# AutoAnime v2 — M1 首个 PR 骨架施工 Plan(终版 v1.0)

> 提交主对话审核。依据:架构决议 #1-#4 + 识别专题结论 A-G(已合并进 ARCHITECTURE.md 5.0a-5.7/9.3b)+ 两轮骨架审核修订 + 用户确认项(旧 autoanime/ 包直接删、按功能切片实现、每件三层测试)。
> 本文档自包含:审阅者无需其他上下文即可判断是否批准开工。

---

## 1. 背景与已确认前提

| # | 决议 | 内容 |
|---|---|---|
| 1 | 运行形态 | 单用户本地工具(NAS/Docker 自部署),不做多用户 |
| 2 | 前端栈 | React 19 + Tailwind 4 + @xyflow/react(复用 AnimeAgent 资产) |
| 3 | 代码策略 | 新主干 + 资产移植;旧识别管线零移植;真实语料+老 pytest 作 TDD 验收 |
| 4 | 存储 | 所有状态进 SQLite,废除文件缓存;WAL + busy_timeout |
| 5 | 模块化 | 核心圈不可插拔(状态机/评分/管线顺序/表结构),外圈接口化;**registry 只服务 providers/gateway,管线不注册** |
| 6 | 落点 | AutoAnime 同仓库开 `v2` 分支;**旧 `autoanime/` 包直接删**(git 可找回),旧单文件移入 `legacy/` |

## 2. 分支与清理(PR1 第一步)

- `git checkout -b v2`(基于 origin/main 6c29b09)
- 删除旧 `autoanime/` 包全部内容;`AutoAnimeMv.py`、`AutoAnimeMv2.py`、`config.ini*`、`test_input/` 移入 `legacy/`
- `local-backup` 分支保留不动
- main 不动,README 后续指 v2

## 3. 目录结构(终版)

```
autoanime/
├── core/                      # 核心圈(不可插拔)
│   ├── models.py              # 10 张表 SQLAlchemy 定义(§5)
│   ├── enums.py               # EpisodeState/SeasonState/MediaType/Confidence/Segment/Decision
│   ├── interfaces.py          # Protocol + 注册表(§4)
│   └── events.py              # EventCategory 枚举 + EventBus Protocol 占位(不实现总线)
├── pipeline/                  # 三级管线:固定编排,不进注册表
│   ├── orchestrator.py        # L1→L2→L3→仲裁,顺序写死(ARCHITECTURE 5.0b/5.7)
│   ├── l1_local.py  l2_memory.py  l3_llm.py  arbiter.py
├── organize/                  # 归档引擎(确定性核心,非插件)
│   ├── naming.py              # Jellyfin/Plex 命名模板
│   ├── mover.py               # 原子移动/改名 + 硬链接(旧 USELINK 语义)
│   ├── upgrade.py             # 洗版:评分/阈值/上限/回滚
│   └── rollback.py            # 按 audit_log.operation_id 批次撤销
├── providers/                 # 外部能力:bangumi.py / tmdb.py(MetadataProvider)
├── gateway/                   # 下载器:qbittorrent.py / aria2.py(Downloader)
├── memory/store.py            # Storage Protocol 的 SQLite async 实现
├── scheduler/scheduler.py     # M4 填实现
├── api/  web/                 # M3 占位
├── cli.py                     # run / import / queue / confirm / report
├── config.py                  # pydantic-settings:TOML + env(secret 仅 env)
├── alembic/                   # day 1 baseline
legacy/                        # 旧单文件与配置
tests/
├── fixtures/samples/          # dir→files 结构化语料,按方言 A-G 分组(第二个 PR 迁入)
├── unit/  integration/  blackbox/
```

模块对应:providers/gateway = 外圈插件(registry);pipeline/organize = 核心圈固定知识;ARCHITECTURE 第 6 节模块划分同步此命名(providers+gateway 替代含糊的 providers/media)。

## 4. 接口契约(interfaces.py)

数据契约(dataclass,自包含,不暴露 DB/配置):
- `RawName`(原始名 + 所在文件夹 + 上级路径)、`ParseContext`(已知 series/放送进度/字幕组偏好,可空)、`ParseResult`(title/season/episode/segment/fansub/level/confidence/missing_fields/evidence)

五个 Protocol(runtime_checkable):
```
Recognizer:       parse(RawName, ParseContext) -> ParseResult | None
MetadataProvider: search(query) / get_detail(id) / get_season_episodes(id, n)   # 全 async
Downloader:       add(TorrentSource) -> hash / status(hash)                     # 全 async
Notifier:         send(Event)                                                   # 全 async
Storage:          记忆/状态读写(SQLite 实现,方法随 schema 定签名)
```

注册表:装饰器 `@register(Protocol, name)` + 按配置装配 + 优雅降级(无 LLM key → l3 不注册,LOW 进人工队列)。**范围钉死:仅 MetadataProvider、Downloader、Notifier 可注册;Recognizer、Storage、EventBus 是类型契约,不进 registry;pipeline 顺序与 organize 是宪法**。registry 抽象往管线上长 = 设计漂移。

## 5. 数据表(10 张,PR1 全建;alembic baseline)

| 表 | 字段要点 |
|---|---|
| `series` | title_cn/jp/romaji, **media_type: TV\|MOVIE\|OVA\|SPECIAL**(MOVIE 不建 season 行,episode 单行), tmdb_id, bangumi_id, fansub_pref, quality_pref, status |
| `season` | series_id FK, number, status(UPCOMING/AIRING/ENDED/COLLECTED) |
| `episode` | series_id FK(非空), season_id FK(可空;MOVIE/SPECIAL 无季行时为空), number, state(MISSING/DOWNLOADING/DOWNLOADED/ORGANIZED/UPGRADED/IGNORED), upgraded_count, quality_score, air_date, file_path, file_hash |
| `release_record` | **season_id/episode_id 双可空 FK + CHECK(二选一非空)**, torrent_hash UNIQUE, fansub, size, seeders, score, decision(accepted/rejected/pending), reason, source_url |
| `parse_memory` | key_level(1/2), **UNIQUE(key_level, key_hash)**, fansub_norm(可空), title_shape, result(JSON), source(manual/llm_confirmed/llm_auto), hit_count, corrected_count, last_hit_at, status(active/pending/deprecated) |
| `alias` | series_id FK, alias_norm, source |
| `bypass_list` | pattern_hash, reason, created_at |
| `pending_queue` | raw_name, context(JSON), stage, reason, **status(pending/resolved/skipped), resolution, resolved_by(auto/manual)**, created_at, resolved_at |
| `audit_log` | **operation_id(批次号,回滚按批原子)**, entity, entity_id, action, instruction(JSON), reverse(JSON), actor(auto/manual) |
| `parse_events` | date, raw_name_hash, level(1/2/3), llm_called, latency_ms, outcome, confidence —— M2 指标报告数据源 |

- 决策类字段用枚举不用 JSON;记忆"答案"存 JSON(结构会进化,免迁移)
- **推迟**:`schedule_state`(M4)、`daily_metrics` 聚合视图(M2,由 parse_events 派生)——接受届时一次 alembic 迁移
- SQLite pragma:busy_timeout=5000,连接层统一设置;WAL 仅用于文件库,内存库不测 WAL

## 6. 工具链(终版)

- Python 3.12 + uv;**SQLAlchemy 2.0 async + aiosqlite day 1**(AnimeAgent 已同构,openai_client 移植零摩擦);CLI 用 asyncio.run 包一层
- pydantic-settings 校验 TOML(tomllib 只读不验);secret 只从 env 进
- dev deps:pytest + pytest-asyncio + pytest-cov + ruff + pyright;alembic day 1
- ruff + pyright 从 PR1 起就是验收门,不事后补

## 7. 实现切片与三层测试(用户要求:最小 MVP,每件必测)

| PR | 交付 | 白盒(单元) | 联动(集成) | 黑盒(CLI 端到端) |
|---|---|---|---|---|
| **PR1(本 plan)** | 骨架+Protocol+10 表+alembic baseline+CLI 占位 | enums 状态机转移表、schema 约束(CHECK/UNIQUE) | alembic up/down、store CRUD(async) | `--help`、create_all |
| PR2 | 语料 fixture(结构化 dir→files,方言 A-G)+ 测试基建 | fixture 加载器 | — | pytest 收集全绿 |
| PR3 | L1 本地解析 | 每方言一组测试 + 锚点切分边界值 + 档位判定表驱动 | — | 单文件识别 |
| PR4 | L2 记忆 | 归一化规则、两级 key、信任分(分支覆盖) | store+pipeline L2 | confirm 后二次命中 |
| PR5 | 编排器 | 档位路由矩阵 | L1→L2 全链 fixture 子集 | run 批量 dry-run |
| PR6 | L3+缓存 | mock LLM 校验/重试 | 槽位轮换+熔断联动 | — |
| PR7 | queue/confirm | pending 状态机 | 确认三件事(记忆/别名/顶替) | CLI 全流程 |
| PR8 | 指标 | parse_events 聚合 | — | report 产出报告 |

## 8. PR1 验收门(机器可验)

- [ ] `uv sync` 一次通过;`autoanime --help` 可用(run/import/queue/confirm/report 占位)
- [ ] `import autoanime` 无错;Protocol runtime_checkable 可断言
- [ ] pytest 收集成功;alembic baseline upgrade/downgrade 均可执行
- [ ] 10 表 create_all 进内存 SQLite;CHECK/UNIQUE 约束生效测试通过
- [ ] `ruff check` + `pyright` 零错误
- [ ] grep 无模块级全局状态、无 `Auxiliary_*` 命名

## 9. 风险与边界

- `organize/` 在 PR1 只有空壳签名,实现排 PR5 后(naming/mover 单元测试依赖 fixture 落地)
- registry 收敛是纪律不是机制:review 时发现管线进 registry 即打回
- schedule_state 推迟的代价 = M4 一次迁移;parse_events 字段若 M2 指标需求变化,同样走 alembic,不手写 SQL 改库
- 旧包删除后若发现遗漏资产,git 历史可查;移植只认 openai_client/cache 思想/语料/前端四项清单

---

**请主对话审核确认**:批准后执行顺序 = §2 清理 → 按 §3-§6 落代码 → 跑 §8 验收门 → 提交 PR1 清单。
