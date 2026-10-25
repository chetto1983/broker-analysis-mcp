# Release 0.3.0

Timestral execution support has been removed after the recorded benchmarks failed to establish
a useful advantage. Removed the CPU adapter, GGUF runner and runner-specific tests. Local model,
Rust/Vulkan runtime, dedicated compiler/cache and raw GPU artifacts were also removed. Historical
results and the [evaluation report](TIMESTRAL_209M_GPU.md) remain; the old implementation is at
tag `v0.2.0`. Timestral was never part of the active portfolio model registry.

Portfolio risk now uses model version `portfolio-risk-3`, adding supplementary independence and
ES severity diagnostics and storing causal per-origin forecasts in the monitoring report.
Sparse/degenerate samples are explicit, and ES severity remains descriptive with no calibration
pass or p-value. These diagnostics do not change the existing model-selection policy.
See [the statistical methods and limitations](PORTFOLIO_MODELS.md).

The new `broker-analysis-monitor --config portfolio.json` command supports external scheduling.
It validates configuration strictly, saves an analysis record, emits a JSON summary and returns
an explicit completion/error/coverage-alarm exit code. See [monitoring setup](PORTFOLIO_MONITORING.md).
No real portfolio configuration, operating-system schedule or notification delivery is installed.

## Verification on 2026-10-25

- 205 offline tests passed; Ruff passed; wheel/source distribution built.
- Six live portfolio MCP tests passed, including independent reconstruction of every monitoring
  origin from the saved inputs, report integrity and persistence across server restart.
- Monitor CLI ran successfully against Yahoo with the illustrative AAPL-long/ENI-short EUR
  portfolio and a separate temporary archive. It returned exit 0 and a retrievable saved record.
- Three agents implemented/reviewed diagnostics, tested CLI behavior and independently audited
  the integration. The independent audit passed 77 targeted tests and found no code blocker.

The live demonstration correctly reports sparse independence data and untested ES calibration.
Completion is not model approval. Upstream pandas/yfinance deprecation warnings remain.
