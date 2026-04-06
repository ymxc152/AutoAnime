# AutoAnimeMv

English | [简体中文](./README.md)

`AutoAnimeMv` is a Python tool for identifying anime titles, seasons, and episodes, then renaming and organizing video and subtitle files into a cleaner library structure. It supports both local batch processing and `qBittorrent` callback workflows.

## Features
- OpenAI-compatible title, season, and episode recognition
- Fallback sources via `Bangumi`, `BGM`, and `TMDB`
- Linked handling for video and subtitle files
- `default` and `emby` naming styles
- Hard link support for seeding-friendly workflows
- `--dry-run`, operation logs, and rollback support
- Recursive scanning and optional separate output directory

## Installation
```bash
python -m pip install -r requirements.txt
```

## Quick Start
1. Copy `config.ini.Template` to local `config.ini`
2. Adjust recognition, naming, proxy, and file handling options as needed
3. Inject real credentials through environment variables instead of storing them in the repository
4. Run a preview with `--dry-run` before doing actual file operations

### PowerShell Example
```powershell
$env:OPENAI_API_KEY="your-openai-key"
$env:TMDB_BEARER_TOKEN="your-tmdb-token"
python AutoAnimeMv.py "D:\Anime" --dry-run
python AutoAnimeMv.py "D:\Anime"
```

## Common Commands
```bash
# Local batch processing
python AutoAnimeMv.py "D:\Anime"

# Emby-style naming
python AutoAnimeMv.py "D:\Anime" --naming-style emby

# Use a separate output directory
python AutoAnimeMv.py "D:\Anime" --output-path "D:\AnimeLibrary"

# Roll back a previous run
python AutoAnimeMv.py rollback --log ".\logs\AutoAnime_operations_xxx.json"
```

## qBittorrent Callback Example
```bash
python AutoAnimeMv.py "%D" "%N" "%C" "%L"
```

## Important Configuration
- `USEOPENAIAPI` / `OPENAI_PRIORITY_FIRST` / `OPENAI_IDENTIFY_ALL`: AI recognition flow
- `OPENAI_API_KEY_ENV` / `TMDB_BEARER_TOKEN_ENV`: credential environment variable names
- `USELINK` / `STRICT_MODE` / `LINKFAILSUSEMOVEFLAGS`: file handling strategy
- `NAMING_STYLE` / `OUTPUT_PATH` / `MAX_FILENAME_LENGTH`: naming and output behavior
- `DRY_RUN` / `OPERATION_LOG_ENABLE` / `OPERATION_LOG_DIR`: preview, audit, and rollback controls

## Public Repository Notes
- Keep `config.ini` local and never commit it
- Store real `API keys` / `tokens` in environment variables only
- Ignore `docs/plans/`, `.cache/`, `logs/`, local logs, and virtual environments
- Dependency setup is handled through `requirements.txt`; `get-pip.py` is no longer kept in the repository
- If you plan to publish the full Git history, review author emails and legacy repository traces first

## Documentation
- Index: `docs/00_文档总目录.md`
- Architecture: `docs/01_项目架构与模块职责.md`
- Deployment: `docs/02_开发环境与构建部署.md`
- External APIs and dependencies: `docs/04_接口协议与外部依赖.md`

## Feedback
Please use the current repository's Issue or Pull Request workflow for bug reports and improvements.

## License
This project is released under [GPL-3.0](./LICENSE).