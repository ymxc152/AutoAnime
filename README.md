# AutoAnime

简体中文 | [English](./README_en.md)

**本地优先（local-first）的番剧库自动化工具**：订阅/导入 → 三级识别 → 硬链接归档 → 洗版 → WebUI 管理。

- 数据（SQLite 单库）与媒体文件都在你自己机器上，不依赖任何云服务账号；
- 识别失败不猜：低置信度进待确认队列，人工确认一次、终身学习（parse_memory）；
- 下载目录原件不动（继续做种），归档侧 hardlink，洗版替换也是原子操作。

## 特性（全部经过真实数据测试验证）

- **三级识别管线**：L1 本地规则（确定性，零网络）→ L2 识别记忆（确认过的命名模式直接命中）→ L3 LLM 识别（可选）+ 参考源消歧，仲裁器（arbiter）按置信度裁决：L1 确定性 HIGH 直通归档；经记忆/LLM 参与的 HIGH 结果仍入待确认队列（来源含学习成分，自动归档出错会被记忆放大——刻意保守）；MEDIUM 待人工确认 / LOW 进待确认队列。
- **Mikan RSS 订阅 + 缺集回补**：RSS 轮询 → 推送下载器 → 进度对账 → 缺集检测与回补（放送进度判定一律 JST，防日本凌晨放送番的假缺口）。
- **Sonarr 兼容归档命名**：`{中文标题}/Season {SS}/{中文标题} - S{SS}E{EE}.{画质}.mkv`，Jellyfin / Plex / Emby 零配置识别；字幕跟随改名。
- **hardlink 做种保留**：下载目录原件不动继续做种，归档侧硬链接后原子改名；洗版替换只 unlink 归档侧链接名，不影响做种。
- **洗版评分闸门**：分辨率/来源/编码/字幕组偏好/做种健康度加权评分，新候选分数 ≥ 现有分数 + `upgrade_threshold`（默认 2）且单集未达洗版上限（默认 2 次）才升级；跨盘时默认降级 copy（`strict` 策略则跳过并记审计）。
- **错配 A/B/C 恢复 + 回补预算**：识别/归档错配的文件按可救程度自动纠正（A/B/C 三分支），单集自动回补有预算上限（默认 2 次），超限转人工，防错标源霸榜死循环烧流量。
- **LLM 机会主义合批**：订阅场景单文件走快路径不凑批；导入场景「同目录 + 同字幕组」队列自然堆积到阈值（默认 5）才打包调用（单批上限 20），快照实测**省 84.1% 的 LLM 调用**。
- **参考源归一化**：罗马音/别名 → 中文权威名；Bangumi + TMDB 双源，顺序可配（默认 Bangumi 优先），带缓存与频控；未配 TMDB key 自动跳过该源。
- **SSE 实时 WebUI**：8 个页面（Dashboard / Subscriptions / RSS Sources / Pending / Library / Pipeline / Logs / Settings），React 19 + Tailwind 4 + xyflow 管线可视化；事件流断线重连自动重放（Last-Event-ID），心跳防代理超时。
- **通知**：webhook（通用 JSON POST）+ Telegram Bot；可订阅事件：新集归档 / 缺集 / 洗版完成 / 待确认积压告警。
- **简单 token 认证**：`AUTOANIME_API_TOKEN` 非空时校验 `X-API-Token` 请求头（SSE 端点同时接受 `?token=` 查询参数）；空串 = 关闭认证。
- **SQLite 单库零外部依赖**：识别记忆、审计日志、订阅状态全在一个 SQLite 文件里，备份即拷走。

## 快速开始

### 1. 安装（需要 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)）

```bash
git clone <仓库地址>
cd AutoAnime
uv sync
```

### 2. 初始化数据库

```bash
uv run autoanime init-db
```

### 3. 配置 `.env`

```bash
cp .env.example .env   # .env 不进 git；所有密钥只放这里
```

常用变量（完整清单见 `.env.example` 与 `autoanime/config.py`，均以 `AUTOANIME_` 为前缀）：

```ini
# L3 LLM（可选；不配 key = L3 关闭，低置信度全进待确认队列——这是设计好的降级路径，不是故障）
AUTOANIME_LLM_ENABLED=false
AUTOANIME_LLM_BASE_URL=          # OpenAI 兼容接口地址
AUTOANIME_LLM_API_KEY=
AUTOANIME_LLM_MODEL=deepseek-chat

# 参考源（TMDB v3 api_key，可选；不配则只用 Bangumi）
AUTOANIME_TMDB_API_KEY=

# API / WebUI
AUTOANIME_API_TOKEN=             # 建议设置；勿暴露公网（见下文安全提示）
AUTOANIME_API_PORT=8000
AUTOANIME_WEB_PORT=3080

# 路径
AUTOANIME_LIBRARY_PATH=./library    # 媒体库（Jellyfin/Plex 指这里）
AUTOANIME_DOWNLOAD_PATH=./downloads # 下载目录（qBittorrent 保存路径；与库目录必须同盘，见下文）

# 下载器（qBittorrent WebUI；本项目任务带 category=autoanime，不碰你的其他种子）
AUTOANIME_DOWNLOADER=qbittorrent
AUTOANIME_QBITTORRENT_HOST=127.0.0.1
AUTOANIME_QBITTORRENT_PORT=8080
AUTOANIME_QBITTORRENT_USERNAME=admin
AUTOANIME_QBITTORRENT_PASSWORD=

# 订阅调度
AUTOANIME_SCHEDULER_ENABLED=true
AUTOANIME_RSS_POLL_INTERVAL_MINUTES=30

# 洗版
AUTOANIME_UPGRADE_THRESHOLD=2
AUTOANIME_UPGRADE_COPY_POLICY=allow   # allow=跨盘降级 copy；strict=永不 copy

# 通知（可选）
AUTOANIME_NOTIFY_ENABLED=false
AUTOANIME_NOTIFY_WEBHOOK_URL=
AUTOANIME_NOTIFY_TELEGRAM_BOT_TOKEN=
AUTOANIME_NOTIFY_TELEGRAM_CHAT_ID=
```

### 4. 常用命令

```bash
uv run autoanime --help          # 全部子命令

# 导入：扫描本地目录，每个文件走 L1/L2/L3 管线后归档或入待确认队列
uv run autoanime import "D:\downloads\番剧" [--dry-run]

# 订阅：建 Series/Season/Episode 并挂 Mikan RSS（每番只订一个字幕组！）
uv run autoanime subscribe --title-cn "示例番剧" --season 1 --episodes 12 \
    --fansub "示例字幕组" --rss-url "https://mikanani.me/RSS/MyBangumi?token=***"

# 手动触发一轮订阅循环（下载对账 + RSS 轮询，与调度器同一批入口）
uv run autoanime rerun

# 待确认队列：查看 / 人工确认（确认结果写入识别记忆，下次同类命名直接命中）
uv run autoanime queue --status pending
uv run autoanime confirm --name "[SubGroup] 示例番剧 [01][1080p].mkv" \
    --title "示例番剧" --season 1 --episode 1 --fansub "SubGroup"

# 识别结果与审计指标报表
uv run autoanime report [--json]

# 单个文件名试跑识别管线（JSON 输出，不落库不碰文件）
uv run autoanime parse --name "[SubGroup] 示例番剧 [01][1080p].mkv"
```

### 5. 启动 API + WebUI

```bash
# 后端 API（FastAPI + SSE + 订阅调度器，单进程；默认 127.0.0.1:8000）
uv run python -m autoanime.api serve          # 可选 --host/--port/--dev

# WebUI 前端（frontend/ 目录下）
npm install
npm run dev    # 开发服务器，默认连内置 mock；连真后端设 VITE_USE_MOCK=0（/api 代理到 127.0.0.1:8000）
```

生产部署用 `npm run build` 后静态托管 `dist/`（构建产物默认连真 API，需把 `/api` 反代到后端 8000 端口），或直接用一键容器化：

```bash
docker compose up -d --build   # WebUI 在 http://127.0.0.1:3080
```

容器化部署、外部依赖（qBittorrent / Mikan / LLM）与部署纪律详见 [docs/DEPLOY.md](docs/DEPLOY.md)；常见问题见 [docs/FAQ.md](docs/FAQ.md)。

## 架构一图流

```
订阅 (Mikan RSS 轮询)          导入 (本地目录扫描)
        └────────────┬──────────────┘
                     ▼
   L1 本地规则识别（anitopy + 确定性规则，零网络）
     · HIGH ──────────────────────────────┐
     · MEDIUM/LOW                          ▼
   L2 识别记忆（parse_memory 命中直接采用）   │
     · 未命中 → 前置消歧（title_aliases     │
       读穿透 → 参考源 canonical 回查）      │
                     ▼                      │
   L3 LLM 识别（可选；机会主义合批）─→ arbiter 仲裁
     · 参考源归一化：罗马音/别名 → 中文权威名   │
     · HIGH 自动 / MEDIUM 待确认 / LOW 人工     │
                     ▼                      ▼
   organize 归档：hardlink + Sonarr 兼容命名 + 字幕跟随
                     │
                     ▼
   洗版引擎（评分闸门：threshold / 单集上限 / copy 降级）
   缺集检测 → 回补；错配恢复（A/B/C）→ 回补预算
```

### 模块地图

| 模块 | 职责 |
| --- | --- |
| `autoanime/pipeline/` | L1 规则 / L2 记忆 / L3 LLM 识别、机会主义合批、前置消歧、仲裁器（arbiter） |
| `autoanime/memory/` | SQLite 存储、识别记忆读写、别名回填、参考源缓存与治理 |
| `autoanime/providers/` | Bangumi / TMDB 参考源适配器、LLM 传输、通知 |
| `autoanime/gateway/` | qBittorrent / aria2 下载器接口、Mikan RSS 拉取 |
| `autoanime/scheduler/` | RSS 轮询、下载轮询与补扫、启动对账、缺集检测、节拍与 JST 时钟 |
| `autoanime/organize/` | hardlink 搬移、Sonarr 兼容命名、洗版评分、错配恢复、回滚 |
| `autoanime/web/` | FastAPI 装配、SSE 事件流、REST 路由（series / subscriptions / rss_sources / pending / organize / audit / metrics / settings / events） |
| `autoanime/api/` | `python -m autoanime.api serve` 启动入口 |
| `frontend/` | React 19 + Tailwind 4 + xyflow 的 WebUI（8 页面，Vite 构建） |

## 测试与质量

- **1065 个后端离线测试**（`uv run pytest -q`，全程不触网）+ **67 个前端测试**（`cd frontend && npm test`），全部通过。
- **五轮真实数据验收**：第一轮修复 4 项；第二、三轮累计修复 10 项（含 episode id 与集号混用、洗版目标位覆盖两个重大缺陷）；第四轮换用全新命名风格（中文方括号字幕组包 / LoliHouse 散文件 / 简繁内嵌同番）修复 4 项；第五轮同批重导全量走记忆路由零 LLM 外呼。
- **WebUI 浏览器实测**：8 页面逐一验证交互与 SSE 事件流（并由此发现并修复了 SSE 装配与订阅缺陷）。
- 洗版触发/评分是确定性代码，不进 AI 边界；识别决策全部落审计日志（audit_log），可解释可回溯。

## 安全提示

**勿暴露公网。** AutoAnime 是单用户本地工具，只有一层简单 token 认证（`AUTOANIME_API_TOKEN`），没有用户体系、没有 HTTPS。请只在 LAN 内使用并设置 token；路由器端口转发 = 把整库管理权暴露给公网，明确不支持。Mikan 私有订阅的独立 token 字段按密钥处理（只进库/env，不进日志与报告）。如直接粘贴带 `?token=` 的 RSS URL，WebUI 会按 URL 原样展示；请勿截图或共享该页面。

## 已知边界（如实）

- **rollback 文件反操作**：v1 仅 organize 域的反操作可执行，其余记录为 `skipped`（不静默丢弃，如实上报）。
- **确认即归档**：`confirm`/`correct`（CLI 与 WebUI 一致）在写入识别记忆的同时，以确认结果把源文件 hardlink 入库（D17 命名 + D21 原件保留 + D21 目标位守卫）；文件已不在位时如实记审计原因，学习不受影响。
- **settings 无持久化表**：WebUI Settings 页的修改只作用于当前进程，重启后回到 `.env` / `autoanime.toml` 的值。
- **docker 实机验证**：compose 结构有自动化自检（`tests/unit/test_compose.py`），真实 `docker compose up` 留待用户环境执行。

## 许可证

本项目使用 [MIT](./LICENSE) 许可证。
