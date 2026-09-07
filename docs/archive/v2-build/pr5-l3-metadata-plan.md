> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# PR5（L3 元数据层 + Arbiter）任务拆分与执行 Plan

> 生成日期：2026-09-05。基线：v2 @ `5360063`（PR1 骨架 + PR2 语料 + PR3 L1 + PR4 L2 记忆层 + PR4.1 评审修复均已合并推送）。
> 用法：与 PR4 相同——按「推荐执行顺序」开侧边聊天，每个侧边聊天先粘贴「公共提示词」，再粘贴对应任务的完整提示词。
> 注：占位文件 `l3_llm.py` 的 docstring 写的是 "PR6"，以本 Plan 为准：本 PR 即 L3 层。

---

## 0. 总览

拆成 **6 个子任务**；真正同时并行的是中间 3 个（store 侧 / LLM provider 侧 / arbiter 侧，模块路径互斥）。

| 阶段 | 任务 | 分支 | 并行性 |
|------|------|------|--------|
| 1 | T1：L3 公共契约 / Protocol / 纯函数 / config 字段 | task/pr5-l3-core | 先行，串行 |
| 2 | T2：store 层 DB 级查询 + llm_cache 表 | task/pr5-store-index | ✅ 三个并行之一 |
| 2 | T3：LLM provider 实现（l3_llm） | task/pr5-l3-llm | ✅ 三个并行之一 |
| 2 | T4：arbiter 仲裁 | task/pr5-arbiter | ✅ 三个并行之一 |
| 3 | T5：orchestrator L2→L3→arbiter 串接 + CLI 全链路 | task/pr5-integration | 串行 |
| 4 | T6：真实快照离线验证 + 真实 LLM 小样本实测 | task/pr5-real-corpus | 最后 |

**T1 不要省**：L3 的 Protocol 签名、LLM 响应解析 schema、仲裁决策表、成本控制常量是 T2/T3/T4/T5 共同依赖的契约。

## 1. 契约决策（标 ⚠️ 的需用户确认后才启动；未标的按 PR 既有决议延续）

1. **路由延续 PR4**：L1 HIGH → archive；MEDIUM → L2（命中融合 → memory 归档；未命中 → 进 L3）；LOW 与 L1-None → L3。HIGH 永不进 L2/L3。
2. **arbiter 是字段级仲裁器**：按「字段 × 来源证据」逐字段仲裁（不是整体结果二选一）。证据优先级：`name > folder > context > memory > llm`（未来加 `bangumi`/`tmdb`，插在 memory 与 llm 之间——用户确认过的 memory 高于外部猜测，API 权威源又高于 LLM）。
3. **arbiter 决策表**（T1 定签名、T4 实现 + 参数化单测钉死）：
   - R1 静态优先级：同字段多来源，按上表优先级取值；
   - R2 只补不覆盖：高优先级已有值，低优先级不得覆盖；值不同时冲突写 audit_log（不丢弃信息，供后续「频繁否决→收紧」策略用）；
   - R3 缺失补齐：高优先级缺失时依次由低优先级补，evidence 记实际来源；
   - R4 一致性升档：L1/L2/L3 对 title+season 结论一致 → 档位 +1（MEDIUM→HIGH）；
   - R5 验证升档：L1-Null 且仅 L3 来源 → 基础 MEDIUM；若 L3 的 title 与 L1 草稿 title_shape 归一化一致（LLM 只是确认而非凭空起名）或通过参考源（MetadataReference）验证 → 升 HIGH；
   - R6 L2 多季消歧：memory 的 `seasons` 列表多值歧义时，L3/参考源的 season 结论可消歧回填（经 R5 旁证规则）；
   - R7 L3 不可用/解析失败 → 保持 L1/L2 原结果（优雅降级）；
   - R8 所有否决/冲突结论进 audit（operation_id 批次），不进 ParseResult。
   - **L1/L2 是否进 arbiter**：L1 内部七方言合并归 L1 自己的 confidence/context 机制；L2 内联融合（apply_memory_hit 只补不覆盖）是 R2+R3 的特例、PR5 不重构——orchestrator 先完成 L2 融合，再把 L1 草稿、L1+L2 融合结果、L3 草稿三方交给 arbiter。arbiter 接口按多源候选设计，未来统一时接口不变。
4. **LLM 输出不可信**：严格 schema 解析（字段白名单 title/season/episode/segment/fansub），非法输出带纠正提示重试 1 次（把解析错误喂回 prompt），再失败才丢弃并计数；prompt 由 T1 纯函数构建。
5. **成本控制（宽松版）**：每个 raw_name 最多一次 LLM 调用（重试除外）；`llm_cache` 按 pattern_hash 缓存（跨进程复用，重试成功结果同样入 cache）；超时 10s；网络/超时重试 ≤2；**预算默认不限**（`llm_budget=None`，可配置，超限只记 audit 不阻断）；无全局 QPS 限速（QPS 限速保留给 PR6 的 Bangumi/TMDB 参考源）。
6. **测试离线**：单测绝不联网；T3 用 fake transport + 录制响应 fixture；真实联网只在 T6 手动小样本实测。
7. **密钥**：`AUTOANIME_LLM_API_KEY` 环境变量（Settings 已有 `llm_api_key: SecretStr`）；不进 git、不进日志、不进回复（脱敏规则见 secrets.md）。
8. **registry 边界**：LLM provider 等外部能力注册进 `Registry`（`interfaces.py` 已有）；L3 的 prompt/解析/仲裁属固定 pipeline 组件，不进 registry。
9. **API 元数据源（Bangumi/TMDB）是权威参考源，不是识别源**：
   - **调用点位唯一**：arbiter 阶段、且只对档位低于 HIGH 的仲裁候选查（L1 HIGH 与 L2 融合升 HIGH 的结果不查；L3 关闭时 arbiter 仍可只凭参考源做 R5 升档——API 兜底不依赖 LLM 开关）。L1 解析与 L2 查询/学习永远不触网
   - **查询粒度是剧目级**（title_shape），结果进 reference_cache（PR6 落表）：一部剧只在首次仲裁时打一次 API，调用量 ≈ 剧目数而非文件数
   - **插件化可调**：每个 provider 实现同一 `MetadataReference` Protocol 并注册进 Registry；config 的 `reference.order = ["bangumi", "tmdb"]` 列表顺序即优先级（链序第一个命中即用），删项即停用、换序即调优先级，代码零改动；各 provider 自带 qps 配置（Bangumi 有限流）
   - **域优先策略默认**：`order` 默认 `["bangumi", "tmdb"]`（Bangumi 动画权威 + 中文名优先，TMDB 回退；冲突按链序 Bangumi 胜并记录）
   - **PR5 只做机制**：T1 定义 Protocol + ReferenceChain 组合器 + config 字段（reference_enabled / reference_order / 各 provider qps 占位），测试全 fake；**真实 Bangumi/TMDB 适配器 + reference_cache 表放 PR6**
   - 参考源介入点汇总：仲裁验证升档（R5）、L2 多季消歧（R6）、（PR6+）organize 命名规范化——HIGH 结果在改名阶段按剧目查 canonical title、（未来）L3 纠偏

## 2. 公共提示词

每个侧边聊天先粘贴这一段。

```text
你在独立 worktree 中工作，不要切换主仓库分支。

仓库：
C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\AutoAnime

基线分支：
v2，当前 commit 5360063（PR4 L2 记忆层 + PR4.1 评审修复已完成：L1 七方言 → orchestrator 路由 → L2 两级 key 记忆层 → L3/arbiter 占位）

worktree 路径会在任务提示词中给出。

项目规则：
1. AutoAnime v2 正在实现 PR5：L3 元数据层（LLM fallback）+ arbiter 仲裁。
2. 不要实现下载器/gateway、scheduler、web、bangumi/tmdb provider；orchestrator 仅 T5 允许修改，且只允许添加 L2→L3→arbiter 段。
3. 不要修改 autoanime/core/interfaces.py，除非任务明确允许（仅 T1 允许，且只允许新增 L3 相关 Protocol，不改动现有 Protocol）。
4. 不要引入模块级可变全局状态。
5. 网络 API 仅 T3 允许在其 provider 模块内调用；所有单元测试必须离线（fake transport + 录制 fixture）。
6. L1/L2 的语义不变；L3 是增强层，arbiter 是仲裁层，不是替代层。
7. 外部能力（LLM provider）注册进 Registry；L3 的纯函数组件不进 registry。
8. 使用 Python 3.12、uv、pytest、ruff、pyright。
9. 每完成一个逻辑单元就 commit（中文 commit message，说清改了什么与影响范围；禁止添加 Co-Authored-By 或任何 Claude 归属 trailer）。
10. 禁止合并到 v2 或 master，只提交并推送自己的 task/* 分支。推送若 GitHub 直连超时，用环境变量 HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 重试（不要改 git config）。
11. L3 的 prompt 构建 / 响应解析 / 预算与重试 / 仲裁决策必须是纯函数，DB 会话只在 store 层出现，网络调用只在 provider 层出现。
12. 存储是 async SQLAlchemy 2.0 + aiosqlite（autoanime/memory/store.py 的 SqliteStorage）；store.py 的公共方法只做增量添加、不改动现有签名（store.py 仅 T2 允许修改）。
13. 密钥不进 git/日志/回复；llm_api_key 用 SecretStr，日志中一律脱敏。
14. 已拍板契约决策见任务提示词附录，不得自行更改；如发现决策在实现中不可行，停下在最终报告中说明，不要静默偏离。
```

### 统一 L3 契约（附录：随公共提示词一起粘贴）

```text
L3 统一契约：
输入：
- L1 的 ParseResult（或 None）+ ParseContext | None
- RawName（raw release name，供 bypass/cache key）

输出：
- L3 草稿（L3Draft）：字段白名单 title/season/episode/segment/fansub，每字段带 evidence="llm"
- None（未调用/不可用/解析失败，交回 orchestrator 按 L1/L2 原结果路由）

调用边界：
- 每个 raw_name 最多一次 LLM 调用（重试除外）；先查 llm_cache（pattern_hash），未命中才真实调用
- 超时 10s；网络/超时类错误重试 ≤2；schema 无效 → 带纠正提示重试 1 次，再失败放弃
- 预算默认不限（llm_budget=None 可配置）；超限只记 audit 不阻断

LLM 响应解析（T1 纯函数）：
- 严格 schema：JSON 对象，字段白名单 title:str / season:int? / episode:int? / segment:enum / fansub:str?
- 非法输出（非 JSON、越界字段、类型错误）→ 带纠正提示重试 1 次 → 仍失败返回 None 并计数

arbiter 决策表（T1 定签名、T4 实现，逐字段仲裁）：
- 证据优先级：name > folder > context > memory > llm（未来 bangumi/tmdb 插在 memory 与 llm 之间）
- R1 静态优先级取值；R2 只补不覆盖（冲突记 audit）；R3 缺失按序补齐
- R4 一致性升档：title+season 三方一致 → MEDIUM→HIGH
- R5 验证升档：L1-None + 仅 LLM → MEDIUM；LLM title 与 L1 草稿 title_shape 归一化一致，或参考源（MetadataReference）验证一致 → HIGH
- R6 L2 多季消歧：memory seasons 多值歧义 + L3/参考源 season 结论 → 消歧回填
- R7 L3 不可用 → 保持 L1/L2 原结果
- R8 否决/冲突结论进 audit（operation_id 批次），不进 ParseResult
- L2 内联融合不重构：orchestrator 先做 L2 融合，arbiter 收到 L1 草稿、L1+L2 融合结果、L3 草稿三方
```

## 3. 任务提示词

### T1：L3 公共契约与基础设施

```text
任务：T1 L3 公共契约与基础设施

分支：task/pr5-l3-core
前置：无（基于 v2 @ 5360063）

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-l3-core" -b task/pr5-l3-core v2

之后所有操作都在该 worktree 目录内进行（先 cd 过去，再 uv sync --locked 装依赖）。

目标：
建立 T2/T3/T4/T5 都依赖的 L3 契约与纯函数基础设施。

只允许修改：
- autoanime/pipeline/l3/**（新建包）
- autoanime/core/interfaces.py（仅新增 L3 相关 Protocol，如 L3Recognizer / LlmTransport / LlmCacheStore / MetadataReference，不改动现有 Protocol）
- autoanime/config.py（仅新增 llm_enabled / llm_model / llm_base_url / llm_timeout_s / llm_max_retries / llm_budget 字段：llm_budget 默认 None=不限；reference_enabled（默认 true）/ reference_order（默认 ["bangumi", "tmdb"]，列表顺序即优先级）字段；不改现有字段）
- tests/unit/test_l3_infra.py
- tests/unit/test_config.py（增量添加新字段测试）
- tests/fixtures/l3/**（新建）

建议模块：
- schema.py：LLM 响应严格解析（字段白名单、类型校验）与 L3Draft 结构（含 evidence="llm"）
- prompt.py：LLM prompt 构建纯函数（输入 RawName + L1 草稿 + 上下文提示，输出 prompt 文本；含「schema 纠正提示」的重试 prompt 构建纯函数；模板内不含密钥）
- budget.py：超时/重试/预算的判定纯函数与常量（网络/超时类错误重试 ≤2；schema 无效 → 纠正提示重试 1 次；预算默认不限、超限只记不阻断）
- cache_key.py：llm_cache 的 key 规范化（复用 l2.bypass 的 normalize_pattern/pattern_hash）与缓存读写语义定义
- draft.py：L3Draft → ParseResult 的构建纯函数（evidence="llm"；不得覆盖 name/folder 证据字段的约束在此实现）
- reference.py：MetadataReference Protocol 语义、ReferenceFacts 结构（canonical_title / seasons / episode_count / aliases 等）、ReferenceChain 组合器（按 reference_order 从 Registry 解析、链序即优先级、第一个命中即用、可整体关闭）

Protocol（定义在 interfaces.py，签名要精确，T2/T3/T4 直接实现）：
- LlmTransport：async complete(prompt: str, *, model: str, timeout_s: float) -> str（唯一网络出口，T3 实现、测试 fake）
- LlmCacheStore：async get(pattern_hash) -> LlmCache | None；async put(...)（T2 实现、测试 fake）
- MetadataReference：async lookup(title_shape: str) -> ReferenceFacts | None（PR6 真实实现 Bangumi/TMDB；本 PR 定义 Protocol + ReferenceFacts 结构 + fake）。**插件化**：各 provider 注册进 Registry（@registry.register(MetadataReference, "bangumi")）；ReferenceChain 组合器（T1 实现）按 config 的 reference_order 从 Registry 解析成链，链序即优先级、第一个命中即用，链可整体关闭（reference_enabled）
- L3Recognizer：async enhance(raw: RawName, result: ParseResult | None, context, transport, cache_store) -> ParseResult | None（签名以实际设计为准，报告中给出精确版本）

roundtrip fixture schema（定义并写示例，参考 tests/fixtures/memory/ 的既有格式）：
{
  "id": "L3_01_parse_then_enhance",
  "llm_response": "...录制的模型输出文本...",
  "query": { "name": "...", "expected": {...按 ParseResult 契约...} }
}
- 至少覆盖：合法响应、非 JSON（纠正重试后成功）、非 JSON（重试后仍失败）、越界字段、缺字段 五类

实现约束：
- 本任务不实现 DB 读写（T2）、不实现网络调用（T3）、不实现 arbiter 决策（T4 做，但你要在报告里给出 arbiter 决策表的精确函数签名供 T4 实现）
- 不修改 l1/l2/memory/orchestrator/cli 任何代码

验收：
- uv sync --locked
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-l3-core

最终报告：分支名、commit hash、模块结构、public API（含全部 Protocol 精确签名与 arbiter 决策表签名）、fixture schema 示例、config 新字段表、测试结果。
```

### T2：store 层 DB 级查询 + llm_cache 表

```text
任务：T2 store 层 DB 级查询 + llm_cache 表

分支：task/pr5-store-index
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-store-index" -b task/pr5-store-index v2

先 cd 过去，再 uv sync --locked。

目标：
解决 PR4 遗留的性能债务（find_parse_memory/has_bypass 全表 Python 过滤），并为 L3 提供 llm_cache 存储。

只允许修改：
- autoanime/memory/store.py（增量添加查询方法；不改动现有方法签名）
- autoanime/memory/lookup.py、autoanime/memory/learn.py（仅把 Python 侧过滤替换为调用 store 新查询方法，语义不变；不改 T1 契约）
- autoanime/core/models.py（仅新增 LlmCache 模型）
- alembic/versions/0002_llm_cache.py（新 migration，参考 0001_baseline）
- tests/unit/test_store_index.py、tests/unit/test_llm_cache.py

实现内容：
1. SqliteStorage 增量查询方法：find_parse_memory_by_key(key_level, key_hash)、has_bypass_hash(pattern_hash) 等（select where，加索引）
2. ParseMemory/BypassList 加索引：在 migration 中 create_index（key_level+key_hash、pattern_hash），参考 0001 的风格写 0002 upgrade/downgrade
3. lookup.py 的 find_parse_memory/has_bypass、learn.py 的 StorageMemoryAccess 改调新方法；行为与既有单测完全兼容
4. LlmCache 模型：id、pattern_hash（unique）、request_fingerprint、response_text、created_at（字段以 T1 cache_key 契约为准，先读 T1 报告）
5. LlmCache 的读写方法进 SqliteStorage（实现 T1 的 LlmCacheStore Protocol 语义）

实现约束：
- 不修改 l1/l2 任何代码、orchestrator、cli、providers
- alembic migration 必须可 upgrade/downgrade（integration 测试有 alembic 用例，保持通过）
- 现有 402 个测试全部保持绿（改查询实现不改语义）

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-store-index

最终报告：分支名、commit hash、新增方法签名、索引与 migration 说明、测试结果。
```

### T3：LLM provider 实现（l3_llm）

```text
任务：T3 LLM provider 实现

分支：task/pr5-l3-llm
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-l3-llm" -b task/pr5-l3-llm v2

先 cd 过去，再 uv sync --locked。

目标：
把 l3_llm.py 占位转为实现：cache 查询 → prompt 构建 → transport 调用 → schema 解析 → L3Draft。

只允许修改：
- autoanime/pipeline/l3_llm.py（占位转实现）
- autoanime/providers/llm.py（新建；LlmTransport 的真实实现，唯一允许网络调用的文件）
- autoanime/providers/__init__.py（如需注册 Registry）
- tests/unit/test_l3_llm.py
- tests/fixtures/llm/**（录制响应 fixture）

实现内容：
1. LlmTransport 真实实现（providers/llm.py）：调用 OpenAI 兼容 chat completions 接口（base_url/model/api_key 全部来自 Settings；api_key 用 SecretStr，日志与异常信息脱敏——不得打印 key 或完整 URL）；httpx 异步客户端；超时与重试走 T1 budget 纯函数
2. L3Recognizer 实现（l3_llm.py）：先查 cache（LlmCacheStore Protocol，T2 实现 DB 版，测试 fake）→ 未命中 → prompt 构建（T1）→ transport → schema 解析（T1）→ 命中写 cache → 返回 L3 草稿构建的 ParseResult
3. 预算/重试判定走 T1 budget 纯函数（网络错误重试 ≤2；schema 无效带纠正提示重试 1 次；预算默认不限、超限只记 audit 不阻断）；不可用/解析失败一律返回 None 并计数
4. Registry 注册 LLM transport（外部能力进 registry 规则）
5. 测试全部离线：fake transport + 录制 fixture（覆盖合法/非 JSON 重试后成功/重试后失败/超时/缓存命中五类）

实现约束：
- prompt/解析/预算纯函数直接复用 T1，不重新实现
- 不修改 store.py（cache 读写走 LlmCacheStore Protocol）、arbiter、orchestrator、cli、l1、l2
- Settings 只读（llm 字段 T1 已加）

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-l3-llm

最终报告：分支名、commit hash、L3Recognizer 决策流程图（cache×budget×解析 的输出矩阵）、脱敏措施说明、测试结果。
```

### T4：arbiter 仲裁

```text
任务：T4 arbiter 仲裁

分支：task/pr5-arbiter
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-arbiter" -b task/pr5-arbiter v2

先 cd 过去，再 uv sync --locked。

目标：
把 arbiter.py 占位转为实现：对 L1/L2/L3 三方结果做证据仲裁，产出最终 ParseResult 与审计事件。

只允许修改：
- autoanime/pipeline/arbiter.py（占位转实现）
- tests/unit/test_arbiter.py
- tests/fixtures/l3/arbiter/**（如需）

实现内容：
1. 仲裁决策表（按 T1 报告给出的精确签名实现，纯函数；决策表 R1-R8 见「统一 L3 契约」附录——逐字段仲裁：静态优先级 name > folder > context > memory > llm、只补不覆盖、缺失补齐、R4 一致性升档、R5 验证升档（title_shape 归一化一致 / MetadataReference 旁证）、R6 L2 多季消歧、R7 降级、R8 否决进 audit）
2. MetadataReference 旁证接入：arbiter 接受可选的 reference 参数（T1 的 ReferenceChain 或单个 provider，测试用 fake）；R5/R6 在有参考源时生效；链序即优先级（config reference_order 可调）、reference_enabled=false 或链空时旁证整体不可用；PR6 接真实 Bangumi/TMDB 插件
3. audit 写入：复用 memory/governance.py 的 record_audit 模式（operation_id 批次；只读该文件作参考，不修改它）
4. 冲突场景的 evidence 记录：被否决方的结论进 audit 的 instruction，不进 ParseResult
5. 全决策表用参数化单测钉死（来源×字段×一致/冲突/None 组合矩阵，含 L2 多季歧义被 L3 消歧的用例）

实现约束：
- 纯函数为主；DB（audit）只通过注入的窄 Protocol（测试用 fake）
- 不修改 l1/l2/l3_llm、memory/**、orchestrator、cli

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-arbiter

最终报告：分支名、commit hash、仲裁决策表（L1×L2×L3 → 输出矩阵）、audit 语义说明、测试结果。
```

### T5：orchestrator 串接 + CLI 全链路

```text
任务：T5 L3 集成：orchestrator L2→L3→arbiter + CLI 全链路

分支：task/pr5-integration
前置：T1-T4 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-integration" -b task/pr5-integration v2

先 cd 过去，再 uv sync --locked。

目标：
把 T1-T4 聚合成完整 L1→L2→L3→arbiter 管线，CLI 端到端走通「识别 → L2 记忆 → L3 LLM 兜底 → 仲裁」。

只允许修改：
- autoanime/pipeline/orchestrator.py（仅添加 L2→L3→arbiter 段）
- autoanime/cli.py（接线 L3/arbiter 依赖注入）
- tests/unit/test_orchestrator_l3.py
- tests/blackbox/test_cli_l3.py

实现内容：
1. 路由：MEDIUM 未命中 L2 / LOW / L1-None → L3（llm_enabled 且预算内）→ arbiter；L3 None 或 arbiter 未采纳 → 保持 L1/L2 原结果路由 l3 占位路由改为 arbiter 输出
2. 优雅降级：L3 关闭（settings.llm_enabled / 无 api_key）、transport 异常、超时、超预算 → 全部回退 L1/L2 路由，不崩溃，degraded 标记
3. CLI：parse 全链路 JSON 输出（含 evidence 来源标注 llm/memory/name）；confirm 沿用
4. 边界测试：L3 关闭降级、transport 抛异常降级、超时重试后成功/失败、schema 纠正重试、arbiter 冲突采纳高优先级来源、L1-None 采纳 L3（title_shape 一致升 HIGH / 不一致 MEDIUM）、L2 多季歧义被 L3 消歧、cache 命中不二次调用
5. 单测全部离线（fake transport）

实现约束：
- 不修改 T1-T4 模块内部逻辑；发现契约问题先在报告中说明
- config.py 不改（字段 T1 已加）

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- CLI 手动实测（fake 或真实 key 均可）：至少 3 条真实样本走完 parse→L3→arbiter 链路，报告 JSON 输出
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-integration

最终报告：分支名、commit hash、路由决策说明、降级矩阵、CLI 示例输出、测试结果。
```

### T6：真实快照离线验证 + 真实 LLM 小样本

```text
任务：T6 L3 真实快照验证

分支：task/pr5-real-corpus
前置：T5 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr5-real-corpus" -b task/pr5-real-corpus v2

先 cd 过去，再 uv sync --locked。

目标：
用 2606 条真实快照做 L3 结构级验证（离线 fake LLM），加一小批真实 LLM 手动实测。

只允许修改：
- scripts/validate_l3_corpus.py（新建）
- tests/unit/test_validate_l3_corpus.py

实现内容：
1. 读取外部快照（不复制进仓库，--snapshot 可覆盖，参考 scripts/validate_l2_corpus.py）
2. 全量跑 L1→L2→L3(fake transport)：统计进入 L3 的数量、L3 fake 命中率、arbiter 采纳/否决数、降级数、每条耗时
3. fake transport 策略：按规则从快照行本身构造「合理响应」（如返回 folder 聚类共识），使验证可离线可复现；规则写进脚本并在报告说明
4. 真实 LLM 小样本（手动，非自动测试）：从 pass1 的 L1-None/LOW 样本中取 10 条，用真实 key 跑 L3，报告逐条输入/输出（脱敏：不打印 api_key 与完整 base_url）；此步输出到报告，不进单测
5. 单元测试无快照自动 skip；单条异常容错继续跑
6. 不修改 L1/L2/L3/arbiter 任何现有代码（发现 bug 只记录）

输出统计 JSON：total / l3_entered / l3_fake_hit / arbiter_accepted / arbiter_override / degraded / 耗时 / cache 命中

验收：
- uv run pytest 全绿（无快照环境 skip）
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr5-real-corpus

最终报告：分支名、commit hash、统计 JSON、fake 策略说明、真实 LLM 10 条逐条结果（脱敏）、发现的 bug 清单（只记录）、测试结果。
```

## 4. 推荐执行顺序

```text
T1
  ↓
T2 / T3 / T4   ← 同时开 3 个侧边聊天（写路径互斥）
  ↓
T5
  ↓
T6
```

## 5. 主会话（或调度方）的合并纪律

1. 每个任务完成后：核对分支 diff 是否越界（只含允许清单文件）→ 合并进 v2 → **在主仓库跑联动测试**（全量 pytest + ruff + pyright）→ push v2。
2. T2/T3/T4 合并前先在临时 worktree 做三路预检合并。
3. T1 合并后、开 T2/T3/T4 前，先审一遍 T1 报告中的 Protocol 签名与 arbiter 决策表签名是否一致。
4. 任何任务报告「契约决策不可行」时，停下 renegotiate。
5. T6 的真实 LLM 实测涉及花销与网络外发，执行前向用户当次确认（L2）。

## 6. PR6（Bangumi/TMDB 适配器）并发执行方案

PR6 拆两段，可以基本被 PR5 关键路径吸收：

```text
PR5:  T1 ──→ (T2 │ T3 │ T4) ──→ T5 ──→ T6
PR6:        (P1 适配器)   ──→ (P2 cache+接线)
             ↑ 前置 T1 合并+签名审查    ↑ 前置 T1+T2 合并
             与 T2/T3/T4 并行           与 T5 并行
```

| 任务 | 分支 | 只允许修改 | 前置 |
|------|------|-----------|------|
| P1 Bangumi/TMDB HTTP 适配器 | task/pr6-reference-adapters | providers/bangumi.py、providers/tmdb.py、providers/__init__.py、tests/unit/test_reference_adapters.py、tests/fixtures/reference/**（录制响应，测试离线） | T1 合并且 Protocol 签名已审（ReferenceFacts 字段冻结） |
| P2 reference_cache + 频控 + 注册 | task/pr6-reference-cache | autoanime/memory/store.py（增量）、autoanime/core/models.py（ReferenceCache 模型）、alembic/versions/0003_*.py、providers/**（注册进 Registry）、tests/unit/test_reference_cache.py | T1+T2 合并 |

**并发前提约定：**
- T5 必须把参考源解析写成「按 config 的 reference_order 从 Registry 取插件」的通用形式（空链/未注册时降级不崩溃）——P2 只注册插件，不改 orchestrator.py/cli.py
- ReferenceFacts 字段一旦在 T1 报告中标「待定」，P1 等定稿再开，避免返工
- 真实 API 冒烟（外发网络请求）放最后收尾，PR5-T6 之后统一做，当次确认（L2）
- 峰值 5 路并行；P1/P2 的合并审查同样走主会话串行纪律

### PR6 公共提示词（每个侧边聊天先粘贴这一段）

```text
你在独立 worktree 中工作，不要切换主仓库分支。

仓库：
C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\AutoAnime

基线分支：
v2（粘贴本提示词前先在主仓库跑 git log --oneline -1 v2，把最新 commit 填在这里：____________。
前置状态：PR5 的 T1 必须已合并（MetadataReference Protocol 已定稿）；P2 还要求 T2 已合并）

worktree 路径在任务提示词中给出。

项目规则：
1. AutoAnime v2 正在实现 PR6：Bangumi/TMDB 权威参考源插件（MetadataReference 的真实实现）。
2. 参考源是权威参考（验证/消歧/规范名），不是识别源；arbiter/管线的调用契约由 PR5 定稿，不得更改。
3. 网络 API 只允许在各 adapter 模块内调用；所有单元测试必须离线（httpx MockTransport / fake + 录制 fixture）。
4. 不要修改 autoanime/core/interfaces.py、autoanime/pipeline/**（含 T1 的 l3/reference.py）、autoanime/memory/lookup.py、learn.py、orchestrator.py、cli.py；发现接口缺口停下在最终报告说明，不要静默绕过。
5. 不要引入模块级可变全局状态；provider 的频控状态属于实例状态，不属于模块级全局。
6. 使用 Python 3.12、uv、pytest、ruff、pyright。
7. 每完成一个逻辑单元就 commit（中文 commit message，说清改了什么与影响范围；禁止添加 Co-Authored-By 或任何 Claude 归属 trailer）。
8. 禁止合并到 v2 或 master，只提交并推送自己的 task/* 分支。推送若 GitHub 直连超时，用环境变量 HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 重试（不要改 git config）。
9. 密钥（Bangumi/TMDB api key）用 SecretStr/环境变量（AUTOANIME_* 前缀），不进 git、不进日志、不进报告（脱敏）；异常信息不得回显完整 URL 与 key。
10. 已定稿契约（PR5 T1 报告的 MetadataReference Protocol 与 ReferenceFacts 字段）见下方附录，不得自行更改；发现不可行，停下在最终报告说明。
```

### 附录：MetadataReference 插件契约（随公共提示词粘贴）

```text
MetadataReference Protocol（PR5 T1 定稿，以 T1 报告的精确签名为准）：
- async lookup(title_shape: str) -> ReferenceFacts | None
- ReferenceFacts（字段以 T1 报告定稿为准）：canonical_title（权威名，动画域优先中文名）、
  seasons（季结构）、episode_count、aliases（别名/日文/罗马音）等

插件注册（Registry 模式，interfaces.py 已有）：
- @registry.register(MetadataReference, "bangumi") / @registry.register(MetadataReference, "tmdb")
- chain 解析由 config 的 reference_order 列表驱动（链序即优先级，第一个命中即用），
  本 PR 只负责把插件注册进 Registry，不改解析代码

参考源语义：
- 查询粒度：剧目级（title_shape 归一化键），非文件级
- 失败语义：网络错误/超时/限流等待后仍失败/查无结果 → 返回 None（链继续问下一个 provider），绝不抛异常到管线
- P2 负责：reference_cache 表（先缓存后网络）、每 provider QPS 频控；P1 只暴露可注入的
  transport/clock 参数便于离线测试
```

### P1：Bangumi/TMDB HTTP 适配器

```text
任务：P1 Bangumi/TMDB HTTP 适配器

分支：task/pr6-reference-adapters
前置：PR5 T1 已合并且 MetadataReference/ReferenceFacts 签名已冻结

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr6-reference-adapters" -b task/pr6-reference-adapters v2

先 cd 过去，再 uv sync --locked。

目标：
实现 Bangumi 与 TMDB 两个 MetadataReference 插件（Registry 注册），请求构建/响应解析/字段映射完整可用，单测离线全绿。

只允许修改：
- autoanime/providers/bangumi.py（占位转实现）
- autoanime/providers/tmdb.py（占位转实现）
- autoanime/providers/__init__.py（如需导出/注册）
- autoanime/providers/_reference_http.py（可选：两个 adapter 共用的 httpx 薄封装：超时/UA/重试参数注入）
- tests/unit/test_reference_adapters.py
- tests/fixtures/reference/**（录制响应 fixture）

实现内容：
1. Bangumi 适配器（api.bgm.tv v0）：
   - search：POST /v0/search/topics 或 GET /v0/search/subject/list（以实际 API 为准，录制 fixture 钉住）；subject_type=2（动画）
   - 字段映射：canonical_title=中文名优先（name_cn，空则 name）、aliases=name+japanese+别名、seasons/episode_count 从 subject 结构与 seasons 线索推导（推导规则写进报告）
   - 请求头带 User-Agent（Bangumi 要求自定义 UA）
2. TMDB 适配器（api.themoviedb.org/3）：
   - /search/tv + /tv/{id}（含 /season/{n} 线索）；api_key 走 env（AUTOANIME_TMDB_API_KEY，SecretStr）
   - 字段映射：canonical_title=中文 first_air_name/name（zh-CN），seasons/episode_count=number_of_seasons/episodes
3. Registry 注册：两个 adapter 注册为 MetadataReference 插件（名 "bangumi" / "tmdb"）
4. 频控参数化：adapter 构造接受可注入的 transport（httpx AsyncClient/MockTransport）与 clock/sleeper（测试零等待）；QPS 执行的纯函数可在此定义（token bucket 等），DB 缓存归 P2
5. 失败语义：网络错误/超时/HTTP 4xx5xx/查无结果/解析失败 → 返回 None（不抛异常）；429 按 Retry-After 退避一次再失败即 None
6. 测试全部离线：httpx MockTransport + 录制 fixture（覆盖：正常命中、查无结果、非 JSON、429 退避、超时 五类 × 两 adapter）

实现约束：
- 不修改 interfaces.py、pipeline/**、memory/**、orchestrator、cli；config.py 只读（若需新增 per-provider 配置字段，停下在报告说明）
- ReferenceFacts 字段映射以 T1 定稿为准；发现字段缺口（如 API 拿不到 aliases）在报告中说明降级策略，不要静默返回错形状

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr6-reference-adapters

最终报告：分支名、commit hash、两 adapter 的端点与字段映射表（API 字段 → ReferenceFacts 字段）、失败/退避语义、录制 fixture 清单、真实 API 冒烟建议（留给收尾的 curl/脚本步骤，不执行）、测试结果。
```

### P2：reference_cache 表 + 频控接线

```text
任务：P2 reference_cache 表 + 频控接线

分支：task/pr6-reference-cache
前置：PR5 T1、T2 均已合并

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr6-reference-cache" -b task/pr6-reference-cache v2

先 cd 过去，再 uv sync --locked。

目标：
为参考源加剧目级缓存与每 provider 频控，并把真实插件接进 Registry 组合，使 PR5 的 chain 无需改动即可用上缓存与真实适配器。

只允许修改：
- autoanime/memory/store.py（增量添加查询/写入方法，不改动现有方法签名）
- autoanime/core/models.py（仅新增 ReferenceCache 模型）
- alembic/versions/0003_reference_cache.py（新 migration，参考 0001/0002 风格，可 upgrade/downgrade）
- autoanime/memory/reference_cache.py（新建：缓存读写 + CachedReference 包装器 + token bucket 频控）
- autoanime/providers/__init__.py（如需在注册组合中包一层 CachedReference）
- tests/unit/test_reference_cache.py

实现内容：
1. ReferenceCache 模型：id、title_shape（unique）、provider、facts（JSON，ReferenceFacts 形状）、fetched_at、expires_at（TTL 可配，默认不过期或 30 天，报告里定并说明理由）
2. SqliteStorage 增量方法：find_reference_cache(title_shape, provider) / add_reference_cache(row)（实现 T1 定稿的缓存读取语义；若 T1 未定义 reference 缓存 Protocol，在本模块内定义窄 Protocol 并在报告中给出签名）
3. CachedReference 包装器（实现 MetadataReference）：先查缓存（命中直接返回）→ 未命中调被包装的真实 adapter → 成功写缓存 → 返回；adapter 返回 None 是否缓存负结果：缓存「查无结果」防重复打 API（TTL 更短），在报告中定并说明
4. 每 provider QPS 频控（token bucket 纯函数 + 实例状态挂 provider 实例上，不做模块级全局）；qps 配置来源：config 只读（若 T1 未加 per-provider qps 字段，本任务允许在 config.py 增量添加 reference_qps 字段并配 test_config 单测——与 P1 文件不冲突）
5. alembic 0003：create table reference_cache + 索引（title_shape+provider unique）；integration 的 alembic 用例保持通过
6. 测试离线：fake adapter（计数调用次数）+ 内存 SQLite（验证二次查询不触 adapter）；频控单测（注入 clock，验证速率）；migration upgrade/downgrade

实现约束：
- 不修改 pipeline/**（含 T1 的 l3/reference.py）、orchestrator、cli、lookup.py、learn.py
- CachedReference 的注册组合方式与 PR5 T5 的「按 reference_order 从 Registry 取插件」约定对齐；若发现必须改 T1 模块才能接线，停下在报告说明，不要静默改
- 不改 P1 的 bangumi.py/tmdb.py（P1 可能并行进行中）

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr6-reference-cache

最终报告：分支名、commit hash、缓存语义（TTL/负缓存）、频控参数与实现方式、新增 store 方法签名、与 chain 的接线说明、测试结果。
```

### P1/P2 合并与收尾

1. P1、P2 各自完成后：主会话核对 diff 越界 → 合并进 v2 → 全量联动 → push。
2. P1 与 P2 均合并后做一次**真实 API 冒烟**（外发请求，当次确认）：Bangumi 搜索 2-3 个真实剧目 + TMDB 1-2 个，验证字段映射与缓存生效；冒烟脚本可临时跑不入库。
3. PR6 完成后，PR5-T6 的统计脚本（validate_l3_corpus.py）可补一个「带真实参考源」的可选模式，另行小任务处理。
