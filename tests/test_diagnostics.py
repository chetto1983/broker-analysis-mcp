"""Nulls distinguish absent fields, sample shortages and mathematical/domain failures."""

import json

import pytest
from test_contract import prices

from broker_analysis_mcp import analysis, data, workbench
from broker_analysis_mcp.fundamentals import Year
from broker_analysis_mcp.models import ConditionSpec, IndicatorSpec, SeriesSpec


def fundamentals(monkeypatch, year, profile=None):
    monkeypatch.setattr(data, "info", lambda ticker: {"quoteType": "EQUITY", "currency": "USD", "marketCap": 1000,
                                                    "currentPrice": 10, **(profile or {})})
    monkeypatch.setattr(data, "statements", lambda ticker: ([year], "USD", "2025-12-31"))
    return analysis.fundamentals_report("A")


@pytest.mark.parametrize(("year", "code", "field"), [(Year(), "missing_input", "diluted_eps"),
                                                         (Year(diluted_eps=0), "undefined", "diluted_eps"),
                                                         (Year(diluted_eps=-1), "not_applicable", "diluted_eps")])
def test_pe_null_names_the_actual_input_and_domain(monkeypatch, year, code, field):
    metric = fundamentals(monkeypatch, year)["metrics"]["pe"]
    assert metric["value"] is None and metric["reason_code"] == code
    assert field in metric["reason"]
    assert field in metric["missing_inputs" if code == "missing_input" else "invalid_inputs"]


def test_fundamental_nulls_all_have_specific_diagnostics_and_zero_yield_survives(monkeypatch):
    result = fundamentals(monkeypatch, Year(), {"dividendYield": 0})
    assert result["metrics"]["yahoo_dividend_yield"]["value"] == 0
    for name, metric in result["metrics"].items():
        if metric["value"] is None:
            assert metric["reason_code"] in {"missing_input", "insufficient_history", "undefined", "not_applicable"}, name
            assert metric["reason"] and "unavailable or" not in metric["reason"]
    json.dumps(result, allow_nan=False)


def test_identity_and_flat_price_nulls_explain_the_denominator(monkeypatch):
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(), data.Meta(ticker, "USD")))
    identity = analysis.risk_report("A", benchmark="A")["metrics"]["information_ratio"]
    assert identity["reason_code"] == "undefined" and "active" in identity["reason"]
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(constant=True), data.Meta(ticker, "USD")))
    metrics = analysis.risk_report("A")["metrics"]
    assert "standard deviation" in metrics["sharpe"]["reason"]
    assert "drawdown" in metrics["calmar"]["reason"]
    assert all(metric.get("reason_code") for metric in metrics.values() if metric["value"] is None)


def test_technical_history_and_etf_profile_nulls_are_distinct(monkeypatch):
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(3), data.Meta(ticker, "USD")))
    monkeypatch.setattr(data, "info", lambda ticker: {"quoteType": "ETF"})
    technical = analysis.technical_report("A")["metrics"]["sma200"]
    etf = analysis.etf_report("A", "B")["metrics"]
    assert technical["reason_code"] == "insufficient_history"
    assert etf["ter"]["reason_code"] == "missing_input" and "netExpenseRatio" in etf["ter"]["reason"]
    assert etf["tracking_error"]["reason_code"] == "insufficient_history"


def test_custom_null_series_explain_lookback_and_undefined_expression(monkeypatch):
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(10), data.Meta(ticker, "USD")))
    result, _ = workbench.run("A", "1y", [IndicatorSpec(id="sma50", fn="sma", params={"n": 50})],
                              [SeriesSpec(id="undefined", expr="close / (close - close)")], [], [], False)
    assert result["series_last_values"] == {"sma50": None, "undefined": None}
    assert result["series_diagnostics"]["sma50"]["reason_code"] == "insufficient_history"
    assert result["series_diagnostics"]["undefined"]["reason_code"] == "undefined"


def test_event_study_empty_sample_explains_null_statistics(monkeypatch):
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(10), data.Meta(ticker, "USD")))
    result, _ = workbench.run("A", "1y", [], [], [ConditionSpec(name="never", when="close < 0")], [], False)
    horizon = result["conditions"][0]["forward_returns"]["5d"]
    assert horizon["after_events"]["reason_code"] == "insufficient_history"
    assert horizon["difference_t_stat_diagnostic"]["reason_code"] == "insufficient_history"
