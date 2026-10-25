"""Independent arithmetic, causal validation, audit integrity and MCP contracts."""

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from broker_analysis_mcp import audit, data, forecasting, portfolio, server
from broker_analysis_mcp.models import Position, StressScenario


@pytest.fixture
def market(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    dates = pd.bdate_range("2022-01-03", periods=850)
    rng = np.random.default_rng(42)
    prices = {
        "A": pd.Series(100 * np.cumprod(1 + rng.normal(0, .012, len(dates))), index=dates),
        "B": pd.Series(80 * np.cumprod(1 + rng.normal(0, .015, len(dates))), index=dates),
    }
    fx = pd.Series(.9 * np.cumprod(1 + rng.normal(0, .003, len(dates))), index=dates)

    def history(ticker, period):
        return pd.DataFrame({"Close": prices[ticker]}), data.Meta(ticker, "USD" if ticker == "A" else "EUR")

    monkeypatch.setattr(data, "history", history)
    monkeypatch.setattr(data, "fx_series", lambda *args: fx)
    return prices, fx


def test_fractional_tail_and_attribution_match_manual_arithmetic():
    # Portfolio losses: 40, 20, 0, -10. Upper 37.5% = one full + one half scenario.
    scenarios = np.array([[-.3, -.1], [-.1, -.1], [.1, -.1], [.1, 0]])
    risk = portfolio.tail_risk(scenarios, np.array([100, 100]), .625)
    assert risk["var"] == 20
    assert risk["expected_shortfall"] == pytest.approx((40 + .5 * 20) / 1.5)
    assert risk["component_es"] == pytest.approx([(30 + .5 * 10) / 1.5, 10])
    assert sum(risk["component_es"]) == pytest.approx(risk["expected_shortfall"])


def test_tied_boundary_has_symmetric_attribution():
    risk = portfolio.tail_risk(np.array([[-.2, 0], [0, -.2]]), np.array([100, 100]), .95)
    assert risk["component_es"] == pytest.approx([10, 10])


def test_es_marginal_matches_frozen_scenario_finite_difference():
    scenarios = np.random.default_rng(6).normal(0, .02, (500, 3))
    exposures = np.array([1000., -300., 250.])
    risk = portfolio.tail_risk(scenarios, exposures, .975)
    for i in range(3):
        moved = exposures.copy()
        moved[i] += .001
        derivative = (portfolio.tail_risk(scenarios, moved, .975)["expected_shortfall"] - risk["expected_shortfall"]) / .001
        assert derivative == pytest.approx(risk["marginal_es"][i], abs=1e-8)


def test_causal_forecasts_do_not_see_future_returns():
    r = np.random.default_rng(3).normal(0, .01, 600)
    original = forecasting.forecasts(r)
    changed = r.copy()
    changed[400:] *= 10
    altered = forecasting.forecasts(changed)
    for model in original:
        np.testing.assert_allclose(original[model][:401], altered[model][:401], equal_nan=True)
    assert original["ewma_094"][61] == pytest.approx(.94 * np.mean(r[:60] ** 2) + .06 * r[60] ** 2)


def test_model_promotes_only_on_validation_and_falls_back_on_deterioration():
    good = {"challenger_loss_improvement": .1, "hac_z": 3.0}
    bad = {"challenger_loss_improvement": -.1, "hac_z": -2.0}
    assert forecasting.select(good, good)["champion"] == "rolling_60"
    result = forecasting.select(good, bad)
    assert result["validation_champion"] == "rolling_60"
    assert result["champion"] == "ewma_094"
    assert result["reason"] == "monitoring_deterioration"
    assert forecasting.select(bad, good)["champion"] == "ewma_094"
    assert forecasting.select(None, None)["reason"] == "insufficient_validation_history"


def test_monitoring_changes_cannot_rewrite_validation():
    rng = np.random.default_rng(19)
    r = rng.normal(0, .01, 600)
    dates = [str(d.date()) for d in pd.bdate_range("2020-01-01", periods=len(r))]
    _, before, _ = forecasting.evaluate(r, dates, .975)
    r[-63:] *= 5
    _, after, _ = forecasting.evaluate(r, dates, .975)
    assert before["validation"] == after["validation"]
    assert before["validation_champion"] == after["validation_champion"]
    assert before["validation"]["window"]["end"] < before["monitoring"]["window"]["start"]


def test_portfolio_fx_stress_snapshot_and_separate_reports(market):
    prices, fx = market
    result = portfolio.report(
        [Position(ticker="A", market_value=1000), Position(ticker="B", market_value=-200)], 1000,
        scenarios=[StressScenario(name="Joint shock", asset_shocks={"A": -.2}, fx_shocks={"USD": -.1})],
    )
    holdings = result["positions"]
    assert holdings[0]["market_value_base"] == pytest.approx(1000 * fx.iloc[-1])
    assert result["residual_cash_base"] == pytest.approx(1200 - 1000 * fx.iloc[-1])
    assert result["stress_scenarios"][0]["pnl"]["value"] == pytest.approx(1000 * fx.iloc[-1] * (-.28))
    assert sum(h["component_es"]["value"] for h in holdings) == pytest.approx(result["metrics"]["expected_shortfall_fhs"]["value"])
    record_id = result["audit"]["record_id"]
    assert audit.record(record_id) == {k: v for k, v in result.items() if k != "audit"}
    snapshot = audit.record(record_id, "snapshot")
    assert snapshot["fx"]["USD"]["values"] == fx.tolist()
    expected = (prices["A"] * fx).pct_change().iloc[1:].to_numpy() * holdings[0]["weight_nav"]
    expected += prices["B"].pct_change().iloc[1:].to_numpy() * holdings[1]["weight_nav"]
    np.testing.assert_allclose(snapshot["portfolio_returns"], expected)
    assert audit.record(record_id, "validation")["state"] == "evaluated"
    assert audit.record(record_id, "development")["model_version"] == forecasting.VERSION
    assert server.model_status(record_id) == result["model_status"]
    assert len(set(result["audit"]["reports"].values())) == 3
    # An unavailable stress is not silently calculated over the available subset.
    assert result["stress_scenarios"][1]["available"] is False
    json.dumps(result, allow_nan=False)


def test_fx_missing_date_is_aligned_before_returns(market, monkeypatch):
    prices, fx = market
    missing = fx.index[300]
    fx = fx.drop(missing)
    monkeypatch.setattr(data, "fx_series", lambda *args: fx)
    r = portfolio.report([Position(ticker="A", market_value=1000)], 1000)
    snap = audit.record(r["audit"]["record_id"], "snapshot")
    assert str(missing.date()) not in snap["common_dates"]
    expected = (prices["A"].reindex(fx.index) * fx).pct_change().dropna() * fx.iloc[-1]
    np.testing.assert_allclose(snap["portfolio_returns"], expected.to_numpy())


def test_zero_variance_is_finite_and_has_no_fake_correlation(market):
    prices, _ = market
    prices["B"][:] = 100
    r = portfolio.report([Position(ticker="B", market_value=1000)], 1000)
    assert r["metrics"]["expected_shortfall_fhs"]["value"] == 0
    assert r["correlation"]["B"]["B"] is None
    assert any("constant" in w for w in r["warnings"])
    json.dumps(r, allow_nan=False)


@pytest.mark.parametrize("kwargs", [{"portfolio_value": 0}, {"portfolio_value": float("inf")},
                                    {"confidence": .999}, {"confidence": float("nan")},
                                    {"base_currency": "?"}, {"period": "bad"}])
def test_invalid_requests_fail_before_network(monkeypatch, kwargs):
    monkeypatch.setattr(data, "history", lambda *args: pytest.fail("network reached"))
    arguments = {"positions": [Position(ticker="A", market_value=1)], "portfolio_value": 100} | kwargs
    with pytest.raises(data.DataError):
        portfolio.report(**arguments)


def test_unknown_stress_is_not_silently_ignored(market):
    with pytest.raises(data.DataError, match="unknown"):
        portfolio.report([Position(ticker="A", market_value=1)], 100,
                         scenarios=[StressScenario(name="typo", asset_shocks={"WRONG": -.2})])


def test_nonfinite_and_invalid_shocks_are_rejected():
    with pytest.raises(ValidationError):
        Position(ticker="A", market_value=float("nan"))
    with pytest.raises(ValidationError):
        StressScenario(name="bad", asset_shocks={"A": -2})


def test_misspelled_stress_and_unsupported_position_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        StressScenario.model_validate({"name": "Crash", "asset_shock": {"A": -.2}})
    with pytest.raises(ValidationError, match="Extra inputs"):
        Position.model_validate({"ticker": "A", "market_value": 1000, "currency": "EUR"})


def test_audit_concurrency_deduplication_and_tampering(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: audit.put({"test": 1}), range(12)))
    assert len(set(ids)) == 1
    path = audit.root() / f"{ids[0]}.json"
    original_time = path.stat().st_mtime_ns
    audit.put({"test": 1})
    assert path.stat().st_mtime_ns == original_time
    path.write_text('{"test":2}', encoding="utf-8")
    with pytest.raises(data.DataError, match="integrity"):
        audit.read(ids[0])
    with pytest.raises(data.DataError, match="integrity"):
        audit.put({"test": 1})
    with pytest.raises(data.DataError, match="digest"):
        audit.read("../../hooks")


def test_linked_object_tampering_rejected_even_for_other_section(market):
    r = portfolio.report([Position(ticker="B", market_value=1000)], 1000)
    path = audit.root() / (r["audit"]["snapshot_hash"] + ".json")
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ToolError, match="integrity"):
        server.model_status(r["audit"]["record_id"])


def test_mcp_registration_and_validated_invocation(market):
    async def exercise():
        tools = {t.name: t for t in await server.server.list_tools()}
        assert tools["portfolio_risk"].annotations.read_only_hint is False
        assert tools["model_status"].annotations.read_only_hint is True
        result = await server.server.call_tool("portfolio_risk", {
            "positions": [{"ticker": "B", "market_value": 1000}], "portfolio_value": 1000,
        })
        return result

    result = asyncio.run(exercise())
    assert result  # MCP validates input models and serializes the complete nested result.


def test_mcp_stdio_record_read_and_invalid_request(market):
    saved = portfolio.report([Position(ticker="B", market_value=1000)], 1000)

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=["-m", "broker_analysis_mcp.server"], env=dict(os.environ))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("model_status", {"record_id": saved["audit"]["record_id"]})
            assert not result.is_error
            assert json.loads(result.content[0].text) == saved["model_status"]
            result = await session.call_tool("analysis_record", {
                "record_id": saved["audit"]["record_id"], "section": "development",
            })
            assert not result.is_error
            assert json.loads(result.content[0].text)["model_version"] == forecasting.VERSION
            # Invalid NAV fails before any Yahoo request.
            result = await session.call_tool("portfolio_risk", {
                "positions": [{"ticker": "B", "market_value": 1000}], "portfolio_value": -1,
            })
            assert result.is_error

    asyncio.run(exercise())
