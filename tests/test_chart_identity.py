"""Distinct chart analyses cannot overwrite a previously returned artifact."""

from datetime import UTC, datetime
from pathlib import Path

from test_contract import prices

from broker_analysis_mcp import data, workbench
from broker_analysis_mcp.models import IndicatorSpec


def test_same_second_charts_preserve_each_returned_result(monkeypatch, tmp_path):
    class Clock:
        @classmethod
        def now(cls, timezone):
            return datetime(2026, 11, 5, tzinfo=UTC)
    monkeypatch.setattr(workbench, "datetime", Clock)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    monkeypatch.setattr(data, "history", lambda ticker, *args: (prices(30), data.Meta(ticker, "USD")))
    first, _ = workbench.run("A", "1y", [IndicatorSpec(id="average", fn="sma", params={"n": 5})], [], [], [], True)
    first_spec = Path(first["chart"]["spec"])
    original = first_spec.read_bytes()
    second, _ = workbench.run("A", "1y", [IndicatorSpec(id="average", fn="sma", params={"n": 10})], [], [], [], True)
    assert first_spec.read_bytes() == original
    assert Path(second["chart"]["spec"]).read_bytes() != original
