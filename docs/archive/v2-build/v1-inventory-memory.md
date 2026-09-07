> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# 仓库盘点记忆(2026-09-05)

> 由主对话只读盘点两个仓库后写入,供后续 agent 使用,无需重读原始仓库。
> 仓库位置:工作区内 `./AutoAnime/`(已对齐 origin/main 6c29b09,本地旧提交备份在 `local-backup` 分支)与 `./AnimeAgent/`(已是最新)。

## 1. AutoAnime(主仓,更新后远超 PLAN 描述)

不再是单文件项目,已有结构化 `autoanime/` 包:

| 模块 | 内容 | 对照 v2 架构 |
|---|---|---|
| `identification/` | openai_identify / local_fallback / title_chain / episode_rules | 三级管线雏形;**当前是 LLM-primary + 本地兜底**,与 v2 的 local-first 相反(`OPENAI_IDENTIFY_ALL` 开关) |
| `apis/openai_client.py` | 多模型槽位轮换(URL/KEY/MODEL 组合)、槽位状态持久化、熔断器(连续 401/403/429 跳过 LLM)、配额判断、JSON 提取 | **v2 LLM 层的最佳基底,直接复用** |
| `cache/` | persistent / canonical / manual_whitelist / trust / audit / migrate / schema_v2 | **记忆飞轮已存在**:白名单、信任分、审计 |
| `pipeline/` | main / mode / operation_log / rollback | 操作日志 + 回滚已有 |
| `tests/` | 16 个 pytest 文件约 140KB | 远超 PLAN 所称"268 行" |

其他要点:
- 命名风格老旧:`Auxiliary_*` / `Processing_*` 前缀 + 模块级全局状态(`from .. import state`)+ 同步 requests
- 本地旧提交:`f9edbee`(中文源+识别失败跳过)已随 `local-backup` 分支保留;未提交的本机代理/硬链接配置改动也已并入该分支
- **M1 真正工作 = 反向改造**:识别主路径从 LLM-primary 翻转为 local-first(anitopy L1 → 记忆 L2 → LLM 仅兜底),而非从零搭管线

## 2. AnimeAgent

### LangGraph(比 v2 预想更重)
- episode 图 14 节点,`EpisodeAgentState` TypedDict 约 30 字段;另有 season_batch 图
- `BaseAgentNode` 是"LLM 思考循环 + 动作空间"模式(每节点最多 3 次 LLM 调用,带 bash/filesystem 工具)
- 与 v2 第 9.1 节"确定性决策"纪律冲突:fetch_rss / poll_download / organize_files 等节点本质是确定性逻辑
- **建议:保留图骨架做编排,确定性节点改为普通函数节点;LangGraph 只留"解析→校验→重试→待确认"真链**

### LLM 层(弱,丢弃)
- `tools/llm_tool.py`:LangChain ChatOpenAI 单模型单 key、无轮换、temperature 0.7、JSON 靠正则硬提取
- `config.py`:单 openai key/model
- **结论:以 AutoAnime 的 openai_client 为基底,AnimeAgent llm_tool 直接丢弃**

### 其他资产
- `memory/models.py`:SQLAlchemy 模型(Subscription/Episode/Batch/RSSSource/ErrorLog/ChatMessage 等),字段比 v2 schema 粗(Subscription 混合了 Series+Season 概念)
- `services/metadata_resolver.py`:Bangumi→AniList→TMDB 链 + 中文季数正则(第二季/2期/Season 2/罗马数字),可参考
- `routers/` 11 个 + `services/` 20+:FastAPI 后端骨架完整,SSE 未实现(前端轮询)
- 越界能力(conversational agent、bash_tool)按 v2 边界处置:丢弃

### 前端(React,已成事实)
- React 19 + Tailwind 4 + 自建 UI 组件库(Badge/Button/Card/Modal/Switch 等 13 个)+ i18n + 8 页面(Dashboard/Episodes/Subscriptions/RSSSources/Discovery/Chat/Logs/Settings)
- **决议倾向:保 React,放弃 PLAN 中的 Vue3+Naive UI;管线可视化用 @xyflow/react 替代 vue-flow;AutoBangumi 的 Soft Ink 设计纪律照搬(CSS token 层实现,与框架无关)**

## 3. 已确认的架构决议
- 决议 #1:单用户本地工具,明确不做多用户 Web 服务(已写入 ARCHITECTURE.md)
- 决议 #2:前端保 React(React19+Tailwind4+@xyflow/react),放弃 Vue3+Naive UI(已写入)
- 决议 #3:新主干+资产移植,不做渐进改造;旧识别管线零移植(用户证实其长期不稳定、靠打补丁维持),老 pytest/真实文件名测试集作为新管线的 TDD 验收标准(已写入)

## 4. 待决策
- (前端栈已定,见决议 #2)
- M1 边界:按新主干重定义中(讨论中)


## 5. 真实命名语料

- 全量快照:
otes/samples/z_downloads_snapshot.txt(Z:\下载 182 目录 + 2424 文件,2026-09-05)
- 方言分析与管线推论:
otes/samples/命名方言分析.md(六类方言 + 档位边界设计依据)
- 新仓库建立后移入 tests/fixtures/ 作为 TDD 语料

- L2 两级 pattern key 已数据验证并写入 ARCHITECTURE.md 5.1b(458 key 覆盖 94.3%,二级仅+1.8%);探针脚本 scripts/pattern_probe.py


## 6. 侧线专题结论(2026-09-05)

识别/记忆/自动化专题拷问完成,全部决议已汇总写入 PLAN.md(0-3 节):模块化原则、三档边界、L2 两级 key 实测数据、自动仲裁管线、L3 机会主义合批、人工介入率<2% 等。详情以 PLAN.md 为准。
