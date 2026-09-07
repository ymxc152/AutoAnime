> 归档状态：2026-09-07 归档。该计划对应的工作已合并；引用路径可能随目录整理失效。

# PR4（L2 记忆层）任务拆分与执行 Plan

> 生成日期：2026-09-05。基线：v2 @ `d41507e`（PR1 骨架 + PR2 语料 + PR3 L1 已全部合并并推送远端）。
> 用法：按「推荐执行顺序」开侧边聊天，每个侧边聊天先粘贴「公共提示词」，再粘贴对应任务的完整提示词。

---

## ✅ 完成状态（2026-09-05 更新）

**PR4 全部完成并合入 v2，最终 v2 @ `5360063`（含 PR4.1 评审修复），全量 402 passed / ruff / pyright 零错误，已推送远端。**

| 任务 | 分支 tip | 合并状态 |
|------|----------|----------|
| T1 公共契约 | `72710e7` | ✅ 合并（v2 `4c5d97a`） |
| T2 学习写入侧 | `fe678e2` | ✅ 合并（v2 `67d484f`） |
| T3 查询融合侧 | `2c21d58` | ✅ 合并（v2 `67d484f`） |
| T4 治理侧 | `916db2e` | ✅ 合并（v2 `ce764d5`） |
| T5 集成 | `eb875f4` | ✅ 合并（v2 `982586f`） |
| T6 真实快照验证 | `3e2c556` | ✅ 合并（v2 `887cd45`） |
| PR4.1 评审修复 | `66da351` + `35a91a7` | ✅ 合并（v2 `5360063`，分支 task/pr4.1-l2-review-fixes） |

**执行偏离记录：**
- T2/T3/T4 并行时为避免同写 `store.py`，约定三者均不改 `store.py`/`interfaces.py`，各自在新建模块组合 `SqliteStorage` 通用 API（T5 未收敛）。
- PR4.1 评审修复（主会话完成）：①剧目级多季合并语义（`seasons` 列表，消除多季剧互判 correction → trust=0 → 整组 miss 的根因）；②DEPRECATED 终态守卫；③audit 命中按 parse pass 批次归组。另合入一处侧边会话改动：L2 开关进 `Settings.l2_enabled`、CLI 接线命中审计、快照 fallback 向上遍历、L1 fallback 测试 delenv。

**T6 真实快照指标（2606 条，对照粗原型）：**
- pass2 命中率 **99.15%**（2213/2232，修复前口径）vs 粗原型 94.3%
- 剧目级 key 242（MEDIUM 口径）vs 粗原型 458（口径不同，报告已注明不可直接对比）
- MEDIUM→HIGH 迁移 2 条（结构性：缺 season 的 [F] 行无信息源）
- 「HIGH 也查记忆」离线估算收益 ≈ 0，验证 HIGH 不过 L2 的决策正确

**仍开放（PR5 输入，详见 memory `pr4-l2-followups`）：**
1. store 层 `find_parse_memory` 全表 Python 过滤——规模化前需真 DB 级查询（T6 脚本已用预载索引子类绕过）
2. L1 噪声标题（`1080p`、`webrip` 等被解析为 title 并进记忆）——属 L1 改进项
3. L3/LLM 元数据源仍为占位（本 PR 明确不实现）


## 0. 总览

拆成 **6 个子任务**；真正同时并行的是中间 3 个（写入侧 / 查询侧 / 治理侧，模块路径互斥）。

| 阶段 | 任务 | 分支 | 并行性 |
|------|------|------|--------|
| 1 | T1：L2 公共契约 / 两级 key / 占位符 / 信任分 / bypass 匹配 / roundtrip fixture | task/pr4-l2-core | 先行，串行 |
| 2 | T2：学习与写入侧（confirm → parse_memory） | task/pr4-l2-learn | ✅ 三个并行之一 |
| 2 | T3：查询与命中融合侧（L2 lookup 实现） | task/pr4-l2-lookup | ✅ 三个并行之一 |
| 2 | T4：治理侧（bypass / alias / status 淘汰 / audit） | task/pr4-l2-governance | ✅ 三个并行之一 |
| 3 | T5：orchestrator L1→L2 串接 + CLI 全链路 | task/pr4-l2-integration | 串行 |
| 4 | T6：真实 2606 快照两遍验证 | task/pr4-l2-real-corpus | 最后 |

**T1 不要省**：两级 key 派生、title_shape 规范化、信任分公式、命中后档位/evidence 语义，是 T2/T3/T4 共同依赖的契约。没有 T1，三个并行任务会各自发明 key 规范化规则，后面无法合并。

## 1. 已拍板的契约决策（所有任务遵守，不得自行更改）

1. **剧目级 key 不含 fansub**：剧目级（level=1）= title_shape 归一化；精确级（level=2）= title_shape + season/episode 结构（fansub_norm 参与精确级校验）。跨字幕组命中由 alias 表兜底。
2. **信任分阈值**（T1 写成常量 + 单测）：信任分 < 0.5 → status 降 PENDING；< 0.8 → 不参与档位融合（可参与 evidence 补充）；>= 0.8 → 允许融合升档。
3. **L1 HIGH 不过 L2**：orchestrator 路由 = HIGH 直接归档 / MEDIUM 进 L2 / LOW 与 L1-None 进 L3 占位。T6 统计中保留「若 HIGH 也查记忆」的观察项但不实现。
4. **evidence 约定**：记忆命中补齐/覆盖的字段 evidence 值为 `"memory"`；L1 已有 name/folder 证据的字段，memory 不得覆盖（filename 优先原则延续）；命中时 evidence 追加 `"key_level"` 键注明 `memory:1` / `memory:2`。
5. **key_hash**：对规范化后的 key 做稳定哈希（同一输入同一 hash，跨进程跨平台一致）；规范化规则在 T1 的 keys.py 中定义并配单测。

---

## 2. 公共提示词

每个侧边聊天先粘贴这一段。

```text
你在独立 worktree 中工作，不要切换主仓库分支。

仓库：
C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\AutoAnime

基线分支：
v2，当前 commit d41507e（PR3 已收尾：L1 七方言 + LocalRecognizer + CLI parse 子命令 + 真实快照验证脚本均已合并）

worktree 路径会在任务提示词中给出。

项目规则：
1. AutoAnime v2 正在实现 PR4：L2 记忆层。
2. 不要实现 L3/LLM/API 元数据源/下载器；orchestrator 仅 T5 允许修改，且只允许添加 L1→L2 段。
3. 不要修改 autoanime/core/interfaces.py，除非任务明确允许（仅 T1 允许，且只允许新增 L2 相关 Protocol，不改动现有 Protocol）。
4. 不要引入模块级可变全局状态。
5. 不要使用网络 API（L2 不调用任何外部元数据 API）。
6. L1 的 ParseResult 语义不变；L2 是增强层，不是替代层。
7. 所有外部能力都不进 registry；L2 的学习/查询/治理属于固定 pipeline 组件。
8. 使用 Python 3.12、uv、pytest、ruff、pyright。
9. 每完成一个逻辑单元就 commit（中文 commit message，说清改了什么与影响范围；禁止添加 Co-Authored-By 或任何 Claude 归属 trailer）。
10. 禁止合并到 v2 或 master，只提交并推送自己的 task/* 分支。
11. L2 的 key 派生 / 占位符 / 信任分 / 档位融合必须是纯函数，DB 会话只在 store 层出现。
12. 存储是 async SQLAlchemy 2.0 + aiosqlite（autoanime/memory/store.py 已有 SqliteStorage）；不要改 store.py 的公共 API，如需新查询方法只做增量添加。
13. 已拍板契约决策见任务提示词附录，不得自行更改；如发现决策在实现中不可行，停下在最终报告中说明，不要静默偏离。
```

### 统一 L2 契约（附录：随公共提示词一起粘贴）

```text
L2 统一契约：
输入：
- L1 的 ParseResult + ParseContext | None
- 存储会话（store 层注入）

输出：
- 增强后的 ParseResult（命中并融合时）
- None（未命中/不适用，交回 orchestrator 按 L1 原结果路由）

两级 key（PLAN 决议）：
- 剧目级（key_level=1，主力）：title_shape 归一化，不含 fansub
- 精确级（key_level=2，兜底）：title_shape + season/episode 结构，fansub_norm 参与校验
- 查找顺序：剧目级优先，未命中落精确级

占位符抽象：
- {ep} / {season}；学习侧从确认结果生成 title_shape，查询侧回填

信任分：
- trust = hit_count / (hit_count + corrected_count)
- < 0.5 → status 降 PENDING；< 0.8 → 不参与档位融合；>= 0.8 → 允许融合升档

档位融合规则：
- L1 MEDIUM + 记忆命中（信任分 >= 0.8）→ 允许补 season/episode 并升 HIGH
- L1 LOW 结果不进 L2（路由 L3）
- memory 不得覆盖 L1 已有 name/folder 证据的字段
- 命中字段 evidence = "memory"，并追加 "key_level": "memory:1" 或 "memory:2"

bypass：
- pattern_hash 命中 bypass_list 的 key 不写入、不参与融合
```

---

## 3. 任务提示词

### T1：L2 公共契约与基础设施

```text
任务：T1 L2 公共契约与基础设施

分支：task/pr4-l2-core
前置：无（基于 v2 @ d41507e）

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-core" -b task/pr4-l2-core v2

之后所有操作都在该 worktree 目录内进行（先 cd 过去，再 uv sync --locked 装依赖）。

目标：
建立 T2/T3/T4 都依赖的 L2 契约与纯函数基础设施。

只允许修改：
- pyproject.toml、uv.lock（如需新依赖）
- autoanime/pipeline/l2/**（新建包）
- autoanime/core/interfaces.py（仅新增 L2 相关 Protocol，如 MemoryRecognizer / MemoryStore 协议，不改动现有 Protocol）
- tests/support/**（扩展 loader 支持 roundtrip fixture）
- tests/unit/test_l2_infra.py
- tests/fixtures/memory/**（新建 roundtrip fixture 目录）

建议模块：
- keys.py：两级 key 规范化与派生（剧目级 = title_shape；精确级 = title_shape + season/episode + fansub_norm），key_hash 稳定哈希
- placeholders.py：title_shape 生成（确认结果 → 含 {ep}/{season} 占位符的模板）与回填（模板 + 集数/季号 → 具体值）
- trust.py：信任分计算、阈值常量（0.5 / 0.8）、档位融合规则（MEDIUM+命中→HIGH 的判定纯函数）
- bypass.py：pattern_hash 规范化与匹配（纯函数部分）
- draft.py：L2 草稿结构与最终 ParseResult 构建（含 evidence="memory" 与 key_level 注记）

roundtrip fixture schema（定义并扩展 loader）：
{
  "id": "R01_learn_then_lookup",
  "learn": { "parse_result": {...}, "confirmed": {...} },
  "query":  { "name": "...", "folder": "...", "expected": {...} }
}
- learn.parse_result 用 L1 真实输出形状；expected 按 ParseResult 契约断言
- 为现有 A01、B01 fixture 各写一条 roundtrip 示例

实现约束：
- 本任务不实现任何 DB 读写逻辑（T2/T3 做）；全部纯函数 + Protocol 定义
- 不实现 l2_memory.py、orchestrator、cli
- 不修改 autoanime/pipeline/l1/** 任何代码

验收：
- uv sync --locked
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-core

最终报告：分支名、commit hash、模块结构、public API（含 Protocol 签名）、roundtrip fixture schema 示例、测试结果。
```

### T2：学习与写入侧

```text
任务：T2 L2 学习与写入侧

分支：task/pr4-l2-learn
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-learn" -b task/pr4-l2-learn v2

先 cd 过去，再 uv sync --locked。

目标：
实现确认结果 → parse_memory 的学习写入路径。

只允许修改：
- autoanime/memory/learn.py（新建）
- autoanime/cli.py（仅 confirm/learn 相关段）
- tests/unit/test_l2_learn.py
- tests/fixtures/memory/roundtrip/learn/**

实现内容：
1. 确认结果 → parse_memory upsert：unique(key_level, key_hash) 冲突时更新而非插入
2. hit_count / corrected_count 计数与信任分更新（走 T1 trust 模块）
3. MemorySource 三来源（manual / llm_confirmed / llm_auto）写入
4. title_shape 生成走 T1 placeholders 模块
5. bypass 命中的 key 不写入：调 T1 bypass 纯函数判定；bypass_list 的 DB 读写接口用 Protocol 注入（实现归 T4，测试用 fake）
6. CLI confirm 子命令增强：确认后写 parse_memory（先读现有 cli.py 的 confirm 实现做增量修改）

实现约束：
- key 派生/信任分等纯函数直接复用 T1，不重新实现
- DB 会话只在 store 层出现（用 SqliteStorage 现有 API + 必要的增量查询方法）
- 不修改 lookup/查询路径、orchestrator、l1

验收：
- uv run pytest 全绿（含 roundtrip learn 用例：内存 SQLite 学习后可查回）
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-learn

最终报告：分支名、commit hash、upsert/计数语义说明、测试结果。
```

### T3：查询与命中融合侧

```text
任务：T3 L2 查询与命中融合侧

分支：task/pr4-l2-lookup
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-lookup" -b task/pr4-l2-lookup v2

先 cd 过去，再 uv sync --locked。

目标：
实现 L2 的查询路径：L1 ParseResult → 两级 key 查找 → 命中融合 → 增强后的 ParseResult。

只允许修改：
- autoanime/pipeline/l2_memory.py（占位转实现）
- autoanime/memory/lookup.py（新建）
- tests/unit/test_l2_lookup.py
- tests/fixtures/memory/roundtrip/lookup/**

实现内容：
1. L1 ParseResult → 两级 key 派生（走 T1 keys 模块）：剧目级优先，未命中落精确级
2. 占位符回填：title_shape + 记忆中的 season/episode 补齐 L1 缺失字段（走 T1 placeholders 模块）
3. 命中融合：走 T1 trust 的档位融合规则——信任分 >= 0.8 允许补字段并升 HIGH；< 0.8 只补 evidence 不融合；< 0.5 视为未命中
4. evidence：命中字段 = "memory"，追加 "key_level" = "memory:1" / "memory:2"；memory 不覆盖 name/folder 证据字段
5. 未命中返回 None（交回 orchestrator 按 L1 原结果路由）
6. 信任分不足 / status 非 ACTIVE 的 key 不参与融合：status 治理归 T4，查询侧只按 status 过滤（用 Protocol 注入读取接口，测试用 fake）

实现约束：
- 不接 orchestrator（T5 做）
- 不修改 learn/写入路径、cli、l1
- l2_memory.py 对外入口对齐 Recognizer 风格的窄接口（T1 定义的 Protocol）

验收：
- uv run pytest 全绿（含 roundtrip lookup 用例：无记忆 → None；有记忆 → 补字段升档；低信任分 → 不融合）
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-lookup

最终报告：分支名、commit hash、查找/融合决策表（命中×信任分×档位的输出矩阵）、测试结果。
```

### T4：治理侧

```text
任务：T4 L2 治理侧

分支：task/pr4-l2-governance
前置：T1 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-governance" -b task/pr4-l2-governance v2

先 cd 过去，再 uv sync --locked。

目标：
实现 L2 的治理横切面：bypass 黑名单、alias 辅助命中、信任分驱动的 status 淘汰、审计写入。

只允许修改：
- autoanime/memory/governance.py（新建）
- autoanime/memory/alias.py（新建）
- tests/unit/test_l2_governance.py
- tests/fixtures/memory/roundtrip/governance/**

实现内容：
1. bypass_list 写入/查询：pattern_hash 走 T1 bypass 模块规范化；记录 reason 与 created_at
2. alias 辅助：alias_norm 查找辅助剧目级 key 跨字幕组命中（alias_norm 归一化复用 T1 keys 模块的规范化函数）
3. status 淘汰：信任分 < 0.5 → PENDING；持续无命中或被纠正 → DEPRECATED（用 T1 trust 阈值常量，不自行定数）
4. audit_log 写入：operation_id 批次字段（先读 models.py 的 audit_log 定义）；本 PR 只记录 L2 命中/淘汰事件
5. 不实现「频繁被撤销→自动收紧」策略（留数据基础，后续 PR 实现）

实现约束：
- 不修改 learn/lookup 的写路径、cli、orchestrator、l1
- 淘汰逻辑提供批处理入口（供 T6/后续定时任务调用），不做后台任务
- DB 会话只在 store 层

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-governance

最终报告：分支名、commit hash、bypass/alias/淘汰语义说明、测试结果。
```

### T5：orchestrator 串接 + CLI 全链路

```text
任务：T5 L2 集成：orchestrator L1→L2 + CLI 全链路

分支：task/pr4-l2-integration
前置：T1-T4 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-integration" -b task/pr4-l2-integration v2

先 cd 过去，再 uv sync --locked。

目标：
把 T1-T4 聚合成完整 L1→L2 管线，CLI 端到端走通「识别 → 确认 → 记忆命中」飞轮。

只允许修改：
- autoanime/pipeline/orchestrator.py（仅添加 L1→L2 段；L3/arbiter 保持占位）
- autoanime/cli.py
- tests/unit/test_orchestrator_l2.py
- tests/blackbox/test_cli_l2.py

实现内容：
1. orchestrator 固定路由：L1 HIGH → 直接归档路径（不过 L2）；MEDIUM → 进 L2（命中融合 / 未命中保持 L1 原结果并路由 L3 占位）；LOW 与 L1-None → 直接路由 L3 占位
2. 优雅降级：L2 关闭（配置或存储不可用）→ 全量走原 L1 路由，不崩溃
3. CLI 端到端子命令：parse → confirm（写记忆）→ 再 parse 展示 evidence=memory 命中
4. 全部 26 条 L1 fixture 的学习→命中往返测试（先按 L1 expected 学习，再确认结果写入，再重放查询断言融合结果）
5. 冲突与边界测试：信任分不足不融合、bypass 命中不写入不融合、memory 不覆盖 name 证据字段、L2 关闭降级、L1 HIGH 不进 L2

实现约束：
- 不修改 T1-T4 的模块内部逻辑；发现契约问题先在报告中说明，不静默绕过
- 不实现 L3 任何逻辑（占位路由即可）

验收：
- uv run pytest 全绿
- uv run ruff check 零错误
- uv run pyright 零错误
- CLI 手动实测：至少 3 条真实样本走完 parse→confirm→再 parse，报告 JSON 输出（含 evidence=memory 的二次识别）
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-integration

最终报告：分支名、commit hash、路由决策说明、26 条 fixture 往返结果汇总、CLI 示例输出、测试结果。
```

### T6：真实快照两遍验证

```text
任务：T6 L2 真实快照两遍验证

分支：task/pr4-l2-real-corpus
前置：T5 已合并到 v2

worktree 路径（先执行）：
git worktree add "C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\worktrees\pr4-l2-real-corpus" -b task/pr4-l2-real-corpus v2

先 cd 过去，再 uv sync --locked。

目标：
用 2606 条真实快照做 L2 结构级两遍验证：冷启动 → 模拟确认学习 → 复测命中，对照 PLAN 粗原型指标。

只允许修改：
- scripts/validate_l2_corpus.py（新建）
- tests/unit/test_validate_l2_corpus.py

实现内容：
1. 读取外部快照（不复制进仓库）：
   C:\Users\17645\Desktop\面试\07_新项目规划\01_AutoAnime产品化升级\notes\samples\z_downloads_snapshot.txt
   支持 --snapshot 参数覆盖；解析 [F]/[D] 前缀（参考 scripts/validate_l1_corpus.py 的现有实现）
2. pass1 冷启动：全量跑 L1→L2（空库），统计 L1 档位分布、L2 未命中率、剧目级 key 数量（对照粗原型 458）
3. 模拟学习：对 pass1 的 MEDIUM/SEASON_PACK 等结果按 folder 聚类生成确认输入，写入 parse_memory（学习走 T2 入口）
4. pass2 复测：全量重跑，统计 L2 命中率、档位迁移数量（MEDIUM→HIGH）、missing_fields 收敛、每条耗时；对照粗原型「309 key 覆盖 94.3%」
5. 附加观察（只记录不修 L1）：
   - PR3 遗留 3 条 returned_none 样本在 L2 后的行为
   - season-residue 疑似误杀样本的 L2 兜底情况
   - 「若 HIGH 也查记忆」的潜在额外命中率（离线估算，不实现）
6. 单元测试在无外部快照时自动 skip（参考 test_validate_l1_corpus.py 的 skip 写法）
7. 脚本对单条异常容错：记录 failed 继续跑，统计体现 failed 数量

输出统计 JSON：
total / pass1: {l2_miss, level1_keys, ...} / pass2: {l2_hit, medium_to_high, ...} / 信任分分布 / 耗时

验收：
- uv run pytest 全绿（无快照环境 skip）
- 真实快照 2606 条左右两遍无异常（如实际行数不同，报告真实数字）
- 输出确定性统计 JSON
- uv run ruff check 零错误
- uv run pyright 零错误
- 不修改 L1/L2 任何现有代码（发现 bug 只记录）
- git status 干净

完成后提交并推送：
git push -u origin task/pr4-l2-real-corpus

最终报告：分支名、commit hash、两遍完整统计 JSON、与粗原型指标对照表、异常/观察样本列表、验收命令真实结果。
```

---

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

1. 每个任务完成后：核对分支 diff 是否越界（只含允许清单文件）→ 合并进 v2 → **在主仓库跑联动测试**（全量 pytest + ruff + pyright；T5 后加 CLI 真实样本实测）→ push v2。
2. T2/T3/T4 合并前可先在临时 worktree 做三路预检合并（PR3 曾用此法提前发现无冲突）。
3. T1 合并后、开 T2/T3/T4 前，先审一遍 T1 报告中的 public API 与契约决策是否一致。
4. 任何任务报告「契约决策不可行」时，停下 renegotiate，不要让后续任务在漂移的契约上继续。
