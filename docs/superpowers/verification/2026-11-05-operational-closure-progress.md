# Execution ledger

Plan: `docs/superpowers/plans/2026-11-05-operational-closure.md`
Base: `9d5d637915cc4486707229604594094aa2467829`

Ruling: existing user approval covers the operational closure described in the assessment;
execute inline without repeating approval questions. Local plugin testing supersedes the
previous Aura VM procedure. Publication follows the already authorized release workflow.

Inventory: MCP 2.3 exposes public middleware and structured CallToolResult. AnyIO already
implements cancellable pooled worker processes; cancellation terminates them. yfinance
1.7 exposes network.retries and debug.hide_exceptions, but no global total-call deadline.
Reusing these avoids a custom transport, executor or retry loop.

Pre-flight: runtime and provider share per-request telemetry; immutable report content
must not receive dynamic timings. Archive publishes through audit.put; hooks use a separate
cross-process lock. The archive CLI uses audit publication. Direct report/cron APIs remain
synchronous; hard execution budgets are a property of the MCP transport.

Ruling: extend the public MCPServer.call_tool dispatch rather than intercepting middleware:
SDK handshake/security/extension checks must still happen before dispatch. The worker calls
the same SDK validation, with no transport context, and cannot recursively spawn workers.

Tasks 1-3: RED observed for text-only errors, missing null diagnostics, invalid FX admitted,
missing OHLCV columns, disk-full leakage, nonexistent archive CLI and concurrent hook loss.
Focused suites pass after fixes. First full suite: 367 passed, 26 live deselected (87.24 s).
Additional cancellation and worker-crash tests pass through real stdio (2/2, 37.97 s).
Unknown tool/configuration tests were observed RED, then GREEN (2/2, 7.09 s).

Ruling: the original 3-second test deadline also rejected cold healthy-worker startup
under concurrent verification. Use an 8-second budget against a 12-second provider fixture;
the test still proves forced termination and checks no late mutation after the original
operation would have completed. Production default remains 120 seconds.

Task 4 skill pressure check: a fresh agent with the old risk skill called the supplied
synthetic result "analisi prototipale". Re-reading the revised skill and repeating the
same scenario omitted that label while retaining USD, the supplied window, unofficial
Yahoo source, the null denominator and risk-free-rate warning. No financial calculation
or invented result was involved. Historical frozen source strings are not rewritten.

Fixture acceptance: 100 requests, concurrency 4, errors 0; startup 1.743 s, median 0.171 s,
p95 0.255 s, maximum 3.231 s, zero provider operations in replay, backup/restore/restart pass.
Live source acceptance: 100 requests, concurrency 4, errors 0; startup 0.770 s, median
0.265 s, p95 0.378 s, maximum 2.966 s, zero provider operations in replay, restore/restart
pass. JSON evidence retains IDs and report hash. These are bounded local samples.

Windows install investigation: ACL grants Davide FullControl and an owned cache canary
could be created/renamed/deleted. psutil independently verified active broker uvx/Python
processes, owned by both Codex app and VS Code, whose cwd is the 0.3.1 cache directory.
Their open current-directory handles explain the repeatable cache backup access denial.

Latest full source suite: 375 passed, 26 live deselected, 115.20 s. Ruff and diff checks
pass; offline wheel/sdist 0.5.0 build passes. A second live acceptance exits 0 with
100 requests, concurrency 4, errors 0, provider-free replay and backup/restore/restart
pass; committed JSON is that second sample (startup 0.858 s, median 0.219 s, p95 0.342 s,
maximum 2.722 s). Windows-native execution and final fresh review are still pending.

Ruling: tasks 1-3 share runtime cancellation, provider error and atomic storage contracts;
commit their verified operational closure together. Tests found and fixed non-finite
custom expressions returning Infinity; empty event studies now explain null statistics.
Native MCP -m startup must use absolute top-level imports because AnyIO re-executes the
parent entry module in its worker initialization; the actual -m portfolio-record test
exposed this and passed after the entrypoint fix.
