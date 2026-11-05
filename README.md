# broker-analysis-mcp

Prototype of an **MCP server for financial analysis** for a broker's analysts, with
skills that teach the agent how to read every number. Data from Yahoo Finance through
`yfinance`.

It ships as one plugin, `broker-analysis/`, that Claude Code, Codex and Aura install as it is: the
server and the five skills together.

## Install

The plugin starts the server with `uvx` from an immutable full commit of this repository, so the machine that
runs it needs `uv` and `git`, and network access on the first start, when uv fetches the package,
its dependencies and Python 3.13 if it is missing.

| Client | Install |
| --- | --- |
| Claude Code | `/plugin marketplace add chetto1983/broker-analysis-mcp`, then `/plugin install broker-analysis@broker-analysis-mcp` |
| Codex | `codex plugin marketplace add chetto1983/broker-analysis-mcp`, then `codex plugin add broker-analysis@broker-analysis-mcp` |
| Aura | Install `chetto1983/broker-analysis-mcp/broker-analysis` from the cockpit's Governance plugin board; upgrade an existing installation from the same board. The server runs in each identity's sandbox box. |

> **Prototype only.** yfinance reads Yahoo's unofficial endpoints, is meant for personal and
> research use, and can break without notice. Production needs a licensed data source
> (Bloomberg or LSEG through their MCP servers, or a vendor with redistribution rights). Nothing
> here places orders or gives personalised advice.

## Tools

| Tool | What it returns |
| --- | --- |
| `portfolio_risk` | Fixed current positions in a base currency: FX-aware returns, correlations, historical and filtered-historical-simulation VaR/ES, component and scenario-frozen marginal ES, named historical/hypothetical stress scenarios. Saves a data snapshot and separate development, validation and monitoring reports. |
| `model_status` | Champion and EWMA fallback decision as of a saved portfolio run, validation and monitoring QLIKE, VaR coverage diagnostics and last validation date. |
| `analysis_record` | Hash-verified output, exact snapshot, manifest or one of the three model reports, by record ID. |
| `market_snapshot` | Acquires one immutable full-OHLCV dataset, optional benchmark, required directional FX and minimal ETF profiles. Returns a shared `snapshot_id` for provider-free replay across reports. |
| `risk_metrics` | CAGR, close-to-close and range volatility (Garman-Klass, Parkinson, Rogers-Satchell), Sharpe with standard error and t statistic, Probabilistic Sharpe Ratio, Sortino, Omega, max drawdown, CDaR, Calmar, historical and Cornish-Fisher VaR, expected shortfall; against a benchmark also beta and correlation, Bloomberg-style adjusted beta (two years weekly, 0.67 raw + 0.33), Jensen's alpha, Treynor, M², information ratio, tracking error and Morningstar-style up/down capture |
| `technical_indicators` | Snapshot of SMAs, RSI, MACD, Bollinger, ATR, 52-week range, all with adjustable parameters |
| `fundamental_ratios` / `peer_comparison` | Multiples, DuPont (ROE, ROA, margins, turnover, equity multiplier), ROIC, working-capital cycle, liquidity, leverage and coverage, payout, revenue CAGR; CAPM cost of equity, WACC, EVA and unlevered beta when the analyst gives the risk-free rate and the equity risk premium; Piotroski F-score, Altman Z and Z'', Beneish M-score with their components (not for banks and insurers); currency conversion between quote and statements |
| `etf_analysis` | TER, tracking difference and tracking error against a benchmark converted to the fund's currency |
| `list_indicator_catalog` | The parametric indicators (typed parameters with defaults and bounds), expression functions and syntax |
| `custom_analysis` | User-defined indicators, derived series, conditions and levels on one ticker; condition events with forward returns against all bars; PNG chart plus a QuantDinger-style JSON spec (plots, signals, layers) |
| `create_hook` / `list_hooks` / `run_hooks` / `delete_hook` | Saved conditions that deliver a JSON event (file or allow-listed webhook) when they fire on a new bar |

Every metric carries its unit and the key of the formula it came from (`references.py`, with
arXiv sources where one exists), plus the data window, source and warnings.

Analysis reports also include `schema_version`, `retrieved_at`, `price_observations` and
`return_observations`. Counts describe the primary history, the common price calendar for
portfolio/ETF reports, or are null for statement-based fundamentals/peer comparisons. Existing
`window.days`, `window.bars`, `window.returns` and `aligned_days` fields keep their meanings.
Unavailable capital metrics include a `reason`, `missing_inputs` and `invalid_inputs`; debt cost
and unlevered beta do not require the analyst's CAPM rates. Technical warmup nulls name the
required observation count. Public parameter bounds appear in the tools' input schemas.

Daily price reports default to completed sessions. Set `include_incomplete_session=true` to
opt into an open/uncertain daily bar; future bars are always excluded. An omitted/null flag
uses the completed-only live default. Reports name the aware daily `last_bar_timestamp`,
`last_bar_complete`, completeness reason and excluded candidates separately from the latest
trade time and scheduled session end. Equity completion uses provider session boundaries;
crypto waits for the next UTC midnight. Live alert hooks explicitly retain incomplete-session
evaluation. Current fundamental quotes and financial statements keep their existing behavior.
When current metadata names another session, acquisition requests yfinance's existing historical
regular-session schedule and matches its dated start/end to the bar. The captured session end
belongs to the selected bar, or is null without matching evidence. If the historical schedule
is also unavailable, the latest bar remains uncertain and can be excluded conservatively.
Previously stored snapshots retain their original metadata; acquire a new one for new evidence.
For comparisons, `comparison_window` names the common calendar and `session_sources` preserves
each asset/benchmark/FX bar's evidence. The aggregate `last_bar_complete` includes all series
used by the calculation; a closed fund does not hide an incomplete benchmark or FX bar.

### Shared datasets

Acquire once with `market_snapshot`, for example:

```json
{"tickers": ["AAPL", "SPY"], "period": "3y", "benchmark": "SPY", "base_currency": "EUR"}
```

Pass its returned `snapshot_id` to `risk_metrics`, `technical_indicators`, `custom_analysis`,
`etf_analysis` or `portfolio_risk`. Omit `period` and `include_incomplete_session` on replay to
inherit the captured values. Explicit conflicts fail. Indicator parameters, confidence and
CAPM rates may vary while prices stay fixed. A snapshot with period shorter than a report's
supported range is rejected by that report.

Replay verifies the stored hash and schema, preserves original retrieval/session metadata,
and never contacts Yahoo, even after an MCP process restart. Missing tickers, benchmark,
directional FX or an ETF profile produce an error; explicitly acquire a broader dataset to
extend coverage. Full OHLCV values, numeric dtypes and aware source labels are retained,
including integer volumes above the exact floating-point integer range. Acquisition returns
coverage and provenance without returning the full time series to the model.

`market_snapshot` makes an additive local write. Its `snapshot_id` identifies a reusable market
dataset and is distinct from `portfolio_risk`'s `audit.record_id` or archived `snapshot_hash`.
The latter remain readable through the existing record tools.

`portfolio_risk` makes additive local audit writes. It is a mutating, non-destructive tool;
repeated calls add fresh timestamped records, so it is not idempotent. Its Yahoo reads remain
open-world interaction. `custom_analysis` declares local writes because its default chart
option saves PNG/JSON files. Hook tools retain their file/webhook effects. No trading operations
are provided.

With `custom_analysis(chart=false)`, indicator/series computation has no chart-pane limit and
does not write chart files. The four-lower-pane limit applies when a chart is requested.
If a configured lookback exceeds the finite history of its source series, the report warns
with the series ID, lookback and available observation count; the unavailable value stays null.

### Portfolio risk and model records

Pass signed **market values**, not share counts, in each security's quote currency (GBP for
London prices normalised from pence). `portfolio_value` is total positive NAV in `base_currency`,
including cash. For example:

```json
{
  "positions": [
    {"ticker": "AAPL", "market_value": 10000},
    {"ticker": "ENI.MI", "market_value": 8000}
  ],
  "portfolio_value": 25000,
  "base_currency": "EUR",
  "period": "5y",
  "confidence": 0.975,
  "scenarios": [{
    "name": "Equity and dollar decline",
    "asset_shocks": {"AAPL": -0.20, "ENI.MI": -0.15},
    "fx_shocks": {"USD": -0.10}
  }]
}
```

Use position values as of the common valuation date returned by the tool. Residual cash earns
zero; negative residual cash represents financing with zero assumed financing cost. This is a
fixed-exposure risk reconstruction, not actual historical portfolio performance. No options or
other nonlinear instruments are repriced. Prices and FX are intersected **before** returns; no
missing values are filled. The horizon is the next common observed session, not necessarily one
calendar day. Closes in different time zones remain asynchronous. Historical stress scenarios
exclude current UTC-date/future bars along with the rest of the analysis. Historical episodes
are unavailable when their full dates are not covered; request `10y` or `max` for older episodes.

The implemented registry contains EWMA (lambda 0.94) and a rolling 60-session mean-square
challenger. It does not yet implement HAR, ARFIMA or GARCH. At least 435 returns are needed for
252 validation observations after a 60-observation warm-up and a separate 63-observation
monitoring window. The challenger must improve QLIKE with a HAC z statistic above 1.96;
subsequent monitoring deterioration or a VaR coverage alarm triggers EWMA. With less history,
the fallback reason is explicit. This policy is evaluated on each `portfolio_risk` call;
`model_status(record_id)` reads that saved decision and does not run a background scheduler.

Every successful run stores its exact price/FX inputs, alignment calendar, request, versions,
source hashes, forecasts, output and three separate reports under
the selected data home's `records/` directory. Home precedence is `BROKER_ANALYSIS_HOME`,
then Aura's identity-scoped `PLUGIN_DATA`, then `~/.broker-analysis`. Charts and hooks use
the same home; read-only record lookup creates no directories. When upgrading an existing
Aura installation, explicitly copy old `~/.broker-analysis` contents into its `PLUGIN_DATA`
before recreating the box, verify hashes and preserve differing files rather than overwriting
them. There is no automatic legacy lookup or migration process.
`audit.record_id` identifies the manifest; use `analysis_record` with `section` set to `output`,
`snapshot`, `development`, `validation` or `monitoring`. Objects use canonical JSON and SHA-256,
are published atomically and are never overwritten by the application. Every lookup verifies
all manifest-linked objects. Administrators can still delete or replace local files: this is
tamper detection and application immutability, not certified WORM storage. Snapshots contain
position values; secure this local directory accordingly. Older single-security tools do not
create these portfolio records. See [model governance](docs/PORTFOLIO_MODELS.md) for the formulas,
validation policy, interpretation and operating workflow.

## Parametric analysis

Indicators are data, not code: `{"id": "bb", "fn": "bollinger", "params": {"n": 30, "k": 2.5}}`.
Derived series and conditions are expressions over the series by id:

```
close / bb.lower - 1
rsi14 < 30 and close > sma200
crosses_above(ema20, ema50)
volume > 2 * mean(volume, 20)
```

Expressions are parsed with `ast` and evaluated over a whitelist of node types and functions;
nothing reaches `eval`. Conditions use edge semantics (an event when the rule becomes true), as
QuantDinger's signal monitoring does.

## Hooks

```
BROKER_ANALYSIS_HOME=~/.broker-analysis          # hooks.json, alerts/*.jsonl, charts/
BROKER_ANALYSIS_WEBHOOK_ALLOW=https://hooks.example.com/   # webhooks are off without it
*/30 9-18 * * 1-5  broker-analysis-hooks          # evaluate saved hooks during market hours
```

## Skills

`broker-analysis/skills/` holds five skills: `risk-analysis`, `fundamental-analysis`,
`technical-analysis`, `etf-selection`, `custom-analysis`. The plugin installs them with the server.
They are written in English and tell the agent to answer in the analyst's language.

## Layout

| Path | What it is |
| --- | --- |
| `broker-analysis/` | The plugin: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.mcp.json`, `skills/` |
| `.claude-plugin/marketplace.json` | The Claude Code marketplace |
| `.agents/plugins/marketplace.json` | The Codex marketplace |
| `src/`, `tests/`, `e2e/`, `docs/` | The server package, its tests and its measurements |

## Release

Both `.mcp.json` and `mcp.json` launch the server from the same full 40-character commit.
A release first commits the tested server with its new version in `pyproject.toml`, `uv.lock`
and all three plugin manifests, then a packaging commit pins both launchers to that server
commit. Tag the tested packaging commit. A cached Git tag still requires remote resolution
on each start; a full commit lets uv reuse the environment and works offline after warming.
`tests/test_plugin_manifest.py` checks manifest versions and immutable, matching launchers;
the installed MCP smoke verifies the pinned package version and behavior.

## Run

```
uv sync
uv run broker-analysis-mcp                      # stdio MCP server
uv run broker-analysis-monitor --config examples/portfolio-monitor.json  # illustrative portfolio
uv run pytest                                   # offline tests
uv run pytest -m live -o addopts=""             # MCP end to end on live Yahoo data
uv run python e2e/agent_e2e.py                  # a real agent answers analyst questions, scored (needs the claude CLI)
uv run ruff check .                             # lint, pinned to ruff 0.17.1
```

`e2e/tsfm_eval.py` retains optional Chronos research adapters; it needs torch and
`chronos-forecasting`, which this project does not install.

Timestral CPU/GPU adapters and the local GPU model/runtime were removed after the benchmark
failed to establish an advantage. The [historical evaluation](docs/TIMESTRAL_209M_GPU.md)
and measured results remain as evidence for that decision. Portfolio models use EWMA and
the rolling 60-session baseline, with no foundation-model runtime dependency.

The [portfolio monitoring command](docs/PORTFOLIO_MONITORING.md) supports cron and Windows
Task Scheduler with strict configuration validation, saved records and explicit exit codes.
Supply current holdings and NAV; the example is illustrative and no schedule is installed.

## Measured, not assumed

`docs/VERIFICA.md` records what was measured, including the formulas checked against
independent implementations and the forecasting models (Mistral Timestral, Amazon Chronos-Bolt,
Chronos-2 and Kronos) that were dropped for not beating naive baselines. The Kronos adapter was
removed afterwards; it is at commit b996f52 of the Aura repository, where this project started, with
the runs it produced.
