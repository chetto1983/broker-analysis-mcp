"""Fixed-exposure, base-currency portfolio risk and auditable model reports."""

from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from . import audit, data, forecasting
from .models import Position, StressScenario


def _series(series: pd.Series, label: str) -> pd.Series:
    if series.empty or series.index.has_duplicates or not series.index.is_monotonic_increasing:
        raise data.DataError(f"{label}: empty, duplicate or unordered dates")
    if not np.isfinite(series.to_numpy()).all() or (series <= 0).any():
        raise data.DataError(f"{label}: prices/rates must be finite and positive")
    return series.astype(float)


def _snapshot(series: pd.Series) -> dict:
    return {"dates": [str(d.date()) for d in series.index], "values": series.tolist()}


def tail_risk(scenarios: np.ndarray, exposures: np.ndarray, confidence: float) -> dict:
    """Empirical ES with fractional boundary mass; contributions sum exactly to ES.

    Marginals hold the simulated scenarios fixed. They do not refit the volatility model
    under a changed portfolio, and are reported as loss per unit of base-currency exposure.
    """
    losses_by_asset = -scenarios * exposures
    losses = losses_by_asset.sum(axis=1)
    order = np.argsort(-losses, kind="stable")
    mass = len(losses) * (1 - confidence)
    weights = np.clip(mass - np.arange(len(losses)), 0, 1) / mass
    tail_weights = np.zeros(len(losses))
    tail_weights[order] = weights
    # Share a boundary atom equally among tied scenarios, avoiding arbitrary attribution.
    boundary = losses[order[min(int(np.ceil(mass)) - 1, len(losses) - 1)]]
    ties = losses == boundary
    tail_weights[ties] = tail_weights[ties].sum() / ties.sum()
    marginal = -tail_weights @ scenarios
    components = marginal * exposures
    return {"var": float(np.quantile(losses, confidence, method="inverted_cdf")),
            "expected_shortfall": float(components.sum()),
            "marginal_es": marginal.tolist(), "component_es": components.tolist(),
            "tail_mass": mass, "scenarios": len(losses)}


def _metric(value: float, unit: str, ref: str) -> dict:
    return {"value": float(value), "unit": unit, "ref": ref}


def report(
    positions: list[Position], portfolio_value: float, base_currency: str = "EUR", period: str = "5y",
    confidence: float = 0.975, scenarios: list[StressScenario] | None = None,
) -> dict:
    if not 1 <= len(positions) <= 30:
        raise data.DataError("pass 1 to 30 nonzero positions")
    if not np.isfinite(portfolio_value) or portfolio_value <= 0:
        raise data.DataError("portfolio_value must be a finite positive NAV in base currency")
    if not np.isfinite(confidence) or not 0.90 <= confidence <= 0.99:
        raise data.DataError("confidence must be between .90 and .99")
    if len(base_currency) != 3 or not base_currency.isascii() or not base_currency.isalpha():
        raise data.DataError("base_currency must be a three-letter currency code")
    base_currency = base_currency.upper()
    if period not in {"1y", "2y", "3y", "5y", "10y", "max"}:
        raise data.DataError("unsupported period")
    tickers = [p.ticker for p in positions]
    if len(set(tickers)) != len(tickers) or any(p.market_value == 0 for p in positions):
        raise data.DataError("aggregate duplicate tickers and omit zero positions")
    scenarios = scenarios or []
    if len(scenarios) > 20 or len({s.name for s in scenarios}) != len(scenarios):
        raise data.DataError("use at most 20 uniquely named scenarios")
    local, metadata, fx = {}, {}, {}
    warnings = ["Current fixed exposures, zero cash return, no transaction costs or nonlinear derivative repricing.",
                "FHS uses one common portfolio volatility scale; it does not forecast changing cross-asset correlations.",
                "Marginal ES freezes scenarios and volatility scales; negative contributions represent hedges."]
    for position in positions:
        prices, meta = data.history(position.ticker, period)
        if len(meta.currency) != 3 or not meta.currency.isupper():
            raise data.DataError(f"unknown quote currency for {position.ticker}")
        local[position.ticker] = _series(prices.Close, position.ticker)
        metadata[position.ticker] = asdict(meta)
        warnings.extend(meta.warnings)
        if meta.currency != base_currency and meta.currency not in fx:
            fx[meta.currency] = _series(data.fx_series(meta.currency, base_currency, period), meta.currency)
    # Intersect prices and FX BEFORE taking returns. No forward filling stale FX or prices.
    frame = pd.concat({**{f"asset:{k}": v for k, v in local.items()},
                       **{f"fx:{k}": v for k, v in fx.items()}}, axis=1).dropna()
    cutoff = pd.Timestamp(datetime.now(UTC).date())
    if (frame.index >= cutoff).any():
        warnings.append("Current UTC-date and future bars excluded to avoid using a possibly incomplete session.")
        frame = frame.loc[frame.index < cutoff]
    if len(frame) < 161:
        raise data.DataError("at least 161 common complete price/FX observations are required")
    dropped_dates = len(set().union(*(set(s.index) for s in [*local.values(), *fx.values()]))) - len(frame)
    if dropped_dates:
        warnings.append(f"Alignment excluded {dropped_dates} dates without complete asset/FX observations.")
    max_gap = int(frame.index.to_series().diff().dt.days.max())
    if max_gap > 7:
        warnings.append(f"Common observations include gaps of up to {max_gap} calendar days; session risks are not uniform daily risks.")
    dates = [str(d.date()) for d in frame.index]
    base_prices, exposures, holdings = {}, [], []
    for position in positions:
        currency = metadata[position.ticker]["currency"]
        rates = frame[f"fx:{currency}"] if currency != base_currency else pd.Series(1.0, index=frame.index)
        base_prices[position.ticker] = frame[f"asset:{position.ticker}"] * rates
        value = position.market_value * float(rates.iloc[-1])
        exposures.append(value)
        holdings.append({"ticker": position.ticker, "quote_currency": currency,
                         "market_value_quote": position.market_value, "fx_to_base": float(rates.iloc[-1]),
                         "market_value_base": value, "weight_nav": value / portfolio_value})
    exposure = np.array(exposures)
    gross_exposure = float(np.abs(exposure).sum())
    if not np.isfinite(gross_exposure) or gross_exposure <= 0:
        raise data.DataError("converted gross exposure must be finite and positive")
    returns = pd.DataFrame(base_prices).pct_change(fill_method=None).iloc[1:]
    matrix = returns.to_numpy()
    portfolio_returns = matrix @ (exposure / portfolio_value)
    # Cash must not change monetary risk through the variance floor or model selection.
    model_returns = matrix @ (exposure / gross_exposure)
    if not np.isfinite(matrix).all() or not np.isfinite(portfolio_returns).all():
        raise data.DataError("portfolio return calculation overflowed")
    predicted, status, reports = forecasting.evaluate(model_returns, dates[1:], confidence)
    model = status["champion"]
    n = len(matrix)
    first = max(forecasting.BURN, n - 500)
    scale = np.sqrt(predicted[model][-1] / predicted[model][first:n])
    filtered = matrix[first:] * scale[:, None]
    fhs = tail_risk(filtered, exposure, confidence)
    historical = tail_risk(matrix[first:], exposure, confidence)
    for i, holding in enumerate(holdings):
        holding["component_es"] = _metric(fhs["component_es"][i], base_currency, "fhs_es")
        holding["marginal_es"] = _metric(fhs["marginal_es"][i], f"{base_currency}/{base_currency} exposure", "fhs_es")
    stress = []
    currencies = {m["currency"] for m in metadata.values()}
    for scenario in scenarios:
        if set(scenario.asset_shocks) - set(tickers) or set(scenario.fx_shocks) - (currencies - {base_currency}):
            raise data.DataError("scenario contains unknown ticker/currency or a shock to the base currency")
        changes = np.array([(1 + scenario.asset_shocks.get(p.ticker, 0)) *
                            (1 + scenario.fx_shocks.get(metadata[p.ticker]["currency"], 0)) - 1 for p in positions])
        pnl = exposure * changes
        stress.append({"name": scenario.name, "type": "hypothetical", "pnl": _metric(pnl.sum(), base_currency, "stress"),
                       "component_pnl": dict(zip(tickers, pnl.tolist(), strict=True))})
    for name, start, end in [("COVID selloff", "2020-02-19", "2020-03-23"),
                             ("2022 rates shock", "2021-12-31", "2022-10-12")]:
        # Never silently truncate a requested historical stress window.
        before = frame.index[frame.index <= pd.Timestamp(start)]
        after = frame.index[frame.index <= pd.Timestamp(end)]
        if not len(before) or not len(after) or frame.index[-1] < pd.Timestamp(end):
            stress.append({"name": name, "type": "historical", "available": False, "reason": "window unavailable"})
            continue
        left, right = before[-1], after[-1]
        if (pd.Timestamp(start) - left).days > 7 or (pd.Timestamp(end) - right).days > 7:
            stress.append({"name": name, "type": "historical", "available": False, "reason": "boundary observations missing"})
            continue
        changes = np.array([base_prices[k].loc[right] / base_prices[k].loc[left] - 1 for k in tickers])
        pnl = exposure * changes
        stress.append({"name": name, "type": "historical", "available": True,
                       "start": str(left.date()), "end": str(right.date()),
                       "pnl": _metric(pnl.sum(), base_currency, "stress"),
                       "component_pnl": dict(zip(tickers, pnl.tolist(), strict=True))})
    if len({m["timezone"] for m in metadata.values()}) > 1 or fx:
        warnings.append("Common dates do not synchronise exchange/FX closing times; daily correlations may be distorted.")
    if frame.index[-1] < max(s.index[-1] for s in local.values()):
        warnings.append("The common valuation date precedes the latest asset observation.")
    age = (datetime.now(UTC).date() - frame.index[-1].date()).days
    if age > 7:
        warnings.append(f"Latest common observation is {age} calendar days old.")
    if fhs["tail_mass"] < 20:
        warnings.append("Fewer than 20 effective tail observations; ES and its attribution are statistically fragile.")
    if status["fallback"]:
        warnings.append(f"EWMA fallback: {status['reason']}; inspect model_status and monitoring report.")
    if np.var(model_returns) < forecasting.FLOOR:
        warnings.append("Portfolio history is effectively constant; forecast variance uses a numerical floor.")
    correlation = returns.corr()
    output = {
        "currency": base_currency, "portfolio_value": portfolio_value,
        "residual_cash_base": portfolio_value - float(exposure.sum()),
        "gross_exposure_base": gross_exposure, "positions": holdings,
        "window": {"start": dates[0], "end": dates[-1], "returns": n},
        "parameters": {"confidence": confidence, "period": period, "horizon": "next common observed session",
                       "fhs_lookback": n - first, "valuation_date": dates[-1]},
        "metrics": {"var_fhs": _metric(fhs["var"], base_currency, "fhs_es"),
                    "expected_shortfall_fhs": _metric(fhs["expected_shortfall"], base_currency, "fhs_es"),
                    "var_historical": _metric(historical["var"], base_currency, "historical"),
                    "expected_shortfall_historical": _metric(historical["expected_shortfall"], base_currency, "historical"),
                    "forecast_volatility_session": _metric(np.sqrt(predicted[model][-1]) * (gross_exposure / portfolio_value) * 100, "% NAV", "variance")},
        "correlation": {a: {b: float(correlation.loc[a, b]) if pd.notna(correlation.loc[a, b]) else None
                            for b in tickers} for a in tickers},
        "stress_scenarios": stress, "model_status": status, "warnings": warnings,
        "sources": metadata,
        "references": {"fhs_es": "Joint historical return vectors scaled by current/past portfolio sigma; empirical upper-tail loss mean with fractional boundary mass.",
                       "historical": "Unscaled empirical losses on the same FHS sample; inverse empirical CDF VaR.",
                       "variance": "Causal zero-mean second moment; see development report.",
                       "stress": "Sum of current base exposures * ((1+local shock)*(1+FX shock)-1). Historical stress uses compounded adjusted-price/FX changes."},
    }
    snapshot = {
        "schema_version": 1, "created_at": datetime.now(UTC).isoformat(),
        "implementation_version": forecasting.VERSION,
        "source_hashes": {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ("portfolio.py", "forecasting.py", "calibration.py", "audit.py", "data.py", "models.py")},
        "runtime_versions": {name: version(name) for name in ("broker-analysis-mcp", "numpy", "pandas", "scipy", "yfinance")},
        "request": {"positions": [p.model_dump() for p in positions], "portfolio_value": portfolio_value,
                    "base_currency": base_currency, "period": period, "confidence": confidence,
                    "scenarios": [s.model_dump() for s in scenarios]},
        "prices": {k: _snapshot(v) for k, v in local.items()}, "fx": {k: _snapshot(v) for k, v in fx.items()},
        "fx_source": data.SOURCE,
        "metadata": metadata, "common_dates": dates,
        "calendar_policy": "Intersection of observed asset and FX dates before returns; exclude current UTC-date/future bars; no fills; no annualisation.",
        "price_policy": "Split/dividend-adjusted historical closes; current market values supplied by analyst.",
        "portfolio_returns": portfolio_returns.tolist(),
        "model_returns": model_returns.tolist(),
        "forecast_return_basis": "gross_exposure",
        "forecast_return_denominator_base": gross_exposure,
        "forecast_variances": {k: v[forecasting.BURN:].tolist() for k, v in predicted.items()},
        "forecast_start_return_index": forecasting.BURN,
    }
    output["audit"] = audit.save(snapshot, output, reports)
    return output
