# Release 0.3.1 preparation

Public tools keep their existing fields and financial formulas. Added input-schema bounds and
report-boundary validation before Yahoo calls, including finite numbers, supported periods,
catalog lookbacks and MACD ordering. Invalid inputs remain actionable domain errors; arbitrary
internal exceptions remain hidden, including individual failures in peer comparisons.

Short and disjoint histories name the observation requirement. Technical warmup nulls name
their required price count, and a zero Bollinger reference is explicitly undefined. Capital
nulls name their actual missing/invalid dependencies. In particular, missing interest expense
explains debt cost, WACC and EVA only when those formulas use debt; debt-free WACC does not
claim to need it. Debt cost and unlevered beta can be returned without analyst CAPM rates.

Reports add schema/retrieval and price/return counts while retaining older window fields.
`portfolio_risk` explicitly declares additive local audit writes, non-idempotence and external
Yahoo reads. `custom_analysis` declares local writes for its default PNG/JSON chart output.
Hook annotations and effects remain unchanged. These truthful hints do not by
themselves override a client's trust/risk policy.
The chart annotation is conservative for the whole tool, including `chart=false` calls;
clients using open-world/non-idempotent escalation may require approval for this tool now.

Server initialization and tool listing defer numerical/provider modules until a tool needs
them. Chart imports occur only when a chart is requested, using Python's normal module cache.
No extra loader, process or cache manager is introduced. Startup latency on the real Aura VM
must be measured separately from this offline contract check.

## Verification on 2026-11-03

- 247 offline tests passed, including 42 new contract cases and existing stdio/audit tests.
- Ruff passed.
- Source distribution and wheel built successfully with `uv build --offline`.
- Windows' pre-existing pytest temp/cache directories deny access; the suite used an isolated
  ignored `.research` base directory and disabled pytest's cache provider.
- Live Yahoo calls, the installed Aura pack, VM startup and release publication remain the
  coordinating integration run's responsibility; this preparation does not establish them.

The package, lockfile, plugin versions and both launchers are prepared for 0.3.1. The launchers
will be pinned to the verified immutable source commit by the coordinating release step;
no release tag or package is published by this preparation.
