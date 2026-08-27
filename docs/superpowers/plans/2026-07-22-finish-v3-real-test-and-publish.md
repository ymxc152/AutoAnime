# AutoAnime v3 Real-Test and Publish Implementation Plan

> **Historical (2026-07-22).** Not current architecture or a runbook. Current product is Web + Worker, Schema v5, Agent 工作台. See `docs/00_文档总目录.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the simplified v3-only repository with real F-drive samples, review the full diff, then publish it on a new branch.

**Architecture:** Keep the existing scanner → parser → catalog → resolver → planner → executor pipeline unchanged. Build a non-overwriting hard-link test library from selected high-risk files, verify SQLite and filesystem state, review all working-tree changes, then branch, commit, and push only after every gate passes.

**Tech Stack:** Python 3.8, pytest, SQLite, PowerShell, Git.

---

### Task 1: Real F-drive integration test

**Files:**
- Read: `F:\下载`
- Create: `F:\AutoAnime_v3_RealTest_20260722\Input`
- Create: `F:\AutoAnime_v3_RealTest_20260722\Library`
- Create: `F:\AutoAnime_v3_RealTest_20260722\report.json`

- [ ] **Step 1:** Verify the test root does not already exist.
- [ ] **Step 2:** Select 18 high-risk source files covering multi-version episodes, nested season folders, subtitles, and absolute episode remapping.
- [ ] **Step 3:** Create source-preserving hard links under `Input`.
- [ ] **Step 4:** Run `python AutoAnimeMv3.py <Input> --output <Library> --mode link --apply --no-cache --report-json <report>`.
- [ ] **Step 5:** Verify input/output counts, hard-link identity, expected title/season/episode mappings, and SQLite organized state.

### Task 2: Repository review and verification

**Files:**
- Review: repository working tree
- Test: `tests/test_v3_*.py`

- [ ] **Step 1:** Run all v3 standard-library unittest tests.
- [ ] **Step 2:** Run compileall, CLI help, alias JSON validation, and `git diff --check`.
- [ ] **Step 3:** Inspect deleted legacy scope and added v3 scope for accidental loss, secrets, generated files, or stale references.
- [ ] **Step 4:** Dispatch an independent code reviewer and resolve all Critical or Important findings.

### Task 3: Publish branch

**Files:**
- Stage: all intended repository changes

- [ ] **Step 1:** Create branch `codex/autoanime-v3-refactor` from the externally managed detached worktree.
- [ ] **Step 2:** Stage only intended source, tests, documentation, and deletion changes.
- [ ] **Step 3:** Commit with a concise refactor message.
- [ ] **Step 4:** Push the branch to `origin` without force.
- [ ] **Step 5:** Report the branch, commit, remote result, real-test location, and verification evidence.
