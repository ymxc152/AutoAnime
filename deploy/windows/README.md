# deploy/windows

生产环境部署配置与示例（Windows 服务化 + 反向代理）：

- `AutoAnimeWeb.xml` / `AutoAnimeWorker.xml` — WinSW 服务定义模板（Web / Worker）
- `Caddyfile.example` — Caddy 反向代理示例（LAN HTTPS，并拒绝远端 bootstrap 请求）

一键启动 / 停止请直接使用**项目根目录**脚本（打开项目文件夹即可看到）：

| 命令 | 作用 |
|------|------|
| `start-autoanime.bat` | 启动 Web + Worker（双击即可） |
| `stop-autoanime.bat` | 停止服务 |

默认：控制台 `http://127.0.0.1:8765`，数据目录 `C:\ProgramData\AutoAnime`，日志 `C:\ProgramData\AutoAnime\logs\`。

自定义数据目录 / 端口：

```powershell
.\start-autoanime.bat -DataDir "D:\AutoAnimeData" -Port 8765
```

注意：

- **Web 与 Worker 必须同时运行**，且使用相同 `--data-dir`。
- 本机 loopback 默认免密登录；远程访问仍需账号密码。
- `start-autoanime.bat` 会自动安装缺失的 Python 依赖；若 `webui\dist` 缺失且本机装有 `pnpm`，会自动构建前端。
- 生产环境建议用 WinSW 把 Web/Worker 注册为 Windows 服务，并用 Caddy 提供 HTTPS；不要在生产环境传 `--insecure-http`。
