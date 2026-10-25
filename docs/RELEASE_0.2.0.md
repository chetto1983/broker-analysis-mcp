# Release 0.2.0 verification

Verified on Windows/Python 3.13 on 2026-10-25. Three independent agents reviewed release
packaging, risk arithmetic and live MCP coverage. The arithmetic and live agents added tests;
the parent applied implementation fixes and executed the network tests.

## Corrections

- Forecast inputs now use gross exposure as their denominator. Adding idle base-currency cash
  cannot change monetary FHS VaR/ES, attribution or model selection through the variance floor.
  Reported volatility still uses percent NAV. Audit snapshots explicitly retain both bases.
- Positions and stress scenarios reject unknown fields. A misspelled `asset_shock` cannot
  silently become a zero-shock scenario; an unsupported position currency is rejected.
- Console `pytest` now resolves the research adapter imports, matching the CI invocation.
- All plugin manifests and MCP launchers target 0.2.0; portfolio requests are included in
  skill routing and plugin descriptions. Model implementation version is `portfolio-risk-2`.

## Executed checks

- `pytest -q`: **159 passed, 25 live tests deselected**.
- Ruff: passed. `uv build`: wheel and source distribution built successfully.
- Existing live MCP suite: **20 passed** against Yahoo Finance.
- New live portfolio suite: **5 passed**, using a real stdio MCP server, AAPL long and ENI
  short, USD/EUR conversion, independent variance/FHS/ES reconstruction, stress arithmetic,
  14-tool registration and installed package version checks.
- Live records survive a server restart. All saved report sections and hashes are checked;
  tampering with a temporary snapshot causes model-status retrieval to fail integrity checks.

Initial sandbox runs could not write the Yahoo cache; the network run required access outside
the sandbox. A shared pytest temporary-directory permission conflict was resolved by running
the five portfolio tests with a fresh dedicated directory under `.research/`. These environment
failures were not counted as successful tests. Upstream pandas/yfinance deprecation warnings
remain; no failed assertion was suppressed.

## Remaining model scope

This release does not add scheduled portfolio monitoring, exception-independence/ES calibration
tests, or additional forecasting champions. See [model policy](PORTFOLIO_MODELS.md).
The [Timestral GPU pilot](TIMESTRAL_209M_GPU.md) remains an experiment: its measured results do
not justify promoting it over the portfolio baselines. No trading operations are introduced.
