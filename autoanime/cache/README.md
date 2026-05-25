# `autoanime.cache` 使用说明

本包负责 **Schema v2 多文件持久化缓存**：整理进度、剧名/别名索引、API 响应分区，以及别名写入校验与审计。业务代码应优先通过下文「推荐入口」调用，避免直接读写 `.cache` 下的 JSON。

- **`cache_doctor` 重命名与剧名纠偏**（七子命令全说明、真实 PowerShell 与 `organization` 例）：专题目录 [cache_doctor_重命名与剧名纠偏_使用说明.md](cache_doctor_重命名与剧名纠偏_使用说明.md)。
- 更完整的设计说明见项目文档：[docs/10_缓存Schema_v2设计.md](../../docs/10_缓存Schema_v2设计.md)。

---

## 1. 磁盘布局（Schema v2）

在 `config.ini` 的 `CACHE_DIR` 目录下（默认项目根目录的 `.cache/`）：

| 文件 | 作用 |
| --- | --- |
| `cache_meta.json` | `schema_version`、各子文件 sha256/条目统计、`legacy_archive`（若有） |
| `organization.json` | 每部番整理进度（`ShowOrganizationIndex`），永不过期 |
| `titles.json` | 中文主名表（`CanonicalTitleIndex`）+ 别名表（`TitleAliasIndex`，含 `trust_level`），永不过期 |
| `api_responses.json` | TMDB / Bangumi / 扩展组等 API 缓存，按条目 TTL 过期 |
| `pollution_audit.jsonl` | 别名写入成功/拒绝等审计行（仅追加） |
| `manual_title_whitelist.json` | 手工剧名白名单（与 v1 相同） |
| `backups/api_cache_legacy_<RunID>.json` | 首次迁移时从旧 `api_cache.json` 归档而来（若存在） |

**判定 v2 是否生效**：存在 `.cache/cache_meta.json` 且其中 `schema_version` 为 `2`。

**旧版单文件**：若仅有 `.cache/api_cache.json` 且无 `cache_meta.json`，则仍按旧版整块 JSON 读写（与 `AutoAnimeMv.py` 行为一致）。

---

## 2. 生命周期（何时加载/保存）

- **加载**：`autoanime.cli.Start_PATH()` → `Auxiliary_LoadPersistentCache()`  
  内部会先 `Auxiliary_MigrateCacheToV2IfNeeded()`（无 `cache_meta` 时归档旧 `api_cache.json` 并初始化空 v2 子文件），再读入内存到 `state.PersistentApiCache`。
- **保存**：`main()` 的 `finally` 中 `Auxiliary_SavePersistentCache()`  
  v2 下只写入 **有改动的子文件**（`state.CacheSubfileDirty`），不会每次全量重写三个 JSON。
- **定时刷盘**：`Auxiliary_MaybeFlushPersistentCache()`（受 `CACHE_FLUSH_INTERVAL_SECONDS` 控制）。

---

## 3. 推荐入口（业务侧）

### 3.1 通用键值缓存（与旧代码签名一致）

适合：TMDB/Bangumi 等 API 结果、Show 记录、Canonical 记录等。

```python
from autoanime.cache.persistent import (
    Auxiliary_LoadPersistentCache,
    Auxiliary_SavePersistentCache,
    Auxiliary_GetPersistentCache,
    Auxiliary_SetPersistentCache,
    Auxiliary_MaybeFlushPersistentCache,
)

# 读取（过期 API 条目会自动删内存键并标记对应子文件 dirty）
value = Auxiliary_GetPersistentCache("TMDB", "Some English Query")

# 写入（自动带 ts / ttl，并标记 api_responses 子文件 dirty）
Auxiliary_SetPersistentCache("TMDB", "Some English Query", "中文标题")
```

**`CacheGroup` 与落盘子文件对应关系**（实现见 `persistent.py`）：

| CacheGroup | 子文件 | TTL |
| --- | --- | --- |
| `ShowOrganizationIndex` | `organization.json` | 永不过期 |
| `CanonicalTitleIndex` | `titles.json`（`canonicals`） | 永不过期 |
| `TitleAliasIndex` | `titles.json`（`aliases`） | 永不过期 |
| `TMDB` | `api_responses.json` → `tmdb.titles` | 默认 86400 秒 |
| `TMDB_EN` | `api_responses.json` → `tmdb.titles_en` | 同上 |
| `TMDBTvSeriesId` | `api_responses.json` → `tmdb.tv_series` | 默认 604800 秒 |
| `TMDBTvSeasons` | `api_responses.json` → `tmdb.tv_seasons` | 同上 |
| `Bangumi` | `api_responses.json` → `bangumi.titles` | 默认 86400 秒 |
| `BGM` | `api_responses.json` → `ext.BGM` | 默认 86400 秒 |
| 其它未列组名 | `api_responses.json` → `ext.<组名>` | 默认 `CACHE_TTL_SECONDS` |

`Auxiliary_GetPersistentCache` 的返回值始终是 **业务 `value`**（例如 TMDB 的中文标题字符串、Show 的一条 dict），不会把 `{"value","ts","ttl"}` 整包返回给调用方。

### 3.2 别名（带信任等级，必须走 canonical）

**不要**对 `TitleAliasIndex` 直接 `Auxiliary_SetPersistentCache`，否则会绕过校验与审计。

```python
from autoanime.cache.canonical import Auxiliary_LinkAliasToCanonical

# 由 SourceTag 推导默认 trust_level；也可显式传入 trust_level=
Auxiliary_LinkAliasToCanonical("Sousou no Frieren", "葬送的芙莉莲", SourceTag="TMDB")
```

内部会调用 `Auxiliary_ValidateAliasWrite`；失败则只写 `pollution_audit.jsonl`（`type=alias_rejected`），不落盘别名。

### 3.3 剧名主记录与解析

```python
from autoanime.cache.canonical import (
    Auxiliary_UpsertCanonicalTitle,
    Auxiliary_GetCanonicalTitleRecord,
    Auxiliary_GetAliasCanonicalID,
    Auxiliary_ResolveCanonicalTitleByAliases,
)
```

### 3.4 整理进度（ShowOrganizationIndex）

```python
from autoanime.cache import show_index

show_index.Auxiliary_ShowHasOrganizedEpisode(canonical_id, se, ep)  # -> (has_tag, expected_dst_path_or_none)
show_index.Auxiliary_ShowMarkOrganizedEpisode(..., DstPath=dst)   # 成功落盘后写入 expected_dst
show_index.Auxiliary_ShowClearOrganizedEpisode(...)                # 自愈：目标缺失时剔除 tag
```

### 3.5 手工白名单

```python
from autoanime.cache.manual_whitelist import Auxiliary_LoadManualWhitelist

Auxiliary_LoadManualWhitelist(force=True)
```

---

## 4. 迁移（v1 单文件 → v2）

```python
from autoanime.cache.migrate import Auxiliary_MigrateCacheToV2IfNeeded

# 幂等：已有 cache_meta.json 则直接返回 None
archive_path = Auxiliary_MigrateCacheToV2IfNeeded()
# 若归档了旧文件，返回 str 路径；否则 None
```

首次迁移时：旧 `.cache/api_cache.json` 会移动到 `.cache/backups/api_cache_legacy_<CurrentRunID>.json`，并生成空的 `organization.json` / `titles.json` / `api_responses.json`（**零数据冷启动**，由你之前在计划里选择的策略决定）。

**回滚到旧单文件**：删除 v2 的 `cache_meta.json` 与子 JSON，把 `backups/` 里备份移回 `.cache/api_cache.json`（仅在使用旧 `AutoAnimeMv.py` 且未改代码路径时有效）。

---

## 5. 信任等级与别名校验（`trust.py`）

| 默认等级 | 典型 `SourceTag` / 条件 |
| --- | --- |
| 100 | `manual` / 白名单 |
| 90 | `BGM` |
| 80 | `Bangumi`、`TMDB` |
| 60 | `openai_identify`、`OpenAI` |
| 40 | 冲突降级、未知来源等 |

`Auxiliary_ValidateAliasWrite` 会拒绝例如：**别名 key 超过 `trust.ALIAS_KEY_MAX_LEN`（当前 100）字符**、纯数字、连续 4 位以上数字噪声、canonical 尚无可用主名、`canonical.locked` 且 trust&lt;100、已有更高 trust 的别名等。

手动校验：

```python
from autoanime.cache.trust import Auxiliary_ValidateAliasWrite, Auxiliary_TrustLevelFromSource

ok, reason = Auxiliary_ValidateAliasWrite("sousounofrieren", "葬送的芙莉莲", 80, new_source="TMDB")
tl = Auxiliary_TrustLevelFromSource("Bangumi")
```

---

## 6. 审计（`audit.py`）

```python
from autoanime.cache.audit import Auxiliary_AppendPollutionAudit

Auxiliary_AppendPollutionAudit("custom_event", {"note": "..."})
```

业务上别名相关事件一般由 `canonical.Auxiliary_LinkAliasToCanonical` 自动写入 `alias_written` / `alias_rejected`。

---

## 7. 底层路径与原子写（`v2_data.py`）

扩展脚本若需直接读子文件，可用：

```python
from autoanime.cache.v2_data import Auxiliary_GetV2DataDir, Auxiliary_GetV2SubfilePath

base = Auxiliary_GetV2DataDir()              # 即 CACHE_DIR
org = Auxiliary_GetV2SubfilePath("organization")  # organization.json
```

`Auxiliary_AtomicWriteJson` 用于先写 `.tmp` 再 `replace`，避免半截 JSON。

---

## 8. 命令行运维（`scripts/cache_doctor.py`，完整指令）

**前置**：在**项目根目录**（含 `scripts/` 与 `autoanime/`）下执行；Python 3.8+。

**查看帮助**（列出所有参数）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --help
```

### 8.1 全局参数

| 参数 | 用途 | 示例 |
| --- | --- | --- |
| `--cache-dir <路径>` | 缓存根目录（默认项目下 `.cache`）；相对路径相对**项目根** | `--cache-dir D:\AnimeData\.cache` |

### 8.2 子命令（七选一，互斥）

以下七个开关**必须且只能**选一个。

| 子命令 | 用途 | 额外必填参数 | 风险 |
| --- | --- | --- | --- |
| `--inspect` | 检查是否为 Schema v2：打印 `cache_meta.json`、`organization.json`、`titles.json`、`api_responses.json`、`pollution_audit.jsonl` 的大小、sha256 前缀、条目统计；`titles.json` 会统计「别名键长度大于 `ALIAS_KEY_MAX_LEN`」「trust 小于 50」等污染嫌疑 | 无 | 只读 |
| `--export-audit` | 从 `pollution_audit.jsonl` 导出 **时间戳 ≥ 指定日期 00:00** 的 JSON 行到标准输出 | **`--since YYYY-MM-DD`** | 只读 |
| `--revert` | 按某条审计记录**撤销一次别名写入**：仅从 `titles.json` 的 `aliases` 中删除对应键（仅支持 `type=alias_written`） | **`--audit-id <UUID>`**（与审计行中 `audit_id` 一致） | **修改** `titles.json` 与 `cache_meta.json` 中 titles 统计 |
| `--rebuild-from-organization` | 用 `organization.json` 的 `records` **覆盖重写** `titles.json`：重建 `canonicals` + 从 zh/en/romaji 生成别名（归一后长度 ≤`ALIAS_KEY_MAX_LEN` 才写入） | 无 | **覆盖** `titles.json`；API 缓存不动 |
| `--set-whitelist` | 写入/合并 **`manual_title_whitelist.json`**（`--alias` + `--zh` 经归一化后的键值）。可选与 `--apply-rename` 联用 | **`--alias`**、**`--zh`**；若加 `--apply-rename` 还需 **`--canonical-id`** 或 **`--old-title-zh`** 以唯一定位 `organization` 一条 | 只写白名单，或**再改** `organization`/`titles` 与**移动**已整理媒体（见下） |
| `--set-title-zh` | 将 **`titles.json` → `canonicals[id].zh`** 与 **`organization` 对应 `title_zh`** 同步为 `--zh`；可选 `--apply-rename` | **`--canonical-id`**、**`--zh`** | **改** `titles`+`organization`；加 `--apply-rename` 时可能 **move 文件** |
| `--rename-episodes` | **仅**使用 `episode_last_dst` 做与 `Sorting_Mv` 一致的重命名**计划**或**执行**；**默认只打印、不改 JSON 不动盘**；加 `--apply-rename` 再 move 并回写主名。七子命令**完整**说明与例见 [cache_doctor_重命名与剧名纠偏_使用说明.md](cache_doctor_重命名与剧名纠偏_使用说明.md) | **`--zh`**，以及 **`--canonical-id`** 或 **`--old-title-zh`** | 加 `--apply-rename` 时**移动**媒体并改缓存 |

### 8.2.1 共用重命名逻辑（`autoanime/episode_dst_rename.py`）

当联用 **`--apply-rename`**（或 **`--set-title-zh` + `--apply-rename`**）时，工具按 `organization` 中该条 **`episode_last_dst`**（`SxxEyy` → 上次落盘绝对路径）计算目标路径，规则与主程序 **`Sorting_Mv`** 一致（`--naming-style` / `--no-use-title-to-ep` 对齐 `NAMING_STYLE` / `USETITLTOEP`）。**`--rename-episodes` 且未**加 `--apply-rename` 时：只打印计划，不修改 `organization` / `titles` / 磁盘。其余：`--set-title-zh` 不加 `--apply-rename` 时仅改 JSON；`--set-whitelist` 只写白名单（除非再配 `--apply-rename`）。

### 8.2.2 与 `--apply-rename` 相关参数

| 参数 | 作用 |
| --- | --- |
| `--apply-rename` | 对 `episode_last_dst` 中列出的文件执行 `shutil.move`，并回写同条 `organization` 内的路径与 `title_zh`（及 `titles` 中 `canonical.zh`） |
| `--naming-style default\|emby` | 与主程序 `NAMING_STYLE` 一致（默认 `default`） |
| `--no-use-title-to-ep` | 对应主程序 `USETITLTOEP=False`（默认不加此项，与 `USETITLTOEP=True` 一致，即 `SxxEyy.剧名` 风格） |
| `--old-title-zh` | 用**归一后**的 `title_zh` 在 `organization.records` 中**唯一条**；用于 `set-whitelist+apply` 或 **`rename-episodes`** |
| `--canonical-id` | 在 `organization.records` 中按键或 `record.canonical_id` 查找；**`--set-title-zh` 必填**；`rename-episodes` 与 `set-whitelist+apply` 时可与 `--old-title-zh` 二选一 |

### 8.3 调用实例（PowerShell）

**实例 1：默认目录快速体检**

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --inspect
```

若输出「未找到有效的 cache_meta.json」，说明仍是**旧版单文件** `api_cache.json` 布局；需先跑一次新入口触发迁移，或仍用旧脚本维护单文件。

**实例 2：自定义缓存目录（例如库与项目分离）**

```powershell
python scripts\cache_doctor.py --inspect --cache-dir "D:\Media\.cache"
```

**实例 3：导出 2026-04-01 以来的审计事件（每行一条 JSON，可重定向到文件）**

```powershell
python scripts\cache_doctor.py --export-audit --since 2026-04-01 --cache-dir .\.cache > audit_export.jsonl
```

说明：匹配条件为事件内 `ts`（Unix 时间戳）≥ 该日 0 点；末尾 `# exported N events...` 在**标准错误**，不会进重定向文件。

**实例 4：撤销某次错误别名（先 export 找到 `audit_id`）**

```powershell
# 1) 导出近期审计，人工找到 type=alias_written 且 alias_key 不对的那条，复制 audit_id
python scripts\cache_doctor.py --export-audit --since 2026-04-20

# 2) 撤销（将 UUID 换成真实 audit_id）
python scripts\cache_doctor.py --revert --audit-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

撤销后需**重启**正在运行的整理进程，或下次启动会重新从磁盘加载。

**实例 5：titles 全坏、organization 仍可信时，从整理进度重建 titles（先备份）**

```powershell
Copy-Item .\.cache\titles.json .\.cache\titles.json.bak
python scripts\cache_doctor.py --rebuild-from-organization --cache-dir .\.cache
```

**实例 6：写入手工白名单（不移动磁盘）**

```powershell
python scripts\cache_doctor.py --set-whitelist --alias "某RAW关键词" --zh "正确中文剧名" --cache-dir .\.cache
```

**实例 7：修正识别用中文名（只改 `titles.json` + `organization.json`）**

```powershell
python scripts\cache_doctor.py --set-title-zh --canonical-id "摩绪" --zh "摩绪" --cache-dir .\.cache
```

**实例 8：按 `episode_last_dst` 在库中重命名/迁移，并回写缓存（显式写磁盘，请先备份；首次可加 `--naming-style` 等与主程序一致）**

```powershell
python scripts\cache_doctor.py --set-title-zh --canonical-id "摩绪" --zh "新剧名" --apply-rename --cache-dir .\.cache
```

**实例 9：先写白名单，再对唯一匹配 `title_zh` 的一部番做迁移（需能唯一定位，否则改用 `--canonical-id`）**

```powershell
python scripts\cache_doctor.py --set-whitelist --alias "x" --zh "正确名" --apply-rename --old-title-zh "旧库中展示名" --cache-dir .\.cache
```

**实例 10：仅重命名已整理集（只预览，不改 JSON、不 move）**

```powershell
python scripts\cache_doctor.py --rename-episodes --canonical-id "摩绪" --zh "摩绪 MAO" --cache-dir .\.cache
```

**实例 11：仅重命名子命令，确认预览后再执行真迁移（迁移前务必备份）**

```powershell
python scripts\cache_doctor.py --rename-episodes --canonical-id "摩绪" --zh "摩绪 MAO" --apply-rename --cache-dir .\.cache
```

更细步骤、**全部子命令**与 Emby/带引号剧名等例见 [cache_doctor_重命名与剧名纠偏_使用说明.md](cache_doctor_重命名与剧名纠偏_使用说明.md)。

---

## 9. `scripts/` 目录各文件用途

| 文件 | 用途 | 典型场景 | 调用示例 | 备注 |
| --- | --- | --- | --- | --- |
| [scripts/cache_doctor.py](../../scripts/cache_doctor.py) | Schema v2 缓存诊断；**`--rename-episodes`**、白名单/改主名/审计等共七子命令 | 排障、别名回滚、白名单、改主名、按已整理目标路径批量改名 | 见 §8 与 [cache_doctor_重命名与剧名纠偏_使用说明.md](cache_doctor_重命名与剧名纠偏_使用说明.md) | 依赖项目根在 `sys.path`；`--apply-rename` 会**移动**媒体 |
| [scripts/verify_refactor_with_real_data.py](../../scripts/verify_refactor_with_real_data.py) | **集成自测**：用固定日志样本 + 缓存样本（脚本内路径）验证 ShowIndex 自愈、CLI 单文件、OpenAI 回退 mock、流水线 dry-run；**克隆缓存到临时目录**，不污染工作区 | CI 或本地回归、改 `pipeline`/`cache` 后快速验收 | `python scripts\verify_refactor_with_real_data.py` | 默认读 `logs/AutoAnime_operations_20260421_204030.json` 与 `.cache/api_cache.json`；若你删了这些文件需改脚本常量 |
| [scripts/normalize_api_cache_cn_punct.py](../../scripts/normalize_api_cache_cn_punct.py) | **旧版单文件** `api_cache.json`：把含中文的标题/键中的半角标点批量换成中文全角（冒号、引号等），减少「同名不同标点」分叉 | 仍在使用 **v1 单文件**且需统一中文标点时 | 先编辑脚本内 `CACHE_PATH` 指向你的 `api_cache.json`，再 `python scripts\normalize_api_cache_cn_punct.py` | **原地覆盖**目标文件；路径当前写死在脚本里，使用前务必改对；**不适用于**已拆分的 v2 `titles.json`（需另写或手工处理） |

**与主程序的关系**：日常整理用 `python AutoAnimeMv2.py ...`；`cache_doctor` 与 `verify_*` 为运维/测试工具，不参与正常整理链路。

---

## 10. 自动化测试

- `tests/test_cache_schema_v2.py`：路由、信任、原子写、迁移、兼容、`cache_doctor` 等。
- `tests/test_episode_dst_rename.py`：`episode_last_dst` 重命名计划与路径计算。

```bash
python -m unittest tests.test_cache_schema_v2 tests.test_episode_dst_rename -v
```

---

## 11. 常见问题

**Q：`Auxiliary_GetPersistentCache('TitleAliasIndex', key)` 返回什么？**  
A：返回 **canonical_id 字符串**（与 v1 行为一致）。磁盘上 `titles.json` 的 `aliases` 可能存的是带 `trust_level` 的对象，加载时会展开为内存中的 `value` 字段。

**Q：为何别名不再接受超长 key？**  
A：防止把整段「文件名归一」写入永久别名表导致污染；长线索应走 OpenAI/季集识别，而不是 alias 表。

**Q：修改了缓存但没退出程序，数据会在磁盘上吗？**  
A：依赖 `Auxiliary_MaybeFlushPersistentCache` 间隔或进程退出时的 `Auxiliary_SavePersistentCache`；调试时可显式调用 `Auxiliary_SavePersistentCache(force=True)`。

---

## 12. 变更记录

| 日期 | 说明 |
| --- | --- |
| 2026-04-23 | 专题目录合并为 [cache_doctor_重命名与剧名纠偏_使用说明.md](cache_doctor_重命名与剧名纠偏_使用说明.md)（`inspect` / 审计 / `revert` / `rebuild` / 白名单 / `set-title-zh` / `rename-episodes` 全表与实机例）；原 `apply_rename_按已整理目标重命名.md` 删除。 |
