# `cache_doctor`：重命名、剧名纠偏与缓存运维（使用说明）

> 本文是 **`scripts/cache_doctor.py`** 的**专题目录**，覆盖 **Schema v2** 下与「已整理资源路径」「别名校正」「剧名主名」相关的**全部子命令**与**共用参数**；与主程序**同名规则**重命名时，实现见 `autoanime/episode_dst_rename.py`（与 `Sorting_Mv` 一致）。**总览与 API 式入口**另见同目录 [README.md](README.md) §8；设计背景见 [docs/10_缓存Schema_v2设计.md](../../docs/10_缓存Schema_v2设计.md)。  
> **执行位置**：下例均在**项目根**（含 `scripts/` 与 `autoanime/`）的 PowerShell 中运行；`--cache-dir` 相对路径相对项目根。  
> **数据示例**：为贴近真实库，下文物有所涉路径来自**示例**；你本机以 `.cache/organization.json` 为准。下面 JSON 可与你仓库中结构对照。

---

## 1. 前置条件

| 项 | 说明 |
| --- | --- |
| Python | 3.8+，且可 `import autoanime`（项目根在解释器路径中，直接 `python scripts\cache_doctor.py` 时脚本会注入项目根） |
| Schema v2 | `.cache/cache_meta.json` 存在且 `schema_version` 为 `2`；否则 `inspect` 会提示单文件 `api_cache.json` 老布局，本文多数子命令针对 v2 |
| 备份 | 任何**写**操作（`--revert`、`--rebuild`、`--apply-rename`、白名单/改剧名等）前建议备份 `.cache/` 下相关 JSON 与媒体库 |

---

## 2. 全局与共用参数

| 参数 | 适用 | 说明 |
| --- | --- | --- |
| `--cache-dir <路径>` | 全部 | 缓存根，默认**项目下** `.cache`；可写 `.\.cache` 或绝对路径。 |
| `--zh` | `set-whitelist` / `set-title-zh` / `rename-episodes` | 白名单**值**、或**新**中文主名（经与主程序相同归一化后落盘/计算路径）。 |
| `--apply-rename` | `set-whitelist`、`set-title-zh`、`rename-episodes` | **实际**对 `episode_last_dst` 中文件做 `shutil.move` 并回写 `organization`/`titles`；未加时行为见下表各子命令。 |
| `--naming-style default\|emby` | 含 `apply-rename` 的各子命令、`rename-episodes` | 与主程序 `NAMING_STYLE` 一致。 |
| `--no-use-title-to-ep` | 同上 | 对应主程序 `USETITLTOEP=False`（集文件名不拼剧名，如 `S01E01.mkv`）。 |
| `--canonical-id` | `set-title-zh`（必填）；`set-whitelist`+`apply-rename`；`rename-episodes` 二选一 | 在 `organization.records` 中按键名或 `record.canonical_id` 命中一条。 |
| `--old-title-zh` | `set-whitelist`+`apply-rename` 或 `rename-episodes` 二选一 | 用**归一后**的 `title_zh` 在 `records` 中**唯一条**匹配。 |
| `--alias` | 仅 `set-whitelist` | 白名单 **key** 原始串，脚本内会 `Auxiliary_NormalizeAliasKey`。 |
| `--since` | 仅 `export-audit` | 必填，格式 `YYYY-MM-DD`（从该日 0 点起按事件 `ts` 过滤）。 |
| `--audit-id` | 仅 `revert` | 必填，`pollution_audit.jsonl` 中某行 `audit_id`（仅支持撤销 `type=alias_written`）。 |

---

## 3. 七子命令总览

以下七个开关在一条命令中**互斥，必须选其一**。

| 子命令 | 作用摘要 | 是否改磁盘上媒体/JSON | 下文章节 |
| --- | --- | --- | --- |
| `--inspect` | 看各 v2 子文件大小、sha256、条数、别名嫌疑等 | 只读 | [§4](#4-inspect) |
| `--export-audit` | 按日期导出 `pollution_audit.jsonl` 行到 stdout | 只读 | [§5](#5-export-audit) |
| `--revert` | 按 `audit_id` 从 `titles.json` 的 `aliases` 删一条**已写入**的别名 | 改 `titles` + `cache_meta` | [§6](#6-revert) |
| `--rebuild-from-organization` | 用 `organization` **整表覆盖**重生成 `titles` | **覆盖** `titles.json` | [§7](#7-rebuild-from-organization) |
| `--set-whitelist` | 写/合并 `manual_title_whitelist.json` | 可只写白名单，或加 `--apply-rename` 再迁盘 | [§8](#8-set-whitelist) |
| `--set-title-zh` | 把 `titles.canonical[id].zh` 与 `organization` 的 `title_zh` 同步为 `--zh` | 默认**必写**两 JSON；加 `--apply-rename` 再 move | [§9](#9-set-title-zh) |
| `--rename-episodes` | **只**用 `episode_last_dst` 做与 `Sorting` 一致的迁盘**计划/执行** | 默认**只预览**；加 `--apply-rename` 才写盘与两 JSON | [§10](#10-rename-episodes) |

---

## 4. `--inspect`

**用途**：确认 v2 是否就绪、子文件是否齐全、快速扫 `titles` 中可疑别名规模。

**实际例**（默认 `.cache`）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --inspect
```

指定缓存目录例：

```powershell
python scripts\cache_doctor.py --inspect --cache-dir "D:\Project\AutoAnime\.cache"
```

若输出首行提示**未找到有效的 cache_meta.json**，表示仍是旧单文件 `api_cache.json` 布局，需先走迁移或新入口；详见 [README](README.md)。

---

## 5. `--export-audit`

**用途**：从 `pollution_audit.jsonl` 导出**时间戳 ≥ 指定日期 0 点**的 JSON 行，便于查 `alias_written` / `alias_rejected` 与 `audit_id`（为 `--revert` 准备）。  
**必填**：`--since YYYY-MM-DD`。

**实际例**（打到文件；统计行在 stderr 不进文件）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --export-audit --since 2026-04-20 --cache-dir .\.cache 2> audit_meta.txt | Out-File -Encoding utf8 audit_lines.jsonl
```

若只终端查看：

```powershell
python scripts\cache_doctor.py --export-audit --since 2026-04-01 --cache-dir .\.cache
```

人工找到 `"type":"alias_written"` 且别名字段不对的那行，复制其中 `audit_id`（UUID）。

---

## 6. `--revert`

**用途**：**仅**撤销一次**已成功写入**的别名：从 `titles.json` 的 `aliases` 中删除与审计记录 `alias_key` 对应项；不碰 `organization`、不移动媒体。  
**必填**：`--audit-id <UUID>`。

**实际例**（把 UUID 换成上一步从审计里复制的 `audit_id`）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --revert --audit-id "3fa85f64-5717-4562-b3fc-2c963f66afa6" --cache-dir .\.cache
```

成功后需**重启**正在跑的整理进程，或下次启动以重新从磁盘加载缓存。

---

## 7. `--rebuild-from-organization`

**用途**：`titles` 全坏、但 `organization` 仍可信时，**按** `organization.json` 的 `records` **覆盖重写** `titles.json` 的 `canonicals` 与由 zh/en/romaji 推的短 `aliases`（见脚本 `cmd_rebuild`）。**会覆盖**当前 `titles.json`，请先备份。

**实际例**：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
Copy-Item .\.cache\titles.json .\.cache\titles.json.bak
python scripts\cache_doctor.py --rebuild-from-organization --cache-dir .\.cache
```

---

## 8. `--set-whitelist`

**用途**：在 **`.cache/manual_title_whitelist.json`** 中**合并**一条「归一化别名键 → 归一化中文主名」；识别链会**优先**用白名单值（与主程序 `Auxiliary_GetManualWhitelistedTitle` 一致）。  
**必填**：`--alias`、`--zh`。  
**可选** `--apply-rename`：在写白名单之后，对**同一条** `organization` 记录做与 `rename-episodes` 相同的 move + 主名回写；此时**另外必填** `--canonical-id` 或 `--old-title-zh`（唯一定位一条记录）。

**实际例 8.1 只改白名单、不动盘、不动 `organization`/`titles` 主名流程以外的逻辑**（适合：先加映射防再认错的场景）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --set-whitelist --alias "MAO" --zh "摩绪" --cache-dir .\.cache
```

**实际例 8.2** 写白名单后，按 `canonical_id` 对「摩绪」这一部做迁盘+缓存对齐（**先**确认 `episode_last_dst` 中路径在磁盘上仍存在；**执行前务必备份**）：

```powershell
python scripts\cache_doctor.py --set-whitelist --alias "某 RAW 名" --zh "摩绪" --apply-rename --canonical-id "摩绪" --cache-dir .\.cache
```

**实际例 8.3 不记 `canonical_id` 键，用当前 `title_zh` 唯一定位**（例如全库只有一条归一后等于 `出租女友` 的 `title_zh`）：

```powershell
python scripts\cache_doctor.py --set-whitelist --alias "rent" --zh "理想女友" --apply-rename --old-title-zh "出租女友" --cache-dir .\.cache
```

若存在两条以上归一后相同的 `title_zh`，会**无法**唯一定位，应改用 `--canonical-id` 指向 `records` 的键，例如 `出租女友`。

---

## 9. `--set-title-zh`

**用途**：把**识别/展示用**中文主名写进 **`titles.json` → `canonicals[canonical_id].zh`** 与 **对应** `organization` 条目的 **`title_zh`**，保持二者一致。  
**必填**：`--canonical-id`、`--zh`。  
**可选** `--apply-rename`：在写出上述 JSON **之前**，若加该开关，会按 `episode_last_dst` 先做 move（与下节 `rename-episodes`+apply 相同逻辑），再写两 JSON。  
**不加** `--apply-rename` 时**仍会**更新两个 JSON 中的主名，**不**对媒体做 move（可能仅在「只修缓存、磁盘下次整理再对」场景使用）。

**实际例 9.1 只改缓存主名、不迁盘**（若某条键为 `无尾熊绘日记`）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --set-title-zh --canonical-id "无尾熊绘日记" --zh "无尾熊绘画日记" --cache-dir .\.cache
```

**实际例 9.2 先按 `episode_last_dst` 把文件迁到「新主名」目录，再写回主名**（**务必先 `--inspect` 与备份**）：

```powershell
python scripts\cache_doctor.py --set-title-zh --canonical-id "摩绪" --zh "摩绪 MAO" --apply-rename --cache-dir .\.cache
```

**实际例 9.3 Emby 用户迁盘+改主名**（与当时整理时 `NAMING_STYLE=emby` 一致时）：

```powershell
python scripts\cache_doctor.py --set-title-zh --canonical-id "无尾熊绘日记" --zh "新展示名" --apply-rename --naming-style emby --cache-dir .\.cache
```

---

## 10. `--rename-episodes`

**用途**：**只**根据 `organization` 中一条的 `episode_last_dst` 计算目标路径，**不**经白名单；默认**只打印** `[SxxEyy] 源 -> 目`，**不**改 `organization`/`titles`、**不** move；**加** `--apply-rename` **才**执行与主程序相同规则的迁盘，并**再**回写 `episode_last_dst`、`title_zh`、`canonical.zh`。

**必填**：`--zh`（新主名），以及 `--canonical-id` 或 `--old-title-zh`（唯一定位一条）。

**实际例 10.1 仅预览**（`records` 键 `摩绪`，目标展示名加后缀供观察路径变化；**无**`--apply-rename`）：

```powershell
cd C:\Users\17645\Desktop\AutoAnime
python scripts\cache_doctor.py --rename-episodes --canonical-id "摩绪" --zh "摩绪 MAO" --cache-dir .\.cache
```

你本地若 `episode_last_dst` 为（摘自真实结构，路径以你机为准）：

```text
"F:\动漫库\摩绪\Season01\S01E03.摩绪.mp4"
```

则标准输出**类似**一行：

```text
[S01E03] F:\动漫库\摩绪\Season01\S01E03.摩绪.mp4 -> F:\动漫库\摩绪 MAO\Season01\S01E03.摩绪 MAO.mp4
```

行末另有说明当前为**预览**、未改缓存与磁盘。

**实际例 10.2 确认预览后真执行**（**危险**，先备份媒体与 `.cache`）：

```powershell
python scripts\cache_doctor.py --rename-episodes --canonical-id "摩绪" --zh "摩绪 MAO" --apply-rename --cache-dir .\.cache
```

**实际例 10.3** 用 `--old-title-zh` 而不用 `--canonical-id`（`title_zh` 归一后唯一，例如「出租女友」）：

```powershell
python scripts\cache_doctor.py --rename-episodes --old-title-zh "出租女友" --zh "理想女友" --cache-dir .\.cache
```

**实际例 10.4** 与主程序 `USETITLTOEP=False`、且 `NAMING_STYLE=emby` 时对齐的预览：

```powershell
python scripts\cache_doctor.py --rename-episodes --canonical-id "无尾熊绘日记" --zh "新番名" --naming-style emby --no-use-title-to-ep --cache-dir .\.cache
```

---

## 11. 仓库内 `organization.json` 样例（便于对照键名与路径形态）

下为与当前工作区**结构一致**的摘录（`episode_last_dst` 为**示例路径**；盘符/文件夹以你本机为准）。键名多等于 `canonical_id` / `title_zh`（经归一后可用于 `--canonical-id`）。

**「摩绪」**（`default`+ 常见 `S01E03.摩绪.mp4` 形式）：

```json
"摩绪": {
  "canonical_id": "摩绪",
  "episode_last_dst": {
    "S01E03": "F:\\动漫库\\摩绪\\Season01\\S01E03.摩绪.mp4"
  },
  "title_zh": "摩绪"
}
```

**「出租女友」**（`Season05` 与 `S05E01`）：

```json
"出租女友": {
  "canonical_id": "出租女友",
  "episode_last_dst": {
    "S05E01": "F:\\动漫库\\出租女友\\Season05\\S05E01.出租女友.mp4"
  },
  "title_zh": "出租女友"
}
```

**长剧名**与 **`.mkv` 集文件**（扩展名参与重命名目标 basename）可在本机用键 `哪里有温柔对待阿宅的辣妹` 与 `想结束这场我爱你的游戏` 对 `organization.json` 中记录跑 `--rename-episodes` 仅预览，核对输出是否指向你盘上的真实路径。

---

## 12. 与 `--apply-rename` 相关的决策（简表）

| 你的目标 | 建议子命令与开关 |
| --- | --- |
| 只加/改一条手工**别名 → 主名** | `--set-whitelist`（**不要**加 `--apply-rename`） |
| 只改**缓存**里的识别用主名、**不**动媒体 | `--set-title-zh`（**不要**加 `--apply-rename`） |
| **只**看迁盘计划、**不**写 JSON | `--rename-episodes` + `--zh` + 定位参数（**不要**加 `--apply-rename`） |
| 动媒体且与 `Sorting` 一致，并同步主名到 `titles` + `organization` | `--rename-episodes` + `--apply-rename`，或 `--set-title-zh` + `--apply-rename`（后者**必然**会先把主名写进两 JSON，带 `--apply-rename` 时还会先 move） |
| 白名单 + 同一部**顺手**迁盘 | `--set-whitelist` + `--apply-rename` + `--canonical-id` 或 `--old-title-zh` |

---

## 13. 常见错误与排障

| 现象 | 可能原因与处理 |
| --- | --- |
| `inspect` 报非 v2 | 无 `cache_meta.json` 或为旧单文件布局；先迁移/新入口见 [README](README.md)。 |
| `export-audit` 无输出 | 该日期后无事件，或 `pollution_audit.jsonl` 为空。 |
| `revert` 报 type 不可撤销 | 非 `alias_written`（如 `alias_rejected` 从未写入，无需撤）。 |
| `rename-episodes` 或带 `--apply-rename` 时报源文件不存在 | `episode_last_dst` 过旧、文件已挪走或手删；先修库或重整理。 |
| 报**不同剧集根目录** | 同一条 `episode_last_dst` 中路径不属同一剧文件夹下；分目录整理后分批跑。 |
| `old-title-zh` 匹配不到或匹配多条 | 归一后 `title_zh` 不唯一或字不一致；用 `--canonical-id` 指定 `records` 的键。 |
| 预览与记忆不符 | `--naming-style`、`--no-use-title-to-ep` 与当时整理**主程序**配置不一致；对齐后重试。 |

---

## 14. 变更记录

| 日期 | 说明 |
| --- | --- |
| 2026-04-23 | 全量重写为七子命令 + 全局参数 + 决策表 + 样例/排障；文件定名为 `cache_doctor_重命名与剧名纠偏_使用说明.md`（原 `apply_rename_按已整理目标重命名.md` 已弃用并删除）。 |
| 2026-04-23 | 初版专题目录：独立 `--rename-episodes` 与摩绪/出租女友实例。 |
