# Windows 一键启动 / 开机自启

> 推荐直接使用项目根目录脚本（打开文件夹就能看到）：
>
> - `start-autoanime.bat`
> - `stop-autoanime.bat`
> - `install-autostart.bat`
> - `uninstall-autostart.bat`
>
> 本目录下的同名脚本是兼容包装，会转发到根目录。

## 文件

| 文件 | 作用 |
|------|------|
| `../../start-autoanime.bat` | 启动 Web + Worker（双击即可） |
| `../../stop-autoanime.bat` | 停止 Web + Worker |
| `../../install-autostart.bat` | 注册登录后自动启动 |
| `../../uninstall-autostart.bat` | 取消开机自启 |

## 首次使用前

在项目根目录准备好 Python 依赖，并确保能构建前端：

```powershell
cd C:\path\to\autoanime-webui
python -m pip install -r requirements.txt
pnpm --dir webui install
pnpm --dir webui build
```

若尚未构建 `webui\dist`，`start-autoanime.bat` 会在本机已安装 `pnpm` 时尝试自动构建。

## 立即启动

双击项目根目录：

```text
start-autoanime.bat
```

默认：

- 数据目录：`C:\ProgramData\AutoAnime`
- 地址：`http://127.0.0.1:8765`
- 日志：`C:\ProgramData\AutoAnime\logs\`
- 使用 `--insecure-http`（适合本机/可信局域网直连）
- 默认管理员：`admin` / `AutoAnime-Admin-ChangeMe!`
- 本机 loopback 默认免密登录（可在 WebUI「系统设置」关闭）

自定义示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-autoanime.ps1 -DataDir "D:\AutoAnimeData" -Port 8765
```

## 开机自动运行

双击：

```text
install-autostart.bat
```

会注册：

1. 当前用户计划任务 `AutoAnime WebUI`（登录约 20 秒后启动）
2. 开始菜单「启动」文件夹快捷方式（备份）

取消：

```text
uninstall-autostart.bat
```

## 停止服务

```text
stop-autoanime.bat
```

## 注意

- **Web 与 Worker 必须同时运行**，且使用相同 `--data-dir`。
- 默认会自动创建管理员账号；远程访问仍需账号密码。
- 若项目路径移动，请重新运行 `install-autostart.bat`。
- 开机自启不会替你安装 Python/Node；请先在本机装好依赖。
