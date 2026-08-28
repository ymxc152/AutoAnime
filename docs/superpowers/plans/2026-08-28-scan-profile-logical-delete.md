# 扫描方案逻辑删除与历史快照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让扫描方案在有历史记录时也能从当前配置中逻辑删除，同时保留可追溯的方案快照、扫描运行、整理计划和回滚审计。

**Architecture:** 保留 `scan_profiles` 行并增加删除标记及删除快照；当前配置查询过滤已删除方案。扫描运行和整理计划分别保存创建时的方案 JSON 快照，历史链路继续使用现有外键和计划关系。删除事务会清理当前自动化配置，并阻止仍有活动扫描任务的方案被删除。

**Tech Stack:** FastAPI, SQLite/SQLAlchemy schema bootstrap, Python `unittest`, React, TanStack Query, Vitest, Vite.

---

### Task 1: Lock schema migration and snapshot shape with failing tests

**Files:**
- Modify: `tests/test_v3_web_schema.py`
- Modify: `tests/test_v3_api.py`
- Modify: `tests/test_v3_scan_service.py`

- [ ] **Step 1: Extend the migration test for schema version 6 and new columns**

Add assertions that a migrated database has `deleted_at` and `deleted_snapshot_json` on `scan_profiles`, plus `profile_snapshot_json` on both `scan_runs` and `plans`. Update the fresh-database idempotent version assertion from `[(5,)]` to `[(6,)]`; an existing database that already recorded v5 may contain both v5 and v6.

```python
def columns(self, table):
    connection = sqlite3.connect(str(self.database))
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(%s)" % table)}
    finally:
        connection.close()

def test_logical_delete_columns_are_migrated(self):
    self.migration_module().run_migrations(self.database)
    self.assertTrue({"deleted_at", "deleted_snapshot_json"}.issubset(self.columns("scan_profiles")))
    self.assertIn("profile_snapshot_json", self.columns("scan_runs"))
    self.assertIn("profile_snapshot_json", self.columns("plans"))
```

- [ ] **Step 2: Add an API regression test for snapshot persistence and hidden deletion**

Create a profile, run a scan that creates a plan, then delete the profile with its current revision. Assert the delete succeeds, `GET /api/v1/profiles` no longer contains it, `scan_profiles.deleted_at` and `deleted_snapshot_json` are populated, and both `scan_runs.profile_snapshot_json` and `plans.profile_snapshot_json` decode to the original name, mode, execution policy, source path, and library path.

- [ ] **Step 3: Add a service regression test for a profile with history**

Call `ProfileService.delete_profile` on a profile with a scan run and assert it returns `{"id": profile.id, "deleted": True}` without deleting the rows in `scan_runs`, `plans`, `scan_items`, or `review_items`.

- [ ] **Step 4: Run the new tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_v3_web_schema tests.test_v3_api.ApiTests.test_delete_profile_preserves_history tests.test_v3_scan_service -v
```

Expected: FAIL because schema version 6, snapshot columns, and logical deletion are not implemented yet.

### Task 2: Add reusable profile snapshot and database migration support

**Files:**
- Create: `autoanime_v3/db/profile_snapshots.py`
- Modify: `autoanime_v3/db/schema.py`
- Modify: `autoanime_v3/db/migrations.py`
- Modify: `tests/test_v3_web_schema.py`

- [ ] **Step 1: Add the snapshot builder used by services and migration**

Implement `build_profile_snapshot(connection, profile_id, profile_row=None, snapshot_at=None)` in `autoanime_v3/db/profile_snapshots.py`. It must read the profile, source/library roots, and optional `profile_rules` row and return a JSON-serializable dictionary with `profile_id`, `name`, `revision`, `source_root_id`, `source_path`, `library_root_id`, `library_path`, `mode`, `execution_policy`, `min_confidence`, `stability_seconds`, `watch_enabled`, `enabled`, `rules`, and `snapshot_at`. Raise `KeyError` when the profile does not exist.

- [ ] **Step 2: Declare the new columns in the SQLAlchemy schema**

Add `deleted_at` and nullable `deleted_snapshot_json` to `scan_profiles`. Add non-null `profile_snapshot_json` with default `'{}'` to `scan_runs` and `plans` so new databases have valid values immediately.

- [ ] **Step 3: Implement idempotent schema version 6 migration**

Set `SCHEMA_VERSION = 6`. Use `PRAGMA table_info` and `ALTER TABLE ... ADD COLUMN` for existing databases. Backfill empty history snapshots by joining each run/plan to its current profile through `build_profile_snapshot`; for plans prefer the corresponding scan-run snapshot. Backfill existing profile deletion snapshot fields only when a deleted row already has a value. Never overwrite a non-empty snapshot and never alter history statuses.

- [ ] **Step 4: Run migration tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_v3_web_schema -v
```

Expected: all schema tests pass, including repeated migration and the new column/backfill assertions.

### Task 3: Capture snapshots during scans and expose them from historical records

**Files:**
- Modify: `autoanime_v3/services/scans.py`
- Modify: `autoanime_v3/db/repositories/plans.py`
- Modify: `autoanime_v3/domain/entities.py`
- Modify: `autoanime_v3/api/app.py`
- Modify: `tests/test_v3_scan_service.py`
- Modify: `tests/test_v3_api.py`

- [ ] **Step 1: Make the scan test assert the exact snapshot**

After a scan completes, query `scan_runs.profile_snapshot_json` and `plans.profile_snapshot_json`, decode both, and assert they contain the same original profile revision and root paths. Change the profile configuration after the scan and assert the stored values remain unchanged.

- [ ] **Step 2: Capture the profile snapshot before analysis starts**

In `ScanService.run`, build the snapshot from the initially loaded profile row and carry it through the analysis. Pass `json.dumps(snapshot, ensure_ascii=False)` to `ScanRepository.create_run`, and insert the same JSON into `plans.profile_snapshot_json`. Add the column to the repository insert SQL.

- [ ] **Step 3: Include snapshots in plan views and list responses**

Add an optional `profile_snapshot` dictionary field to `PlanView`; decode `profile_snapshot_json` in `plan_from_rows`. For `GET /api/v1/plans`, decode each row’s snapshot and include a stable `profile_name` derived from it. For `GET /api/v1/operations`, join through plans and include the same `profile_name`/snapshot fields without requiring a live profile row.

- [ ] **Step 4: Run focused scan/history tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_v3_scan_service tests.test_v3_review_plan_service tests.test_v3_api -v
```

Expected: all focused tests pass and historical plan data remains readable after profile changes.

### Task 4: Implement transactional logical deletion and deleted-profile guards

**Files:**
- Modify: `autoanime_v3/services/profiles.py`
- Modify: `autoanime_v3/services/scans.py`
- Modify: `autoanime_v3/services/jobs.py`
- Modify: `autoanime_v3/api/app.py`
- Modify: `webui/src/api/errors.ts`
- Modify: `webui/src/api/errors.test.ts`
- Modify: `tests/test_v3_api.py`
- Modify: `tests/test_v3_roots_profiles.py`

- [ ] **Step 1: Add failing tests for delete behavior and active-job protection**

Test these cases:

```python
deleted = profiles.delete_profile(profile.id, profile.revision)
assert deleted == {"id": profile.id, "deleted": True}

with self.assertRaises(ValidationError):
    profiles.update_profile(profile.id, profile.revision, {"enabled": True})

active_job = queue.enqueue("scan", {"profile_id": profile.id, "paths": []}, "active", 0, now)
with self.assertRaises(ValidationError):
    profiles.delete_profile(profile.id, profile.revision)
```

The API test must also assert schedules and webhook sources bound to a successfully deleted profile are gone, and a deleted profile is excluded from `GET /api/v1/profiles`.

- [ ] **Step 2: Implement the logical-delete transaction**

In `ProfileService.delete_profile`, keep not-found and revision checks, reject active scan jobs whose status is `queued`, `running`, or `leased`, build and serialize the snapshot, then update the profile with `deleted_at = CURRENT_TIMESTAMP`, `deleted_snapshot_json = ?`, `enabled = 0`, and `watch_enabled = 0`. Delete matching schedules and webhook sources in the same unit of work. Do not increment `revision` and do not delete historical rows.

- [ ] **Step 3: Guard updates and new scans**

Reject `update_profile` when `deleted_at` is set. In `ScanService.run` and the job submission boundary, reject deleted profiles with a mapped domain error. Keep existing plans executable because the tombstone row and captured snapshot remain available; only new scans and automation triggers are blocked.

- [ ] **Step 4: Filter active configuration endpoints**

Update `GET /api/v1/profiles` to use `WHERE p.deleted_at IS NULL`. Ensure schedule/webhook list queries only expose active profile bindings. Preserve the current profile history count fields for non-deleted rows.

- [ ] **Step 5: Add Chinese error mappings and run focused API tests**

Map the new errors for an active scan job and deleted profile. Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_v3_api tests.test_v3_roots_profiles -v
```

Expected: all tests pass, including logical deletion of profiles with scan and plan history.

### Task 5: Update the WebUI deletion flow

**Files:**
- Modify: `webui/src/pages/ConsolePages.tsx`
- Modify: `webui/src/pages/ProfilesPage.test.tsx`
- Modify: `webui/e2e/console-flow.spec.ts`

- [ ] **Step 1: Add failing UI tests for deleting a historical profile**

Change the profile fixture to include history and a mocked `api.delete`. Assert the row still shows `删除`, clicking it after `window.confirm` calls `/profiles/1` with the current revision, and a successful response invalidates the profile list. Assert the confirmation text says the profile will disappear while history is retained.

- [ ] **Step 2: Render deletion for all non-deleted profiles**

Remove the history-based omission of the profile delete button. Keep the enable/disable action, and change the confirmation to:

```tsx
window.confirm(`删除扫描方案“${profile.name}”？方案会从当前配置中消失，但扫描记录、整理计划和回滚审计会保留。`)
```

The list API already filters logically deleted rows, so no extra client-side deleted state is needed.

- [ ] **Step 3: Run focused UI tests**

Run:

```powershell
corepack pnpm --dir webui test --run src/api/errors.test.ts src/pages/ProfilesPage.test.tsx
```

Expected: all focused tests pass.

- [ ] **Step 4: Update the end-to-end console flow**

Extend the existing profile lifecycle scenario to create a history-backed profile, delete it from the scan page, verify the row disappears after refresh, and verify the history endpoint still exposes the stored profile name. Keep the existing fresh-profile deletion coverage.

### Task 6: Full regression verification and handoff

**Files:**
- Modify: `README.md` only if the implementation exposes a user-facing lifecycle rule not already documented.

- [ ] **Step 1: Run the complete backend test suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: zero failures. Existing deprecation warnings from `zhconv` may remain and are not introduced by this feature.

- [ ] **Step 2: Run the complete frontend test suite**

```powershell
corepack pnpm --dir webui test --run
```

Expected: all feature-related tests pass. Any pre-existing unrelated failure must be reported with its exact test name and output.

- [ ] **Step 3: Build the frontend**

```powershell
corepack pnpm --dir webui build
```

Expected: TypeScript compilation and Vite build exit with code 0.

- [ ] **Step 4: Review the final diff**

```powershell
git diff --check
git status --short
git diff --stat
```

Confirm only the intended logical-delete, snapshot, migration, test, and documentation files changed; preserve unrelated user modifications and do not delete generated artifacts owned by the user.
