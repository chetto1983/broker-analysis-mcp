"""Immutable full-OHLCV datasets in the existing verified audit object store."""

import math
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from . import audit, data
from .errors import DataError
from .models import Finite, HistoryParameters, SnapshotParameters, validate_parameters

COLUMNS = ("Open", "High", "Low", "Close", "Volume")
DTYPES = {"float32", "float64", "int32", "int64", "uint32", "uint64"}
PROFILE_FIELDS = ("quoteType", "netExpenseRatio", "totalAssets", "currency", "longName", "shortName", "fundFamily")
META_ADAPTER = TypeAdapter(data.Meta)


class ETFProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quoteType: Literal["ETF"]
    netExpenseRatio: Finite | None
    totalAssets: Finite | None
    currency: str | None
    longName: str | None
    shortName: str | None
    fundFamily: str | None


def _profile_data(value: dict) -> dict:
    try:
        return ETFProfile.model_validate(value, strict=True).model_dump()
    except ValidationError as error:
        raise DataError(f"invalid snapshot ETF profile: {error}") from error


def _check_session_time(meta: data.Meta, labels: list, provider: dict) -> None:
    observed = data._aware(meta.observed_at)
    last = data._aware(labels[-1])
    if observed > data._aware(meta.retrieved_at):
        raise ValueError("observation occurs after retrieval")
    schedule = data.regular_schedule(provider)
    period = provider.get("currentTradingPeriod")
    if period is None:
        period = {}
    if not isinstance(period, dict):
        raise ValueError("invalid provider trading-period shape")
    regular = period.get("regular")
    if regular is None:
        regular = {}
    if not isinstance(regular, dict):
        raise ValueError("invalid provider regular-session shape")
    expected = None
    crypto = provider.get("instrumentType") == "CRYPTOCURRENCY"
    if crypto:
        end = last.tz_convert("UTC").normalize() + pd.Timedelta(days=1)
        expected = observed >= end
    else:
        start, end = data.session_bounds(last, provider, schedule)
        if start is not None:
            if observed < start:
                raise ValueError("future regular session")
            expected = observed >= end
    if "tradingPeriods" in provider:
        if provider.get("exchangeTimezoneName") != meta.timezone:
            raise ValueError("session schedule timezone differs from source metadata")
        for label in labels:
            stamp = data._aware(label)
            if stamp.tz_convert(meta.timezone).tz_localize(None) != stamp.tz_localize(None):
                raise ValueError("source label contradicts exchange-local session timezone")
            start, boundary = data.session_bounds(stamp, provider, schedule)
            if crypto:
                boundary = stamp.tz_convert("UTC").normalize() + pd.Timedelta(days=1)
            if start is not None and observed < start:
                raise ValueError("retained future regular session")
            if boundary is not None and not meta.include_incomplete_session and observed < boundary:
                raise ValueError("retained incomplete session contradicts completed-only policy")
        if ((end is None and meta.session_end_timestamp is not None)
                or data._aware(meta.session_end_timestamp) != end):
            raise ValueError("session end contradicts captured dated schedule")
    if expected is not None and meta.last_bar_complete is not expected:
        raise ValueError("completion contradicts captured session/observation times")


def _serialize(prices: pd.DataFrame, meta: data.Meta) -> dict:
    return {"dates": [stamp.isoformat() for stamp in prices.index],
            "columns": {name: {"dtype": str(prices[name].dtype), "values": prices[name].tolist()} for name in COLUMNS},
            "source_timestamps": prices.attrs.get("source_timestamps", []),
            "provider_metadata": prices.attrs.get("provider_metadata", {}), "meta": asdict(meta)}


def _restore(value: dict) -> tuple[pd.DataFrame, data.Meta]:
    if not isinstance(value, dict) or not isinstance(value.get("columns"), dict):
        raise DataError("invalid snapshot history schema")
    dates = value.get("dates")
    labels = value.get("source_timestamps")
    if not isinstance(dates, list) or not dates or not isinstance(labels, list) or len(dates) != len(labels):
        raise DataError("invalid snapshot dates/source labels")
    columns = {}
    try:
        index = pd.DatetimeIndex(dates)
        if index.tz is not None or index.hasnans:
            raise ValueError("calculation dates must be nonmissing and naive")
        for name in COLUMNS:
            column = value["columns"][name]
            dtype, numbers = column["dtype"], column["values"]
            if dtype not in DTYPES or not isinstance(numbers, list) or len(numbers) != len(index):
                raise ValueError("invalid column dtype/length")
            if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) for number in numbers):
                raise ValueError("column values must be finite numbers")
            if dtype.startswith(("int", "uint")) and any(not isinstance(number, int) for number in numbers):
                raise ValueError("integer column contains a noninteger")
            columns[name] = pd.Series(numbers, index=index, dtype=dtype)
        if not isinstance(value["meta"], dict) or set(value["meta"]) != {entry.name for entry in fields(data.Meta)}:
            raise ValueError("invalid metadata fields")
        meta = META_ADAPTER.validate_json(audit.canonical(value["meta"]), strict=True)
        if ((meta.last_bar_complete is not None and type(meta.last_bar_complete) is not bool)
                or type(meta.include_incomplete_session) is not bool
                or meta.last_bar_timestamp != labels[-1]
                or meta.periods_per_year <= 0
                or (not meta.include_incomplete_session and meta.last_bar_complete is not True)
                or data._aware(meta.observed_at) is None or data._aware(meta.retrieved_at) is None):
            raise ValueError("invalid session metadata")
        for date, label in zip(index, labels, strict=True):
            stamp = data._aware(label)
            if stamp is None or stamp.tz_localize(None).normalize() != date:
                raise ValueError("source labels do not match calculation dates")
            if stamp > data._aware(meta.observed_at) or stamp > data._aware(meta.retrieved_at):
                raise ValueError("source label occurs after acquisition/observation")
        provider = value.get("provider_metadata")
        if not isinstance(provider, dict):
            raise ValueError("invalid provider metadata")
        _check_session_time(meta, labels, provider)
        prices = pd.DataFrame(columns, index=index)
        data.require_prices(prices)
        if any(not math.isfinite(number) or number < 0 for number in prices.Volume):
            raise ValueError("invalid volume")
        prices.attrs["source_timestamps"] = labels
        prices.attrs["provider_metadata"] = provider
        return prices, meta
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise DataError(f"invalid snapshot history: {error}") from error


@dataclass
class Dataset:
    payload: dict
    histories: dict[str, tuple[pd.DataFrame, data.Meta]]
    rates: dict[str, tuple[pd.DataFrame, data.Meta]]

    def history(self, ticker: str) -> tuple[pd.DataFrame, data.Meta]:
        key = ticker.upper()
        if key not in self.histories:
            raise DataError(f"snapshot coverage missing ticker {ticker!r}")
        prices, meta = self.histories[key]
        return prices.copy(deep=True), data.Meta(**asdict(meta))

    def fx(self, from_currency: str, to_currency: str) -> pd.Series:
        key = f"{from_currency}/{to_currency}"
        if key not in self.rates:
            raise DataError(f"snapshot FX coverage missing {key}")
        prices, meta = self.rates[key]
        result = prices.Close.copy()
        result.attrs["session_metadata"] = data.session_metadata(meta)
        result.attrs["source_timestamps"] = prices.attrs.get("source_timestamps", [])
        return result

    def profile(self, ticker: str) -> dict:
        profile = self.payload.get("profiles", {}).get(ticker.upper())
        if not isinstance(profile, dict) or not isinstance(profile.get("data"), dict):
            raise DataError(f"snapshot missing required ETF profile for {ticker!r}")
        return profile["data"].copy()


def load(snapshot_id: str) -> Dataset:
    raw = audit.read(snapshot_id)
    if not isinstance(raw, dict) or raw.get("kind") != "market_snapshot":
        raise DataError("snapshot kind must be market_snapshot; portfolio audit records are separate")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1 or not isinstance(raw.get("histories"), dict) or not isinstance(raw.get("fx"), dict):
        raise DataError("unsupported snapshot schema")
    try:
        parameters = SnapshotParameters.model_validate(raw["parameters"], strict=True)
        if parameters.model_dump() != raw["parameters"]:
            raise ValueError("noncanonical acquisition parameters")
        histories = {ticker: _restore(value) for ticker, value in raw["histories"].items()}
        rates = {pair: _restore(value) for pair, value in raw["fx"].items()}
        expected = set(parameters.tickers) | ({parameters.benchmark} if parameters.benchmark else set())
        if set(histories) != expected:
            raise ValueError("history coverage differs from declared tickers/benchmark")
        for ticker, (_, meta) in histories.items():
            if meta.ticker.upper() != ticker or meta.include_incomplete_session != parameters.include_incomplete_session:
                raise ValueError("history identity/session policy differs from acquisition")
        expected_fx = {f"{source}/{target}" for source, target in _pairs(histories, parameters.tickers, parameters.benchmark, parameters.base_currency)}
        if set(rates) != expected_fx:
            raise ValueError("FX coverage differs from declared acquisition")
        for pair, (_, meta) in rates.items():
            if meta.ticker != pair.replace("/", "") + "=X" or meta.include_incomplete_session != parameters.include_incomplete_session:
                raise ValueError("FX identity/session policy differs from acquisition")
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("profiles must be a mapping")
        for ticker, profile in profiles.items():
            if (ticker not in histories or not isinstance(profile, dict) or not isinstance(profile.get("data"), dict)
                    or set(profile["data"]) != set(PROFILE_FIELDS) or profile["data"]["quoteType"] != "ETF"
                    or data._aware(profile.get("retrieved_at")) is None):
                raise ValueError("invalid ETF profile/provenance")
            _profile_data(profile["data"])
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise DataError(f"invalid snapshot schema: {error}") from error
    return Dataset(raw, histories, rates)


def _pairs(histories: dict[str, tuple[pd.DataFrame, data.Meta]], tickers: list[str], benchmark: str | None,
           base_currency: str | None) -> set[tuple[str, str]]:
    pairs = set()
    if benchmark:
        bench_currency = histories[benchmark.upper()][1].currency
        if len(bench_currency) != 3 or not bench_currency.isupper():
            raise DataError(f"snapshot unknown quote currency for {benchmark!r}")
    for ticker in tickers:
        currency = histories[ticker.upper()][1].currency
        if len(currency) != 3 or not currency.isupper():
            raise DataError(f"snapshot unknown quote currency for {ticker!r}")
        if base_currency and currency != base_currency:
            pairs.add((currency, base_currency))
        if benchmark:
            bench_currency = histories[benchmark.upper()][1].currency
            if bench_currency != currency:
                pairs.add((bench_currency, currency))
    return pairs


def request(snapshot_id: str | None, period: str | None, default_period: str, include: bool | None,
            tickers: list[str], benchmark: str | None = None, base_currency: str | None = None,
            etf_profile: bool = False) -> tuple[Dataset | None, str, bool]:
    if snapshot_id is None:
        selected_period = period or default_period
        validate_parameters(HistoryParameters, period=selected_period)
        return None, selected_period, bool(include)
    dataset = load(snapshot_id)
    parameters = dataset.payload["parameters"]
    if period is not None and period != parameters["period"]:
        raise DataError(f"snapshot period conflict: captured {parameters['period']!r}, requested {period!r}")
    policy = parameters["include_incomplete_session"]
    if include is not None and include != policy:
        raise DataError("snapshot session policy conflict; acquire a new snapshot for a different policy")
    for ticker in [*tickers, *([benchmark] if benchmark else [])]:
        dataset.history(ticker)
    for source, target in _pairs(dataset.histories, tickers, benchmark, base_currency):
        dataset.fx(source, target)
    if etf_profile:
        dataset.profile(tickers[0])
    return dataset, parameters["period"], policy


def history(dataset: Dataset | None, ticker: str, period: str, include: bool) -> tuple[pd.DataFrame, data.Meta]:
    return dataset.history(ticker) if dataset is not None else data.history(ticker, period, include)


def fx_series(dataset: Dataset | None, source: str, target: str, period: str, include: bool) -> pd.Series:
    return dataset.fx(source, target) if dataset is not None else data.fx_series(source, target, period, include)


def acquire(tickers: list[str], period: str = "3y", benchmark: str | None = None, base_currency: str | None = None,
            include_incomplete_session: bool = False) -> dict:
    parameters = validate_parameters(SnapshotParameters, tickers=tickers, period=period, benchmark=benchmark,
                                    base_currency=base_currency, include_incomplete_session=include_incomplete_session)
    names = list(dict.fromkeys([*parameters.tickers, *([parameters.benchmark] if parameters.benchmark else [])]))
    histories = {ticker: data.history(ticker, period, include_incomplete_session) for ticker in names}
    rates = {f"{source}/{target}": data.history(f"{source}{target}=X", period, include_incomplete_session)
             for source, target in sorted(_pairs(histories, parameters.tickers, parameters.benchmark, base_currency))}
    profiles = {}
    for ticker, (prices, _) in histories.items():
        if prices.attrs.get("provider_metadata", {}).get("instrumentType") == "ETF":
            profile = data.info(ticker)
            profiles[ticker] = {"data": _profile_data({key: profile.get(key) for key in PROFILE_FIELDS}),
                                "retrieved_at": datetime.now(UTC).isoformat()}
    payload = {"kind": "market_snapshot", "schema_version": 1, "parameters": parameters.model_dump(),
               "histories": {ticker: _serialize(*value) for ticker, value in histories.items()},
               "fx": {pair: _serialize(*value) for pair, value in rates.items()}, "profiles": profiles,
               "retrieved_at": max(meta.retrieved_at for _, meta in [*histories.values(), *rates.values()])}
    for value in [*payload["histories"].values(), *payload["fx"].values()]:
        _restore(value)
    try:
        snapshot_id = audit.put(payload)
    except (OSError, ValueError) as error:
        raise DataError("market snapshot could not be recorded") from error
    return {"snapshot_id": snapshot_id, "schema_version": 1, "parameters": parameters.model_dump(),
            "tickers": names, "fx_pairs": sorted(rates), "retrieved_at": payload["retrieved_at"],
            "sources": {ticker: asdict(meta) for ticker, (_, meta) in histories.items()},
            "fx_sources": {pair: asdict(meta) for pair, (_, meta) in rates.items()},
            "profiles_available": sorted(profiles),
            "profile_retrievals": {ticker: profile["retrieved_at"] for ticker, profile in profiles.items()}}
