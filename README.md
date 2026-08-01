# AutoAnime

简体中文 | [English](./README_en.md)

AutoAnime 是一个面向 Emby、Jellyfin、Plex 等媒体库的番剧识别与整理工具。仓库已经收敛为单一 v3.1.1 实现：入口为 `AutoAnimeMv3.py`，核心代码位于 `autoanime_v3/`。

## v3 的设计重点

- 同时支持季度/合集文件夹和单个视频文件。
- 本地解析、人工别名目录、可选远程 agent 分层运行，每个结果都保存证据与置信度。
- 默认只预览；只有加 `--apply` 才会改动文件。
- 未达到阈值、季集缺失或证据冲突时进入待确认，不会猜测后移动。
- 同一集的 Baha、friDay、LINETV、CR、V2/V3、无修版、配音版等使用由文件自身决定的稳定标签；没有发布信息的文件使用稳定 `version-xxxxxxxx` 键，支持分批增量加入且不覆盖文件。
- 支持 `link`、`copy`、`move`；move 会先把源原子重命名为同卷 staging，再校验并只删除 staging，避免误删下载器刚重建的新文件。操作日志保存 SHA-256，批次失败会安全回滚已完成项。
- 使用 SQLite 资料库，不依赖分散、难维护的 JSON 缓存。
- 提供 FastAPI + React Web 管理控制台，支持单管理员登录、局域网访问、配置修改、任务审核、计划批准、真实文件执行与安全回滚。

## 项目结构

```text
AutoAnimeMv3.py              CLI 入口
AutoAnimeWeb.py              Web/API 入口
AutoAnimeWorker.py           持久任务 Worker 入口
autoanime_v3/
├─ scanner.py                单文件/季度目录扫描
├─ parser.py                 文件名与季集解析
├─ catalog.py                别名和季度布局规则
├─ resolver.py               agent 编排与安全判定
├─ planner.py                生成无覆盖的整理计划
├─ executor.py               link/copy/move、日志与回滚
├─ db/                       Web Schema、迁移和 repositories
├─ services/                 Web/Worker 共用应用服务
├─ api/                      FastAPI 应用与安全边界
├─ jobs/                     持久队列、Worker、定时器和监听器
├─ repository.py / cache.py  CLI 兼容资料库边界
└─ data/aliases.json         可维护的标题与季集规则
webui/                       React/Vite 管理控制台
deploy/windows/              WinSW 与 Caddy 示例
tests/                       v3 自动化测试
docs/                        架构与 WebUI 规划
```

## 安装

生产运行需要 Python 3.11 或更高版本。构建 WebUI 需要 Node.js 20 或更高版本及 pnpm 10 或更高版本：

```bash
python -m pip install -r requirements.txt
```

复制配置模板（可选）：

```powershell
Copy-Item config.v3.ini.Template config.v3.ini
```

配置文件不会自动读取；需要时显式传入 `--config config.v3.ini`。密钥应放在环境变量中，不要提交到仓库。

## 快速开始

### 预览整个下载目录

不加 `--apply` 时绝不会移动、复制或创建媒体文件：

```powershell
python AutoAnimeMv3.py "F:\下载" --output "F:\动漫库"
```

### 预览季度文件夹或单个文件

```powershell
python AutoAnimeMv3.py "F:\下载\Grand.Blue.Dreaming.S03" --output "F:\动漫库"
python AutoAnimeMv3.py "F:\下载\[SubGroup] Anime Title - 03.mkv" --output "F:\动漫库"
```

### 确认后实际整理

```powershell
# 移动
python AutoAnimeMv3.py "F:\下载" --output "F:\动漫库" --mode move --apply

# 硬链接（适合保种）
python AutoAnimeMv3.py "F:\下载" --output "F:\动漫库" --mode link --apply

# 复制
python AutoAnimeMv3.py "F:\下载" --output "F:\动漫库" --mode copy --apply
```

目标文件已存在时不会覆盖。

### 导出审核报告

```powershell
python AutoAnimeMv3.py "F:\下载" --output "F:\动漫库" --report-json ".\report.json"
```

报告包含源路径、目标路径、统一番名、季集、置信度、agent 证据、警告和动作类型。

## 输出结构

```text
动漫库/
└─ 番剧中文名/
   └─ Season 03/
      ├─ S03E01 - 番剧中文名 [Baha].mkv
      └─ S03E01 - 番剧中文名 [friDay].mkv
```

电影放在番剧/电影名目录下。PV、TVSP、OVA 等没有集号的单文件必须在别名目录中显式声明，通常整理到 `Season 00`。

## 识别 agent 管线

1. **文件名解析 agent**：提取字幕组、标题、季、集、电影/特殊项和发布源。
2. **目录上下文 agent**：季度文件夹作为辅助证据，但不会覆盖更明确的文件名。
3. **别名目录 agent**：将罗马音、英文、繁简译名和不同官方译名合并到同一番剧。
4. **季集规则 agent**：处理绝对集数，例如史莱姆 87 → S04E15、我的英雄学院 171 → S08E12。
5. **可选 OpenAI agent**：仅处理本地未收敛项；结果若与明确季集冲突会被拒绝。
6. **安全策略**：标题、季度、集数、证据与阈值全部通过后才进入执行计划。

内置目录位于 `autoanime_v3/data/aliases.json`。可通过 `--aliases my_aliases.json` 加载用户覆盖文件，无需修改 Python。目录内容变化会改变决策版本，旧识别记录自动失效。

## v3 资料库

默认路径是 `.autoanime-v3/library.sqlite3`。它不是不可编辑的缓存黑盒，而是未来 CLI/WebUI 共用的资料库：

- `shows`、`seasons`、`episodes`：番剧、季度和剧集；
- `media_files`：以规范化 `source_key` 表示同一物理来源的当前路径、当前剧集归属、发布版本和状态；
- `resolutions`：按决策指纹保存识别结果、证据、规则版本和置信度；规则更新时历史记录可保留，但不会重复污染当前媒体事实；
- `operations`：每次 link/copy/move/自动回滚；
- `corrections`：人工纠正草案与文件迁移计划；
- `show_progress`：季度已识别/已整理进度视图。

同一路径如果被一个大小或修改时间不同的新下载复用，会自动重置为 `identified`，不会继承旧文件的已整理位置。

清空 v3 资料库：

```powershell
python AutoAnimeMv3.py --database-reset
```

这只会清空 v3 SQLite 资料库，不会操作媒体文件。

## 回滚

每次预览和执行都会生成 JSONL 操作日志。实际执行日志会保存目标文件大小、修改时间和 SHA-256。手动回滚会同步恢复 SQLite 中的文件位置和状态；如果目标文件在整理后又被修改，回滚会拒绝删除或移动它。旧 copy/link 日志若没有摘要，也会拒绝破坏性删除：

```powershell
python AutoAnimeMv3.py --rollback ".\.autoanime-v3\operations\20260722_xxxxxx.jsonl"
```

批次执行中途失败时，v3 会先自动回滚本批次已经完成的文件。

## 配置

主要配置见 `config.v3.ini.Template`：

- `database_path`：SQLite 资料库路径；
- `alias_file`：内置/自定义别名目录；
- `min_confidence`：允许自动整理的最低置信度；
- `output_root`、`mode`、`operation_dir`：输出与文件策略；
- `openai_*`：只用于本地无法收敛时的可选远程 agent。

命令行的 `--output`、`--mode` 会覆盖配置文件。

## Web 管理控制台

WebUI 面向 Windows 常驻服务器和局域网内的单管理员使用。它可以查看并修改：

- 多下载根、多媒体库根及逐 profile 的 link/copy/move、审核策略、置信度、稳定窗口和目录监听开关；
- 持久扫描任务、任务事件、审核项、不可变整理计划和操作批次；
- 番剧资料、海报/简介/放送状态（可选元数据），以及带修订检查的人工标题纠正；
- 版本化 JSON 规则的草稿、校验、激活和回退；
- 普通 JSON 设置、DPAPI 加密密钥状态和 SQLite 在线备份。

首次构建前端：

```powershell
pnpm --dir webui install
pnpm --dir webui build
```

本机或可信局域网内直接使用 HTTP：

```powershell
# 终端 1：Web/API；--insecure-http 只用于没有 HTTPS 的可信内网
python AutoAnimeWeb.py --data-dir C:\ProgramData\AutoAnime --insecure-http

# 终端 2：Worker
python AutoAnimeWorker.py --data-dir C:\ProgramData\AutoAnime
```

首次管理员只能从服务器本机创建：先在服务器上访问 `http://127.0.0.1:8765` 完成初始化，再允许局域网访问。不要在尚未初始化时把端口或反向代理暴露给局域网。初始化完成后可访问 `http://服务器IP:8765`。生产部署建议让 Web 仅监听 `127.0.0.1`，通过 `deploy/windows/Caddyfile.example` 提供局域网 HTTPS；Caddy 示例也会拒绝远程 bootstrap 请求，此时不要传 `--insecure-http`。WinSW 服务模板位于 `deploy/windows/`；应使用可访问下载目录和媒体库、但权限尽量小的专用 Windows 服务账号，并限制防火墙只允许可信子网访问。

完整安全和数据设计见 [docs/11_v3_WebUI与数据层规划.md](./docs/11_v3_WebUI与数据层规划.md)。密钥只返回“是否已配置”和更新时间，永不通过 API 或页面回显明文/密文。

## 文档与测试

- [v3 架构与迁移](./docs/12_v3_架构与迁移.md)
- [WebUI 与数据层规划](./docs/11_v3_WebUI与数据层规划.md)
- [文档总目录](./docs/00_文档总目录.md)

```powershell
python -m unittest discover -s tests -p "test_v3_*.py" -v
pnpm --dir webui test --run
pnpm --dir webui build
pnpm --dir webui e2e
pnpm --dir webui audit --prod --audit-level high
```

可选的真实大文件三模式验证使用隔离目录，不会触碰正式媒体库：

```powershell
$env:AUTOANIME_REAL_TEST_ROOT = 'F:\AutoAnime-WebUI-Validation'
$env:AUTOANIME_REAL_SAMPLE = 'F:\Samples\episode.mkv'
pnpm --dir webui e2e real-file-modes.spec.ts
```

## 许可证

本项目使用 [GPL-3.0](./LICENSE) 许可证。
