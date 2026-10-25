"""Independent statistical arithmetic and causal monitoring audit checks."""

import json
import math

import numpy as np
import pytest

from broker_analysis_mcp import calibration, forecasting


def test_independence_counts_and_likelihood_match_hand_calculation():
    result = calibration.independence([0, 0, 1, 1, 0, 1, 0, 0])
    assert result["counts"] == {"n00": 2, "n01": 2, "n10": 2, "n11": 1}
    log_null = 4 * math.log(4 / 7) + 3 * math.log(3 / 7)
    log_markov = 4 * math.log(.5) + 2 * math.log(2 / 3) + math.log(1 / 3)
    expected = 2 * (log_markov - log_null)
    assert result["lr_statistic"] == pytest.approx(expected)
    # Chi-square(1) survival is the two-sided standard normal tail.
    assert result["asymptotic_p_value"] == pytest.approx(math.erfc(math.sqrt(expected / 2)))
    assert result["transitions"] == 7
    assert result["state"] == "sparse_data"


def test_independence_distinguishes_clustering_with_the_same_exception_rate():
    clustered = calibration.independence([0] * 50 + [1] * 50)
    mixed = calibration.independence([0, 0, 1, 1] * 25)
    assert clustered["lr_statistic"] > 100
    assert clustered["asymptotic_p_value"] < 1e-20
    assert mixed["asymptotic_p_value"] > .8
    assert clustered["state"] == "sparse_data"  # Observed zero needs caution even with a large LR.
    assert mixed["state"] == "asymptotic_diagnostic"


@pytest.mark.parametrize("hits", [[], [False], [True], [False] * 63, [True] * 63, [True, False]])
def test_independence_degenerate_history_does_not_claim_success(hits):
    result = calibration.independence(hits)
    assert result["state"] == "insufficient_data"
    assert result["asymptotic_p_value"] is None
    assert result["lr_statistic"] is None
    assert result["transitions"] == max(0, len(hits) - 1)
    json.dumps(result, allow_nan=False)


def test_empirical_var_es_integrates_fractional_mass_even_at_ties():
    var, es = calibration.empirical_var_es(np.array([40, 20, 0, -10]), .625)
    assert var == 20
    assert es == pytest.approx((40 + .5 * 20) / 1.5)
    assert calibration.empirical_var_es(np.array([10, 10, 0, 0]), .75) == (10, 10)


def test_es_severity_hand_calculation_and_sign_convention():
    result = calibration.es_severity(np.array([0, 3, 6, 1]), np.full(4, 2.), np.full(4, 4.), .75)
    assert result["var_exceptions"] == 2
    assert result["mean_loss_on_var_exception"] == 4.5
    assert result["mean_forecast_es_on_var_exception"] == 4
    assert result["mean_loss_minus_es_on_var_exception"] == .5
    # Per-origin residuals are -2, 2, 14, -2: mean 3 in loss-positive convention.
    assert result["mean_es_residual"] == 3
    assert result["calibration_conclusion"] == "not_tested"
    assert result["p_value"] is None
    assert result["used_for_model_selection"] is False


@pytest.mark.parametrize("value", [0., -1.])
def test_es_degenerate_nonpositive_values_need_no_division_or_fake_pass(value):
    result = calibration.es_severity(np.full(4, value), np.full(4, value), np.full(4, value), .975)
    assert result["var_exceptions"] == 0  # Equality is not an exception.
    assert result["mean_loss_on_var_exception"] is None
    assert result["mean_es_residual"] == 0
    assert result["calibration_conclusion"] == "not_tested"
    json.dumps(result, allow_nan=False)


def test_es_empty_history_reports_no_evidence():
    result = calibration.es_severity(np.array([]), np.array([]), np.array([]), .975)
    assert result["state"] == "insufficient_data"
    assert result["mean_es_residual"] is None


def test_monitoring_rows_preserve_forecasts_when_current_and_future_returns_change():
    returns = np.random.default_rng(18).normal(0, .01, 600)
    dates = [f"session-{i:04d}" for i in range(len(returns))]
    _, old_status, old_reports = forecasting.evaluate(returns, dates, .975)
    changed = returns.copy()
    changed[570:] = -.4
    _, new_status, new_reports = forecasting.evaluate(changed, dates, .975)
    old = old_reports["monitoring"]["forecast_observations"]
    new = new_reports["monitoring"]["forecast_observations"]
    assert old_status["validation"] == new_status["validation"]
    assert old_status["validation_champion"] == new_status["validation_champion"]
    assert len(old) == 63
    assert old[:33] == new[:33]
    for key in ("var", "expected_shortfall", "forecast_origin", "history_end", "history_observations"):
        assert old[33][key] == new[33][key]
    assert old[33]["realized_loss"] != new[33]["realized_loss"]
    assert old[33]["forecast_origin"] == dates[569]
    assert old[33]["realized_date"] == dates[570]
    for row in old:
        assert row["forecast_origin"] == row["history_end"] < row["realized_date"]
        assert row["expected_shortfall"] >= row["var"]
    json.dumps(old_reports, allow_nan=False)


def test_monitoring_es_matches_direct_origin_scenario_integral():
    returns = np.random.default_rng(4).normal(0, .02, 600)
    predicted, status, reports = forecasting.evaluate(returns, [str(i) for i in range(600)], .975)
    row = reports["monitoring"]["forecast_observations"][0]
    t = row["return_index"]
    start = max(forecasting.BURN, t - 500)
    variance = predicted[status["validation_champion"]]
    ordered = np.sort(-returns[start:t] * np.sqrt(variance[t] / variance[start:t]))
    # Independent quantile integral: .025 * 477 = 11.925 observations.
    mass = len(ordered) * .025
    full = math.floor(mass)
    expected = (ordered[-full:].sum() + (mass - full) * ordered[-full - 1]) / mass
    assert row["expected_shortfall"] == pytest.approx(expected)
    assert row["realized_loss"] == -returns[t]


def test_short_history_reports_empty_monitoring_and_no_fake_calibration():
    _, status, reports = forecasting.evaluate(np.zeros(100), [str(i) for i in range(100)], .975)
    assert reports["monitoring"]["forecast_observations"] == []
    assert status["calibration"]["independence"]["state"] == "insufficient_data"
    assert status["calibration"]["es_severity"]["state"] == "insufficient_data"
    assert status["champion"] == "ewma_094"
    json.dumps(reports, allow_nan=False)


def test_added_diagnostics_cannot_change_champion_policy(monkeypatch):
    returns = np.random.default_rng(27).normal(0, .01, 600)
    dates = [str(i) for i in range(600)]
    _, before, _ = forecasting.evaluate(returns, dates, .975)
    monkeypatch.setattr(calibration, "independence", lambda hits: {"lr_statistic": 1000, "asymptotic_p_value": 0})
    monkeypatch.setattr(calibration, "es_severity", lambda *args: {"mean_es_residual": 1e6})
    _, after, _ = forecasting.evaluate(returns, dates, .975)
    for key in ("champion", "validation_champion", "reason", "fallback"):
        assert before[key] == after[key]
