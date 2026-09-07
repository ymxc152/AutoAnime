> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# PR7（参考源归一化：罗马音/别名 → 中文权威名）任务拆分与执行 Plan

> 生成日期：2026-09-06。基线：v2 @ `15c565d`（PR5 T1-T6 + PR6 P1/P2/CLI 接线均已合并推送）。
> 状态：**已执行完毕（2026-09-06）**——4 项 ⚡ 已拍板：别名存新表 `title_aliases`、回填仅 confirm 侧、L3 缓存键剧目级化本期不做、PR7 先行于 M2-M4。M1 合并后经用户确认继续，M2/M3/M2b/V1 已全部完成合入 v2 并推送（执行结果见文末「5. 执行记录」）。
> 动机：release name 常态含罗马音/英文，L1 无翻译能力，L2 记忆按 confirm 时的语言存 key——同一部剧的罗马音/日文/中文名是三条互不相认的 L2 key，导致 L2 长期冷启动、每文件 fallback LLM。

---

## 0. 决策依据（PR5-T6 统计 + PR6 冒烟 + 真实 LLM 实测）

**T6 统计（2606 条，离线 fake）**：
- `l3_entered=2233`（86%）、`routes.memory=0`（L2 冷启动）、`levels: medium=2198`——绝大多数文件在 L2 miss 后进 L3。
- pass1/pass2 决策完全一致、pass2 `transport_calls=0`——llm_cache 回放语义验证通过。
- `arbiter_field_conflicts=454`（title 426）、`arbiter_upgraded=35`、`accepted=2`——arbiter 证据优先级工作正常。

**真实 LLM 实测（10 条，deepseek-v4-flash）**：
- LLM title 幻觉明显：`7³ACG`、`ktnbytes`（把字幕组括号当标题）、`logs→segment=movie`；arbiter 全部按 name 证据正确压制（conflicts=3 写 audit）。
- **结论：LLM 不适合做「标题归一化」的主路径**；参考源（权威、可缓存、便宜）才是。

**PR6 冒烟 + 补充验证**：
- Bangumi 搜索索引支持罗马音（"Sousou no Frieren" 搜回正确条目），但 `pick_candidate` 匹配层拒绝——瓶颈在本地匹配，非 API。
- TMDB `language=zh-CN` 下拉丁 query 失配（返回名全为中文/日文）。
- `ReferenceFacts.aliases` 已含罗马音/日文别名——**归一化数据源现成**。

## 1. 契约决策（标 ⚠️ 需拍板；未标按 PR5/PR6 既有决议延续）

1. **参考源前置消歧（主路径，R9）**：orchestrator 在 L2 miss 后、进 L3 前，用 L1 草稿 title_shape 查参考源（经 reference_cache，剧目级一次 API）；命中则用 `canonical_title` 归一出的 canonical shape **重查一次 L2**——命中即采纳（memory 语义不变，只是 key 归一），未命中继续原路由进 L3。arbiter 的 R5/R6 旁证机制不变。
2. **证据优先级不变**：`name > folder > context > memory > (bangumi/tmdb) > llm`——前置消歧不引入新的仲裁方，只是让 memory 的 key 能被罗马音命中。
3. **别名存储形状（已拍板 2026-09-06）**：**新窄表 `title_aliases`**（`title_shape_norm` PK → `canonical_shape`、`source`、`created_at`），而不是 ParseMemory 双写多行——L2 主表语义零改动，别名只是「key 等价类」映射。（否决备选：ParseMemory 每 alias 一行，语义混入）
4. **别名回填时机（已拍板 2026-09-06）**：**仅 confirm 侧回填**——confirm/确认写 ParseMemory 成功时顺带查一次参考源把 `canonical_title + aliases` 写入 `title_aliases`（一次 API/剧目，之后查询侧零外呼）。（否决 learn 侧回填：learn 是批量路径，避免隐式网络依赖）
5. **匹配层修复（M1）**：
   - Bangumi/TMDB `pick_candidate`：增加「候选名集合」匹配——除 `name/name_cn` 外，把搜索结果列表内的兄弟条目名、（TMDB）`original_name` 纳入比较；罗马音↔日文可用 `title_shape` 级归一（casefold + 剥占位符）后比对。
   - 短 query（≤4 字）保护保留，但「前缀匹配且唯一命中」时放行（修复「孤独摇滚」vs「孤独摇滚！」）。
   - TMDB 拉丁 query：search 改/加一次 `language=en`（或 `ja-JP`）查询再并集候选（+1 请求/剧目，可接受）；不引入 alternative_titles 端点（控制请求数）。
6. **L3 缓存键剧目级化（已拍板：本期不做）**：llm_cache 目前按 raw `pattern_hash` 存——同剧不同文件名各存一份、LLM 重复付费。改双键会触碰 T1 cache_key 契约与 prompt 构建纯函数，且 M2 落地后进 L3 的量会大降，收益需重估——留 PR8+ 再议。
7. **LLM 歧义兜底（原方案 C）降级为不做**：真实 LLM 实测幻觉证据（上节）+ M2 后歧义场景大减；如未来需要，挂 R5 旁证链里再议（PR8+）。
8. **降级语义**：参考源不可用/超时/全 miss → 前置消歧整体跳过，路由与 PR5 完全一致（零行为差异）；`reference_enabled=false` 时同样跳过。
9. **与 M2-M4 大计划的关系（已拍板 2026-09-06）**：**PR7 先行于 M2-M4（E1-E4）**——用户确认其影响大（L2 冷启动是飞轮核心堵点）。

## 2. 任务拆分

| 阶段 | 任务 | 分支 | 并行性 | 前置 |
|------|------|------|--------|------|
| 1 | M1：参考源匹配层修复 | task/pr7-matcher | 先行串行（M2 命中率依赖它） | 无 |
| 2 | M2：前置消歧 + canonical 重查 L2 | task/pr7-canonical | 串行 | M1 合并 |
| 2 | M3：别名回填（confirm 侧） | task/pr7-alias-backfill | ✅ 与 M2 并行（写路径见下） | M1 合并 + ⚠️3/4 拍板 |
| 3 | V1：全量快照回归验证 | task/pr7-validate | 最后 | M2+M3 合并 |

**并行注意**：M2 触 `orchestrator.py`；M3 触 `learn.py`/`confirm` 路径 + `models.py` + store 增量——`store.py` 若双方都要加方法，M2 串行在 M3 之后合并（或 M2 不动 store：canonical 重查复用现有 `find_parse_memory_by_key`，M3 只加 alias 表读写）。**约束：M2 不改 store.py/models.py，M3 才是唯一动 schema 的任务**（migration 0004）。

### M1：参考源匹配层修复（task/pr7-matcher）

只允许修改：`autoanime/providers/bangumi.py`、`tmdb.py`、（可选）`_reference_http.py`、`tests/unit/test_reference_adapters.py`、`tests/fixtures/reference/**`。

实现内容：
1. `pick_candidate` 候选名集合化：比较范围扩到搜索结果全部条目的 `name`/`name_cn`（Bangumi）与 `name`/`original_name`（TMDB）；命中条目本身仍从「最相似候选」取。
2. 短 query 前缀唯一放行：≤4 字 query 允许「唯一前缀命中」。
3. TMDB 双语 search：zh-CN 结果与 en（拉丁）结果并集候选，去重后匹配。
4. title_shape 级归一比对（复用 l2 normalize 的语义，providers 内不可 import pipeline——把需要的归一纯函数放 providers 本地或在 M1 报告说明选择）。
5. fixture 补录：罗马音 query 命中（Sousou no Frieren / Kusuriya no Hitorigoto）、孤独摇滚短 query、TMDB 拉丁 query 双语并集。
6. 用真实样本离线断言：`Sousou no Frieren`、`Kusuriya no Hitorigoto`、`孤独摇滚`、`Frieren` 四类 query 的匹配路径有单测覆盖。

验收：pytest/ruff/pyright 全绿 + git status 干净；报告给出修复前后四类 query 的命中矩阵（可附真实 API 手测结果，标注为手测）。

### M2：参考源前置消歧（task/pr7-canonical）

只允许修改：`autoanime/pipeline/orchestrator.py`（仅 L2 miss → L3 之间插入前置消歧段）、`autoanime/pipeline/l3/reference.py`（如需暴露归一辅助，不改已有签名）、`tests/unit/test_orchestrator_canonical.py`（新建）、既有 orchestrator 测试增量。

实现内容：
1. R9 前置消歧段：L2 miss 后查 `reference_chain.lookup(title_shape)`（已在 reference_cache 内，命中零外呼）→ `canonical_title` 归一成 shape → `find_parse_memory_by_key` 重查一次 → 命中则按 memory 语义产出结果（evidence 仍为 memory）。
2. canonical shape 的归一函数与 L2 key 计算严格同源（报告说明复用点；不可复制两份逻辑）。
3. 降级矩阵：参考源 None / reference_enabled=false / canonical 重查 miss → 与现行为逐字节一致。
4. 单测：fake reference 命中重查成功、miss、关闭、异常四类；确保不触真实网络。

验收：全绿 + `scripts/validate_l3_corpus.py` 快照回归（离线）统计对比 PR5-T6 基线（`l3_entered` 应显著下降——fake reference 策略下），报告给出对比 JSON。

### M3：别名回填（task/pr7-alias-backfill，⚠️3/4 拍板后开）

只允许修改：`autoanime/memory/learn.py`（confirm/learn 成功路径钩子，语义不变）、`autoanime/core/models.py`（新增 TitleAlias 模型）、`alembic/versions/0004_title_aliases.py`、`autoanime/memory/store.py`（增量 alias 读写）、`autoanime/memory/reference_cache.py`（如需复用 CachedReference 调用点，不改已有类签名）、`tests/unit/test_alias_backfill.py`、`tests/unit/test_schema_constraints.py`（表数 12→13 机械更新）。

实现内容：
1. TitleAlias 模型 + migration 0004（可 upgrade/downgrade）。
2. confirm 路径成功后：查参考源（title_shape）→ 写 `canonical_shape` + aliases→canonical 映射（失败静默跳过，绝不阻断 confirm 主流程）。
3. store 增量：`find_alias_key(shape)` / `put_alias_map(...)`。
4. M2 的重查段可选地先查 alias 表再查参考源（若 M2 已合并则增量接线；未合并则本任务只做写侧，读侧归 M2——**以先合并者负责读侧**，报告说明）。
5. 单测：fake reference、失败静默、migration upgrade/downgrade、alias 命中重查。

### V1：快照回归验证（task/pr7-validate）

只允许修改：`scripts/validate_pr7_corpus.py`（新建，复用 validate_l3_corpus 的快照读取与 fake 策略）+ 单测。输出对比 JSON：`l3_entered`（目标：显著低于 2233）、`canonical_rel2_hit`（前置消歧命中数）、`alias_hit`、路由分布、耗时。真实 LLM 不重跑（10 条实测仍有效）。

## 3. 执行顺序

```text
M1（匹配层）
  ↓
M2（前置消歧） │ M3（别名回填，⚠️ 拍板后）
  ↓
V1（快照回归）
```

## 4. 合并纪律

同 PR5/PR6：越界核对 → 三路预检（多人并行时）→ 合并进 v2 → 全量联动 → push；⚠️ 决策未拍板不派对应任务；V1 统计作为 PR7 收尾报告输入。

---

## 5. 执行记录（2026-09-06 收尾）

**v2 HEAD @ `19f81f9`，已推送 origin。全量 715 passed / ruff 零错误 / pyright 0 errors。**

| 任务 | 分支 | 关键 commit | 结果 |
|------|------|-------------|------|
| M1 匹配层修复 | task/pr7-matcher | 9fb545a | 四类 query 全通（罗马音/拉丁/短 query 前缀/多义保护）；实际匹配载体为 infobox 别名（计划字面的 name/name_cn 集合对罗马音不生效） |
| M3 别名回填 | task/pr7-alias-backfill | 2d59e46 | title_aliases 窄表 + migration 0004 + confirm 侧护栏钩子（3s 超时静默）；confirmed 标题形状本身也入映射 |
| M2 前置消歧 | task/pr7-canonical | 55d3ae7 | L2 miss 后 alias 表 → 参考链 canonical → lookup_memory 两级重查；降级矩阵 11 项单测 |
| M2b 读侧接线 | task/pr7-alias-wiring | f592d26 | 查找链 alias 环置首（零外呼）+ cli confirm 装配 reference_lookup；发现 build_title_shape 数字相邻分隔符非幂等边类（alias 环静默 miss 由参考链救回，无正确性影响） |
| 透传补丁 | task/pr7-alias-readthrough | d79e007 | StorageMemoryStore 透传 find_alias_key——V1 发现的生产装配缺口（缺失时 CLI 下 alias 环被鸭子类型探测静默跳过） |
| V1 快照回归 | task/pr7-validate | c417f76 | scripts/validate_pr7_corpus.py；对比 JSON 见下 |

**V1 对比 JSON（2606 条，fake 策略：seed canonical 级 memory + title_aliases）**：
routes: archive=373（恒等基线）/ memory=2232 / l3 fallback=**1**（基线 2233）；l3_entered=2233 不变（PR5 契约：L3 对 memory 命中仍作 arbiter 输入，收益指标用 routes.l3）；canonical_requery_hit=315、alias_hit=315（alias 环零外呼验证通过）、direct_l2_hit=1917；总耗时 27.3s，单条 p95 9.8ms。

**遗留（PR8+ 输入）**：
1. TMDB fixture 未经真实 API 验证（本机无 key，M1 手写 fixture 已标注）。
2. Bangumi 纯短中文词保守拒绝（如「药屋」）——别名扩展属归一化层职责，现状由 confirm 回填逐步覆盖。
3. llm_cache 剧目级键未做（拍板维持），M2 落地后 l3 fallback 大降，收益需按真实数据重估。
4. build_title_shape 非幂等边类（`S02E05.720p` 型）：alias 键两侧均单次成形不受影响；如未来在别处复用 shape 做键，需先收敛该边类。
