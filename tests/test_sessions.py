"""Daily-session selection uses provider boundaries, never a UTC-date shortcut."""

from datetime import datetime
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from broker_analysis_mcp import analysis, audit, data, hooks, portfolio, server, workbench
from broker_analysis_mcp.models import Position


def frame(last="2026-11-03", timezone="America/New_York", count=270):
    dates = pd.date_range(end=last, periods=count, tz=timezone)
    close = 100 * np.cumprod(1 + np.random.default_rng(8).normal(.0002, .01, len(dates)))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99,
                         "Close": close, "Volume": np.arange(len(dates), dtype="int64") + 2**53}, index=dates)


def provider(monkeypatch, prices, start=None, end=None, instrument="EQUITY", timezone=None, schedule=None):
    metadata = dict(currency="USD", exchangeTimezoneName=timezone or str(prices.index.tz), instrumentType=instrument)
    if schedule is not None:
        metadata["tradingPeriods"] = schedule
    if start and end:
        metadata["currentTradingPeriod"] = {"regular": {"start": pd.Timestamp(start), "end": pd.Timestamp(end)}}
    class Ticker:
        history_metadata = metadata
        info: ClassVar[dict] = {"currency": "USD", "quoteType": "ETF"}
        def history(self, **kwargs):
            return prices.copy()
    monkeypatch.setattr(data.yf, "Ticker", lambda *a: Ticker())
    return metadata


@pytest.mark.parametrize(("start", "end", "now", "expected"), [
    ("2026-11-03T09:30-05:00", "2026-11-03T16:00-05:00", "2026-11-03T20:59Z", "2026-11-02"),
    ("2026-11-03T09:30-05:00", "2026-11-03T16:00-05:00", "2026-11-03T21:00Z", "2026-11-03"),
    ("2026-11-03T09:30-05:00", "2026-11-03T13:00-05:00", "2026-11-03T18:00Z", "2026-11-03"),
    ("2026-11-04T09:30-05:00", "2026-11-04T16:00-05:00", "2026-11-04T10:00Z", "2026-11-02"),
    ("2026-11-02T18:00-05:00", "2026-11-03T17:00-05:00", "2026-11-03T20:00Z", "2026-11-02"),
])
def test_equity_close_early_close_next_session_and_overnight(monkeypatch, start, end, now, expected):
    provider(monkeypatch, frame(), start, end)
    prices, meta = data.history("A", observed_at=datetime.fromisoformat(now))
    assert str(prices.index[-1].date()) == expected
    assert meta.last_bar_complete is True
    assert meta.include_incomplete_session is False
    assert pd.Timestamp(meta.last_bar_timestamp).tzinfo is not None


@pytest.mark.parametrize(("day", "offset", "now"), [
    ("2026-03-06", "-05:00", "2026-03-06T21:00Z"),
    ("2026-03-09", "-04:00", "2026-03-09T20:00Z"),
])
def test_exchange_daylight_saving_boundaries(monkeypatch, day, offset, now):
    provider(monkeypatch, frame(day), f"{day}T09:30{offset}", f"{day}T16:00{offset}")
    prices, meta = data.history("A", observed_at=datetime.fromisoformat(now))
    assert str(prices.index[-1].date()) == day
    assert pd.Timestamp(meta.last_bar_timestamp).utcoffset() == pd.Timestamp(f"{day}T00:00{offset}").utcoffset()


def test_explicit_opt_in_keeps_open_bar_but_never_future_bars(monkeypatch):
    provider(monkeypatch, frame("2026-11-04"), "2026-11-03T09:30-05:00", "2026-11-03T16:00-05:00")
    prices, meta = data.history("A", include_incomplete_session=True, observed_at=datetime.fromisoformat("2026-11-03T16:00Z"))
    assert str(prices.index[-1].date()) == "2026-11-03"
    assert meta.last_bar_complete is False
    assert any(item["reason"].startswith("future") for item in meta.excluded_candidates)


@pytest.mark.parametrize("now", ["2026-11-07T23:59Z", "2026-11-07T23:59:59Z", "2026-11-08T00:00Z"])
def test_crypto_weekend_waits_for_exact_utc_midnight(monkeypatch, now):
    provider(monkeypatch, frame("2026-11-07", "UTC"), "2026-11-07T00:00Z", "2026-11-07T23:59Z", "CRYPTOCURRENCY")
    prices, meta = data.history("BTC-USD", observed_at=datetime.fromisoformat(now))
    assert str(prices.index[-1].date()) == ("2026-11-07" if now == "2026-11-08T00:00Z" else "2026-11-06")
    assert meta.last_bar_complete is True


@pytest.mark.parametrize("stale", [False, True])
def test_unknown_latest_completion_is_excluded_with_reason(monkeypatch, stale):
    provider(monkeypatch, frame(), "2026-10-30T09:30-04:00" if stale else None, "2026-10-30T16:00-04:00" if stale else None)
    prices, meta = data.history("A", observed_at=datetime.fromisoformat("2026-11-03T16:00Z"))
    assert str(prices.index[-1].date()) == "2026-11-02"
    assert meta.excluded_candidates[-1]["complete"] is None
    assert "metadata" in meta.excluded_candidates[-1]["reason"]


@pytest.mark.parametrize(("day", "offset", "close", "observed", "expected"), [
    ("2026-11-04", "-05:00", "16:00", "2026-11-05T08:10Z", "2026-11-04"),
    ("2026-11-04", "-05:00", "13:00", "2026-11-04T17:59Z", "2026-11-03"),
    ("2026-11-04", "-05:00", "13:00", "2026-11-04T18:00Z", "2026-11-04"),
    ("2026-03-06", "-05:00", "16:00", "2026-03-09T10:00Z", "2026-03-06"),
])
def test_historical_schedule_matches_bar_date_before_next_session(monkeypatch, day, offset, close, observed, expected):
    prices = frame(day)
    schedule = pd.DataFrame({"start": [pd.Timestamp(f"{day}T09:30{offset}")],
                             "end": [pd.Timestamp(f"{day}T{close}{offset}")]}, index=prices.index[-1:])
    provider(monkeypatch, prices, "2026-11-05T09:30-05:00", "2026-11-05T16:00-05:00", schedule=schedule)
    selected, meta = data.history("A", observed_at=datetime.fromisoformat(observed))
    assert str(selected.index[-1].date()) == expected
    assert meta.last_bar_complete is True
    assert meta.session_end_timestamp == (f"{day}T{close}:00{offset}" if expected == day else None)
    assert selected.attrs["provider_metadata"]["tradingPeriods"][0]["date"] == day


def test_unmatched_bar_does_not_borrow_another_session_end(monkeypatch):
    provider(monkeypatch, frame(), "2026-11-04T09:30-05:00", "2026-11-04T16:00-05:00")
    _, meta = data.history("A", observed_at=datetime.fromisoformat("2026-11-04T10:00Z"))
    assert meta.session_end_timestamp is None


def test_same_completed_frame_reaches_every_report(monkeypatch, tmp_path):
    provider(monkeypatch, frame(), "2026-11-03T09:30-05:00", "2026-11-03T16:00-05:00")
    original = data.history
    def history(ticker, period="3y", include_incomplete_session=False):
        return original(ticker, period, include_incomplete_session, observed_at=datetime.fromisoformat("2026-11-03T21:01Z"))
    monkeypatch.setattr(data, "history", history)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    reports = [analysis.risk_report("A"), analysis.technical_report("A"),
               workbench.run("A", "1y", [], [], [], [], False)[0],
               analysis.etf_report("A", "A"), portfolio.report([Position(ticker="A", market_value=1000)], 1000, "USD")]
    assert {r["window"]["end"] for r in reports} == {"2026-11-03"}
    for report in reports:
        assert report["last_bar_complete"] is True
        assert report["include_incomplete_session"] is False
        assert report["last_bar_timestamp"] == "2026-11-03T00:00:00-05:00"
    assert audit.record(reports[-1]["audit"]["record_id"])["last_bar_complete"] is True


def test_portfolio_session_provenance_describes_the_aligned_bar(monkeypatch, tmp_path):
    prices = frame()
    metadata = provider(monkeypatch, prices, "2026-11-03T09:30-05:00", "2026-11-03T16:00-05:00")
    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
        @property
        def history_metadata(self):
            if self.ticker == "A":
                return metadata
            return metadata | {"currentTradingPeriod": {"regular": {
                "start": pd.Timestamp("2026-11-02T09:30-05:00"), "end": pd.Timestamp("2026-11-02T16:00-05:00")}}}
        def history(self, **kwargs):
            return prices.copy() if self.ticker == "A" else prices.iloc[:-1].copy()
    monkeypatch.setattr(data.yf, "Ticker", Ticker)
    original = data.history
    monkeypatch.setattr(data, "history", lambda ticker, period, include: original(
        ticker, period, include, observed_at=datetime.fromisoformat("2026-11-03T16:00Z")))
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    report = portfolio.report([Position(ticker="A", market_value=500), Position(ticker="B", market_value=500)],
                              1000, "USD", include_incomplete_session=True)
    assert report["window"]["end"] == "2026-11-02"
    assert report["last_bar_timestamp"] == "2026-11-02T00:00:00-05:00"
    assert report["last_bar_complete"] is True


def test_live_hook_loader_explicitly_requests_incomplete_sessions(monkeypatch):
    called = []
    def history(ticker, period, include_incomplete_session=False):
        called.append(include_incomplete_session)
        return frame().tz_localize(None), data.Meta(ticker, "USD")
    monkeypatch.setattr(data, "history", history)
    monkeypatch.setattr(data.yf, "Ticker", lambda *a: pytest.fail("legacy loader reached provider instead of explicit live policy"))
    hook = {"ticker": "A", "period": "1y", "indicators": [], "series": [],
            "condition": {"name": "positive", "when": "close > 0"}}
    hooks._evaluate(hook, hooks.create.__defaults__[-1])
    assert called == [True]


def test_public_opt_in_is_exposed_and_validated_without_provider(monkeypatch):
    import asyncio

    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    for name in ("risk_metrics", "technical_indicators", "custom_analysis", "etf_analysis", "portfolio_risk"):
        assert tools[name].input_schema["properties"]["include_incomplete_session"]["default"] is None


@pytest.mark.parametrize("report_kind", ["risk", "etf"])
@pytest.mark.parametrize("open_source", ["benchmark", "fx"])
def test_comparison_reports_describe_incomplete_benchmark_and_fx(monkeypatch, report_kind, open_source):
    """Rome has closed while the New York benchmark or required FX still trades."""
    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker
        @property
        def history_metadata(self):
            timezone = "Europe/Rome" if self.ticker == "FUND" else "America/New_York"
            open_now = self.ticker == ("BENCH" if open_source == "benchmark" else "USDEUR=X")
            return {"currency": "EUR" if self.ticker == "FUND" else "USD",
                    "exchangeTimezoneName": timezone, "instrumentType": "ETF",
                    "currentTradingPeriod": {"regular": {
                        "start": pd.Timestamp("2026-11-03T09:00", tz=timezone),
                        "end": pd.Timestamp("2026-11-03T16:00" if open_now else "2026-11-03T10:00", tz=timezone)}}}
        def history(self, **kwargs):
            timezone = "Europe/Rome" if self.ticker == "FUND" else "America/New_York"
            return frame(timezone=timezone)
        @property
        def info(self):
            return {"quoteType": "ETF", "currency": "EUR"}
    monkeypatch.setattr(data.yf, "Ticker", Ticker)
    original = data.history
    monkeypatch.setattr(data, "history", lambda ticker, period, include: original(
        ticker, period, include, observed_at=datetime.fromisoformat("2026-11-03T18:00Z")))
    report = (analysis.risk_report if report_kind == "risk" else analysis.etf_report)(
        "FUND", benchmark="BENCH", include_incomplete_session=True)
    assert report["last_bar_complete"] is False
    assert report["session_sources"]["FUND"]["last_bar_complete"] is True
    assert report["session_sources"]["BENCH" if open_source == "benchmark" else "USDEUR=X"]["last_bar_complete"] is False
    assert report["comparison_window"]["end"] == "2026-11-03"
