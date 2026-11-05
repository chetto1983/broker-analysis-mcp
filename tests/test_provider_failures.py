"""Exercise the provider boundary; transport fault fixtures live in test_runtime."""

import pandas as pd
import pytest

from broker_analysis_mcp import data


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan")])
def test_invalid_fx_is_rejected_before_conversion(monkeypatch, bad):
    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame({"Close": [bad]})
    monkeypatch.setattr(data.yf, "Ticker", lambda *a: Ticker())
    with pytest.raises(data.DataError, match="FX"):
        data.fx_rate("USD", "EUR")


def test_malformed_provider_frame_is_a_domain_failure(monkeypatch):
    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame({"Close": [100.]})
    monkeypatch.setattr(data.yf, "Ticker", lambda *a: Ticker())
    with pytest.raises(data.DataError, match="OHLCV"):
        data.history("A")
