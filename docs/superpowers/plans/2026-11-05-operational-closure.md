# Standalone operational closure implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with TDD and one fresh final review.

**Goal:** Close the measured operational gaps and ship the updated standalone plugin.

**Architecture:** MCP transport dispatch delegates tool work to AnyIO's existing cancellable process
pool. Existing report functions and immutable storage remain the numerical/data boundary.

**Tech Stack:** Python 3.13+, MCP 2.3+, AnyIO, yfinance, filelock, pytest, ZIP.

**Spec:** `docs/superpowers/specs/2026-11-05-operational-closure-design.md`.

## Global Constraints

- Work directly on main; no Aura VM, DB or deployment dependency.
- Preserve immutable records and provider-free replay; no new indicators.
- Default execution budget 120 seconds including queueing; at most two workers.
- No credentials, holdings or provider exception text in operational logs.
- No prototype label in current skills/product descriptions; retain source limitations.
- Each modified source file stays below 600 lines.

## Review Focus

- Timed-out/cancelled workers must stop before subsequent mutations can occur.
- Worker reuse must not reuse request timing state or the wrong data directory.
- Malformed/nested input errors must not leak values or become successful MCP results.
- Restore must prevalidate all members and refuse traversal, duplicates and corrupt targets.
- Concurrent hook writes and an interrupted publish must preserve existing data.

### Task 1: Bounded MCP execution and structured errors

**Files:** create `runtime.py`, `telemetry.py`; modify `errors.py`, `server.py`, `models.py`;
test `tests/test_runtime.py` and `tests/runtime_fixture.py`.

**Interfaces:** `BoundedServer` extends public MCP dispatch; `invoke(name, arguments)` returns a
CallToolResult plus telemetry. `error_payload(error)` returns a sanitized error object.

- [x] Write and run failing wire tests for validation constraints, missing snapshot,
  hard timeout, cancellation, worker recovery, concurrency and request metadata.
- [x] Implement dispatch with AnyIO fail_after/to_process.run_sync(cancellable=True),
  bounded capacity, native SDK validation and per-request telemetry.
- [x] Run the focused tests and existing contract/snapshot tests; commit.

### Task 2: Provider failures and precise nulls

**Files:** modify `data.py`, `analysis.py`, `fundamentals.py`; create `diagnostics.py`;
test `tests/test_provider_failures.py`, `tests/test_diagnostics.py`.

**Interfaces:** provider operations populate telemetry and raise typed DataError;
metric diagnostics add reason_code, reason, missing_inputs/invalid_inputs when null.

- [x] Write failing tests for provider timeout/rate limit/connection failure, invalid
  frames and finite FX; null ratios, flat risk, short samples and missing ETF profile.
- [x] Reuse native yfinance retries, explicit history timeout and exception controls;
  instrument provider calls, sanitize errors, implement field-specific diagnostics.
- [x] Run provider/diagnostic and existing numerical/report tests; commit.

### Task 3: Archive recovery and concurrent hook safety

**Files:** create `archive.py`; modify `audit.py`, `hooks.py`, `pyproject.toml`, `uv.lock`;
test `tests/test_archive.py`, `tests/test_storage_failures.py`.

**Interfaces:** CLI `broker-analysis-archive verify|backup PATH|restore PATH` emits JSON;
backup/restore uses verified content-addressed records and merge-only publication.

- [x] Write failing tests for roundtrip restore, hostile ZIP members, corruption,
  conflicting existing records, disk-full and interrupted atomic publication.
- [x] Add fsync for published directory entries, sanitized write failures, read-only
  verification, atomic ZIP publication, prevalidated restore and filelock hook updates.
- [x] Run archive/storage/hooks and portfolio/snapshot regressions; commit.

### Task 4: Operational acceptance and skills

**Files:** create `scripts/operational_acceptance.py`, `docs/OPERATIONS.md`;
modify current plugin skills/descriptions, CI and packaging metadata.

- [x] Run a baseline skill pressure scenario; preserve source/unit/window/warning/null
  handling while removing the obsolete label. Check behavior with the revised skill.
- [x] Add a repeatable standalone fault/load/restart/restore acceptance using MCP SDK.
  Record measured limits and operating/backup procedures, including mutable hook state.
- [x] Run full offline suite, Ruff and package build; obtain one fresh code review;
  fix Important/Critical findings with RED/GREEN tests; commit.

### Task 5: Install, live verification and release

**Files:** plugin manifests/immutable launchers, release notes, verification report.

- [ ] Pin the reviewed runtime source commit; tag/push the release and verify CI.
- [ ] Update/install the local plugin and execute real standalone MCP acceptance on
  the installed artifact, including live acquisition and offline snapshot replay.
- [ ] Record exact versions, tests, load/fault/restore evidence and limits; commit/push
  the completed evidence and verify the corresponding CI.
