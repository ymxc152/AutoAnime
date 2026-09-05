# 部署草稿（docs/DEPLOY.md，E4 v1）

> 状态：v1 草稿（E4 交付）。README 部署段落另行任务；本文件先承载一键部署
> 事实与安全纪律。

## 1. 一键起（docker-compose）

```bash
cp .env.example .env   # 按需修改；密钥只放 .env（不进 git）
docker compose up -d --build
```

- backend：`uv` 容器，**单进程同时承载 FastAPI + AsyncIOScheduler**（D16：
  RSS 轮询 / 下载轮询 / COLLECTED 降频 / 通知泵 / 启动对账都在 backend）。
- frontend：E3 产物 `npm run build` → nginx 静态托管 + `/api` 反代（SSE 已
  关缓冲）。
- 验证：`curl http://127.0.0.1:8000/api/health` → `{"status":"ok"}`；
  WebUI 在 `http://127.0.0.1:3080`。

## 2. 部署前必读（三条纪律）

1. **每番只订一个字幕组**（Mikan 最佳实践）：同一番剧订阅多个字幕组的 RSS
   会造成同集反复下载/洗版互踩。Subscriptions 页的提示文案同此。
2. **勿暴露公网**（拍板 D6）：单用户本地工具，无认证体系；LAN 内使用并
   设置 `AUTOANIME_API_TOKEN`（SSE 端点同时接受 `?token=`，B7）。路由器
   端口转发 = 把整库管理权暴露给公网，明确不支持。
3. **两个挂载点须同一盘/同一卷**（审核 B8）：`./library` 与 `./downloads`
   必须同盘，hardlink 洗版才生效；跨盘时按 D9 默认全量降级 copy（大文件
   翻倍 IO），`AUTOANIME_UPGRADE_COPY_POLICY=strict` 时干脆跳过。compose
   内两者同在 `/data` 下即同盘。

## 3. 外部依赖（自配，项目不内置）

- **qBittorrent**：开启 WebUI；`AUTOANIME_QBITTORRENT_*` 指向它；本项目的
  任务带 `category=autoanime`，轮询/补扫只碰自己的任务。
- **Mikan RSS**：主站部分地区被墙时可用备用域名 `mikanime.tv`；私有订阅
  的 `?token=` 按密钥处理（只进库/env，不进日志与报告）。网络失败重试后
  跳过本轮，不会 crash。
- **LLM（可选，D19）**：不配 key = L3 关闭，低置信度全进待确认队列（这是
  验收过的优雅降级路径，不是故障）。

## 4. 首次使用

```bash
# 库操作走 CLI（调度器只在 backend 进程内）
uv run autoanime init-db
uv run autoanime subscribe --title-cn "示例番剧" --season 1 --episodes 12 \
    --fansub "示例字幕组" --rss-url "https://mikanani.me/RSS/MyBangumi?token=***"
uv run autoanime rerun    # 手动触发一轮（与调度器同一批 store 入口）
```

订阅也可走 WebUI（Subscriptions / RSSSources 页）。 air_date 判定一律
JST（防日本凌晨放送番的假缺口），界面展示转本地时区（D20）。

## 5. 本机无 docker 的验收替代（D10 自检口径）

`tests/unit/test_compose.py` 做 compose 结构自检：YAML 可解析、服务齐备
（backend/frontend）、挂载点同盘对应、`.env.example` 与 compose 的环境
变量对得上、无真实密钥。真实 `docker compose up` 留在用户环境执行。
