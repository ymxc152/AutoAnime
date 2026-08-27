# AutoAnime Web Console Implementation Plan

> **Historical (2026-07-23).** Construction checklist from before the console shipped. Unchecked boxes are not remaining work. Current behavior: `docs/11_v3_WebUI与数据层规划.md`. There is no backup-restore HTTP API; navigation is 首页/扫描/待处理/运行记录/资料库/设置.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows LAN, single-administrator Web console that manages AutoAnime scanning, review, immutable plans, safe execution, library editing, automation, metadata, backup, and recovery.

**Architecture:** Keep the existing v3 scanner/parser/resolver/planner/executor as the domain core. Add a SQLite-backed application layer, FastAPI Web/API process, lease-based Worker, and React/Vite frontend. The Web process only submits commands and queries state; the Worker is the sole owner of file-changing jobs.

**Tech Stack:** Python 3.11 production runtime, Python stdlib + SQLAlchemy 2/Alembic, FastAPI/Uvicorn, Argon2, Windows DPAPI adapter, watchdog, React, TypeScript, Vite, TanStack Query, React Router, Vitest, Playwright.

---

## File structure

### Existing files retained

- `autoanime_v3/scanner.py`: filesystem discovery and source snapshots.
- `autoanime_v3/parser.py`: filename parsing.
- `autoanime_v3/resolver.py`: evidence resolution.
- `autoanime_v3/planner.py`: core destination planning helpers.
- `autoanime_v3/executor.py`: low-level safe file operations; later adapted behind the operation service.

### New backend packages

- `autoanime_v3/domain/enums.py`: persisted state values.
- `autoanime_v3/domain/entities.py`: application-facing immutable DTOs.
- `autoanime_v3/domain/errors.py`: stable business error codes.
- `autoanime_v3/db/engine.py`: SQLite connection and transaction setup.
- `autoanime_v3/db/schema.py`: SQLAlchemy metadata and tables.
- `autoanime_v3/db/migrations.py`: schema version bootstrap and migration runner.
- `autoanime_v3/db/repositories/*.py`: focused persistence adapters.
- `autoanime_v3/services/*.py`: business use cases and transaction boundaries.
- `autoanime_v3/security/*.py`: passwords, sessions, CSRF and secret storage.
- `autoanime_v3/jobs/*.py`: persistent queue, Worker, Scheduler and Watcher.
- `autoanime_v3/api/*.py`: FastAPI app, dependencies, errors and routes.
- `autoanime_v3/integrations/*.py`: optional provider boundaries.

### New frontend

- `webui/src/app`: app shell, router and providers.
- `webui/src/api`: generated/shared API client and SSE client.
- `webui/src/components`: reusable code-native controls.
- `webui/src/features`: feature-owned queries, forms and views.
- `webui/src/pages`: route composition only.
- `webui/src/styles`: accepted visual system tokens and global styles.

---

### Task 1: Schema v3 foundation and migrations

**Files:**

- Create: `autoanime_v3/domain/enums.py`
- Create: `autoanime_v3/db/__init__.py`
- Create: `autoanime_v3/db/engine.py`
- Create: `autoanime_v3/db/schema.py`
- Create: `autoanime_v3/db/migrations.py`
- Create: `tests/test_v3_web_schema.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write the failing schema creation test**

```python
def test_schema_creates_web_console_tables(tmp_path):
    database = tmp_path / "library.sqlite3"
    run_migrations(database)
    names = table_names(database)
    assert {"users", "storage_roots", "scan_profiles", "jobs", "job_events"} <= names
    assert {"media_files", "file_locations", "plans", "plan_items"} <= names
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.test_v3_web_schema -v`

Expected: import failure for `autoanime_v3.db.migrations`.

- [ ] **Step 3: Define persisted enums and SQLAlchemy metadata**

Define exact string enums:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
```

Create the tables listed in `docs/11_v3_WebUI与数据层规划.md`, including foreign keys, unique normalized paths, revisions and timestamps.

- [ ] **Step 4: Implement SQLite setup and migration bootstrap**

`create_engine_for_path()` must enable foreign keys, WAL and busy timeout for every connection. `run_migrations()` must be idempotent and write the current schema version.

- [ ] **Step 5: Run the focused and existing tests**

Run:

```powershell
python -m unittest tests.test_v3_web_schema -v
python -m unittest discover -s tests -p "test_v3_*.py" -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add requirements.txt autoanime_v3/domain autoanime_v3/db tests/test_v3_web_schema.py
git commit -m "feat: add web console schema foundation"
```

### Task 2: File facts, roots, profiles and repositories

**Files:**

- Create: `autoanime_v3/domain/entities.py`
- Create: `autoanime_v3/domain/errors.py`
- Create: `autoanime_v3/db/repositories/roots.py`
- Create: `autoanime_v3/db/repositories/profiles.py`
- Create: `autoanime_v3/db/repositories/library.py`
- Create: `autoanime_v3/services/roots.py`
- Create: `autoanime_v3/services/profiles.py`
- Create: `tests/test_v3_roots_profiles.py`
- Create: `tests/test_v3_file_facts.py`

- [ ] **Step 1: Write failing root safety tests**

Test exact behaviors:

- normalized Windows paths are compared case-insensitively;
- duplicate roots are rejected;
- output equal to or beneath source is rejected;
- paths outside registered roots cannot be converted into operation targets.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_roots_profiles -v`

Expected: service imports fail.

- [ ] **Step 3: Implement root and profile services**

Public interfaces:

```python
class RootService:
    def create_root(self, kind: str, path: Path) -> StorageRoot: ...
    def validate_root(self, root_id: int) -> RootHealth: ...

class ProfileService:
    def create_profile(self, command: CreateProfile) -> ScanProfile: ...
    def update_profile(self, profile_id: int, revision: int, patch: dict) -> ScanProfile: ...
```

- [ ] **Step 4: Write failing multi-location file tests**

Prove one media object can own a source location and a library hardlink location. Prove path reuse with a changed file creates a new media generation and marks the old location `replaced`.

- [ ] **Step 5: Implement the library repository**

Do not expose ORM rows. Return frozen DTOs and require explicit Unit of Work commits.

- [ ] **Step 6: Run tests and commit**

Run: `python -m unittest tests.test_v3_roots_profiles tests.test_v3_file_facts -v`

Expected: all pass.

### Task 3: Authentication, sessions, CSRF and secret storage

**Files:**

- Create: `autoanime_v3/security/passwords.py`
- Create: `autoanime_v3/security/sessions.py`
- Create: `autoanime_v3/security/csrf.py`
- Create: `autoanime_v3/security/secrets.py`
- Create: `autoanime_v3/services/auth.py`
- Create: `autoanime_v3/db/repositories/auth.py`
- Create: `tests/test_v3_auth_security.py`

- [ ] **Step 1: Write failing password and session tests**

```python
def test_login_returns_random_session_and_never_password_hash(...): ...
def test_expired_or_revoked_session_is_rejected(...): ...
def test_state_changing_request_requires_matching_csrf_token(...): ...
def test_secret_read_returns_configured_flag_not_plaintext(...): ...
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_auth_security -v`

- [ ] **Step 3: Implement security primitives**

- Use Argon2id through `argon2-cffi`.
- Hash random session and webhook tokens before storage.
- Compare tokens with constant-time comparison.
- Implement `SecretStore` protocol and Windows `DpapiSecretStore`; provide encrypted-file fallback for tests and non-Windows development.

- [ ] **Step 4: Test login throttling and bootstrap behavior**

The first-run bootstrap command creates exactly one administrator. Re-running without an explicit reset must fail.

- [ ] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_v3_auth_security -v`

### Task 4: Persistent jobs, leases and event stream

**Files:**

- Create: `autoanime_v3/db/repositories/jobs.py`
- Create: `autoanime_v3/jobs/queue.py`
- Create: `autoanime_v3/jobs/worker.py`
- Create: `autoanime_v3/services/jobs.py`
- Create: `tests/test_v3_jobs.py`

- [ ] **Step 1: Write failing queue tests**

Test:

- enqueue idempotency;
- one Worker lease owner;
- heartbeat renewal;
- expired lease becomes interrupted;
- ordered event sequence;
- cancellation only at safe boundaries.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_jobs -v`

- [ ] **Step 3: Implement the queue state machine**

Public interface:

```python
class JobQueue:
    def enqueue(self, job_type: str, payload: dict, idempotency_key: str) -> Job: ...
    def lease_next(self, worker_id: str, lease_seconds: int) -> Optional[Job]: ...
    def heartbeat(self, job_id: int, worker_id: str) -> None: ...
    def append_event(self, job_id: int, event_type: str, payload: dict) -> JobEvent: ...
    def complete(self, job_id: int, worker_id: str) -> None: ...
```

- [ ] **Step 4: Test crash recovery**

Simulate Worker termination after lease acquisition and verify the next Worker does not silently execute an unknown file-changing job.

- [ ] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_v3_jobs -v`

### Task 5: Scan jobs, review queue and immutable plans

**Files:**

- Create: `autoanime_v3/services/scans.py`
- Create: `autoanime_v3/services/reviews.py`
- Create: `autoanime_v3/services/plans.py`
- Create: `autoanime_v3/db/repositories/scans.py`
- Create: `autoanime_v3/db/repositories/reviews.py`
- Create: `autoanime_v3/db/repositories/plans.py`
- Create: `tests/test_v3_scan_service.py`
- Create: `tests/test_v3_review_plan_service.py`

- [ ] **Step 1: Write the failing scan orchestration test**

Given a temporary source root containing safe, uncertain and conflicting files, verify the service records file facts, identification evidence, review items and one draft plan without touching the output root.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_scan_service -v`

- [ ] **Step 3: Implement scan orchestration**

Call existing scanner/resolver/planner through injected adapters. Persist a scan snapshot before building reviews and plans.

- [ ] **Step 4: Write stale-plan and approval tests**

Prove:

- approved plans cannot be modified;
- changed source identity makes a plan stale;
- changed profile or rule revision makes a plan stale;
- conflicts prevent approval.

- [ ] **Step 5: Implement review resolution and plan approval**

Resolving a review must generate a new plan revision, never mutate the previous plan.

- [ ] **Step 6: Run tests and commit**

Run: `python -m unittest tests.test_v3_scan_service tests.test_v3_review_plan_service -v`

### Task 6: Operation batches, execution and rollback integration

**Files:**

- Create: `autoanime_v3/services/operations.py`
- Create: `autoanime_v3/db/repositories/operations.py`
- Modify: `autoanime_v3/executor.py`
- Create: `tests/test_v3_operation_service.py`
- Extend: `tests/test_v3_executor_safety.py`

- [ ] **Step 1: Write failing all-batch preflight tests**

Verify no file changes occur when any item has a changed source, occupied target, root escape, cross-volume link or stale plan.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_operation_service -v`

- [ ] **Step 3: Implement operation service**

The service must:

1. acquire leases;
2. preflight every plan item;
3. create an operation batch;
4. call low-level executor functions;
5. record result identity and SHA-256;
6. compensate in reverse order on failure;
7. reconcile file locations.

- [ ] **Step 4: Write and pass rollback safety tests**

Cover replaced destinations, changed digest, recreated source path and orphan staging.

- [ ] **Step 5: Run full file safety suite and commit**

Run:

```powershell
python -m unittest tests.test_v3_operation_service tests.test_v3_executor_safety tests.test_v3_planner_executor -v
```

### Task 7: Rules, library corrections and metadata boundary

**Files:**

- Create: `autoanime_v3/services/rules.py`
- Create: `autoanime_v3/services/changes.py`
- Create: `autoanime_v3/integrations/metadata.py`
- Create: `autoanime_v3/db/repositories/rules.py`
- Create: `autoanime_v3/db/repositories/metadata.py`
- Create: `tests/test_v3_rules_changes.py`
- Create: `tests/test_v3_metadata.py`

- [ ] **Step 1: Write failing rule revision tests**

Test JSON schema validation, immutable revisions, activation, rollback and decision hash changes.

- [ ] **Step 2: Write failing change-request tests**

Test title/season/episode changes, manual locks, base revision conflicts, path impact preview and reversal.

- [ ] **Step 3: Implement rules and changes**

Rules activate only after validation. Changes affecting paths create plan items and use the normal operation service.

- [ ] **Step 4: Implement read-only metadata adapter contract**

Metadata failures return an unavailable state and never fail scan or execution jobs.

- [ ] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_v3_rules_changes tests.test_v3_metadata -v`

### Task 8: Scheduler, Watcher, Webhook and backup

**Files:**

- Create: `autoanime_v3/jobs/scheduler.py`
- Create: `autoanime_v3/jobs/watcher.py`
- Create: `autoanime_v3/services/webhooks.py`
- Create: `autoanime_v3/services/backups.py`
- Create: `tests/test_v3_automation.py`
- Create: `tests/test_v3_backups.py`

- [ ] **Step 1: Write failing automation tests**

Test event debounce, stable-file window, ignored temporary suffixes, active-job coalescing, schedule deduplication and webhook root scope.

- [ ] **Step 2: Implement automation producers**

Scheduler, Watcher and Webhook may only enqueue scan jobs. They cannot invoke the executor.

- [ ] **Step 3: Write failing online-backup tests**

Test backup during WAL activity, checksum, retention, maintenance-mode restore and schema verification.

- [ ] **Step 4: Implement backup service**

Use the SQLite online backup API. Sanitized exports exclude secret ciphertext.

- [ ] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_v3_automation tests.test_v3_backups -v`

### Task 9: FastAPI app and API contract

**Files:**

- Create: `autoanime_v3/api/app.py`
- Create: `autoanime_v3/api/dependencies.py`
- Create: `autoanime_v3/api/errors.py`
- Create: `autoanime_v3/api/routes/*.py`
- Create: `tests/test_v3_api.py`
- Create: `tests/test_v3_api_security.py`

- [ ] **Step 1: Write failing auth and health API tests**

Use FastAPI TestClient/httpx ASGI transport. Verify bootstrap/login/logout/me, Cookie flags, CSRF, `/health/live`, `/health/ready` and standard error envelopes.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_v3_api tests.test_v3_api_security -v`

- [ ] **Step 3: Implement app factory and dependencies**

```python
def create_app(settings: ServerSettings, services: ServiceContainer) -> FastAPI:
    ...
```

No module import may create a database, start a Worker or bind a socket.

- [ ] **Step 4: Implement endpoint groups**

Implement the endpoints specified in the design document, including cursor pagination, idempotency keys, revisions and SSE `Last-Event-ID`.

- [ ] **Step 5: Run API and backend suites and commit**

Run:

```powershell
python -m unittest tests.test_v3_api tests.test_v3_api_security -v
python -m unittest discover -s tests -p "test_v3_*.py" -v
```

### Task 10: React application shell and design system

**Files:**

- Create: `webui/package.json`
- Create: `webui/vite.config.ts`
- Create: `webui/src/main.tsx`
- Create: `webui/src/app/App.tsx`
- Create: `webui/src/app/router.tsx`
- Create: `webui/src/styles/tokens.css`
- Create: `webui/src/styles/global.css`
- Create: `webui/src/components/AppShell.tsx`
- Create: `webui/src/components/AppShell.test.tsx`

- [ ] **Step 1: Write failing AppShell test**

Verify the approved navigation copy, selected state, keyboard focus and responsive collapse.

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir webui test --run src/components/AppShell.test.tsx`

- [ ] **Step 3: Implement accepted design tokens**

Use true neutral near-white background, graphite text, deep indigo primary, amber/red semantics, 220px desktop sidebar, 8px radius, thin borders, table/rail container model and no decorative gradients.

- [ ] **Step 4: Implement route shell**

Keep App as composition glue. Import icons directly from the chosen icon package; avoid a barrel import.

- [ ] **Step 5: Run tests/build and commit**

Run:

```powershell
pnpm --dir webui test --run
pnpm --dir webui build
```

### Task 11: Dashboard, jobs, reviews and plans UI

**Files:**

- Create: `webui/src/api/client.ts`
- Create: `webui/src/api/events.ts`
- Create: `webui/src/features/dashboard/*`
- Create: `webui/src/features/jobs/*`
- Create: `webui/src/features/reviews/*`
- Create: `webui/src/features/plans/*`
- Create: corresponding `*.test.tsx`

- [ ] **Step 1: Write failing Dashboard tests**

Test active task progress, operational counts, scan-root status, activity and system heartbeat using real component state and a test server adapter.

- [ ] **Step 2: Implement Dashboard**

Match the accepted concept. No fake charts or additional metrics.

- [ ] **Step 3: Write failing Job and SSE tests**

Test reconnect with last event ID, ordered event rendering, safe cancel states and retry visibility.

- [ ] **Step 4: Implement review and plan workflows**

Include filters, evidence, selected-row inspector, stale/conflict handling and disabled approval while conflicts remain.

- [ ] **Step 5: Run feature tests/build and commit**

Run: `pnpm --dir webui test --run && pnpm --dir webui build`

### Task 12: Library, settings, automation, rules and history UI

**Files:**

- Create: `webui/src/features/library/*`
- Create: `webui/src/features/profiles/*`
- Create: `webui/src/features/rules/*`
- Create: `webui/src/features/operations/*`
- Create: `webui/src/features/settings/*`
- Create: corresponding `*.test.tsx`

- [ ] **Step 1: Write failing Library and correction tests**

Test show/season/episode navigation, multi-location display, evidence, metadata-unavailable state, change preview and stale revision errors.

- [ ] **Step 2: Implement Library and change flow**

High-risk edits always show a migration preview; do not provide direct inline save for path-affecting fields.

- [ ] **Step 3: Write failing configuration and history tests**

Test path validation, secret non-disclosure, schedule/watcher controls, rule activation impact and rollback refusal.

- [ ] **Step 4: Implement remaining pages**

Use feature-local queries and forms; do not place all server state in a global store.

- [ ] **Step 5: Run tests/build and commit**

Run: `pnpm --dir webui test --run && pnpm --dir webui build`

### Task 13: Packaging, browser E2E and Windows verification

**Files:**

- Create: `AutoAnimeWeb.py`
- Create: `AutoAnimeWorker.py`
- Create: `deploy/windows/AutoAnimeWeb.xml`
- Create: `deploy/windows/AutoAnimeWorker.xml`
- Create: `deploy/windows/Caddyfile.example`
- Create: `webui/e2e/*.spec.ts`
- Modify: `README.md`
- Modify: `README_en.md`
- Modify: `docs/00_文档总目录.md`

- [ ] **Step 1: Write E2E tests before completing entry points**

Cover login, first root/profile, manual scan, review resolution, plan approval, execution, operation history and safe rollback using temporary directories.

- [ ] **Step 2: Implement CLI entry points and static frontend serving**

Web and Worker entry points must share the same settings and database but never start each other implicitly during import.

- [ ] **Step 3: Verify Windows service definitions**

Confirm ProgramData paths, service account instructions, restart policy, firewall restriction and optional Caddy HTTPS.

- [ ] **Step 4: Run full verification**

```powershell
python -m unittest discover -s tests -p "test_v3_*.py" -v
pnpm --dir webui test --run
pnpm --dir webui build
pnpm --dir webui exec playwright test
```

Expected: zero failures.

- [ ] **Step 5: Visual fidelity verification**

Run the app in the built-in browser, capture the dashboard and plan-review screens at 1536x1024 and a mobile viewport. Inspect accepted concepts and implementation screenshots with `view_image`. Record at least five comparison points for layout, typography, palette, table density, sidebar, inspector, controls and responsive behavior.

- [ ] **Step 6: Final safety checks**

- No secret values in API fixtures, browser bundles, logs or snapshots.
- No output target escapes registered roots.
- No debug routes or seed credentials.
- No generated placeholder art used as actual UI.
- Existing v3 parser/resolver/executor regression tests remain green.

- [ ] **Step 7: Commit**

```powershell
git add .
git commit -m "feat: add full AutoAnime Web console"
```

---

## Execution handoff

Implement tasks in order. Tasks 1-9 establish the safe backend and API; Tasks 10-12 implement the accepted interface; Task 13 performs packaging and complete functional and visual verification. Every production behavior follows RED, verified RED, GREEN, verified GREEN, then refactor.

