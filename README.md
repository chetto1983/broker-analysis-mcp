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
| `risk_metrics` | CAGR, close-to-close and range volatility (Garman-Klass, Parkinson, Rogers-Satchell), Sharpe with standard error and t statistic, Probabilistic Sharpe Ratio, Sortino, Omega, max drawdown, CDaR, Calmar, historical and Cornish-Fisher VaR, expected shortfall; against a benchmark also beta and correlation, Bloomberg-style adjusted beta (two years weekly, 0.67 raw + 0.33), Jensen's alpha, Treynor, M², information ratio, tracking error and Morningstar-style up/down capture |
| `technical_indicators` | Snapshot of SMAs, RSI, MACD, Bollinger, ATR, 52-week range, all with adjustable parameters |
| `fundamental_ratios` / `peer_comparison` | Multiples, DuPont (ROE, ROA, margins, turnover, equity multiplier), ROIC, working-capital cycle, liquidity, leverage and coverage, payout, revenue CAGR; CAPM cost of equity, WACC, EVA and unlevered beta when the analyst gives the risk-free rate and the equity risk premium; Piotroski F-score, Altman Z and Z'', Beneish M-score with their components (not for banks and insurers); currency conversion between quote and statements |
| `etf_analysis` | TER, tracking difference and tracking error against a benchmark converted to the fund's currency |
| `list_indicator_catalog` | The parametric indicators (typed parameters with defaults and bounds), expression functions and syntax |
| `custom_analysis` | User-defined indicators, derived series, conditions and levels on one ticker; condition events with forward returns against all bars; PNG chart plus a QuantDinger-style JSON spec (plots, signals, layers) |
| `create_hook` / `list_hooks` / `run_hooks` / `delete_hook` | Saved conditions that deliver a JSON event (file or allow-listed webhook) when they fire on a new bar |

Every metric carries its unit and the key of the formula it came from (`references.py`, with
arXiv sources where one exists), plus the data window, source and warnings.

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

## Measured, not assumed

`docs/VERIFICA.md` records what was measured, including the formulas checked against
independent implementations and the forecasting models (Mistral Timestral, Amazon Chronos-Bolt,
Chronos-2 and Kronos) that were dropped for not beating naive baselines. The Kronos adapter was
removed afterwards; it is at commit b996f52 of the Aura repository, where this project started, with
the runs it produced.
