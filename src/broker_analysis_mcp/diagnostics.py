"""Field and domain evidence for unavailable financial metrics; no replacement values."""

import math

from .fundamentals import Year, avg


def unavailable(code: str, reason: str, missing=(), invalid=()) -> dict:
    return {"reason_code": code, "reason": reason, "missing_inputs": list(missing), "invalid_inputs": list(invalid)}


RATIO_INPUTS = {
    "pe": "diluted_eps", "ev_ebitda": "total_debt cash ebitda", "ev_ebit": "total_debt cash ebit",
    "pb": "equity", "ps": "revenue", "fcf_yield": "free_cash_flow", "enterprise_value": "total_debt cash",
    "roe": "net_income equity", "roa": "net_income total_assets", "ebit_roa": "ebit total_assets",
    "roic": "ebit tax_rate invested_capital", "gross_margin": "gross_profit revenue",
    "ebitda_margin": "ebitda revenue", "operating_margin": "ebit revenue", "pretax_margin": "pretax_income revenue",
    "net_margin": "net_income revenue", "asset_turnover": "revenue total_assets", "equity_multiplier": "total_assets equity",
    "receivables_turnover": "revenue receivables", "days_sales_outstanding": "revenue receivables",
    "inventory_turnover": "cost_of_revenue inventory", "days_inventory": "cost_of_revenue inventory",
    "days_payables": "payables cost_of_revenue", "cash_conversion_cycle": "revenue receivables cost_of_revenue inventory payables",
    "working_capital": "current_assets current_liabilities", "current_ratio": "current_assets current_liabilities",
    "quick_ratio": "cash receivables current_liabilities", "cash_ratio": "cash current_liabilities",
    "operating_cash_flow_to_current_liabilities": "operating_cash_flow current_liabilities",
    "net_debt": "total_debt cash", "net_debt_ebitda": "total_debt cash ebitda", "debt_to_equity": "total_debt equity",
    "long_term_debt_to_equity": "long_term_debt equity", "liabilities_to_assets": "total_liabilities total_assets",
    "liabilities_to_equity": "total_liabilities equity", "equity_to_assets": "equity total_assets",
    "interest_coverage": "ebit interest_expense", "cash_coverage": "ebit depreciation_amortization interest_expense",
    "payout": "dividends_paid net_income",
}
DENOMINATORS = {
    "pe": "diluted_eps", "ev_ebitda": "ebitda", "ev_ebit": "ebit", "pb": "equity", "ps": "revenue",
    "roe": "average_equity", "roa": "average_total_assets", "ebit_roa": "average_total_assets", "roic": "invested_capital",
    "gross_margin": "revenue", "ebitda_margin": "revenue", "operating_margin": "revenue", "pretax_margin": "revenue",
    "net_margin": "revenue", "asset_turnover": "average_total_assets", "equity_multiplier": "average_equity",
    "receivables_turnover": "average_receivables", "days_sales_outstanding": "receivables_turnover",
    "inventory_turnover": "average_inventory", "days_inventory": "inventory_turnover", "days_payables": "cost_of_revenue",
    "current_ratio": "current_liabilities", "quick_ratio": "current_liabilities", "cash_ratio": "average_current_liabilities",
    "operating_cash_flow_to_current_liabilities": "average_current_liabilities", "net_debt_ebitda": "ebitda",
    "debt_to_equity": "equity", "long_term_debt_to_equity": "equity", "liabilities_to_assets": "total_assets",
    "liabilities_to_equity": "equity", "equity_to_assets": "total_assets", "interest_coverage": "interest_expense",
    "cash_coverage": "interest_expense", "payout": "net_income",
}


def fundamental_nulls(years: list[Year], values: dict) -> dict:
    current = years[0] if years else Year()
    previous = years[1] if len(years) > 1 else Year()
    inputs = vars(current) | {f"average_{key}": avg(getattr(current, key), getattr(previous, key))
                              for key in ("equity", "total_assets", "receivables", "inventory", "current_liabilities")}
    inputs |= values
    out = {}
    for name, required in RATIO_INPUTS.items():
        if values.get(name) is not None and math.isfinite(values[name]):
            continue
        missing = [key for key in required.split() if getattr(current, key) is None]
        nonfinite = [key for key in required.split() if getattr(current, key) is not None and not math.isfinite(getattr(current, key))]
        if missing:
            diagnostic = unavailable("missing_input", f"{name} needs statement fields: {', '.join(missing)}", missing)
        elif nonfinite:
            diagnostic = unavailable("undefined", f"{name} has non-finite statement inputs: {', '.join(nonfinite)}", invalid=nonfinite)
        else:
            denominator = DENOMINATORS.get(name)
            value = inputs.get(denominator)
            if denominator and value is not None and value <= 0:
                diagnostic = unavailable("undefined" if value == 0 else "not_applicable",
                                         f"{name} requires positive {denominator}; denominator is {'zero' if value == 0 else 'negative'}",
                                         invalid=[denominator])
            elif name == "cash_conversion_cycle":
                bad = [key for key in ("days_sales_outstanding", "days_inventory", "days_payables") if values[key] is None]
                diagnostic = unavailable("not_applicable", f"cash_conversion_cycle requires defined components: {', '.join(bad)}", invalid=bad)
            else:
                diagnostic = unavailable("undefined", f"{name} calculation is not finite for its reported inputs", invalid=required.split())
        out[name] = diagnostic
    if values.get("revenue_cagr") is None:
        revenue = [year.revenue for year in years if year.revenue is not None]
        out["revenue_cagr"] = (unavailable("insufficient_history", "revenue_cagr needs at least two reported fiscal years", ["revenue_history"])
                               if len(revenue) < 2 else unavailable("not_applicable", "revenue_cagr requires positive endpoint revenues", invalid=["revenue"]))
    return out


def risk_nulls(metrics: dict, *, returns: int, weeks: int | None = None, up_months=None, down_months=None) -> None:
    reasons = {
        "sharpe": "return standard deviation is zero or non-finite",
        "sharpe_standard_error": "Sharpe ratio is undefined because return standard deviation is zero or non-finite",
        "sharpe_t_stat": "Sharpe ratio or its standard error is undefined",
        "probabilistic_sharpe_vs_0": "return dispersion or the skewness/kurtosis-adjusted Sharpe variance is not positive",
        "sortino": "downside deviation is zero: no returns below the stated threshold",
        "omega": "loss sum below the stated threshold is zero",
        "calmar": "maximum drawdown is zero",
        "beta": "benchmark return variance is zero or non-finite",
        "correlation": "asset or benchmark return standard deviation is zero or non-finite",
        "beta_weekly_2y": "weekly benchmark return variance is zero or non-finite",
        "beta_adjusted": "weekly benchmark beta is undefined",
        "jensen_alpha": "benchmark beta is undefined",
        "treynor": "Treynor is not interpreted for non-positive or undefined beta",
        "m_squared": "asset Sharpe ratio is undefined",
        "m_squared_vs_benchmark": "asset or benchmark Sharpe ratio is undefined",
        "information_ratio": "active weekly return standard deviation is zero or non-finite",
        "tracking_error": "fewer than two aligned weekly returns",
        "up_capture": "up-market benchmark geometric return is zero or non-finite",
        "down_capture": "down-market benchmark geometric return is zero or non-finite",
        "var_cornish_fisher_1d": "return skewness or kurtosis is undefined for these observations",
    }
    for name, metric in metrics.items():
        if metric["value"] is not None:
            continue
        code, reason = "undefined", reasons.get(name, f"{name} calculation is non-finite for the observed returns")
        if name == "treynor":
            code = "not_applicable"
        if (name in {"information_ratio", "tracking_error", "beta_weekly_2y", "beta_adjusted"} and weeks is not None and weeks < 2):
            code, reason = "insufficient_history", f"{name} needs at least two aligned weekly returns; available {weeks}"
        if (name == "up_capture" and up_months == 0) or (name == "down_capture" and down_months == 0):
            code, reason = "insufficient_history", f"{name} has no matching benchmark months"
        metric.update(unavailable(code, reason))
        metric["return_observations"] = returns
