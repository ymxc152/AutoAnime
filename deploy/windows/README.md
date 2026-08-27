# deploy/windows

生产环境部署配置与示例（Windows 服务化 + 反向代理）：

- `AutoAnimeWeb.xml` / `AutoAnimeWorker.xml` — WinSW 服务定义模板（Web / Worker）
- `Caddyfile.example` — Caddy 反向代理示例（LAN HTTPS，并拒绝远端 bootstrap 请求）

日常启动 / 停止使用**项目根目录**脚本（逻辑在 `scripts/autoanime.ps1`）：

| 命令 | 作用 |
|------|------|
| `start-autoanime.bat` | 启动同一 `--data-dir` 的 Web + Worker，等待 `/health/live` |
| `stop-autoanime.bat` | 只停止该数据目录的 PID（不误杀 e2e） |
| `status-autoanime.bat` | 查看进程、端口、健康检查 |

默认：控制台 `http://127.0.0.1:8765`，数据目录 `C:\ProgramData\AutoAnime`，日志 `logs\`，PID `run\web.pid` / `run\worker.pid`。

```powershell
.\start-autoanime.bat -DataDir "D:\AutoAnimeData" -Port 8765 -NoBuild
.\stop-autoanime.bat
.\stop-autoanime.bat -Force
```

注意：

- **Web 与 Worker 必须同时运行**，且使用相同 `--data-dir`。qB webhook / 定时扫描 / 执行 / 回滚都靠 Worker 拉队列。
- 本机 loopback 默认免密登录；远程访问仍需账号密码。
- 前端仅在源码新于 `webui\dist` 时重建；`-NoBuild` 跳过构建。
- 停止默认按数据目录匹配，不会把 Playwright e2e 的临时进程一并杀掉。
- 生产环境建议用 WinSW 把 Web/Worker 注册为 Windows 服务，并用 Caddy 提供 HTTPS；不要在生产环境传 `--insecure-http`。
