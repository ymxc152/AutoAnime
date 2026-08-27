# 扫描方案历史记录保护 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scan profiles with scan or plan history disable-only in the WebUI while preserving historical records; keep deletion for fresh profiles.

**Architecture:** Keep the existing backend delete guard and optimistic update flow. Extend the profile-list query with correlated history counts so the frontend can choose the correct action without per-row requests. Use the existing profile PATCH endpoint to toggle `enabled`, and keep manual scans disabled for inactive profiles.

**Tech Stack:** FastAPI, SQLite, Python `unittest`, React, TanStack Query, Vitest, TypeScript.

---

### Task 1: Lock the backend/API behavior with regression tests

**Files:**
- Modify: `tests/test_v3_api.py:288-326`

- [ ] **Step 1: Extend the existing profile deletion test**

After the history-backed delete assertion, call `PATCH /api/v1/profiles/{used.id}` with `{"revision": used.revision, "patch": {"enabled": false}}`; assert `200`, assert the response has `enabled == false`, and query `scan_runs` and `plans` directly to assert both counts remain greater than zero.

Also fetch `GET /api/v1/profiles` and assert the history-backed item exposes positive `scan_runs` and `plans` counts while the fresh item exposes zero for both before deleting the fresh item.

- [ ] **Step 2: Run the focused test to verify RED**

Run: `python -m unittest tests.test_v3_api.ApiTests.test_delete_profile_blocks_history_and_deletes_fresh -v`

Expected: FAIL because the profile list does not expose `scan_runs`/`plans` and the test cannot assert the new fields.

### Task 2: Return profile history counts from the API

**Files:**
- Modify: `autoanime_v3/api/app.py:512-514`

- [ ] **Step 1: Replace the raw profile list query**

Use one query with correlated counts:

```python
@app.get("/api/v1/profiles")
def list_profiles(user=Depends(current_user)):
    return {
        "items": rows(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM scan_runs sr WHERE sr.profile_id = p.id) AS scan_runs,
                   (SELECT COUNT(*) FROM plans pl WHERE pl.profile_id = p.id) AS plans
            FROM scan_profiles p
            ORDER BY p.id
            """
        ),
        "next_cursor": None,
    }
```

- [ ] **Step 2: Run the focused test to verify GREEN**

Run: `python -m unittest tests.test_v3_api.ApiTests.test_delete_profile_blocks_history_and_deletes_fresh -v`

Expected: PASS, including the existing delete protection and the new history-count assertions.

### Task 3: Expose disable/enable instead of delete for historical profiles

**Files:**
- Modify: `webui/src/pages/ConsolePages.tsx:64-75`
- Modify: `webui/src/pages/ProfilesPage.test.tsx`

- [ ] **Step 1: Add failing UI tests for historical and fresh actions**

Replace the inline mocked profile with a mutable `profileFixture` object. Set its initial `scan_runs` and `plans` values to `1`. Assert the row contains a `停用` button and no `删除` button. Click `停用`, then assert `apiPatch` was called with `/profiles/1` and a patch containing `{ enabled: false }`.

Add a test that sets `profileFixture.scan_runs = 0` and `profileFixture.plans = 0` before rendering. Assert that row renders `删除` and does not render `停用` as the history action.

- [ ] **Step 2: Run the focused UI test to verify RED**

Run: `pnpm --dir webui test --run src/pages/ProfilesPage.test.tsx`

Expected: FAIL because the page currently renders `删除` for every profile and has no toggle mutation.

- [ ] **Step 3: Implement the minimal UI behavior**

Add a profile toggle mutation that calls the existing PATCH endpoint with the current `revision` and `{ enabled: !Boolean(profile.enabled) }`. Compute history from `Number(profile.scan_runs || 0) > 0 || Number(profile.plans || 0) > 0`. Render:

```tsx
<button
  className="text-button"
  onClick={() => toggleProfile.mutate(profile)}
>
  {profile.enabled ? '停用' : '启用'}
</button>
{!hasHistory ? (
  <button
    className="text-button danger"
    onClick={() => {
      if (window.confirm(`删除扫描方案“${profile.name}”？`)) deleteProfile.mutate(profile)
    }}
  >
    <Trash2 size={13} />删除
  </button>
) : null}
```

Keep `disabled={!profile.enabled}` on the manual scan button, invalidate `['profiles']` after a successful toggle, and route errors to the existing `actionError` message.

- [ ] **Step 4: Run the focused UI test to verify GREEN**

Run: `pnpm --dir webui test --run src/pages/ProfilesPage.test.tsx`

Expected: PASS with the historical row showing only enable/disable and the fresh row retaining delete.

### Task 4: Run regression verification

**Files:**
- No additional files.

- [ ] **Step 1: Run backend profile/API tests**

Run: `python -m unittest tests.test_v3_api tests.test_v3_roots_profiles -v`

Expected: zero failures.

- [ ] **Step 2: Run the complete frontend test suite**

Run: `pnpm --dir webui test --run`

Expected: zero failures.

- [ ] **Step 3: Build the frontend**

Run: `pnpm --dir webui build`

Expected: TypeScript compilation and Vite build exit with code 0.

- [ ] **Step 4: Review the final diff**

Run: `git diff --check; git status --short; git diff --stat`

Expected: no whitespace errors; only the plan/spec files and the targeted backend/frontend/test files are changed.
