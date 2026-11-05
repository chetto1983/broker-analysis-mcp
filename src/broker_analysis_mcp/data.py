"""Yahoo Finance access through yfinance.

Prototype only: yfinance reads Yahoo's unofficial endpoints, is meant for personal and research
use, and can break without notice. Every result carries its source and retrieval time so an
analyst can tell where a number came from.
"""

import math
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd
import yfinance as yf

from .errors import DataError
from .fundamentals import Year
from .models import HistoryParameters, validate_parameters
from .risk import PERIODS, periods_per_year

SOURCE = "Yahoo Finance via yfinance (unofficial, prototype only)"

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="yfinance")


@dataclass
class Meta:
    ticker: str
    currency: str
    timezone: str = "?"
    periods_per_year: int = PERIODS
    retrieved_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    source: str = SOURCE
    warnings: list[str] = field(default_factory=list)
    last_bar_timestamp: str | None = None
    last_bar_complete: bool | None = None
    include_incomplete_session: bool = False
    completeness_reason: str = "session metadata unavailable"
    excluded_candidates: list[dict] = field(default_factory=list)
    observed_at: str | None = None
    last_trade_timestamp: str | None = None
    session_end_timestamp: str | None = None


def report_metadata(price_observations: int | None = None, return_observations: int | None = None,
                    retrieved_at: str | None = None) -> dict:
    return {"schema_version": 1,
            "retrieved_at": retrieved_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "price_observations": price_observations, "return_observations": return_observations}


def session_metadata(meta: Meta | dict, observations=None, date=None) -> dict:
    details = {key: (meta.get(key) if isinstance(meta, dict) else getattr(meta, key)) for key in (
        "last_bar_timestamp", "last_bar_complete", "include_incomplete_session", "completeness_reason",
        "excluded_candidates", "observed_at", "last_trade_timestamp", "session_end_timestamp")}
    if observations is not None and date is not None and date < observations.index[-1]:
        selected = observations.index.searchsorted(date, side="right") - 1
        if selected < 0:
            raise DataError("session provenance has no observation on or before the aligned date")
        labels = observations.attrs.get("source_timestamps", [])
        details["last_bar_timestamp"] = labels[selected] if len(labels) == len(observations) else None
        details["last_bar_complete"] = True
        details["completeness_reason"] = "aligned historical daily bar precedes a later observed session"
        label = _aware(details["last_bar_timestamp"])
        provider = observations.attrs.get("provider_metadata", {})
        end = session_bounds(label, provider)[1] if label is not None else None
        if label is not None and provider.get("instrumentType") == "CRYPTOCURRENCY":
            end = label.tz_convert("UTC").normalize() + pd.Timedelta(days=1)
        details["session_end_timestamp"] = end.isoformat() if end is not None else None
    return details


def combined_session_metadata(sources: dict[str, dict]) -> dict:
    """Summarise every series actually used; preserve individual source evidence."""
    details = next(iter(sources.values())).copy()
    states = [source["last_bar_complete"] for source in sources.values()]
    details["last_bar_complete"] = False if False in states else True if all(state is True for state in states) else None
    details["completeness_reason"] = "completion of all reported asset, benchmark and FX observations"
    details["excluded_candidates"] = [candidate | {"source": name} for name, source in sources.items()
                                       for candidate in source["excluded_candidates"]]
    return details | {"session_sources": sources}


def _aware(value) -> pd.Timestamp | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        stamp = pd.Timestamp(value, unit="s", tz="UTC") if isinstance(value, (int, float)) else pd.Timestamp(value)
        return stamp if stamp.tzinfo is not None and not pd.isna(stamp) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _json_metadata(value):
    if isinstance(value, dict):
        return {key: _json_metadata(item) for key, item in value.items()}
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _session_labels(prices: pd.DataFrame, metadata: dict) -> pd.DatetimeIndex:
    labels = prices.index
    if labels.tz is None:
        timezone = metadata.get("exchangeTimezoneName")
        if not timezone:
            raise DataError("price history needs an exchange timezone to establish daily sessions")
        labels = labels.tz_localize(timezone)
    return labels


def _schedule_day(start: pd.Timestamp, end: pd.Timestamp, timezone: str) -> str | None:
    if not isinstance(timezone, str) or not timezone:
        raise DataError("session schedule requires an exchange timezone")
    try:
        local_start, local_end = start.tz_convert(timezone), end.tz_convert(timezone)
    except (KeyError, TypeError, ValueError):
        raise DataError("invalid exchange timezone for session schedule") from None
    # yfinance derives its schedule index from the start date. It cannot establish
    # which daily label belongs to a session crossing local midnight.
    return local_start.date().isoformat() if local_start.date() == local_end.date() else None


def regular_schedule(metadata: dict) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Validate the minimal dated evidence captured from yfinance's public schedule."""
    schedule = metadata.get("tradingPeriods", [])
    if not isinstance(schedule, list):
        raise DataError("invalid captured session schedule")
    out = {}
    for row in schedule:
        if not isinstance(row, dict) or set(row) != {"date", "start", "end"}:
            raise DataError("invalid captured session schedule row")
        start, end = _aware(row["start"]), _aware(row["end"])
        day = row["date"]
        if (not isinstance(day, str) or start is None or end is None or start >= end
                or day != _schedule_day(start, end, metadata.get("exchangeTimezoneName")) or day in out):
            raise DataError("invalid captured session schedule date/boundary")
        out[day] = (start, end)
    return out


def session_bounds(label: pd.Timestamp, metadata: dict, schedule=None) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    schedule = regular_schedule(metadata) if schedule is None else schedule
    if label.date().isoformat() in schedule:
        return schedule[label.date().isoformat()]
    period = metadata.get("currentTradingPeriod") or {}
    regular = period.get("regular") or {}
    start, end = _aware(regular.get("start")), _aware(regular.get("end"))
    # An overnight session's start date can also label the preceding closed bar.
    # Same-local-day dated evidence wins; current-period fallback uses its closing date.
    if start is not None and end is not None and start < end and label.date() == end.tz_convert(label.tz).date():
        return start, end
    return None, None


def _capture_schedule(raw_metadata, metadata: dict, labels: pd.DatetimeIndex, observed: pd.Timestamp) -> None:
    candidates = labels[labels <= observed]
    if metadata.get("instrumentType") == "CRYPTOCURRENCY" or not len(candidates) or session_bounds(candidates[-1], metadata)[0] is not None:
        return
    # yfinance 1.7 HistoryMetadata lazily fetches 5d/1h metadata for this public key.
    # https://github.com/ranaroussi/yfinance/blob/1.7.0/yfinance/scrapers/history.py
    # Request it only for an unmatched candidate, never by converting the whole mapping.
    schedule = raw_metadata.get("tradingPeriods")
    rows = []
    if isinstance(schedule, pd.DataFrame) and {"start", "end"} <= set(schedule.columns):
        for label, row in schedule.iterrows():
            start, end = _aware(row["start"]), _aware(row["end"])
            if (start is not None and end is not None
                    and _schedule_day(start, end, metadata.get("exchangeTimezoneName")) is not None):
                rows.append({"date": label.date().isoformat(), "start": start.isoformat(), "end": end.isoformat()})
    metadata["tradingPeriods"] = rows
    regular_schedule(metadata)


def _completed_sessions(prices: pd.DataFrame, metadata: dict, observed: pd.Timestamp, include: bool) -> tuple[pd.DataFrame, dict]:
    labels = _session_labels(prices, metadata)
    schedule = regular_schedule(metadata)
    crypto = metadata.get("instrumentType") == "CRYPTOCURRENCY"
    not_future = [i for i, label in enumerate(labels) if label <= observed]
    latest = not_future[-1] if not_future else -1
    keep, states, excluded = [], {}, []
    for i, label in enumerate(labels):
        complete, reason = None, "session metadata does not establish completion"
        start, end = session_bounds(label, metadata, schedule)
        future = label > observed
        if future:
            reason = "future daily bar excluded"
        elif crypto:
            boundary = label.tz_convert("UTC").normalize() + pd.Timedelta(days=1)
            complete, reason = observed >= boundary, "crypto daily session ends at next UTC midnight"
        elif start is not None:
            future = observed < start
            complete, reason = observed >= end, "provider regular session end"
            if future:
                reason = "future regular session excluded"
        elif i < latest:
            complete, reason = True, "a later observed daily session establishes this historical bar"
        states[i] = (complete, reason)
        if not future and (complete is True or include):
            keep.append(i)
        else:
            excluded.append({"timestamp": label.isoformat(), "complete": complete, "reason": reason})
    if not keep:
        raise DataError("insufficient observations: no eligible daily sessions; session metadata or completion is unavailable")
    selected = prices.iloc[keep].copy()
    source_labels = labels.take(keep)
    selected.index = source_labels.tz_localize(None).normalize()
    selected.attrs["source_timestamps"] = [label.isoformat() for label in source_labels]
    selected.attrs["provider_metadata"] = _json_metadata(metadata)
    complete, reason = states[keep[-1]]
    end = (source_labels[-1].tz_convert("UTC").normalize() + pd.Timedelta(days=1) if crypto
           else session_bounds(source_labels[-1], metadata, schedule)[1])
    details = {"last_bar_timestamp": source_labels[-1].isoformat(), "last_bar_complete": complete,
               "include_incomplete_session": include, "completeness_reason": reason,
               "excluded_candidates": excluded, "observed_at": observed.isoformat(),
               "last_trade_timestamp": (_aware(metadata.get("regularMarketTime")).isoformat()
                                        if _aware(metadata.get("regularMarketTime")) is not None else None),
               "session_end_timestamp": end.isoformat() if end is not None else None}
    return selected, details


def require_prices(prices: pd.DataFrame, minimum: int = 1, label: str = "price history") -> None:
    if len(prices) < minimum:
        raise DataError(f"insufficient observations for {label}: {len(prices)} prices, need at least {minimum}; request a longer period")
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise DataError(f"{label}: duplicate or unordered dates")
    columns = [c for c in ("Open", "High", "Low", "Close") if c in prices]
    if "Close" not in columns or not all(math.isfinite(v) and v > 0 for v in prices[columns].to_numpy().flat):
        raise DataError(f"{label}: prices must be finite and positive")


def _normalise_currency(prices: pd.DataFrame, currency: str) -> tuple[pd.DataFrame, str]:
    # London lines quote in pence (GBp); everything downstream works in pounds.
    if currency == "GBp":
        cols = [c for c in ("Open", "High", "Low", "Close") if c in prices]
        prices = prices.copy()
        prices[cols] = prices[cols] / 100
        return prices, "GBP"
    return prices, currency


def history(ticker: str, period: str = "3y", include_incomplete_session: bool = False,
            *, observed_at: datetime | None = None) -> tuple[pd.DataFrame, Meta]:
    """Daily OHLC adjusted for splits and dividends (total-return closes)."""
    validate_parameters(HistoryParameters, period=period)
    t = yf.Ticker(ticker)
    prices = t.history(period=period, interval="1d", auto_adjust=True)
    if prices.empty:
        raise DataError(f"no price history for {ticker!r}")
    prices = prices[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    raw_meta = t.history_metadata or {}
    meta = {key: raw_meta.get(key) for key in ("currency", "exchangeTimezoneName", "instrumentType", "currentTradingPeriod", "regularMarketTime")}
    observed = _aware(observed_at or datetime.now(UTC))
    if observed is None:
        raise DataError("observed_at must include a timezone")
    _capture_schedule(raw_meta, meta, _session_labels(prices, meta), observed)
    prices, details = _completed_sessions(prices, meta, observed, include_incomplete_session)
    require_prices(prices)
    currency = meta.get("currency") or t.info.get("currency") or "?"
    prices, currency = _normalise_currency(prices, currency)
    return prices, Meta(
        ticker=ticker,
        currency=currency,
        timezone=meta.get("exchangeTimezoneName") or "?",
        periods_per_year=periods_per_year(meta.get("instrumentType") or "?"),
        retrieved_at=observed.isoformat(),
        **details,
        warnings=[f"Excluded {len(details['excluded_candidates'])} incomplete, unknown or future daily bars"] if details["excluded_candidates"] else [],
    )


def fx_rate(from_ccy: str, to_ccy: str) -> float:
    if from_ccy == to_ccy:
        return 1.0
    pair = f"{from_ccy}{to_ccy}=X"
    closes = yf.Ticker(pair).history(period="5d")["Close"].dropna()
    if closes.empty:
        raise DataError(f"no FX rate for {pair}")
    return float(closes.iloc[-1])


def fx_series(from_ccy: str, to_ccy: str, period: str, include_incomplete_session: bool = False) -> pd.Series:
    if from_ccy == to_ccy:
        raise ValueError("same currency")
    prices, meta = history(f"{from_ccy}{to_ccy}=X", period, include_incomplete_session)
    closes = prices["Close"].copy()
    closes.attrs["session_metadata"] = session_metadata(meta)
    closes.attrs["source_timestamps"] = prices.attrs.get("source_timestamps", [])
    return closes


def info(ticker: str) -> dict:
    data = yf.Ticker(ticker).info or {}
    if not data or data.get("quoteType") is None:
        raise DataError(f"no profile for {ticker!r}")
    return data


# Statement lines by Year field: the first (statement, row) pair Yahoo reports wins.
ROWS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenue": (("inc", "Total Revenue"), ("inc", "Operating Revenue")),
    "cost_of_revenue": (("inc", "Cost Of Revenue"), ("inc", "Reconciled Cost Of Revenue")),
    "gross_profit": (("inc", "Gross Profit"),),
    "sga": (("inc", "Selling General And Administration"),),
    "ebitda": (("inc", "EBITDA"), ("inc", "Normalized EBITDA")),
    "ebit": (("inc", "EBIT"), ("inc", "Operating Income")),
    "pretax_income": (("inc", "Pretax Income"),),
    "net_income": (("inc", "Net Income Common Stockholders"), ("inc", "Net Income")),
    "net_income_continuing": (
        ("inc", "Net Income From Continuing Operation Net Minority Interest"),
        ("inc", "Net Income Continuous Operations"),
    ),
    "interest_expense": (("inc", "Interest Expense"), ("inc", "Interest Expense Non Operating")),
    "tax_rate": (("inc", "Tax Rate For Calcs"),),
    "diluted_eps": (("inc", "Diluted EPS"),),
    "total_assets": (("bal", "Total Assets"),),
    "current_assets": (("bal", "Current Assets"),),
    "current_liabilities": (("bal", "Current Liabilities"),),
    "cash": (("bal", "Cash And Cash Equivalents"), ("bal", "Cash Cash Equivalents And Short Term Investments")),
    "short_term_investments": (("bal", "Other Short Term Investments"),),
    "receivables": (("bal", "Accounts Receivable"), ("bal", "Receivables")),
    "inventory": (("bal", "Inventory"),),
    "payables": (("bal", "Accounts Payable"), ("bal", "Payables")),
    "net_ppe": (("bal", "Net PPE"),),
    "total_debt": (("bal", "Total Debt"),),
    "long_term_debt": (("bal", "Long Term Debt"),),
    "current_debt": (("bal", "Current Debt"),),
    "tax_payable": (("bal", "Income Tax Payable"), ("bal", "Total Tax Payable")),
    "total_liabilities": (("bal", "Total Liabilities Net Minority Interest"),),
    "retained_earnings": (("bal", "Retained Earnings"),),
    "equity": (("bal", "Stockholders Equity"), ("bal", "Common Stock Equity")),
    "invested_capital": (("bal", "Invested Capital"),),
    "shares": (("bal", "Ordinary Shares Number"), ("bal", "Share Issued")),
    "operating_cash_flow": (("cf", "Operating Cash Flow"), ("cf", "Cash Flow From Continuing Operating Activities")),
    "free_cash_flow": (("cf", "Free Cash Flow"),),
    "dividends_paid": (("cf", "Cash Dividends Paid"), ("cf", "Common Stock Dividend Paid")),
    "depreciation": (("cf", "Depreciation"),),
    "depreciation_amortization": (("cf", "Depreciation And Amortization"), ("inc", "Reconciled Depreciation")),
    "stock_issuance": (("cf", "Common Stock Issuance"), ("cf", "Issuance Of Capital Stock")),
}


def _value(frames: dict[str, pd.DataFrame], pairs: tuple[tuple[str, str], ...], date: pd.Timestamp) -> float | None:
    for statement, row in pairs:
        df = frames[statement]
        if row in df.index and date in df.columns:
            value = df.at[row, date]
            if pd.notna(value):
                return float(value)
    return None


def statements(ticker: str) -> tuple[list[Year], str, str]:
    """Annual statements newest first, their currency and the latest fiscal year end.

    The three statements can list different numbers of years, so lines are matched by fiscal
    year-end date, with the income statement's years as the reference.
    """
    t = yf.Ticker(ticker)
    frames = {"inc": t.income_stmt, "bal": t.balance_sheet, "cf": t.cashflow}
    if frames["inc"].empty or frames["bal"].empty:
        raise DataError(f"no annual statements for {ticker!r}")
    years = [
        Year(**{name: _value(frames, pairs, date) for name, pairs in ROWS.items()})
        for date in frames["inc"].columns
    ]
    profile = t.info or {}
    currency = profile.get("financialCurrency") or profile.get("currency") or "?"
    return years, currency, str(frames["inc"].columns[0].date())
