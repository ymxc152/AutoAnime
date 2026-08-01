# AutoAnime WebUI Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current `codex/webui-full-console` working tree into a committed, security-audited, Windows-tested release candidate and publish it as a pull request to `main`.

**Architecture:** Preserve the existing FastAPI/SQLite worker architecture and React SPA. Make only release-blocking changes: update the vulnerable router dependency, make Windows Playwright teardown reliable, align documented runtime requirements with actual dependencies, and publish the complete existing WebUI working tree.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, SQLite, React, TypeScript, Vite, pnpm, Vitest, Playwright, WinSW, Caddy.

---

### Task 1: Close production dependency advisories

**Files:**
- Modify: `webui/package.json`
- Modify: `webui/pnpm-lock.yaml`

- [x] **Step 1: Preserve the failing security check**

Run: `pnpm --dir webui audit --prod --audit-level high`

Expected: non-zero exit with the React Router XSS/open-redirect advisory.

- [x] **Step 2: Upgrade the router dependency**

Upgrade `react-router-dom` to the latest compatible v6 patch that removes high/critical advisories and regenerate `pnpm-lock.yaml` through pnpm. Current published releases cannot remove every moderate advisory without introducing an unavailable or RSC-affected major line, so the release gate rejects high and critical findings.

- [x] **Step 3: Verify security and compatibility**

Run:

```powershell
pnpm --dir webui audit --prod --audit-level high
pnpm --dir webui test --run
pnpm --dir webui build
```

Expected: audit reports no high or critical production vulnerabilities; tests and build exit 0.

### Task 2: Make Windows E2E teardown reliable

**Files:**
- Modify: `webui/e2e/console-flow.spec.ts`

- [x] **Step 1: Reproduce the teardown failure**

Run: `pnpm --dir webui e2e console-flow.spec.ts --grep "library title correction"`

Expected before the fix: business assertions complete, then teardown fails with `EPERM` at temporary-root deletion.

- [x] **Step 2: Implement bounded asynchronous cleanup**

Close child-process streams after the Web process exits and replace the synchronous deletion with an explicit bounded retry loop for Windows `EPERM`, `EBUSY`, and `ENOTEMPTY`. Re-throw all other errors and re-throw the final retry failure.

- [x] **Step 3: Verify the regression and full flow**

Run:

```powershell
pnpm --dir webui e2e console-flow.spec.ts --grep "library title correction"
pnpm --dir webui e2e console-flow.spec.ts
```

Expected: focused and complete suites exit 0.

### Task 3: Align release documentation

**Files:**
- Modify: `README.md`
- Modify: `README_en.md`
- Modify: `webui/package.json`

- [x] **Step 1: Correct runtime requirements**

Document Python 3.11 as the production runtime and Node.js 20+ with pnpm 10+ as the frontend build environment. Add matching `engines` metadata to `webui/package.json`.

- [x] **Step 2: Correct the Playwright command**

Use the package script form `pnpm --dir webui e2e` in both READMEs so Windows resolves the local Playwright binary consistently.

- [x] **Step 3: Verify documentation commands**

Run the documented unit-test, build, and E2E commands from the repository root and confirm they exit 0.

### Task 4: Verify and publish the complete branch

**Files:**
- Stage the complete intended working tree, including Web/API, worker, frontend, deployment templates, tests, documentation, and this plan.

- [x] **Step 1: Run release gates**

Run:

```powershell
python -m unittest discover -s tests -p "test_v3_*.py" -v
python -m compileall -q AutoAnimeMv3.py AutoAnimeWeb.py AutoAnimeWorker.py autoanime_v3
python -m pip check
pnpm --dir webui test --run
pnpm --dir webui build
pnpm --dir webui e2e console-flow.spec.ts
pnpm --dir webui audit --prod --audit-level high
git diff --check
```

Expected: all commands exit 0, with only the documented Windows symlink-permission test skipped.

- [ ] **Step 2: Commit and push**

Stage the complete intended WebUI release candidate, commit with a terse release description, and push `codex/webui-full-console` with upstream tracking.

- [ ] **Step 3: Open a draft PR**

Open a draft pull request targeting `main`. The body must summarize the V3 replacement, authenticated Web console, worker/automation, security controls, deployment templates, release fixes, and exact validation results.
