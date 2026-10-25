"""Independent portfolio review: financial invariants and hand-calculated stress P&L."""

import numpy as np
import pandas as pd
import pytest

from broker_analysis_mcp import audit, data, portfolio
from broker_analysis_mcp.models import Position, StressScenario


def test_empirical_es_matches_integral_of_quantiles_and_is_permutation_invariant():
    shocks = np.array([[-.3, .1], [-.1, .3], [.05, -.2], [.2, -.1], [.1, .2]])
    exposures = np.array([100., -50.])
    loss = -(shocks * exposures).sum(axis=1)
    # At 70%, the worst 1.5 observations carry 1 + 0.5 units of probability mass.
    expected = (np.sort(loss)[-1] + .5 * np.sort(loss)[-2]) / 1.5
    actual = portfolio.tail_risk(shocks, exposures, .7)
    assert actual["expected_shortfall"] == pytest.approx(expected)
    assert actual["expected_shortfall"] >= actual["var"]
    permuted = portfolio.tail_risk(shocks[[4, 1, 3, 0, 2]], exposures, .7)
    assert permuted["component_es"] == pytest.approx(actual["component_es"])


def test_historical_and_hypothetical_fx_stress_match_short_position_arithmetic(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    dates = pd.bdate_range("2019-01-01", "2023-01-31")
    prices = pd.Series(100., index=dates)
    fx = pd.Series(1., index=dates)
    prices.loc["2020-03-23":] = 80.
    fx.loc["2020-03-23":] = .9
    monkeypatch.setattr(data, "history", lambda *args: (pd.DataFrame({"Close": prices}), data.Meta("A", "USD")))
    monkeypatch.setattr(data, "fx_series", lambda *args: fx)
    result = portfolio.report(
        [Position(ticker="A", market_value=-1000)], 2000,
        scenarios=[StressScenario(name="price and FX", asset_shocks={"A": -.2}, fx_shocks={"USD": -.1})],
    )
    # Current short exposure -900 EUR, joint change -28%; gain = +252 EUR.
    assert result["positions"][0]["market_value_base"] == -900
    assert result["residual_cash_base"] == 2900
    hypothetical, covid = result["stress_scenarios"][:2]
    assert hypothetical["pnl"]["value"] == pytest.approx(252)
    assert covid["available"] is True
    assert covid["pnl"]["value"] == pytest.approx(252)
    assert covid["start"] == "2020-02-19" and covid["end"] == "2020-03-23"


@pytest.mark.parametrize("market_value", [1000, -1000])
def test_currency_risk_is_invariant_to_zero_risk_cash_nav(monkeypatch, tmp_path, market_value):
    """Adding base-currency idle cash must not change monetary losses of fixed holdings."""
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    rng = np.random.default_rng(42)
    returns = rng.normal(0, .005, 650)
    returns[-50:] *= 4
    prices = pd.Series(100 * np.cumprod(1 + returns), index=pd.bdate_range("2022-01-03", periods=650))
    monkeypatch.setattr(data, "history", lambda *args: (pd.DataFrame({"Close": prices}), data.Meta("A", "EUR")))
    positions = [Position(ticker="A", market_value=market_value)]
    invested = portfolio.report(positions, 1000)
    cash_heavy = portfolio.report(positions, 1_000_000_000)
    # Historical monetary risk already obeys this invariant; conditional risk must as well.
    assert invested["metrics"]["expected_shortfall_historical"] == cash_heavy["metrics"]["expected_shortfall_historical"]
    assert invested["metrics"]["expected_shortfall_fhs"]["value"] == pytest.approx(
        cash_heavy["metrics"]["expected_shortfall_fhs"]["value"], rel=1e-10,
    )
    assert invested["model_status"]["champion"] == cash_heavy["model_status"]["champion"]
    assert invested["metrics"]["var_fhs"] == cash_heavy["metrics"]["var_fhs"]
    assert invested["positions"][0]["component_es"] == cash_heavy["positions"][0]["component_es"]
    # Currency sigma is NAV times sigma in percent NAV, independent of idle cash.
    assert invested["metrics"]["forecast_volatility_session"]["value"] * 1000 == pytest.approx(
        cash_heavy["metrics"]["forecast_volatility_session"]["value"] * 1_000_000_000,
    )
    # Stored exposures remain unchanged; only cash/NAV should differ.
    snap = audit.record(cash_heavy["audit"]["record_id"], "snapshot")
    assert snap["request"]["positions"] == [{"ticker": "A", "market_value": float(market_value)}]
    assert snap["forecast_return_basis"] == "gross_exposure"
    assert snap["forecast_return_denominator_base"] == 1000
    assert np.array(snap["model_returns"]) == pytest.approx(np.array(snap["portfolio_returns"]) * 1_000_000)
