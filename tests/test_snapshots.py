"""Explicit full-OHLCV acquisition and immutable provider-free report replay."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from broker_analysis_mcp import audit, data, hooks, server
from broker_analysis_mcp.models import IndicatorSpec, Position


@pytest.fixture
def market(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    frames, metadata, calls = {}, {}, []
    for ticker, currency, timezone, instrument in [
        ("AAPL", "USD", "America/New_York", "EQUITY"), ("ENI.MI", "EUR", "Europe/Rome", "EQUITY"),
        ("SPY", "USD", "America/New_York", "ETF"), ("USDJPY=X", "JPY", "UTC", "CURRENCY"),
        ("USDEUR=X", "EUR", "UTC", "CURRENCY"),
        ("EURJPY=X", "JPY", "UTC", "CURRENCY"),
    ]:
        dates = pd.bdate_range(end="2026-11-02", periods=550, tz=timezone)
        close = 100 * np.cumprod(1 + np.random.default_rng(8).normal(.0002, .01, len(dates)))
        frames[ticker] = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99,
                                       "Close": close, "Volume": np.arange(len(dates), dtype="int64") + 2**53}, index=dates)
        metadata[ticker] = {"currency": currency, "exchangeTimezoneName": timezone, "instrumentType": instrument,
                            "currentTradingPeriod": {"regular": {
                                "start": pd.Timestamp("2026-11-02T09:00", tz=timezone),
                                "end": pd.Timestamp("2026-11-02T17:30", tz=timezone)}}}
    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
            calls.append(ticker)
        @property
        def history_metadata(self):
            return metadata[self.ticker]
        def history(self, **kwargs):
            return frames[self.ticker].copy()
        @property
        def info(self):
            return {"quoteType": "ETF", "currency": "USD", "netExpenseRatio": .09,
                    "totalAssets": 10000000, "longName": "Index ETF", "fundFamily": "Example",
                    "unrelated_private_profile_field": "must not be persisted"}
    monkeypatch.setattr(data.yf, "Ticker", Ticker)
    original = data.history
    clock = {"observed_at": datetime.fromisoformat("2026-11-02T23:00Z")}
    monkeypatch.setattr(data, "history", lambda ticker, period="3y", include_incomplete_session=False: original(
        ticker, period, include_incomplete_session, observed_at=clock["observed_at"]))
    return frames, metadata, calls, clock


def acquire(**kwargs):
    result = asyncio.run(server.server.call_tool("market_snapshot", {"tickers": ["AAPL"], **kwargs}))
    return json.loads(result.content[0].text)


def forbid_yahoo(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("snapshot replay reached provider")
    monkeypatch.setattr(data.yf, "Ticker", forbidden)


def test_storage_precedence_and_lookup_remains_pure(monkeypatch, tmp_path):
    standalone = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: standalone)
    monkeypatch.delenv("BROKER_ANALYSIS_HOME", raising=False)
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    assert audit.root() == standalone / ".broker-analysis" / "records"
    plugin = tmp_path / "identity-plugin"
    monkeypatch.setenv("PLUGIN_DATA", str(plugin))
    assert audit.root() == plugin / "records"
    assert not plugin.exists()
    with pytest.raises(data.DataError):
        audit.read("a" * 64)
    assert not plugin.exists()
    override = tmp_path / "override"
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(override))
    assert audit.root() == override / "records"
    assert hooks.home() == override and override.is_dir()


def test_acquisition_serializes_full_ohlcv_without_losing_integer_volume(market):
    from broker_analysis_mcp import snapshots

    result = acquire()
    assert len(result["snapshot_id"]) == 64
    raw = audit.read(result["snapshot_id"])
    assert raw["kind"] == "market_snapshot" and raw["schema_version"] == 1
    stored = snapshots.load(result["snapshot_id"])
    restored, meta = stored.history("AAPL")
    original = market[0]["AAPL"].copy()
    original.index = original.index.tz_localize(None).normalize()
    pd.testing.assert_frame_equal(restored, original, check_freq=False)
    assert int(restored.Volume.iloc[-1]) == 2**53 + 549
    assert restored.attrs["source_timestamps"][-1] == "2026-11-02T00:00:00-05:00"
    assert meta.last_bar_complete is True
    assert "values" not in json.dumps(result)


def test_historical_schedule_is_frozen_and_replays_without_provider(market, monkeypatch):
    from broker_analysis_mcp import snapshots

    frames, metadata, _, clock = market
    for ticker in metadata:
        regular = metadata[ticker]["currentTradingPeriod"]["regular"]
        metadata[ticker]["tradingPeriods"] = pd.DataFrame(
            {"start": [regular["start"]], "end": [regular["end"]]}, index=frames[ticker].index[-1:])
        metadata[ticker]["currentTradingPeriod"] = {"regular": {
            "start": regular["start"] + pd.Timedelta(days=1), "end": regular["end"] + pd.Timedelta(days=1)}}
    clock["observed_at"] = datetime.fromisoformat("2026-11-03T08:00Z")
    snapshot_id = acquire()["snapshot_id"]
    forbid_yahoo(monkeypatch)
    clock["observed_at"] = datetime.fromisoformat("2026-11-10T08:00Z")
    prices, meta = snapshots.load(snapshot_id).history("AAPL")
    assert str(prices.index[-1].date()) == "2026-11-02"
    assert meta.session_end_timestamp == "2026-11-02T17:30:00-05:00"
    assert meta.last_bar_complete is True
    assert meta.observed_at == "2026-11-03T08:00:00+00:00"


@pytest.mark.parametrize("schedule", [42, {}, [{"date": "2026-11-02", "start": "bad", "end": "bad"}],
                                      [{"date": "2026-11-02", "start": "2026-11-02T17:30Z", "end": "2026-11-02T09:00Z"}]])
def test_replay_rejects_malformed_captured_historical_schedule(market, schedule):
    from broker_analysis_mcp import snapshots

    raw = audit.read(acquire()["snapshot_id"])
    raw["histories"]["AAPL"]["provider_metadata"]["tradingPeriods"] = schedule
    with pytest.raises(data.DataError, match=r"schedule|session"):
        snapshots.load(audit.put(raw))


def test_replay_rejects_session_end_without_matching_captured_evidence(market):
    from broker_analysis_mcp import snapshots

    raw = audit.read(acquire()["snapshot_id"])
    value = raw["histories"]["AAPL"]
    value["provider_metadata"]["tradingPeriods"] = []
    value["provider_metadata"]["currentTradingPeriod"] = {}
    with pytest.raises(data.DataError, match="session end"):
        snapshots.load(audit.put(raw))


def test_snapshot_replay_is_shared_by_all_five_reports_and_frozen(market, monkeypatch):
    result = acquire(tickers=["AAPL", "ENI.MI", "SPY"], benchmark="SPY", base_currency="EUR")
    snapshot_id = result["snapshot_id"]
    forbid_yahoo(monkeypatch)
    r = server.risk_metrics("AAPL", snapshot_id=snapshot_id)
    t = server.technical_indicators("AAPL", snapshot_id=snapshot_id)
    c = json.loads(server.custom_analysis("AAPL", [IndicatorSpec(id="sma50", fn="sma", params={"n": 50}),
                                                   IndicatorSpec(id="rsi14", fn="rsi")], chart=False, snapshot_id=snapshot_id)[0])
    e = server.etf_analysis("SPY", "SPY", snapshot_id=snapshot_id)
    p = server.portfolio_risk([Position(ticker="AAPL", market_value=1000)], 1000,
                              base_currency="EUR", snapshot_id=snapshot_id)
    for report in (r, t, c, e, p):
        assert report["snapshot_id"] == snapshot_id
        assert report["last_bar_complete"] is True
        assert report["window"]["end"] == "2026-11-02"
        assert report["retrieved_at"] == "2026-11-02T23:00:00+00:00"
    assert t["metrics"]["close"]["value"] == c["last_close"]
    assert t["metrics"]["sma50"]["value"] == c["series_last_values"]["sma50"]
    assert t["metrics"]["rsi14"]["value"] == c["series_last_values"]["rsi14"]
    assert e["metrics"]["tracking_difference"]["value"] == e["metrics"]["tracking_error"]["value"] == 0
    assert e["name"] == "Index ETF"
    assert "unrelated_private_profile_field" not in json.dumps(audit.read(snapshot_id))
    assert sum(position["component_es"]["value"] for position in p["positions"]) == pytest.approx(p["metrics"]["expected_shortfall_fhs"]["value"])


@pytest.mark.parametrize("change", [{"period": "1y"}, {"include_incomplete_session": True}, {"ticker": "OTHER"}, {"benchmark": "OTHER"}])
def test_conflicting_replay_requests_fail_before_network(market, monkeypatch, change):
    snapshot_id = acquire()["snapshot_id"]
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match=r"snapshot|period|policy|coverage") as error:
        server.risk_metrics(**({"ticker": "AAPL", "snapshot_id": snapshot_id} | change))
    assert not isinstance(error.value, UnexpectedToolError)


def test_missing_fx_and_etf_profile_do_not_fall_back_to_live_data(market, monkeypatch):
    from broker_analysis_mcp import snapshots

    first = acquire()["snapshot_id"]
    second = acquire(tickers=["SPY"], benchmark="SPY")["snapshot_id"]
    raw = audit.read(second)
    raw["profiles"] = {}
    missing_profile = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(data.DataError, match="FX"):
        snapshots.request(first, None, "5y", None, ["AAPL"], base_currency="EUR")
    with pytest.raises(ToolError, match="profile"):
        server.etf_analysis("SPY", "SPY", snapshot_id=missing_profile)


def test_wrong_kind_corruption_and_identity_boundaries_fail_without_fallback(market, monkeypatch, tmp_path):
    snapshot_id = acquire()["snapshot_id"]
    wrong = audit.put({"kind": "portfolio_analysis", "schema_version": 1})
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="kind"):
        server.technical_indicators("AAPL", snapshot_id=wrong)
    with pytest.raises(ToolError, match="digest"):
        server.technical_indicators("AAPL", snapshot_id="../../escape")
    path = audit.root() / f"{snapshot_id}.json"
    content = path.read_bytes()
    path.write_bytes(b"{}")
    with pytest.raises(ToolError, match="integrity"):
        server.technical_indicators("AAPL", snapshot_id=snapshot_id)
    path.write_bytes(content)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "another-identity"))
    with pytest.raises(ToolError, match="read"):
        server.technical_indicators("AAPL", snapshot_id=snapshot_id)


def test_snapshot_replay_survives_real_stdio_restart(market, monkeypatch):
    snapshot_id = acquire()["snapshot_id"]
    async def session():
        code = ("import yfinance as yf; yf.Ticker=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('provider forbidden')); "
                "from broker_analysis_mcp.server import main; main()")
        params = StdioServerParameters(command=sys.executable, args=["-c", code], env=dict(os.environ))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool("technical_indicators", {"ticker": "AAPL", "snapshot_id": snapshot_id})
            assert not result.is_error, str(result.content)
            return json.loads(result.content[0].text)
    first, second = asyncio.run(session()), asyncio.run(session())
    assert first == second


def test_new_catalog_stays_lazy_and_declares_snapshot_persistence():
    probe = subprocess.run([sys.executable, "-c", "import asyncio,sys; from broker_analysis_mcp.server import server; "
                            "tools=asyncio.run(server.list_tools()); assert len(tools)==15; "
                            "assert not ({'pandas','numpy','scipy','yfinance','matplotlib'} & sys.modules.keys())"],
                           capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    tools = {tool.name: tool for tool in asyncio.run(server.server.list_tools())}
    assert tools["market_snapshot"].annotations.read_only_hint is False
    assert tools["risk_metrics"].annotations.read_only_hint is True


def test_directional_fx_coverage_is_acquired_upfront(market):
    result = acquire(tickers=["AAPL", "ENI.MI"], benchmark="SPY", base_currency="JPY")
    # No reverse or synthetic crosses: USD->JPY, EUR->JPY and USD->EUR are separately required.
    assert set(result["fx_pairs"]) == {"USD/JPY", "EUR/JPY", "USD/EUR"}


@pytest.mark.parametrize("malformed", ["missing_declared", "invalid_policy", "invalid_dates", "missing_metadata", "bad_profile", "missing_fx", "fx_policy"])
def test_hash_valid_malformed_snapshot_fails_as_domain_error(market, monkeypatch, malformed):
    snapshot_id = acquire(tickers=["AAPL", "SPY"], base_currency="EUR")["snapshot_id"]
    raw = audit.read(snapshot_id)
    if malformed == "missing_declared":
        del raw["histories"]["SPY"]
    elif malformed == "invalid_policy":
        raw["parameters"]["include_incomplete_session"] = "false"
    elif malformed == "invalid_dates":
        raw["histories"]["AAPL"]["dates"][0] += "T01:00:00"
    elif malformed == "missing_metadata":
        raw["histories"]["AAPL"]["meta"]["last_bar_complete"] = "yes"
    elif malformed == "bad_profile":
        raw["profiles"]["SPY"] = {"data": {}, "retrieved_at": None}
    elif malformed == "missing_fx":
        raw["fx"] = {}
    elif malformed == "fx_policy":
        raw["fx"]["USD/EUR"]["meta"]["include_incomplete_session"] = True
    invalid = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="snapshot") as error:
        server.technical_indicators("AAPL", snapshot_id=invalid)
    assert not isinstance(error.value, UnexpectedToolError)


def test_opt_in_policy_and_acquisition_metadata_remain_frozen(market, monkeypatch):
    result = acquire(include_incomplete_session=True)
    raw = audit.read(result["snapshot_id"])
    # Move the applicable close into the future at acquisition, then capture a new opt-in dataset.
    market[1]["AAPL"]["currentTradingPeriod"]["regular"]["end"] = pd.Timestamp("2026-11-03T00:00Z")
    result = acquire(include_incomplete_session=True)
    forbid_yahoo(monkeypatch)
    report = server.technical_indicators("AAPL", snapshot_id=result["snapshot_id"])
    assert report["include_incomplete_session"] is True
    assert report["last_bar_complete"] is False
    assert report["retrieved_at"] == "2026-11-02T23:00:00+00:00"
    assert report["last_bar_timestamp"] == raw["histories"]["AAPL"]["source_timestamps"][-1]
    with pytest.raises(ToolError, match="policy"):
        server.technical_indicators("AAPL", snapshot_id=result["snapshot_id"], include_incomplete_session=False)


def test_invalid_capture_is_rejected_before_provider_and_persistence(monkeypatch, tmp_path):
    forbid_yahoo(monkeypatch)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "absent"))
    for request in ({"tickers": ["aapl", "AAPL"]}, {"tickers": [" "]}, {"period": "invalid"}, {"base_currency": "EURO"}):
        with pytest.raises(ToolError):
            acquire(**request)
    assert not (tmp_path / "absent").exists()


def test_snapshot_etf_close_warning_uses_captured_date_not_replay_clock(market, monkeypatch):
    from broker_analysis_mcp import analysis

    for ticker, timezone in (("SPY", "Europe/Rome"), ("AAPL", "Africa/Lagos")):
        market[0][ticker].index = market[0][ticker].index.tz_localize(None).tz_localize(timezone)
        market[1][ticker].update(currency="EUR", exchangeTimezoneName=timezone,
                                currentTradingPeriod={"regular": {
                                    "start": pd.Timestamp("2026-11-02T09:00", tz=timezone),
                                    "end": pd.Timestamp("2026-11-02T17:30", tz=timezone)}})
    snapshot_id = acquire(tickers=["SPY"], benchmark="AAPL")["snapshot_id"]
    forbid_yahoo(monkeypatch)
    class Clock(datetime):
        month = 11
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, cls.month, 2, 12, tzinfo=tz)
    monkeypatch.setattr(analysis, "datetime", Clock)
    first = server.etf_analysis("SPY", "AAPL", snapshot_id=snapshot_id)
    Clock.month = 7
    second = server.etf_analysis("SPY", "AAPL", snapshot_id=snapshot_id)
    assert first == second


@pytest.mark.parametrize("field,value", [
    ("ticker", None), ("warnings", None), ("currency", []), ("periods_per_year", 0),
    ("last_bar_complete", False),
    ("observed_at", "2026-11-01T00:00:00Z"), ("retrieved_at", "2026-11-01T00:00:00Z"),
])
def test_malformed_snapshot_metadata_is_rejected_before_report_calculation(market, monkeypatch, field, value):
    raw = audit.read(acquire()["snapshot_id"])
    raw["histories"]["AAPL"]["meta"][field] = value
    invalid = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="snapshot") as error:
        server.technical_indicators("AAPL", snapshot_id=invalid)
    assert not isinstance(error.value, UnexpectedToolError)


@pytest.mark.parametrize("field,value", [("netExpenseRatio", "invalid"), ("totalAssets", "invalid"), ("currency", [])])
def test_malformed_snapshot_etf_values_are_rejected_before_report_calculation(market, monkeypatch, field, value):
    raw = audit.read(acquire(tickers=["SPY"], benchmark="SPY")["snapshot_id"])
    raw["profiles"]["SPY"]["data"][field] = value
    invalid = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="snapshot") as error:
        server.etf_analysis("SPY", "SPY", snapshot_id=invalid)
    assert not isinstance(error.value, UnexpectedToolError)


def test_filtered_fx_benchmark_retains_aware_aligned_source_label(market, monkeypatch):
    # The benchmark includes an extra final session; FX lacks its leading history.
    for ticker in ("SPY", "USDEUR=X"):
        next_row = market[0][ticker].iloc[[-1]].copy()
        next_row.index += pd.Timedelta(days=1)
        market[0][ticker] = pd.concat([market[0][ticker], next_row])
    market[0]["USDEUR=X"] = market[0]["USDEUR=X"].loc["2025-01-01":]
    market[3]["observed_at"] = datetime.fromisoformat("2026-11-03T23:00Z")
    # Rome primary ends Nov2; New York benchmark ends Nov3. Unknown latest bar is opt-in.
    snapshot_id = acquire(tickers=["ENI.MI"], benchmark="SPY", include_incomplete_session=True)["snapshot_id"]
    forbid_yahoo(monkeypatch)
    report = server.risk_metrics("ENI.MI", benchmark="SPY", snapshot_id=snapshot_id)
    assert report["comparison_window"]["end"] == "2026-11-02"
    source = report["session_sources"]["SPY"]
    assert source["last_bar_timestamp"] == "2026-11-02T00:00:00-05:00"
    assert source["last_bar_complete"] is True


def test_snapshot_cannot_claim_completion_before_captured_regular_close(market, monkeypatch):
    raw = audit.read(acquire()["snapshot_id"])
    meta = raw["histories"]["AAPL"]["meta"]
    meta["observed_at"] = meta["retrieved_at"] = "2026-11-02T15:00:00Z"
    invalid = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="snapshot"):
        server.technical_indicators("AAPL", snapshot_id=invalid)


def test_invalid_provider_etf_profile_is_domain_error_without_publishing(market, monkeypatch):
    monkeypatch.setattr(data, "info", lambda ticker: {"quoteType": "ETF", "netExpenseRatio": "invalid"})
    with pytest.raises(ToolError, match="snapshot") as error:
        acquire(tickers=["SPY"])
    assert not isinstance(error.value, UnexpectedToolError)
    assert not audit.root().exists()


@pytest.mark.parametrize("period", [["invalid"], {"regular": ["invalid"]}])
def test_malformed_snapshot_provider_session_shape_is_domain_error(market, monkeypatch, period):
    raw = audit.read(acquire()["snapshot_id"])
    raw["histories"]["AAPL"]["provider_metadata"]["currentTradingPeriod"] = period
    invalid = audit.put(raw)
    forbid_yahoo(monkeypatch)
    with pytest.raises(ToolError, match="snapshot") as error:
        server.technical_indicators("AAPL", snapshot_id=invalid)
    assert not isinstance(error.value, UnexpectedToolError)
