"""Causal variance forecasts and a fixed validation/monitoring policy.

Rolling mean-square is a deliberately simple challenger. No research-paper performance is
assumed to transfer to this implementation. All forecasts at t use returns strictly before t.
"""

import numpy as np
from scipy.stats import binomtest

from broker_analysis_mcp import calibration as diagnostics

VERSION = "portfolio-risk-3"
BURN = 60
VALIDATION = 252
MONITORING = 63
FLOOR = 1e-12


def forecasts(returns: np.ndarray) -> dict[str, np.ndarray]:
    n = len(returns)
    ewma = np.full(n + 1, np.nan)
    rolling = np.full(n + 1, np.nan)
    ewma[BURN] = max(float(np.mean(returns[:BURN] ** 2)), FLOOR)
    for t in range(BURN, n + 1):
        rolling[t] = max(float(np.mean(returns[t - BURN:t] ** 2)), FLOOR)
        if t < n:
            ewma[t + 1] = max(0.94 * ewma[t] + 0.06 * returns[t] ** 2, FLOOR)
    return {"ewma_094": ewma, "rolling_60": rolling}


def qlike(realized: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    # Constants independent of the forecast are omitted. This remains finite at r=0.
    return np.log(predicted) + realized / predicted


def comparison(returns: np.ndarray, predictions: dict, start: int, end: int) -> dict:
    loss = {name: qlike(returns[start:end] ** 2, values[start:end]) for name, values in predictions.items()}
    delta = loss["ewma_094"] - loss["rolling_60"]
    centered = delta - delta.mean()
    n = len(delta)
    variance = float(centered @ centered / n)
    for lag in range(1, min(5, n - 1) + 1):
        variance += 2 * (1 - lag / 6) * float(centered[lag:] @ centered[:-lag] / n)
    se = np.sqrt(max(variance, 0) / n)
    return {"observations": n, "mean_qlike": {k: float(v.mean()) for k, v in loss.items()},
            "challenger_loss_improvement": float(delta.mean()),
            "hac_z": float(delta.mean() / se) if se > 0 else None}


def select(validation: dict | None, monitoring: dict | None) -> dict:
    champion = "ewma_094"
    reason = "insufficient_validation_history"
    if validation is not None:
        reason = "challenger_not_supported"
        if validation["challenger_loss_improvement"] > 0 and (validation["hac_z"] or 0) > 1.96:
            champion, reason = "rolling_60", "challenger_passed_validation"
    validation_champion = champion
    if champion != "ewma_094" and monitoring is not None and monitoring["challenger_loss_improvement"] <= 0:
        champion, reason = "ewma_094", "monitoring_deterioration"
    return {"champion": champion, "validation_champion": validation_champion, "reason": reason,
            "fallback": champion == "ewma_094", "fallback_model": "ewma_094"}


def evaluate(returns: np.ndarray, dates: list[str], confidence: float) -> tuple[dict, dict, dict]:
    predicted = forecasts(returns)
    n = len(returns)
    split = n - MONITORING
    validation = monitoring = None
    if split - VALIDATION >= BURN:
        validation = comparison(returns, predicted, split - VALIDATION, split)
        validation["window"] = {"start": dates[split - VALIDATION], "end": dates[split - 1]}
        monitoring = comparison(returns, predicted, split, n)
        monitoring["window"] = {"start": dates[split], "end": dates[-1]}
    status = select(validation, monitoring)
    # Backtest the champion selected BEFORE monitoring, with origin-specific residual histories.
    chosen = status["validation_champion"]
    observations = []
    for t in range(max(BURN + 100, split), n):
        past = returns[max(BURN, t - 500):t]
        scales = predicted[chosen][max(BURN, t - 500):t]
        losses = -past / np.sqrt(scales) * np.sqrt(predicted[chosen][t])
        threshold, expected_shortfall = diagnostics.empirical_var_es(losses, confidence)
        observations.append({
            "forecast_origin": dates[t - 1], "realized_date": dates[t], "return_index": t,
            "history_start": dates[max(BURN, t - 500)], "history_end": dates[t - 1],
            "history_observations": len(past), "realized_loss": float(-returns[t]),
            "var": threshold, "expected_shortfall": expected_shortfall,
            "var_exception": bool(-returns[t] > threshold),
        })
    count = len(observations)
    hits = [o["var_exception"] for o in observations]
    breaches = sum(hits)
    calibration = {
        "model": chosen, "observations": count, "var_exceptions": breaches,
        "loss_unit": "fraction of gross base-currency exposure",
        "observed_coverage": 1 - breaches / count if count else None,
        "target_coverage": confidence,
        "binomial_p_value": float(binomtest(breaches, count, 1 - confidence).pvalue) if count else None,
        "independence": diagnostics.independence(hits),
        "es_severity": diagnostics.es_severity(
            np.array([o["realized_loss"] for o in observations]), np.array([o["var"] for o in observations]),
            np.array([o["expected_shortfall"] for o in observations]), confidence),
        "note": "Existing coverage alarm uses an independence-assuming binomial test. Independence and ES severity diagnostics do not change model selection; ES calibration is not established.",
    }
    if count and calibration["binomial_p_value"] < 0.01:
        status.update(champion="ewma_094", fallback=True, reason="coverage_alarm")
    status.update(model_version=VERSION, as_of=dates[-1], last_validation=validation["window"]["end"] if validation else None,
                  baselines=["ewma_094", "rolling_60"], calibration=calibration,
                  validation=validation, monitoring=monitoring)
    reports = {
        "development": {
            "model_version": VERSION, "purpose": "Next common-session variance of fixed current portfolio exposures",
            "models": {"ewma_094": "v[t+1] = .94*v[t] + .06*r[t]^2; first 60 returns seed v[60]",
                       "rolling_60": "v[t] = mean(r[t-60:t]^2)"},
            "variance_floor": FLOOR, "conditional_mean": 0,
            "return_basis": "Fixed-exposure P&L divided by gross base-currency exposure; output volatility converted to percent NAV.",
            "selection_policy": "252 validation observations: positive QLIKE improvement and HAC z > 1.96 (lag 5).",
            "monitoring_policy": "Next 63 observations: nonpositive QLIKE improvement or coverage p < .01 triggers EWMA.",
            "limitations": ["Squared returns are a noisy variance proxy.", "No independent human approval recorded.",
                            "Repeated rolling selection is not a fresh independent experiment.",
                            "Fallback is a simpler estimate, not a guarantee of adequate calibration."],
            "references": ["https://filteredhistoricalsimulation.com/", "https://faculty.runi.ac.il/kobi/riskmgt/rmtd.pdf",
                           "https://www.mathworks.com/help/risk/varbacktest.cci.html",
                           "https://www.mathworks.com/help/risk/overview-of-expected-shortfall-backtesting.html",
                           "https://users.ssc.wisc.edu/~behansen/718/Christoffersen1998.pdf"],
        },
        "validation": {"model_version": VERSION, "result": validation, "selected_model": status["validation_champion"],
                       "state": "evaluated" if validation else "insufficient_history"},
        "monitoring": {"model_version": VERSION, "result": monitoring, "calibration": calibration, "decision": status,
                       "forecast_model": chosen, "loss_unit": "fraction of gross base-currency exposure",
                       "forecast_observations": observations},
    }
    return predicted, status, reports
