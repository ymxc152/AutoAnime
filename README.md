# AutoAnimeMv

简体中文 | [English](./README_en.md)

`AutoAnimeMv` 是一个用于番剧视频和字幕自动识别、重命名、整理的 Python 工具，支持本地批处理和 `qBittorrent` 回调两种工作方式，适合在 Emby、Jellyfin、Plex 等媒体库入库前做标准化整理。

## 功能概览
- 支持 OpenAI 兼容接口优先识别剧名、季、集
- 支持用 AI 罗马音/英文优先查询 `TMDB` 回填中文名，未命中再回退 AI 中文名
- 支持 `Bangumi`、`BGM`、`TMDB` 作为 AI 失败时的回退数据源
- 支持视频与字幕联动整理
- 支持 `default` / `emby` 两种命名风格
- 支持硬链接、`--dry-run` 预览、操作日志和回滚
- 支持递归扫描子目录，并可指定独立输出目录
- 硬链接模式下若目标已存在，默认保留原文件，并缓存新重复资源的识别结果以减少后续重复识别

## 安装
```bash
python -m pip install -r requirements.txt
```

## 快速开始
1. 复制 `config.ini.Template` 为本地 `config.ini`
2. 按需修改识别、命名、代理和落盘策略
3. 通过环境变量注入真实密钥，不要把凭据写入仓库
4. 先运行 `--dry-run` 预览，再执行真实整理

### PowerShell 示例
```powershell
$env:OPENAI_API_KEY="your-openai-key"
$env:TMDB_BEARER_TOKEN="your-tmdb-token"
python AutoAnimeMv.py "D:\Anime" --dry-run
python AutoAnimeMv.py "D:\Anime"
```

## 常用命令
```bash
# 本地批处理
python AutoAnimeMv.py "D:\Anime"

# Emby 风格命名
python AutoAnimeMv.py "D:\Anime" --naming-style emby

# 指定输出目录
python AutoAnimeMv.py "D:\Anime" --output-path "D:\AnimeLibrary"

# 回滚最近一次整理
python AutoAnimeMv.py rollback --log ".\logs\AutoAnime_operations_xxx.json"
```

## qBittorrent 回调示例
```bash
python AutoAnimeMv.py "%D" "%N" "%C" "%L"
```

未完成下载的临时文件（如 `.!qB`、`.part`、`.partial`、`.aria2`、`.crdownload`）会自动跳过，不参与番剧整理。

## 关键配置
- `USEOPENAIAPI` / `OPENAI_PRIORITY_FIRST` / `OPENAI_IDENTIFY_ALL`: AI 识别链路开关
- `OPENAI_API_KEY_ENV` / `TMDB_BEARER_TOKEN_ENV`: 凭据环境变量名
- `USELINK` / `STRICT_MODE` / `LINKFAILSUSEMOVEFLAGS`: 文件整理策略
- `NAMING_STYLE` / `OUTPUT_PATH` / `MAX_FILENAME_LENGTH`: 命名与输出控制
- `DRY_RUN` / `OPERATION_LOG_ENABLE` / `OPERATION_LOG_DIR`: 预览、审计与回滚

## 公开仓库建议
- `config.ini` 只保留在本地，不提交到仓库
- 真实 `API Key` / `Token` 只通过环境变量注入
- `docs/plans/`、`.cache/`、`logs/`、本地日志和虚拟环境建议忽略
- 依赖安装统一使用 `requirements.txt`，不再保留 `get-pip.py`
- 如果要公开完整 Git 历史，请额外检查历史提交中的作者邮箱和旧仓库痕迹

## 项目文档
- 文档总入口：`docs/00_文档总目录.md`
- 架构说明：`docs/01_项目架构与模块职责.md`
- 部署说明：`docs/02_开发环境与构建部署.md`
- 接口与依赖：`docs/04_接口协议与外部依赖.md`

## 反馈
如需反馈问题或提交改进建议，请直接使用当前仓库的 Issue 或 Pull Request。

## 许可证
本项目使用 [GPL-3.0](./LICENSE) 许可证。
