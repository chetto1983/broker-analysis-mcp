"""Real Yahoo data through stdio MCP, with independent portfolio arithmetic.

Run explicitly: pytest tests/test_live_portfolio.py -m live -o addopts="".
No network failure is converted to a skip or success. BROKER_ANALYSIS_TEST_MCP_CONFIG can select
an installed plugin's mcp.json; BROKER_TEST_SERVER_JSON can supply a JSON command array instead.
"""

import asyncio
import hashlib
import json
import math
import os
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

pytestmark = pytest.mark.live

REQUEST = {
    "positions": [{"ticker": "AAPL", "market_value": 10000}, {"ticker": "ENI.MI", "market_value": -3000}],
    "portfolio_value": 15000,
    "base_currency": "EUR",
    "period": "5y",
    "confidence": 0.975,
    "scenarios": [{"name": "Equity and dollar selloff", "asset_shocks": {"AAPL": -.20, "ENI.MI": -.15},
                   "fx_shocks": {"USD": -.10}}],
}


def _server(home: Path) -> StdioServerParameters:
    config_path = os.environ.get("BROKER_ANALYSIS_TEST_MCP_CONFIG")
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))["mcpServers"]["broker-analysis"] if config_path else None
    command = [config["command"], *config["args"]] if config else json.loads(os.environ.get("BROKER_TEST_SERVER_JSON", "null"))
    if command is None:
        command = [sys.executable, "-m", "broker_analysis_mcp.server"]
    env = os.environ | {"BROKER_ANALYSIS_HOME": str(home)}
    env.pop("BROKER_ANALYSIS_WEBHOOK_ALLOW", None)
    return StdioServerParameters(command=command[0], args=command[1:], env=env)


async def _call(session, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    assert not result.is_error, f"{name}: {result.content}"
    return json.loads(result.content[0].text)


async def _exercise(home: Path) -> dict:
    async with stdio_client(_server(home)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tool_list = await session.list_tools()
        tools = {tool.name: tool for tool in tool_list.tools}
        result = await _call(session, "portfolio_risk", REQUEST)
        record_id = result["audit"]["record_id"]
        sections = {}
        for section in ("manifest", "output", "snapshot", "development", "validation", "monitoring"):
            sections[section] = await _call(session, "analysis_record", {"record_id": record_id, "section": section})
        status = await _call(session, "model_status", {"record_id": record_id})
        invalid = {}
        for name, arguments in {
            "negative_nav": REQUEST | {"portfolio_value": -1},
            "duplicate_position": REQUEST | {"positions": [REQUEST["positions"][0]] * 2},
            "invalid_confidence": REQUEST | {"confidence": .999},
            "invalid_shock": REQUEST | {"scenarios": [{"name": "Impossible", "asset_shocks": {"AAPL": -2}}]},
            "typo_asset_shock": REQUEST | {"scenarios": [{"name": "Typo", "asset_shock": {"AAPL": -.2}}]},
            "unsupported_position_currency": REQUEST | {
                "positions": [REQUEST["positions"][0] | {"currency": "EUR"}, REQUEST["positions"][1]]
            },
        }.items():
            bad = await session.call_tool("portfolio_risk", arguments)
            invalid[name] = {"error": bad.is_error, "text": str(bad.content)}
        bad_id = await session.call_tool("analysis_record", {"record_id": "../../hooks"})
        invalid["path_traversal"] = {"error": bad_id.is_error, "text": str(bad_id.content)}
    # Restart the actual server: saved results must not depend on the original process memory.
    async with stdio_client(_server(home)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        restarted = await _call(session, "analysis_record", {"record_id": record_id})
        # Deliberately corrupt only this fixture's temporary archived snapshot.
        snapshot_hash = result["audit"]["snapshot_hash"]
        assert len(snapshot_hash) == 64 and all(c in "0123456789abcdef" for c in snapshot_hash)
        snapshot_path = (home / "records" / f"{snapshot_hash}.json").resolve()
        assert snapshot_path.is_relative_to(home.resolve())
        original_snapshot = snapshot_path.read_bytes()
        assert hashlib.sha256(original_snapshot).hexdigest() == snapshot_hash
        try:
            snapshot_path.write_bytes(b'{"tampered":true}')
            tampered = await session.call_tool("model_status", {"record_id": record_id})
            assert tampered.is_error, str(tampered.content)
            assert "integrity" in str(tampered.content).lower(), str(tampered.content)
        finally:
            # Preserve the original fixture objects for the independent hash assertions below.
            snapshot_path.write_bytes(original_snapshot)
    return {"report": result, "sections": sections, "status": status, "tools": tools,
            "invalid": invalid, "restarted_output": restarted, "home": home}


@pytest.fixture(scope="module")
def live_portfolio(tmp_path_factory):
    return asyncio.run(_exercise(tmp_path_factory.mktemp("live-portfolio")))


def _series(snapshot: dict) -> pd.Series:
    return pd.Series(snapshot["values"], index=pd.to_datetime(snapshot["dates"]), dtype=float)


def _reconstruct(snapshot: dict) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    aapl = _series(snapshot["prices"]["AAPL"])
    eni = _series(snapshot["prices"]["ENI.MI"])
    usd_eur = _series(snapshot["fx"]["USD"])
    levels = pd.concat({"AAPL": aapl, "ENI.MI": eni, "USDEUR": usd_eur}, axis=1).dropna()
    cutoff = pd.Timestamp(datetime.fromisoformat(snapshot["created_at"]).date())
    levels = levels.loc[levels.index < cutoff]
    converted = pd.DataFrame({"AAPL": levels.AAPL * levels.USDEUR, "ENI.MI": levels["ENI.MI"]})
    returns = converted.to_numpy()[1:] / converted.to_numpy()[:-1] - 1
    exposures = np.array([10000 * levels.USDEUR.iloc[-1], -3000.0])
    return levels, returns, exposures


def _tail(values: np.ndarray, confidence: float) -> tuple[float, float]:
    """Independent order-statistic computation; the boundary contributes fractional mass."""
    sorted_losses = sorted(float(v) for v in values)
    mass = len(values) * (1 - confidence)
    whole = math.floor(mass)
    fraction = mass - whole
    var = sorted_losses[math.ceil(len(values) * confidence) - 1]
    upper_sum = sum(sorted_losses[-whole:]) if whole else 0
    upper_sum += fraction * sorted_losses[-whole - 1]
    return var, upper_sum / mass


def test_live_tools_and_as_of_model_reports(live_portfolio):
    bundle = live_portfolio
    report = bundle["report"]
    assert len(bundle["tools"]) == 14
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert bundle["sections"]["snapshot"]["runtime_versions"]["broker-analysis-mcp"] == project["project"]["version"]
    assert bundle["tools"]["portfolio_risk"].annotations.read_only_hint is False
    assert bundle["tools"]["model_status"].annotations.read_only_hint is True
    assert report["currency"] == "EUR" and report["window"]["returns"] > 900
    assert report["positions"][0]["quote_currency"] == "USD"
    assert report["positions"][1]["quote_currency"] == "EUR"
    assert bundle["status"] == report["model_status"] == bundle["sections"]["monitoring"]["decision"]
    status = bundle["status"]
    assert status["validation"]["observations"] == 252
    assert status["monitoring"]["observations"] == 63
    assert status["validation"]["window"]["end"] < status["monitoring"]["window"]["start"]
    assert status["as_of"] == report["window"]["end"]
    assert status["champion"] in {"ewma_094", "rolling_60"}
    assert len(set(report["audit"]["reports"].values())) == 3


def test_live_price_fx_alignment_and_current_exposures(live_portfolio):
    snapshot = live_portfolio["sections"]["snapshot"]
    report = live_portfolio["report"]
    levels, returns, exposures = _reconstruct(snapshot)
    assert snapshot["request"] == REQUEST
    assert snapshot["common_dates"] == [str(d.date()) for d in levels.index]
    assert len(returns) == report["window"]["returns"]
    np.testing.assert_allclose(snapshot["portfolio_returns"], returns @ (exposures / REQUEST["portfolio_value"]), atol=1e-14)
    gross_exposure = np.abs(exposures).sum()
    np.testing.assert_allclose(snapshot["model_returns"], returns @ (exposures / gross_exposure), atol=1e-14)
    assert snapshot["forecast_return_basis"] == "gross_exposure"
    assert snapshot["forecast_return_denominator_base"] == pytest.approx(gross_exposure)
    np.testing.assert_allclose([p["market_value_base"] for p in report["positions"]], exposures)
    assert report["residual_cash_base"] == pytest.approx(REQUEST["portfolio_value"] - exposures.sum())
    assert report["gross_exposure_base"] == pytest.approx(np.abs(exposures).sum())
    assert report["correlation"]["AAPL"]["ENI.MI"] == pytest.approx(np.corrcoef(returns.T)[0, 1], abs=1e-12)


def test_live_fhs_and_es_match_independent_reconstruction(live_portfolio):
    snapshot, report = live_portfolio["sections"]["snapshot"], live_portfolio["report"]
    _, returns, exposures = _reconstruct(snapshot)
    gross_exposure = np.abs(exposures).sum()
    model_returns = returns @ (exposures / gross_exposure)
    # Compute forecasts independently as an exponential weighted sum, not the runtime recursion.
    n = len(returns)
    seed = np.mean(model_returns[:60] ** 2)
    first = max(60, n - 500)
    model = report["model_status"]["champion"]
    forecasts = []
    for t in range(first, n + 1):
        if model == "ewma_094":
            weights = .94 ** np.arange(t - 60 - 1, -1, -1)
            v = .94 ** (t - 60) * seed + .06 * np.dot(weights, model_returns[60:t] ** 2)
        else:
            v = np.mean(model_returns[t - 60:t] ** 2)
        forecasts.append(max(float(v), 1e-12))
    forecasts = np.array(forecasts)
    stored = snapshot["forecast_variances"][model][first - snapshot["forecast_start_return_index"]:]
    np.testing.assert_allclose(stored, forecasts, rtol=1e-12, atol=1e-15)
    volatility = report["metrics"]["forecast_volatility_session"]
    assert volatility["unit"] == "% NAV"
    assert volatility["value"] == pytest.approx(
        np.sqrt(forecasts[-1]) * gross_exposure / REQUEST["portfolio_value"] * 100, rel=1e-12
    )
    filtered = returns[first:] * np.sqrt(forecasts[-1] / forecasts[:-1])[:, None]
    for label, scenarios in (("fhs", filtered), ("historical", returns[first:])):
        var, es = _tail(-(scenarios @ exposures), REQUEST["confidence"])
        assert report["metrics"][f"var_{label}"]["value"] == pytest.approx(var, rel=1e-10, abs=1e-8)
        assert report["metrics"][f"expected_shortfall_{label}"]["value"] == pytest.approx(es, rel=1e-10, abs=1e-8)
    es = report["metrics"]["expected_shortfall_fhs"]["value"]
    assert sum(p["component_es"]["value"] for p in report["positions"]) == pytest.approx(es, abs=1e-8)
    # Freeze scenarios and vary one exposure: this is exactly the documented marginal definition.
    for i, position in enumerate(report["positions"]):
        perturbed = exposures.copy()
        perturbed[i] += .001
        _, changed_es = _tail(-(filtered @ perturbed), REQUEST["confidence"])
        assert (changed_es - es) / .001 == pytest.approx(position["marginal_es"]["value"], abs=1e-6)


def test_live_hypothetical_stress_respects_short_and_fx(live_portfolio):
    report = live_portfolio["report"]
    aapl, eni = report["positions"]
    scenario = report["stress_scenarios"][0]
    # AAPL falls 20% locally and USD falls 10%: combined return -28%.
    # A short EUR position gains when its underlying falls.
    expected_aapl = aapl["market_value_base"] * -.28
    expected_eni = -3000 * -.15
    assert scenario["name"] == "Equity and dollar selloff"
    assert scenario["component_pnl"]["AAPL"] == pytest.approx(expected_aapl)
    assert scenario["component_pnl"]["ENI.MI"] == pytest.approx(expected_eni)
    assert scenario["pnl"]["value"] == pytest.approx(expected_aapl + expected_eni)
    assert eni["market_value_base"] == -3000


def test_live_records_verify_and_survive_server_restart(live_portfolio):
    report = live_portfolio["report"]
    sections = live_portfolio["sections"]
    saved_output = {key: value for key, value in report.items() if key != "audit"}
    assert sections["output"] == saved_output == live_portfolio["restarted_output"]
    manifest = sections["manifest"]
    assert manifest["snapshot"] == report["audit"]["snapshot_hash"]
    assert manifest["output"] == report["audit"]["output_hash"]
    object_ids = [report["audit"]["record_id"], manifest["snapshot"], manifest["output"], *manifest["reports"].values()]
    for object_id in object_ids:
        path = live_portfolio["home"] / "records" / f"{object_id}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == object_id
    assert all(result["error"] for result in live_portfolio["invalid"].values()), live_portfolio["invalid"]
