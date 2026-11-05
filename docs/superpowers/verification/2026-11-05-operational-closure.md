# Standalone operational closure — 5 November 2026

## Delivered artifact

- Broker Analysis **0.5.0**, annotated tag `v0.5.0` at
  `b0436436a19c6aa07a6fdd0ba68f2d248217fd6c`.
- Both launchers select reviewed runtime source
  `843b681e44d2bd674f03572352d56ff28d7359e3` by immutable full commit.
- Installed and enabled locally with `codex plugin add broker-analysis@broker-analysis-mcp`:
  `C:\Users\Davide\.codex\plugins\cache\broker-analysis-mcp\broker-analysis\0.5.0`.
- Installed manifest was read back and its launcher exercised directly via the MCP SDK.
  It exposes **15 tools** and initialize instructions without the old product label.
  No Aura VM, database or deployment was used for this closure.

## Checks actually executed

| Check | Result |
|---|---|
| Full final source suite, Python 3.13 on WSL | **383 passed**, 26 live deselected, 191.54 seconds |
| Windows operational/archive/storage tier before review fixes | **23 passed**, 120.88 seconds |
| Windows changed-path tier after review fixes | **16 passed**, 58.40 seconds |
| Ruff, diff check, locked dependency resolution | Pass |
| Offline wheel/sdist build, version 0.5.0 | Pass |
| Packaging manifest coherence | **5 passed** |
| CI on packaged main | [42011264135: success](https://github.com/chetto1983/broker-analysis-mcp/actions/runs/42011264135) |
| CI on v0.5.0 | [42011270598: success](https://github.com/chetto1983/broker-analysis-mcp/actions/runs/42011270598) |
| Installed Git launcher on native Windows | **1,000 replay requests**, concurrency **8**, **0 errors** |
| Provider operations during those replays | **0** |
| Backup, restore into a fresh state root, restart and exact report replay | Pass |

The installed test acquired AAPL/SPY from Yahoo, checked standard/custom agreement and
benchmark identity, then replayed one frozen dataset. This is not 1,000 independent Yahoo
acquisitions. The recorded [JSON](2026-11-05-operational-installed-windows.json) contains
the snapshot ID, complete technical-report hash, timing sample and restore result.

Cached installed startup was **3.879 seconds**. Client-observed replay median was
**0.457 seconds**, p95 **0.624 seconds**, maximum **5.544 seconds**, including initial
worker startup/queueing. The first Git artifact launch took **25.344 seconds** including
first fetch/build/setup; it is recorded separately and is not called a cached start.
These are local samples, not latency or availability guarantees.

The rehearsal uses isolated temporary data directories and removes them after its
restore/restart checks. Recorded IDs identify that measured temporary dataset, not a
published/shared archive. Operators can retain a rehearsal's data with `--workdir`.

## Fault and integrity evidence

Real stdio tests exercise execution timeout, cancellation and worker death followed by
successful work in the same MCP session. The timeout/cancellation tests also wait past
the simulated operation's original completion and check that no late write occurs.
Fixtures inject provider rate limit, timeout, connection loss, missing data, malformed
OHLCV and invalid FX. Failed acquisitions publish no partial market snapshot.

Archive tests cover traversal, duplicate members, hash corruption, missing dependencies,
corrupt existing destinations, restore size limits and dependency-first publication.
Storage tests exercise disk-full/fsync errors and process death before hook publication.
Existing hook state survives and subsequent updates succeed. Both concurrent threads
and actual MCP workers preserve all created hook configurations.

## Independent review and disposition

One fresh read-only review of `9d5d637..b409804` found two Important findings and one
Minor. One subsequent fix pass has failing-then-passing regressions:

- Application specification errors omit rejected values and operator webhook allowlists,
  while preserving safe fields and constraint details.
- A non-finite final custom observation returns null with diagnostics, without silently
  substituting an older finite observation.
- Nested indicator warmup uses finite observations of its configured source. This was
  regraded Important because the wrong category suggests the wrong remediation.
- The reviewer set chart collisions aside; the executor regraded the risk Important.
  Two different same-second analyses now retain their distinct returned artifacts.

The review fix pass is `843b681`; the final full/native checks above exercised that fixed source.
No second independent review is claimed. Skill behavior was checked with a fresh agent
before/after the label edit: source, window, currency, warning and undefined-ratio reason
were preserved; the obsolete label disappeared. Historical immutable source strings
were deliberately not rewritten.

## Local installation failure resolved

Cache-directory ACLs granted Davide FullControl; creating/renaming an owned probe worked.
psutil independently identified broker uvx/Python processes from both Codex app and
VS Code with current directories inside the old version cache. Windows therefore refused
the installer's cache backup rename. Terminating only those broker MCP process trees
released the directories; the same installation command then succeeded with 0.5.0.
No ACL change or manual cache deletion was needed.

The ongoing conversation's preloaded tool descriptions can still show its old 14-tool
catalogue. Reopen/reload the Codex session to refresh that in-memory catalogue. The
installed artifact and newly initialized standalone MCP sessions were verified separately.

## Operational boundary

The MCP budget encloses queueing and execution; direct synchronous Python/cron entry
points retain their own execution semantics. Timings distinguish server, provider and
processing/worker overhead; an external gateway requires its own measurements. Null
reasons distinguish missing data, history shortage, inapplicability and undefined results.
Record recovery is merge-only; mutable hook backups require stopped runners. Delivery is
at-least-once, and filesystem/power-loss durability or hostile local writers are not
certified by these tests. Historical overnight session ambiguity remains conservative.
Unofficial Yahoo-source and data-use caveats remain explicit. See [OPERATIONS.md](../../OPERATIONS.md).
