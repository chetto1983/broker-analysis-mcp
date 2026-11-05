"""Network boundary fixtures for real standalone MCP subprocesses, never production hooks."""

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from curl_cffi.requests.exceptions import ConnectionError, Timeout
from yfinance.exceptions import YFRateLimitError


class Ticker:
    def __init__(self, ticker):
        self.ticker = ticker

    @property
    def history_metadata(self):
        return {"currency": "USD", "exchangeTimezoneName": "America/New_York", "instrumentType": "EQUITY",
                "currentTradingPeriod": {"regular": {"start": "2026-11-04T09:30:00-05:00",
                                                      "end": "2026-11-04T16:00:00-05:00"}}}

    @property
    def info(self):
        return {"quoteType": "EQUITY", "currency": "USD"}

    def history(self, **kwargs):
        if self.ticker == "SLOW":
            Path(os.environ["LATE_WRITE_MARKER"] + ".started").write_text("started")
            time.sleep(12)
            Path(os.environ["LATE_WRITE_MARKER"]).write_text("late mutation")
        if self.ticker == "CRASH":
            os._exit(37)
        if self.ticker == "RATE":
            raise YFRateLimitError()
        if self.ticker == "TIMEOUT":
            raise Timeout("private provider URL and token")
        if self.ticker == "DOWN":
            raise ConnectionError("private endpoint")
        if self.ticker == "EMPTY":
            return pd.DataFrame()
        close = 100 + np.arange(270) * .1 + np.sin(np.arange(270))
        return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                             "Close": close, "Volume": 1000},
                            index=pd.bdate_range(end="2026-11-04", periods=270, tz="America/New_York"))


def install():
    import yfinance

    yfinance.Ticker = Ticker
