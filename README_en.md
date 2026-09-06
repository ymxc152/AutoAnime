# AutoAnime

English | [简体中文](./README.md)

**A local-first anime library automation tool**: subscribe/import → three-tier recognition → hardlink archiving → upgrades → WebUI management.

- Data (a single SQLite database) and media files stay on your own machine — no cloud accounts;
- No guessing on failure: low-confidence releases go to a pending queue; confirm once, learned forever (parse_memory);
- Originals in the download dir keep seeding — archives are hardlinks, and upgrades replace atomically.

## Features (all verified against real data)

- **Three-tier recognition pipeline**: L1 local rules (deterministic, zero network) → L2 parse memory (confirmed naming patterns hit directly) → L3 LLM recognition (optional) + reference-source disambiguation. An arbiter rules by confidence: HIGH auto-archive / MEDIUM manual confirm / LOW to the pending queue.
- **Mikan RSS subscriptions + gap backfill**: RSS polling → push to downloader → progress reconcile → missing-episode detection and backfill (air-date checks are always computed in JST to avoid false gaps for late-night shows).
- **Sonarr-compatible naming**: `{Title CN}/Season {SS}/{Title CN} - S{SS}E{EE}.{quality}.mkv` — recognized by Jellyfin / Plex / Emby with zero config; subtitles are renamed along.
- **Hardlink seed preservation**: the download-side original stays untouched for seeding; the archive side is a hardlink atomically renamed; upgrade replacement only unlinks the archive-side name, seeding is unaffected.
- **Upgrade scoring gate**: weighted score over resolution / source / encoding / fansub preference / seeder health. A candidate upgrades only when score ≥ current + `upgrade_threshold` (default 2) and the episode is under the upgrade cap (default 2). Cross-device falls back to copy by default (`strict` policy skips and records audit).
- **Mismatch A/B/C recovery + backfill budget**: mismatched files are auto-corrected by recoverability (A/B/C branches); per-episode auto-backfill has a budget cap (default 2), beyond which it escalates to humans — preventing mislabeled sources from burning bandwidth in loops.
- **LLM opportunistic batching**: subscription flow keeps the single-file fast path; import flow batches calls once a "same folder + same fansub" queue naturally piles up (default 5, cap 20 per batch) — snapshot-measured **84.1% fewer LLM calls**.
- **Reference-source normalization**: romaji/aliases → authoritative Chinese title; Bangumi + TMDB dual sources with configurable order (Bangumi first by default), caching and rate limiting; missing TMDB key just skips that source.
- **SSE realtime WebUI**: 8 pages (Dashboard / Subscriptions / RSS Sources / Pending / Library / Pipeline / Logs / Settings), React 19 + Tailwind 4 + xyflow pipeline visualization; Last-Event-ID replay on reconnect, heartbeats against proxy timeouts.
- **Notifications**: generic webhook (JSON POST) + Telegram Bot; subscribable events: episode organized / gap / upgrade completed / pending backlog alert.
- **Simple token auth**: when `AUTOANIME_API_TOKEN` is set, requests must carry the `X-API-Token` header (the SSE endpoint also accepts `?token=`); empty string disables auth.
- **Single SQLite database, zero external services**: parse memory, audit log and subscription state all live in one file — back up by copying.

## Quick Start

### 1. Install (Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/))

```bash
git clone <repo-url>
cd AutoAnime
uv sync
```

### 2. Initialize the database

```bash
uv run autoanime init-db
```

### 3. Configure `.env`

```bash
cp .env.example .env   # .env is git-ignored; keep all secrets here
```

Common variables (full list in `.env.example` and `autoanime/config.py`; all prefixed `AUTOANIME_`):

```ini
# L3 LLM (optional; no key = L3 off, low-confidence goes to the pending queue — designed degradation, not a failure)
AUTOANIME_LLM_ENABLED=false
AUTOANIME_LLM_BASE_URL=          # OpenAI-compatible endpoint
AUTOANIME_LLM_API_KEY=
AUTOANIME_LLM_MODEL=deepseek-chat

# Reference source (TMDB v3 api_key, optional; Bangumi needs no key)
AUTOANIME_TMDB_API_KEY=

# API / WebUI
AUTOANIME_API_TOKEN=             # recommended; do NOT expose to the public internet
AUTOANIME_API_PORT=8000
AUTOANIME_WEB_PORT=3080

# Paths
AUTOANIME_LIBRARY_PATH=./library    # media library (point Jellyfin/Plex here)
AUTOANIME_DOWNLOAD_PATH=./downloads # download dir (qBittorrent save path; must share a disk with the library, see below)

# Downloader (qBittorrent WebUI; tasks carry category=autoanime and never touch your other torrents)
AUTOANIME_DOWNLOADER=qbittorrent
AUTOANIME_QBITTORRENT_HOST=127.0.0.1
AUTOANIME_QBITTORRENT_PORT=8080
AUTOANIME_QBITTORRENT_USERNAME=admin
AUTOANIME_QBITTORRENT_PASSWORD=

# Scheduler
AUTOANIME_SCHEDULER_ENABLED=true
AUTOANIME_RSS_POLL_INTERVAL_MINUTES=30

# Upgrades
AUTOANIME_UPGRADE_THRESHOLD=2
AUTOANIME_UPGRADE_COPY_POLICY=allow   # allow=fallback to copy across devices; strict=never copy

# Notifications (optional)
AUTOANIME_NOTIFY_ENABLED=false
AUTOANIME_NOTIFY_WEBHOOK_URL=
AUTOANIME_NOTIFY_TELEGRAM_BOT_TOKEN=
AUTOANIME_NOTIFY_TELEGRAM_CHAT_ID=
```

### 4. Common commands

```bash
uv run autoanime --help          # all subcommands

# Import: scan a local directory; every release goes through L1/L2/L3, then archives or pends
uv run autoanime import "D:\downloads\anime" [--dry-run]

# Subscribe: create Series/Season/Episode and attach a Mikan RSS (one fansub per show!)
uv run autoanime subscribe --title-cn "示例番剧" --season 1 --episodes 12 \
    --fansub "SubGroup" --rss-url "https://mikanani.me/RSS/MyBangumi?token=***"

# Trigger one subscription-loop cycle manually (same store entries as the scheduler)
uv run autoanime rerun

# Pending queue: inspect / confirm manually (confirmations are learned into parse memory)
uv run autoanime queue --status pending
uv run autoanime confirm --name "[SubGroup] Show [01][1080p].mkv" \
    --title "Show" --season 1 --episode 1 --fansub "SubGroup"

# Parse metrics and audit summary
uv run autoanime report [--json]

# Dry-run the recognition pipeline on a single file name (JSON output, no DB, no files touched)
uv run autoanime parse --name "[SubGroup] Show [01][1080p].mkv"
```

### 5. Start the API + WebUI

```bash
# Backend API (FastAPI + SSE + scheduler in one process; default 127.0.0.1:8000)
uv run python -m autoanime.api serve          # options: --host/--port/--dev

# WebUI frontend (inside frontend/)
npm install
npm run dev    # dev server, built-in mock by default; set VITE_USE_MOCK=0 for the real backend (/api proxied to 127.0.0.1:8000)
```

For production, `npm run build` and statically host `dist/` (the build defaults to the real API; reverse-proxy `/api` to the backend port 8000), or use the one-command containers:

```bash
docker compose up -d --build   # WebUI at http://127.0.0.1:3080
```

See [docs/DEPLOY.md](docs/DEPLOY.md) for container deployment, external dependencies (qBittorrent / Mikan / LLM) and deployment rules; see [docs/FAQ.md](docs/FAQ.md) for FAQs.

## Architecture at a Glance

```
Subscription (Mikan RSS polling)        Import (local directory scan)
        └──────────────┬──────────────────────┘
                       ▼
   L1 local rule recognition (anitopy + deterministic rules, zero network)
     · HIGH ───────────────────────────────────┐
     · MEDIUM/LOW                              ▼
   L2 parse memory (hits adopted directly)      │
     · miss → pre-L3 disambiguation (alias      │
       read-through → reference canonical)      │
                       ▼                        │
   L3 LLM recognition (optional; batching) → arbiter
     · reference normalization: romaji/alias →    │
       authoritative Chinese title                │
     · HIGH auto / MEDIUM pending / LOW manual    │
                       ▼                        ▼
   organize: hardlink + Sonarr-compatible naming + subtitles
                       │
                       ▼
   Upgrade engine (scoring gate: threshold / per-episode cap / copy fallback)
   Gap detection → backfill; mismatch recovery (A/B/C) → budget
```

### Module Map

| Module | Responsibility |
| --- | --- |
| `autoanime/pipeline/` | L1 rules / L2 memory / L3 LLM recognition, opportunistic batching, pre-L3 disambiguation, arbiter |
| `autoanime/memory/` | SQLite storage, parse memory, alias backfill, reference cache and governance |
| `autoanime/providers/` | Bangumi / TMDB reference adapters, LLM transport, notifications |
| `autoanime/gateway/` | qBittorrent / aria2 downloader interfaces, Mikan RSS fetching |
| `autoanime/scheduler/` | RSS polling, download polling and reconcile, startup reconcile, gap detection, cadence and JST clock |
| `autoanime/organize/` | hardlink moves, Sonarr-compatible naming, upgrade scoring, mismatch recovery, rollback |
| `autoanime/web/` | FastAPI assembly, SSE event stream, REST routes (series / subscriptions / rss_sources / pending / organize / audit / metrics / settings / events) |
| `autoanime/api/` | `python -m autoanime.api serve` entry point |
| `frontend/` | React 19 + Tailwind 4 + xyflow WebUI (8 pages, built with Vite) |

## Testing & Quality

- **1065 backend offline tests** (`uv run pytest -q`, fully offline) + **67 frontend tests** (`cd frontend && npm test`), all passing.
- **Five rounds of real-data acceptance**: round 1 fixed 4 issues; rounds 2–3 fixed 10 more, including two major defects (episode id vs episode number mix-up, upgrade target-slot overwrite); round 4 switched to brand-new naming styles (CJK bracket fansub packs / LoliHouse loose files / simplified+traditional twins) and fixed 4 more; round 5 re-imported the same batch — all memory-routed, zero LLM calls.
- **WebUI tested in a real browser**: all 8 pages exercised for interactions and the SSE event stream (which surfaced and fixed SSE wiring/subscription defects).
- Upgrade triggers/scoring are deterministic code, never inside the AI boundary; every recognition decision lands in the audit log — explainable and traceable.

## Security Notice

**Do not expose to the public internet.** AutoAnime is a single-user local tool with only simple token auth (`AUTOANIME_API_TOKEN`) — no user system, no HTTPS. Use it on your LAN with a token set; router port forwarding means handing your entire library management to the internet, which is explicitly unsupported. Mikan private-subscription `?token=` values are treated as secrets (stored in DB/env only, never logged or reported).

## Known Limitations (as-is)

- **Rollback file-level inverse operations**: v1 executes inverse operations in the organize domain only; anything else is recorded as `skipped` (never silently dropped).
- **confirm learns only, does not archive**: `confirm` writes the confirmation into parse memory but does not trigger archiving of that file — the top v2 gap; re-run `import` after confirming.
- **Settings are not persisted**: changes in the WebUI Settings page apply to the current process only; after restart, values fall back to `.env` / `autoanime.toml`.
- **Docker on real hardware**: the compose file has an automated structural self-check (`tests/unit/test_compose.py`); a real `docker compose up` is left for the user's environment.

## License

This project is licensed under the [MIT](./LICENSE) license.
