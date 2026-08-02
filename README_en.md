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


### Moving from legacy organize scripts to the Web console

The repo is a single v3 stack (`AutoAnimeMv3.py` + Web/Worker). There is no automatic v2 database importer. Typical cutover:

1. Stop old schedulers so two tools do not move the same tree.
2. Start Web + Worker (`start-autoanime.bat` or dev Vite on port 5173).
3. In **Scan profiles**, add source and library roots. On the host machine, use **Browse…** for the Windows folder picker; from another PC on the LAN, paste paths.
4. Create a profile (prefer `link` if you seed torrents). Start with **review all**, then switch to auto-apply once results look right.
5. Flow: manual scan → reviews → plans → approve/execute → rollback from operation history if needed.
6. Optional automation: schedules and downloader webhooks under Settings; local trusted hook at `POST /api/v1/hooks/local`.
7. CLI still works for one-off dry runs; it does not automatically share the Web data directory SQLite.

## Web console

### Windows one-click start

Root-level scripts (visible as soon as you open the project folder):

| File | Purpose |
|------|---------|
| `start-autoanime.bat` | Start Web + Worker |
| `stop-autoanime.bat` | Stop services |

Defaults: `http://127.0.0.1:8765`, data under `C:\ProgramData\AutoAnime`.

### Default credentials and local passwordless login

On first Web start a default administrator is created:

| Field | Value |
|------|-------|
| Username | `admin` |
| Password | `AutoAnime-Admin-ChangeMe!` |

Security policy:

- **Local passwordless login (on by default)** for loopback (`127.0.0.1` / `::1`).
- LAN clients still need the username/password.
- Toggle under WebUI **Settings → Local access & hooks**.
- **Local hook trust (on by default)** allows `POST /api/v1/hooks/local` from loopback without a webhook token.
- Change the default password before exposing the service more broadly.

Build the React application, then run the Web/API and Worker processes against the same data directory:

```powershell
pnpm --dir webui install
pnpm --dir webui build

# Trusted LAN HTTP development only
python AutoAnimeWeb.py --data-dir C:\ProgramData\AutoAnime --insecure-http
python AutoAnimeWorker.py --data-dir C:\ProgramData\AutoAnime
```

Open `http://127.0.0.1:8765` for the console (passwordless on loopback by default). Use `http://server-ip:8765` with the default credentials from another machine. The console manages multiple source/library roots, per-profile link/copy/move policies, manual scans, job events, reviews, immutable plans, operation rollback, library-title corrections, versioned JSON rules, encrypted secret status, ordinary settings, and online backups.

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


### Privacy and local data

This is a local open-source tool. It does **not** upload your folder paths, API keys, media, or settings to the project authors or a vendor cloud.

- API keys are encrypted on the machine running AutoAnimeWeb (DPAPI on Windows when available).
- With AI recognition enabled, only unresolved items may send **filename/metadata text** to the **Base URL you configured** (OpenAI or a compatible endpoint you choose). Video bytes are never uploaded by AutoAnime.
- Keep AI disabled if you do not want any remote calls. Do not commit `config.v3.ini`, `.dev-data/`, `secret-store/`, or SQLite databases.

