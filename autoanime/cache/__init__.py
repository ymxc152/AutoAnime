"""
autoanime 持久化缓存与索引子包（Schema v2：多子文件 + 路由 + 增量刷盘）

使用说明与示例见同目录 [README.md](README.md)；设计文档见 `docs/10_缓存Schema_v2设计.md`。

- `persistent`   : `Auxiliary_Load/Save/Get/SetPersistentCache`、`MaybeFlush`
- `migrate`      : `Auxiliary_MigrateCacheToV2IfNeeded`
- `v2_data`      : v2 路径、空结构、原子写 JSON
- `trust`        : 别名信任等级与 `Auxiliary_ValidateAliasWrite`
- `audit`        : `pollution_audit.jsonl` 追加
- `canonical`    : 剧名主记录 + 别名链接（应走 `LinkAlias`，勿直接 Set 别名）
- `show_index`   : ShowOrganizationIndex（已整理集 + episode_last_dst）
- `manual_whitelist` : 手工剧名白名单
"""
