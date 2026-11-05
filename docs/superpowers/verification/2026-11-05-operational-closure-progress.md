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

Fresh final review (read-only, b409804, gpt-6-astra): R1 Important, application SpecError
messages echoed rejected values/operator allowlists; R2 Important, custom latest values
silently selected an older finite observation; R3 Minor, nested-source warmup misclassified.
Ruling: regrade R3 Important because wrong null taxonomy suggests the wrong remediation;
fix it in the same single RED/GREEN pass. Dedicated regressions observed R1 (3/4 cases),
R2 and R3 fail before changes. Safe public SpecError messages/constraint details, final-bar
evaluation and shared finite-source warmup evidence address them.

Final: Ruling on declined chart collisions: concurrent analyses must preserve returned
artifacts even though generated charts are outside record backup. Regraded Important;
two different same-second real charts reproduced overwrite (RED). Unique artifact names
fix it within this pass. Process-death-before-hook-publication coverage was also added.

Other declined dimensions stand as documented: direct-process static launch configuration,
filesystem/power-loss and hostile local writers, at-least-once external delivery, additional
complete objects after interrupted restore, mutable state backed up while stopped, and
bounded local capacity rather than an uptime SLA. Package pins/CI/installation follow.

Windows-native source: 23 operational/archive/storage tests pass in 120.88 s after using
a fresh explicit --basetemp (the preexisting global pytest temp root denied access).
Windows wheel acceptance: 100 live-snapshot replays, concurrency 4, errors 0 and full
backup/restore/restart pass; first uv install included in startup 11.566 s. Linux fixture
soak: 1000 replays, concurrency 8, errors 0, median 0.268 s, p95 0.510 s, max 3.383 s,
zero provider operations and restore/restart pass. These precede the review fix pass.

Single review fix pass complete: all Important findings have RED/GREEN regressions;
full final source suite 383 passed, 26 live deselected in 191.54 s. Ruff/diff and locked
resolution pass. Offline wheel/sdist rebuilt. Windows changed-path acceptance is 16/16
passing in 58.40 s, including private-input/configuration redaction and latest-bar nulls.
No second independent review is claimed. Remaining publication/installation/CI steps
are deliberately separate from source acceptance.

Task 5: complete. Runtime source 843b681, packaging/tag b043643, local installed/enabled
version 0.5.0 verified from cache manifest. Native installed launcher: 1000 live-snapshot
replays, concurrency 8, errors 0, provider operations 0; backup/restore/restart pass.
Initialization asserts no obsolete product label. Cached startup 3.879 s; first Git
artifact setup 25.344 s is recorded separately. Main CI 42011264135 and tag CI 42011270598
both completed success. Full final evidence is in 2026-11-05-operational-closure.md.
The old Windows cache lock was released by terminating only broker MCP process trees;
the unchanged install command succeeded immediately afterward. Preloaded current-chat
tool descriptions require session reload; new MCP sessions verify the installed artifact.
