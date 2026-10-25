# broker-analysis-mcp

Prototype of a **read-only MCP server for financial analysis** for a broker's analysts, with
skills that teach the agent how to read every number. Data from Yahoo Finance through
`yfinance`.

It ships as one plugin, `broker-analysis/`, that Claude Code, Codex and Aura install as it is: the
server and the five skills together.

## Install

The plugin starts the server with `uvx` from this repository's release tag, so the machine that
runs it needs `uv` and `git`, and network access on the first start, when uv fetches the package,
its dependencies and Python 3.13 if it is missing.

| Client | Install |
| --- | --- |
| Claude Code | `/plugin marketplace add chetto1983/broker-analysis-mcp`, then `/plugin install broker-analysis@broker-analysis-mcp` |
| Codex | `codex plugin marketplace add chetto1983/broker-analysis-mcp`, then `codex plugin add broker-analysis@broker-analysis-mcp` |
| Aura | `aura pack install chetto1983/broker-analysis-mcp/broker-analysis`, then `aura pack trust chetto1983/broker-analysis-mcp/broker-analysis --class sandboxed_local --reason "..."`; the server runs in each identity's sandbox box |

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
| `risk_metrics` | CAGR, close-to-close and range volatility (Garman-Klass, Parkinson, Rogers-Satchell), Sharpe with standard error and t statistic, Probabilistic Sharpe Ratio, Sortino, Omega, max drawdown, CDaR, Calmar, historical and Cornish-Fisher VaR, expected shortfall; against a benchmark also beta and correlation, Bloomberg-style adjusted beta (two years weekly, 0.67 raw + 0.33), Jensen's alpha, Treynor, M², information ratio, tracking error and Morningstar-style up/down capture |
| `technical_indicators` | Snapshot of SMAs, RSI, MACD, Bollinger, ATR, 52-week range, all with adjustable parameters |
| `fundamental_ratios` / `peer_comparison` | Multiples, DuPont (ROE, ROA, margins, turnover, equity multiplier), ROIC, working-capital cycle, liquidity, leverage and coverage, payout, revenue CAGR; CAPM cost of equity, WACC, EVA and unlevered beta when the analyst gives the risk-free rate and the equity risk premium; Piotroski F-score, Altman Z and Z'', Beneish M-score with their components (not for banks and insurers); currency conversion between quote and statements |
| `etf_analysis` | TER, tracking difference and tracking error against a benchmark converted to the fund's currency |
| `list_indicator_catalog` | The parametric indicators (typed parameters with defaults and bounds), expression functions and syntax |
| `custom_analysis` | User-defined indicators, derived series, conditions and levels on one ticker; condition events with forward returns against all bars; PNG chart plus a QuantDinger-style JSON spec (plots, signals, layers) |
| `create_hook` / `list_hooks` / `run_hooks` / `delete_hook` | Saved conditions that deliver a JSON event (file or allow-listed webhook) when they fire on a new bar |

Every metric carries its unit and the key of the formula it came from (`references.py`, with
arXiv sources where one exists), plus the data window, source and warnings.

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
`$BROKER_ANALYSIS_HOME/records` (default `~/.broker-analysis/records`).
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

`.mcp.json` launches the server from the tag `v<version>`. A release bumps the version in
`pyproject.toml`, in both `plugin.json` files and in the tag `.mcp.json` names, then tags the commit.
`tests/test_plugin_manifest.py` fails while the four disagree.

## Run

```
uv sync
uv run broker-analysis-mcp                      # stdio MCP server
uv run pytest                                   # offline tests
uv run pytest -m live -o addopts=""             # MCP end to end on live Yahoo data
uv run python e2e/agent_e2e.py                  # a real agent answers analyst questions, scored (needs the claude CLI)
uv run ruff check .                             # lint, pinned to ruff 0.17.1
```

`e2e/tsfm_eval.py` reruns the forecasting test; it needs an environment with torch, `timestral` and
`chronos-forecasting`, which this project does not install.

`e2e/timestral_gguf_eval.py` tests the specific
[Timestral-209M F32 GGUF conversion](https://huggingface.co/EvoAwaken-Workshop/Timestral-209M-gguf)
with its documented [Rust/Vulkan runtime](https://github.com/gouzil/rust-model-inference/tree/cc72a61fcd0c4314479291bd189555f5718ca85e).
It needs a separately built Vulkan binary and the checksum-verified model, not PyTorch.
Example after placing the runtime and model under the ignored `.research/` directory:

```powershell
.venv/Scripts/python.exe -m e2e.timestral_gguf_eval `
  --binary .research/rust-model-inference/target/release-fast/rust-model-inference.exe `
  --model .research/models/Timestral-209M-F32.gguf `
  --out e2e/runs/timestral_209m_gguf.json
```

The default is an exploratory three-asset, eight-origin pilot (100-session span, step 10).
Use `--span 750` to match the larger existing rolling window; `--smoke-only` checks numerical
agreement between the Rust CPU and GPU paths. GPU dispatch evidence is required and saved
alongside exact requests, outputs, logs and price snapshots. CPU/GPU agreement is not an
independent comparison against the official PyTorch implementation. The GGUF model is a separate
experiment and is not automatically admitted to the portfolio model registry.

## Measured, not assumed

`docs/VERIFICA.md` records what was measured, including the formulas checked against
independent implementations and the forecasting models (Mistral Timestral, Amazon Chronos-Bolt,
Chronos-2 and Kronos) that were dropped for not beating naive baselines. The Kronos adapter was
removed afterwards; it is at commit b996f52 of the Aura repository, where this project started, with
the runs it produced.
