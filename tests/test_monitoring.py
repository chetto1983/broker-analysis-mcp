"""One-shot CLI contracts, pre-network validation and independently verified archives."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from broker_analysis_mcp import audit, data, monitoring
from broker_analysis_mcp.models import Position, StressScenario


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"positions": [{"ticker": "AAPL", "market_value": 10000}],
                                "portfolio_value": 15000}), encoding="utf-8")
    return path


@pytest.mark.parametrize("reason,code", [("challenger_passed_validation", 0),
                                       ("insufficient_validation_history", 0),
                                       ("monitoring_deterioration", 0), ("coverage_alarm", 3)])
def test_status_exit_codes_and_json(monkeypatch, config, capsys, reason, code):
    calibration = {"observations": 63, "binomial_p_value": .001 if code == 3 else .5}

    def report(**kwargs):
        assert isinstance(kwargs["positions"][0], Position)
        assert kwargs["portfolio_value"] == 15000
        assert kwargs["base_currency"] == "EUR" and kwargs["scenarios"] == []
        return {"audit": {"record_id": "a" * 64}, "warnings": ["Finite tail sample"],
                "model_status": {"as_of": "2026-10-23", "champion": "ewma_094", "reason": reason,
                                 "fallback": True, "calibration": calibration}}

    monkeypatch.setattr(monitoring.portfolio, "report", report)
    assert monitoring.main(["--config", str(config)]) == code
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result == {"status": "coverage_alarm" if code == 3 else "ok", "record_id": "a" * 64,
                      "as_of": "2026-10-23", "champion": "ewma_094", "reason": reason,
                      "fallback": True, "warnings": ["Finite tail sample"], "calibration": calibration}


@pytest.mark.parametrize("change", [
    {"unknown": 1}, {"portfolio_value": 0}, {"portfolio_value": float("inf")},
    {"portfolio_value": "15000"}, {"portfolio_value": True},
    {"confidence": float("nan")}, {"confidence": .999}, {"base_currency": "?"}, {"period": "bad"},
    {"positions": []}, {"positions": [{"ticker": "AAPL", "market_value": 0}]},
    {"positions": [{"ticker": "AAPL", "market_value": "1"}]},
    {"positions": [{"ticker": "AAPL", "market_value": float("inf")}]},
    {"positions": [{"ticker": "AAPL", "market_value": 1, "currency": "USD"}]},
    {"positions": [{"ticker": "AAPL", "market_value": 1}, {"ticker": "aapl", "market_value": 2}]},
    {"scenarios": [{"name": "Typo", "asset_shock": {"AAPL": -.2}}]},
    {"scenarios": [{"name": "Unknown", "asset_shocks": {"OTHER": -.2}}]},
    {"scenarios": [{"name": "Infinity", "asset_shocks": {"AAPL": float("inf")}}]},
    {"scenarios": [{"name": "Coercion", "asset_shocks": {"AAPL": "-.2"}}]},
    {"scenarios": [{"name": "Base", "fx_shocks": {"EUR": -.2}}]},
    {"scenarios": [{"name": "Bad code", "fx_shocks": {"usd": -.2}}]},
    {"scenarios": [{"name": "Repeated"}, {"name": "Repeated"}]},
])
def test_invalid_config_never_reaches_analysis(monkeypatch, config, capsys, change):
    raw = json.loads(config.read_text(encoding="utf-8")) | change
    config.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(monitoring.portfolio, "report", lambda **kwargs: pytest.fail("analysis reached"))
    assert monitoring.main(["--config", str(config)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["status"] == "error"
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("raw", ['{"portfolio_value":1,"portfolio_value":2}', '{broken', '[]'])
def test_bad_json_rejected_before_analysis(monkeypatch, config, capsys, raw):
    config.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(monitoring.portfolio, "report", lambda **kwargs: pytest.fail("analysis reached"))
    assert monitoring.main(["--config", str(config)]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"


def test_missing_config_and_analysis_failure(monkeypatch, config, capsys):
    assert monitoring.main(["--config", str(config.with_name("missing.json"))]) == 2
    assert json.loads(capsys.readouterr().err)["error_type"] == "FileNotFoundError"

    def fail(**kwargs):
        raise data.DataError("provider unavailable")

    monkeypatch.setattr(monitoring.portfolio, "report", fail)
    assert monitoring.main(["--config", str(config)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "provider unavailable"


def test_config_flag_required(capsys):
    with pytest.raises(SystemExit) as error:
        monitoring.main([])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_example_is_explicitly_illustrative_and_valid():
    request = monitoring.load_config(Path(__file__).resolve().parents[1] / "examples" / "portfolio-monitor.json")
    assert [(p.ticker, p.market_value) for p in request.positions] == [("AAPL", 10000), ("ENI.MI", -3000)]
    assert request.portfolio_value == 15000 and request.base_currency == "EUR"
    assert isinstance(request.scenarios[0], StressScenario)
    assert "not real holdings" in request.scenarios[0].name


def test_repeated_runs_preserve_records_and_report_same_market_state(monkeypatch, tmp_path, config, capsys):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "archive"))
    dates = pd.bdate_range("2022-01-03", periods=600)
    prices = 100 * np.cumprod(1 + np.random.default_rng(18).normal(0, .01, len(dates)))
    monkeypatch.setattr(data, "history", lambda *args: (pd.DataFrame({"Close": prices}, index=dates),
                                                       data.Meta("AAPL", "EUR")))
    summaries = []
    for _ in range(2):
        assert monitoring.main(["--config", str(config)]) in {0, 3}
        summaries.append(json.loads(capsys.readouterr().out))
    first, second = summaries
    assert {k: v for k, v in first.items() if k != "record_id"} == {
        k: v for k, v in second.items() if k != "record_id"
    }
    for summary in summaries:
        output = audit.record(summary["record_id"])
        assert summary["as_of"] == str(dates[-1].date()) == output["window"]["end"]
        assert summary["calibration"] == output["model_status"]["calibration"]
        snapshot = audit.record(summary["record_id"], "snapshot")
        np.testing.assert_allclose(snapshot["portfolio_returns"], (prices[1:] / prices[:-1] - 1) * 10000 / 15000)
    assert audit.record(first["record_id"]) == audit.record(second["record_id"])
