"""Diagnostic statistics, not model approval or an additional selection policy.

Christoffersen (1998), Evaluating Interval Forecasts, doi:10.2307/2527341.
https://www.mathworks.com/help/risk/varbacktest.cci.html
https://www.mathworks.com/help/risk/overview-of-expected-shortfall-backtesting.html
"""

from itertools import pairwise

import numpy as np
from scipy.special import xlogy
from scipy.stats import chi2


def empirical_var_es(losses: np.ndarray, confidence: float) -> tuple[float, float]:
    """Inverse empirical CDF VaR and integrated upper quantiles, including boundary mass."""
    ordered = np.sort(np.asarray(losses, dtype=float))[::-1]
    if not len(ordered) or not np.isfinite(ordered).all() or not 0 < confidence < 1:
        raise ValueError("finite nonempty losses and confidence between zero and one required")
    mass = len(ordered) * (1 - confidence)
    weights = np.clip(mass - np.arange(len(ordered)), 0, 1)
    return float(np.quantile(ordered, confidence, method="inverted_cdf")), float(ordered @ weights / mass)


def independence(exceptions: list[bool]) -> dict:
    """Conditional likelihood of adjacent hits; initial state is conditioned upon."""
    hits = np.asarray(exceptions)
    if hits.ndim != 1 or not np.isin(hits, [0, 1]).all():
        raise ValueError("exceptions must be a one-dimensional binary sequence")
    counts = np.zeros((2, 2), dtype=int)
    for before, after in pairwise(hits):
        counts[int(before), int(after)] += 1
    rows = counts.sum(axis=1)
    n = int(counts.sum())
    result = {
        "method": "Christoffersen first-order independence LR",
        "observations": len(hits), "transitions": n,
        "counts": {f"n{i}{j}": int(counts[i, j]) for i in range(2) for j in range(2)},
        "p_exception_after_no_exception": float(counts[0, 1] / rows[0]) if rows[0] else None,
        "p_exception_after_exception": float(counts[1, 1] / rows[1]) if rows[1] else None,
        "lr_statistic": None, "asymptotic_p_value": None, "degrees_of_freedom": 1,
        "state": "insufficient_data", "minimum_expected_transition_count": None,
        "used_for_model_selection": False,
        "limitations": ["Tests first-order dependence only; non-rejection does not establish independence.",
                        "Chi-square reference is asymptotic; sparse transitions can make its p-value unreliable."],
    }
    if n < 2 or np.any(rows == 0) or np.any(counts.sum(axis=0) == 0):
        result["limitations"].append("Both prior states and outcome states must be observed; no inferential result is reported.")
        return result
    pooled = counts.sum(axis=0) / n
    expected = rows[:, None] * pooled
    log_null = float(xlogy(counts, np.broadcast_to(pooled, (2, 2))).sum())
    log_markov = float(xlogy(counts, counts / rows[:, None]).sum())
    lr = max(0.0, 2 * (log_markov - log_null))
    sparse = bool(np.min(expected) < 5 or np.any(counts == 0))
    result.update(lr_statistic=lr, asymptotic_p_value=float(chi2.sf(lr, 1)),
                  state="sparse_data" if sparse else "asymptotic_diagnostic",
                  minimum_expected_transition_count=float(np.min(expected)))
    if sparse:
        result["limitations"].append("An observed zero or expected count below five flags sparsity; this is a reporting heuristic, not a calibrated sample-size rule.")
    return result


def es_severity(losses: np.ndarray, var: np.ndarray, es: np.ndarray, confidence: float) -> dict:
    """Descriptive tail severity and absolute ES residual, without a reference distribution."""
    losses, var, es = (np.asarray(v, dtype=float) for v in (losses, var, es))
    if (losses.ndim != 1 or losses.shape != var.shape or losses.shape != es.shape
            or not all(np.isfinite(v).all() for v in (losses, var, es)) or not 0 < confidence < 1):
        raise ValueError("matching finite one-dimensional arrays and valid confidence required")
    hit = losses > var
    n_hits = int(hit.sum())
    # Loss-positive sign convention: positive residual means realized tail loss exceeds forecast.
    residual = var + np.maximum(losses - var, 0) / (1 - confidence) - es
    return {
        "state": "descriptive_only" if len(losses) else "insufficient_data",
        "calibration_conclusion": "not_tested", "p_value": None, "used_for_model_selection": False,
        "observations": len(losses), "var_exceptions": n_hits,
        "expected_exceptions": float(len(losses) * (1 - confidence)),
        "mean_loss_on_var_exception": float(losses[hit].mean()) if n_hits else None,
        "mean_forecast_es_on_var_exception": float(es[hit].mean()) if n_hits else None,
        "mean_loss_minus_es_on_var_exception": float((losses[hit] - es[hit]).mean()) if n_hits else None,
        "mean_es_residual": float(residual.mean()) if len(losses) else None,
        "es_residual_formula": "VaR + max(realized_loss - VaR, 0)/(1-confidence) - ES",
        "limitations": ["Descriptive severity is not an ES calibration test or a pass.",
                        "No p-value: no validated null distribution, simulated critical values, or dependence correction.",
                        "The absolute residual reverses the sign of the Acerbi-Szekely minimally biased statistic; positive suggests underestimation, without statistical significance.",
                        "ES residuals also depend on VaR accuracy; sparse exceptions and empirical atoms limit interpretation.",
                        "Zero exceptions do not demonstrate adequate ES calibration."],
    }
