# 缓存 Schema v2 设计

> 开发者速查（API 示例、命令表、FAQ）：[`autoanime/cache/README.md`](../autoanime/cache/README.md)

## 功能背景
将原单文件 `api_cache.json` 拆为 `.cache` 下多子文件，降低高写入子集与大体量 API 响应之间的互相拖累；对外仍通过 `Auxiliary_GetPersistentCache` / `Auxiliary_SetPersistentCache` 访问，业务调用点无感。

## 功能边界
- 单文件回退：删除 v2 元数据与子文件、将 `backups/api_cache_legacy_*.json` 移回 `api_cache.json` 可恢复旧行为（与旧 `AutoAnimeMv.py` 一致）。
- 不引入 SQLite；审计为 JSONL 仅追加。

## 子文件与路径

| 文件路径 | 类型 | 作用 | 调用方 | 依赖项 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `.cache/cache_meta.json` | JSON | `schema_version`、子文件 sha256/计数、`legacy_archive` | `autoanime/cache/v2_data.py` `Auxiliary_WriteV2CacheMeta` | 各 v2 子文件 | flush 时部分更新 |
| `.cache/organization.json` | JSON | 整理进度 `ShowOrganizationIndex` | `autoanime/cache/persistent.py` 路由 + `show_index` | `state.PersistentApiCache` | 永不过期 |
| `.cache/titles.json` | JSON | `CanonicalTitleIndex` + `TitleAliasIndex` | `persistent` + `canonical` | 同上 | 永不过期；别名含 `trust_level` |
| `.cache/api_responses.json` | JSON | TMDB / Bangumi / 扩展组 / OpenAI 等 | `persistent`、`apis/*` | TTL 配置 | 分区 TTL |
| `.cache/pollution_audit.jsonl` | JSONL | 别名拒绝、成功写入等审计 | `autoanime/cache/audit.py`、工具脚本 | 无 | 仅追加 |
| `.cache/backups/api_cache_legacy_<RunID>.json` | JSON | 首次迁移时旧 `api_cache.json` 备份 | `autoanime/cache/migrate.py` | 原 `.cache/api_cache.json` | 一次性 |

## 路由与对外函数

| 函数/方法 | 所在文件 | 作用 | 入参/出参 | 上下游依赖 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `Auxiliary_MigrateCacheToV2IfNeeded` | `autoanime/cache/migrate.py` | 无 `cache_meta` 时归档旧文件并初始化 v2 空表 | 无；返回 `legacy_archive` 或 `None` | `state`、v2 路径 | 与 `LoadPersistentCache` 内调用幂等 |
| `Auxiliary_LoadPersistentCache` | `autoanime/cache/persistent.py` | 先 migrate 再按 v1/v2 加载内存 | 无 | 全局 `state` |  |
| `Auxiliary_SavePersistentCache` | 同上 | v2 只 flush `CacheSubfileDirty` 为真的子文件 | `force: bool` | 磁盘子文件、`cache_meta` | 退出时 `force=False` 即子文件粒度 |
| `Auxiliary_GetPersistentCache` / `Auxiliary_SetPersistentCache` | 同上 | 与旧签名一致，按 `CacheGroup` 路由子文件与路径 | 同历史 | 剧名/Show/API 全链路 |  |
| `Auxiliary_ValidateAliasWrite` | `autoanime/cache/trust.py` | 写入别名前校验 | 返回 `(allow, reason)` | `canonical` | 失败不入库 |
| `cmd_inspect` / 各子命令 | `scripts/cache_doctor.py` | 只读/修复工具 | 见命令表 | 直接读 `.cache` 下文件 |  |

## 信任等级（摘要）

| 等级 | 典型来源 | 覆盖规则（摘要） |
| --- | --- | --- |
| 100 | 手动手名单 | 可覆盖；自动来源不可改 locked |
| 80 | TMDB / Bangumi | 可覆盖 ≤80 |
| 60 | OpenAI 推断 | 可覆盖 ≤60 |
| — | 校验拒绝 | 仅 `pollution_audit.jsonl` 记录，不落 titles |

## 启动与退出链路
- 启动：`autoanime/cli.py` 中 `Start_PATH` 在 `Auxiliary_LoadPersistentCache` 之前显式调用 `Auxiliary_MigrateCacheToV2IfNeeded`；`LoadPersistentCache` 内会再次调用（幂等）。
- 退出：`main` 的 `finally` 中 `Auxiliary_SavePersistentCache(force=False)`，v2 下仅写脏子文件。

## 命令表
| 调试命令 | 执行位置 | 用途 | 示例 | 风险 |
| --- | --- | --- | --- | --- |
| `python scripts/cache_doctor.py --inspect` | 项目根 | 子文件大小、sha256、条目、别名异常键 | `python scripts/cache_doctor.py --inspect --cache-dir .cache` | 只读 |
| `python scripts/cache_doctor.py --export-audit --since YYYY-MM-DD` | 项目根 | 按时间筛审计行 | 同左加 `--since 2026-01-01` | 只读 |
| `python scripts/cache_doctor.py --revert --audit-id <UUID>` | 项目根 | 对 `type=alias_written` 撤销 titles 中别名字段 | 需与审计中 `audit_id` 一致 | 修改 `titles.json` |
| `python scripts/cache_doctor.py --rebuild-from-organization` | 项目根 | 从 `organization.json` 重建最小 `titles.json` | 先备份 | **覆盖** `titles.json` |

## 测试
- `tests/test_cache_schema_v2.py`：路由、信任、原子写、迁移、兼容、doctor 等 10 组用例。

## 变更记录
| 日期 | 修改来源 | 修改原因 | 影响范围 |
| --- | --- | --- | --- |
| 2026-05-01 | Agent | 别名键长度上限 30→100（`trust.ALIAS_KEY_MAX_LEN`），减少长罗马音 `alias_key_too_long`；inspect/rebuild 与校验同源常量 | `trust.py`、`cache_doctor`、`README`、`docs/05`、`tests/test_cache_schema_v2` |
| 2026-04-23 | Agent | 落地 v2 文档、CLI 迁移钩子、cache_doctor、单测与索引 | `docs/10`、CLI、scripts、`tests` |
| 2026-04-23 | Agent | 增补 `autoanime/cache/README.md` 使用说明；本文档顶部交叉引用 | `autoanime/cache/README.md` |
| 2026-04-23 | Agent | README 增补 `cache_doctor` 全参数/子命令与实例、`scripts/` 目录说明 | `autoanime/cache/README.md` |
| 2026-04-23 | Agent | 信任校验区分「同 canonical 低信任重复写入」与真冲突；`Upsert` 别名循环按归一化键去重，减少单文件整理时审计 JSONL 噪声 | `autoanime/cache/trust.py`、`autoanime/cache/canonical.py` |
