"""Public contracts: validation precedes Yahoo, sparse data and missing metrics explain why."""

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from broker_analysis_mcp import analysis, data, server, workbench
from broker_analysis_mcp.catalog import SpecError, resolve_params
from broker_analysis_mcp.fundamentals import Year
from broker_analysis_mcp.models import IndicatorSpec


def test_server_catalog_starts_without_importing_analysis_dependencies():
    probe = subprocess.run([sys.executable, "-c", "import asyncio,sys; from broker_analysis_mcp.server import server; "
                            "tools=asyncio.run(server.list_tools()); assert len(tools)==15; "
                            "assert not ({'pandas','numpy','scipy','yfinance','matplotlib'} & sys.modules.keys())"],
                           capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr


def prices(n=260, constant=False):
    close = np.full(n, 100.) if constant else 100 * np.cumprod(1 + np.random.default_rng(13).normal(.0003, .01, n))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99,
                         "Close": close, "Volume": 1000}, index=pd.bdate_range("2025-01-02", periods=n))


@pytest.fixture
def no_yahoo(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("invalid input reached Yahoo Finance")
    monkeypatch.setattr(data, "history", fail)
    monkeypatch.setattr(data, "info", fail)


def test_local_audit_is_an_additive_write_and_hooks_keep_external_effects():
    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    hints = tools["portfolio_risk"].annotations.model_dump(by_alias=True)
    assert hints["readOnlyHint"] is False
    assert hints["destructiveHint"] is False
    assert hints["idempotentHint"] is False  # fresh timestamped records are an additional effect
    assert hints["openWorldHint"] is True  # Yahoo reads remain external interaction
    chart_hints = tools["custom_analysis"].annotations.model_dump(by_alias=True)
    assert chart_hints["readOnlyHint"] is False  # chart=true writes PNG and JSON
    assert chart_hints["destructiveHint"] is False
    for name in ("create_hook", "run_hooks"):
        assert tools[name].annotations.model_dump(by_alias=True)["openWorldHint"] is True


def test_public_schema_exposes_period_and_numeric_bounds():
    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    technical = tools["technical_indicators"].input_schema["properties"]
    assert (technical["rsi_period"]["minimum"], technical["rsi_period"]["maximum"]) == (2, 200)
    risk = tools["risk_metrics"].input_schema["properties"]
    assert risk["confidence"]["exclusiveMinimum"] == 0
    assert risk["confidence"]["exclusiveMaximum"] == 1
    choices = tools["etf_analysis"].input_schema["properties"]["period"]["anyOf"]
    assert "1d" not in next(choice["enum"] for choice in choices if "enum" in choice)


@pytest.mark.parametrize(("tool", "arguments", "field"), [
    ("risk_metrics", {"ticker": "AAPL", "confidence": 1.2}, "confidence"),
    ("risk_metrics", {"ticker": "AAPL", "confidence": float("nan")}, "confidence"),
    ("risk_metrics", {"ticker": "AAPL", "risk_free_annual": -1}, "risk_free_annual"),
    ("risk_metrics", {"ticker": "AAPL", "risk_free_annual": float("inf")}, "risk_free_annual"),
    ("technical_indicators", {"ticker": "AAPL", "rsi_period": 0}, "rsi_period"),
    ("technical_indicators", {"ticker": "AAPL", "sma_fast": 2.5}, "sma_fast"),
    ("technical_indicators", {"ticker": "AAPL", "bollinger_k": float("inf")}, "bollinger_k"),
    ("technical_indicators", {"ticker": "AAPL", "macd_fast": 30, "macd_slow": 20}, "fast"),
    ("etf_analysis", {"ticker": "SPY", "benchmark": "^GSPC", "period": "1d"}, "period"),
    ("fundamental_ratios", {"ticker": "AAPL", "beta": float("nan")}, "beta"),
])
def test_mcp_invalid_input_is_actionable_before_yahoo(no_yahoo, tool, arguments, field):
    with pytest.raises(ToolError, match=field) as error:
        asyncio.run(server.server.call_tool(tool, arguments))
    assert not isinstance(error.value, UnexpectedToolError)


@pytest.mark.parametrize("tool", ["create_hook", "custom_analysis"])
@pytest.mark.parametrize("indicator", [
    {"id": "r", "fn": "rsi", "params": {"n": 0}},
    {"id": "r", "fn": "rsi", "params": {"n": 2.5}},
    {"id": "r", "fn": "rsi", "params": {"n": float("nan")}},
    {"id": "r", "fn": "unknown", "params": {}},
])
def test_catalog_parameters_fail_at_public_boundary_before_provider_or_storage(monkeypatch, tmp_path, tool, indicator):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input reached Yahoo Finance")
    monkeypatch.setattr(data.yf, "Ticker", forbidden)
    home = tmp_path / "uncreated-home"
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(home))
    arguments = {"ticker": "AAPL", "indicators": [indicator]}
    if tool == "create_hook":
        arguments |= {"name": "invalid", "condition": {"name": "oversold", "when": "r < 30"},
                      "action": {"type": "file", "target": "alerts"}}
    with pytest.raises(ToolError, match=r"rsi|unknown") as error:
        asyncio.run(server.server.call_tool(tool, arguments))
    assert not isinstance(error.value, UnexpectedToolError)
    assert not home.exists()


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -float("inf")])
def test_chart_level_price_is_finite_at_public_boundary_before_provider_or_storage(monkeypatch, tmp_path, price):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid level reached Yahoo Finance")
    monkeypatch.setattr(data.yf, "Ticker", forbidden)
    home = tmp_path / "uncreated-home"
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(home))
    with pytest.raises(ToolError, match=r"levels.0.price") as error:
        asyncio.run(server.server.call_tool("custom_analysis", {
            "ticker": "AAPL", "indicators": [], "levels": [{"price": price}],
        }))
    assert not isinstance(error.value, UnexpectedToolError)
    assert not home.exists()


@pytest.mark.parametrize("call", [
    lambda: analysis.risk_report("AAPL", alpha=1.2),
    lambda: analysis.technical_report("AAPL", params={"rsi_period": 0}),
    lambda: analysis.etf_report("SPY", "^GSPC", "1d"),
    lambda: analysis.fundamentals_report("AAPL", beta=float("inf")),
    lambda: workbench.run("AAPL", "1y", [IndicatorSpec(id="r", fn="rsi", params={"n": 0})], [], [], [], False),
])
def test_direct_reports_validate_before_yahoo(no_yahoo, call):
    with pytest.raises((SpecError, data.DataError), match=r"confidence|rsi|period|beta"):
        call()


@pytest.mark.parametrize("value", [2.5, float("inf"), float("nan")])
def test_custom_integer_lookbacks_reject_fractional_and_nonfinite_values(value):
    with pytest.raises(SpecError):
        resolve_params("rsi", {"n": value})


@pytest.mark.parametrize("count", [0, 1, 2])
def test_risk_short_history_names_observations_and_remedy(monkeypatch, count):
    monkeypatch.setattr(data, "history", lambda *a: (prices(count), data.Meta("A", "USD")))
    with pytest.raises(ToolError, match=r"insufficient observations.*longer period"):
        server.risk_metrics("A")


def test_disjoint_etf_history_is_an_insufficient_observations_error(monkeypatch):
    monkeypatch.setattr(data, "info", lambda *a: {"quoteType": "ETF"})
    def history(ticker, period, include_incomplete_session=False):
        frame = prices()
        if ticker == "B":
            frame.index += pd.DateOffset(years=5)
        return frame, data.Meta(ticker, "USD")
    monkeypatch.setattr(data, "history", history)
    with pytest.raises(ToolError, match=r"insufficient observations.*aligned"):
        server.etf_analysis("A", "B")


def test_short_etf_history_explains_weekly_tracking_sample(monkeypatch):
    monkeypatch.setattr(data, "info", lambda *a: {"quoteType": "ETF"})
    monkeypatch.setattr(data, "history", lambda *a: (prices(3), data.Meta("A", "USD")))
    result = server.etf_analysis("A", "B")
    metric = result["metrics"]["tracking_error"]
    assert metric["value"] is None
    assert metric["required_observations"] == 2
    assert "weekly" in metric["reason"]


@pytest.mark.parametrize("bad", [float("inf"), 0., -2.])
def test_bad_provider_prices_are_domain_errors(monkeypatch, bad):
    frame = prices()
    frame.iloc[-1, frame.columns.get_loc("Close")] = bad
    monkeypatch.setattr(data, "history", lambda *a: (frame, data.Meta("A", "USD")))
    with pytest.raises(ToolError, match="finite and positive"):
        server.risk_metrics("A")


def test_flat_prices_return_json_safe_nulls_with_reasons(monkeypatch):
    monkeypatch.setattr(data, "history", lambda *a: (prices(constant=True), data.Meta("A", "USD")))
    result = server.risk_metrics("A")
    assert result["metrics"]["sharpe"]["value"] is None
    assert "reason" in result["metrics"]["sharpe"]
    json.dumps(result, allow_nan=False)


def test_technical_warmup_explains_each_unavailable_metric(monkeypatch):
    monkeypatch.setattr(data, "history", lambda *a: (prices(5), data.Meta("A", "USD")))
    result = server.technical_indicators("A")
    for key in ("sma50", "sma200", "rsi14", "macd_signal", "atr14"):
        metric = result["metrics"][key]
        assert metric["value"] is None
        assert "observations" in metric["reason"]
    assert result["metrics"]["rsi14"]["required_observations"] == 15
    assert result["metrics"]["macd_signal"]["required_observations"] == 34


@pytest.mark.parametrize(("fast", "slow"), [(20, 200), (200, 20)])
def test_technical_warmup_does_not_confuse_period_prefixes(monkeypatch, fast, slow):
    monkeypatch.setattr(data, "history", lambda *a: (prices(5), data.Meta("A", "USD")))
    result = server.technical_indicators("A", sma_fast=fast, sma_slow=slow)
    assert result["metrics"][f"sma{slow}"]["required_observations"] == slow


def test_zero_bollinger_lower_band_is_undefined_instead_of_crashing(monkeypatch):
    frame = prices(260, constant=True)
    frame.loc[frame.index[-2], "Close"] = 20.
    frame.loc[frame.index[-1], "Close"] = 100.
    monkeypatch.setattr(data, "history", lambda *a: (frame, data.Meta("A", "USD")))
    # Two closes 20,100 have mean60 and population deviation40: lower band60-1.5*40=0.
    result = server.technical_indicators("A", bollinger_n=2, bollinger_k=1.5)
    assert result["metrics"]["price_vs_bollinger_lower"]["value"] is None
    json.dumps(result, allow_nan=False)


@pytest.fixture
def company(monkeypatch):
    current = Year(revenue=1200, ebit=200, total_debt=400, tax_rate=.25, invested_capital=800,
                   interest_expense=20, net_income=100, equity=600, total_assets=1500)
    monkeypatch.setattr(data, "info", lambda *a: {"quoteType": "EQUITY", "currency": "USD",
                                                "currentPrice": 100, "marketCap": 1000, "beta": 1.2,
                                                "sector": "Financial Services"})
    def set_years(**changes):
        years = [replace(current, **changes), Year(total_debt=300)]
        monkeypatch.setattr(data, "statements", lambda *a: (years, "USD", "2025-12-31"))
    set_years()
    return set_years


def test_missing_interest_explains_debt_wacc_and_eva_with_actual_dependencies(company):
    company(interest_expense=None)
    result = server.fundamental_ratios("AAPL", .03, .05)
    for key in ("cost_of_debt_pretax", "wacc", "eva"):
        assert result["metrics"][key]["value"] is None
        assert "interest_expense" in result["metrics"][key]["missing_inputs"]
        assert result["metrics"][key]["reason"]
        assert any(key in warning for warning in result["warnings"])
    assert result["metrics"]["cost_of_equity"]["value"] == 9.


def test_eva_missing_ebit_does_not_make_debt_or_wacc_unavailable(company):
    company(ebit=None)
    result = server.fundamental_ratios("AAPL", .03, .05)
    assert result["metrics"]["cost_of_debt_pretax"]["value"] == pytest.approx(5.7143)
    assert result["metrics"]["wacc"]["value"] == pytest.approx(7.6531)
    assert result["metrics"]["eva"]["missing_inputs"] == ["ebit"]


def test_debt_free_wacc_does_not_claim_interest_is_required(company):
    company(total_debt=0, interest_expense=None)
    result = server.fundamental_ratios("AAPL", .03, .05)
    assert result["metrics"]["wacc"]["value"] == 9.
    assert result["metrics"]["eva"]["value"] == 78.


def test_unlevered_beta_zero_denominator_is_explicitly_undefined(company):
    company(tax_rate=3.5)
    result = server.fundamental_ratios("AAPL", .03, .05)
    assert result["metrics"]["unlevered_beta"]["value"] is None
    assert "denominator" in result["metrics"]["unlevered_beta"]["reason"]


def test_missing_analyst_inputs_are_metric_specific(company):
    result = server.fundamental_ratios("AAPL")
    assert result["metrics"]["wacc"]["missing_inputs"] == ["risk_free_annual", "equity_risk_premium"]
    assert result["metrics"]["cost_of_debt_pretax"]["value"] == pytest.approx(5.7143)


def test_unknown_peer_exception_details_stay_private(monkeypatch):
    def fail(*args):
        raise RuntimeError("secret server credential")
    monkeypatch.setattr(analysis, "fundamentals_report", fail)
    result = analysis.peer_report(["A", "B"])
    assert "secret" not in json.dumps(result)
    assert set(result["errors"]) == {"A", "B"}


def test_reports_add_consistent_metadata_and_preserve_old_observation_fields(monkeypatch, company):
    monkeypatch.setattr(data, "history", lambda *a: (prices(), data.Meta("A", "USD")))
    reports = [analysis.risk_report("A"), analysis.technical_report("A"), analysis.fundamentals_report("A")]
    monkeypatch.setattr(data, "info", lambda *a: {"quoteType": "ETF"})
    reports.append(analysis.etf_report("A", "B"))
    reports.append(workbench.run("A", "1y", [], [], [], [], False)[0])
    for report in reports:
        assert report["schema_version"] == 1
        assert datetime.fromisoformat(report["retrieved_at"]).tzinfo is not None
        assert "price_observations" in report and "return_observations" in report
    assert reports[0]["window"]["days"] == reports[0]["price_observations"] == 260
    assert reports[0]["return_observations"] == 259
    assert reports[2]["price_observations"] is None and reports[2]["return_observations"] is None
    assert reports[3]["metrics"]["aligned_days"]["value"] == reports[3]["return_observations"] == 259
    assert reports[4]["window"]["bars"] == 260


def test_unexpected_exception_details_stay_private(monkeypatch):
    def fail(*args):
        raise RuntimeError("secret provider implementation detail")
    monkeypatch.setattr(analysis, "risk_report", fail)
    with pytest.raises(UnexpectedToolError) as error:
        asyncio.run(server.server.call_tool("risk_metrics", {"ticker": "A"}))
    assert "secret" not in str(error.value)
