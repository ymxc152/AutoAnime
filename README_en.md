# AutoAnime

[简体中文](./README.md) | English

AutoAnime identifies and organizes anime files for Emby, Jellyfin, Plex, and similar media libraries. The repository now contains one implementation only: v3.1.1, entered through `AutoAnimeMv3.py` with its core in `autoanime_v3/`.

Production requires Python 3.11 or newer. Building the WebUI requires Node.js 20 or newer and pnpm 10 or newer.

## Highlights

- Handles a season/batch directory or one video file.
- Defaults to preview mode; files change only with `--apply`.
- Rejects low-confidence or conflicting identification instead of guessing.
- Uses intrinsic stable labels for known releases and a stable `version-xxxxxxxx` key when metadata is absent, so incremental imports do not overwrite each other.
- Supports hard-link, copy, and move modes. Move first atomically claims the source as a same-volume staging file, verifies it, and deletes only that staging path, so a newly recreated download path is never removed. Logs include SHA-256 and failed batches roll back automatically.
- Uses a normalized SQLite library database instead of fragmented JSON cache files.
- Includes a FastAPI + React single-administrator LAN console for configuration, review, immutable plan approval, real file execution, and safe rollback.

## Quick start

```powershell
python -m pip install -r requirements.txt

# Preview a download directory
python AutoAnimeMv3.py "F:\Downloads" --output "F:\AnimeLibrary"

# Preview one season directory or one file
python AutoAnimeMv3.py "F:\Downloads\Some.Show.S03" --output "F:\AnimeLibrary"
python AutoAnimeMv3.py "F:\Downloads\Some.Show.S03E02.mkv" --output "F:\AnimeLibrary"

# Apply after reviewing the plan
python AutoAnimeMv3.py "F:\Downloads" --output "F:\AnimeLibrary" --mode move --apply
python AutoAnimeMv3.py "F:\Downloads" --output "F:\AnimeLibrary" --mode link --apply

# Export an auditable JSON report
python AutoAnimeMv3.py "F:\Downloads" --output "F:\AnimeLibrary" --report-json report.json
```

Copy `config.v3.ini.Template` to `config.v3.ini` if a config file is desired, then pass it explicitly with `--config config.v3.ini`. Keep API credentials in environment variables.

## Library database

The default database is `.autoanime-v3/library.sqlite3`. It contains normalized shows, seasons, episodes, media files, identification evidence, operation history, and correction drafts. A normalized source key keeps one current media fact while historical resolution decisions remain auditable. Alias/rule changes are included in the decision fingerprint, so stale decisions are invalidated without duplicating the current media row. Manual rollback restores database state and verifies the logged SHA-256 before destructive actions.

```powershell
python AutoAnimeMv3.py --database-reset
python AutoAnimeMv3.py --rollback ".\.autoanime-v3\operations\run.jsonl"
```

Database reset does not modify media files.

## Web console

Build the React application, then run the Web/API and Worker processes against the same data directory:

```powershell
pnpm --dir webui install
pnpm --dir webui build

# Trusted LAN HTTP development only
python AutoAnimeWeb.py --data-dir C:\ProgramData\AutoAnime --insecure-http
python AutoAnimeWorker.py --data-dir C:\ProgramData\AutoAnime
```

The first administrator can only be created from the server itself. Before exposing the port or reverse proxy to the LAN, open `http://127.0.0.1:8765` on the server and complete bootstrap. After that, use `http://server-ip:8765`. The console manages multiple source/library roots, per-profile link/copy/move policies, manual scans, job events, reviews, immutable plans, operation rollback, library-title corrections, versioned JSON rules, encrypted secret status, ordinary settings, and online backups.

For production, bind the Web process to loopback and use the example Caddy configuration in `deploy/windows/` for LAN HTTPS. The example also rejects remote bootstrap requests. Do not pass `--insecure-http` behind HTTPS. WinSW templates for the Web and Worker services are included in the same directory.

## Documentation

- [Chinese README with full usage](./README.md)
- [v3 architecture and migration](./docs/12_v3_架构与迁移.md)
- [WebUI and data-layer plan](./docs/11_v3_WebUI与数据层规划.md)

## Tests

```powershell
python -m unittest discover -s tests -p "test_v3_*.py" -v
pnpm --dir webui test --run
pnpm --dir webui build
pnpm --dir webui e2e
pnpm --dir webui audit --prod --audit-level high
```

## License

[GPL-3.0](./LICENSE)
