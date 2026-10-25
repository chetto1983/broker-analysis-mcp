# Portfolio model development, validation and monitoring

Implementation version: `portfolio-risk-3`. This is an analyst risk prototype for linear cash
securities. No independent model approval or regulatory certification is implied.

## Development report

Each run writes a separately hashed development report, referenced by its analysis manifest.
The portfolio input is signed current market value in quote currency plus positive NAV in base
currency. Values are converted using the last common FX date. Cash is NAV less converted net
exposure, with zero return and zero financing costs. A security's base-currency total return is
`(1 + local_return) * (1 + FX_return) - 1`. Align price and FX levels first, then calculate
returns. Exposures are held constant across the risk reconstruction. Adjusted closes are not
used to infer share counts or current market values.
Bars dated on the current UTC date or later are excluded conservatively to avoid incomplete
sessions. The archived snapshot still retains the retrieved observations and the exact common
dates used in the calculation.

The registry has two zero-mean variance models, with a `1e-12` numerical floor:

- EWMA: `v[t+1] = .94*v[t] + .06*r[t]^2`; seed `v[60]` with the first 60 squared returns' mean.
- Challenger: `v[t] = mean(r[t-60:t]^2)`.

`r` is fixed-exposure portfolio P&L divided by gross base-currency exposure. This basis keeps
the numerical floor and model decisions independent of idle cash and NAV. Reported volatility
is converted to percent NAV; the snapshot retains both NAV returns and model returns and
labels the forecast denominator. A forecast at index `t` never uses
return `r[t]`. No annualisation or square-root-of-time extrapolation is applied. HAR, GARCH,
ARFIMA and foundation models are not in this registry; adding one requires a new development
record, selection policy and comparative validation.

Filtered historical simulation (FHS) scales each joint asset-return vector by the same ratio
`sqrt(v[next] / v[historical_origin])`, over up to 500 observations after warm-up. Joint historical
dependence is retained through the aligned vectors; the model does not forecast changes in
correlation or fit separate asset volatility processes. This common portfolio-scale variant is
deliberately narrower than a full multivariate FHS implementation. Scaled simple-return scenarios
are linear shocks, not guaranteed economically feasible future price paths.

Loss is minus simulated P&L. VaR is the inverse empirical CDF at the requested confidence. ES
averages the upper `(1-confidence)*N` probability mass, assigning fractional weight to the
boundary observation and sharing boundary mass across ties. The ES marginal for asset `i` is
the tail-weighted mean of minus its scenario return. Component ES equals that marginal times
signed base-currency exposure. These components sum to ES, and hedges may have negative values.
Marginals freeze the scenario distribution: they do not include refitting the volatility model
after changing holdings. Loss values are not clipped at zero, preserving the attribution identity.

Historical stress uses endpoint compounded adjusted-price and FX moves on current exposures.
The built-in episodes are explicitly chosen dates, not an exhaustive search for worst losses.
Hypothetical shocks combine local prices and FX multiplicatively. Missing specified factors
mean zero shock; unknown tickers/currencies fail validation. Historical dates must be covered;
boundary substitutions are limited to seven days and actual dates are reported.

Method sources: [FHS authors' overview](https://filteredhistoricalsimulation.com/) and
[RiskMetrics Technical Document](https://faculty.runi.ac.il/kobi/riskmgt/rmtd.pdf).
The implementation and its acceptance policy are local choices, not claims of reproducing
every method or empirical result in those sources.

## Validation report

The policy is fixed in code. The last 63 returns are reserved for monitoring; the preceding 252
are validation. Both follow at least 60 warm-up observations (435 returns total). Less history
produces an `insufficient_history` validation report and EWMA fallback, never a fabricated pass.

QLIKE is `log(v) + r^2/v`; forecast-independent constants are omitted, so negative values are
valid. Squared returns are a noisy variance proxy. The paired loss differential is EWMA minus
challenger. Its mean must be positive and its z statistic must exceed 1.96, using a Bartlett HAC
variance estimate with five lags. This is an approximate large-sample comparison for one fixed
challenger, not a universal guarantee of superiority. Selection on repeatedly refreshed rolling
windows is not independent new evidence. With more challengers, account for multiple testing
and use a separate untouched evaluation dataset before changing the policy.

The saved validation report contains the exact window, observations, losses, loss improvement,
statistic, model version and champion selected before monitoring. It is separate from both the
development description and monitoring decision. The manifest binds it to its exact snapshot.

## Monitoring report

The subsequent 63-observation window assesses the validation champion without reselecting it
using that window. Nonpositive challenger improvement against EWMA triggers fallback. VaR
coverage is backtested with origin-specific past residuals, up to 500 samples and at least 100
after warm-up. It tests the pre-monitoring champion, not a retrospectively substituted fallback.
Coverage p-values below .01 also trigger EWMA; with short input history, coverage may have fewer
than 63 observations or be unavailable. Coverage count and tested model are always explicit.

The two-sided exact binomial test assumes independent exceptions and has limited power over
short windows. A supplementary Christoffersen first-order independence likelihood ratio now
reports transition counts and an asymptotic chi-square p-value. Missing outcome/prior states
produce no inference; zero cells or expected counts below five flag sparse data. The latter is
a reporting heuristic, not a guarantee that a larger sample is adequate. Non-rejection does
not establish independence. See the [method reference](https://www.mathworks.com/help/risk/varbacktest.cci.html).

ES severity diagnostics report losses on VaR exceptions and the average loss-positive residual
`VaR + max(realized_loss - VaR, 0)/(1-confidence) - ES`. They are descriptive only: no calibrated
null distribution or p-value is supplied, and zero exceptions do not establish ES calibration.
See [ES backtesting assumptions](https://www.mathworks.com/help/risk/overview-of-expected-shortfall-backtesting.html).
These additions do not alter champion selection or the existing coverage alarm. EWMA fallback
is a simpler estimate, not evidence that risk is well calibrated.

The monitoring report retains each forecast origin, realized date, historical window, VaR,
ES and observed loss in fractions of gross base-currency exposure. Each forecast uses only
returns before its realized date and the model selected before monitoring. These rows make
the diagnostic calculations independently reconstructible from the saved snapshot.

Monitoring runs when `portfolio_risk` is called again. `model_status(record_id)` returns a
verified historical decision and its as-of/last-validation dates. It does not update old reports
or install a scheduler. The [monitor CLI](PORTFOLIO_MONITORING.md) can be invoked by an external
scheduler with a current holdings/NAV configuration. An operator can also invoke the tool periodically;
each run gets a new manifest. Human review should follow persistent coverage alarms, stale
inputs, unexpected exposure changes or poor baseline performance. Record independent review
and approval outside this prototype before operational model adoption.

## Records and reproduction

Each successful run writes six content-addressed objects (snapshot, output, development,
validation, monitoring and manifest), deduplicating identical content. Canonical JSON forbids
NaN and infinity. Unique temporary files are fsynced, then published by atomic hard links;
existing files are verified, never overwritten. The manifest is published last. A failed run
may leave unreferenced complete objects, but does not return an unrecorded result as success.
The record directory must be on a filesystem supporting hard links.

The output hash covers the stored result before its `audit` envelope (avoiding self-reference).
The snapshot includes raw adjusted closes and FX observations, source/retrieval metadata,
common dates and calendar policy, inputs, return series, model forecasts, installed versions,
implementation version and implementation-file hashes. It freezes the source data as retrieved;
it does not turn Yahoo's revised history into a point-in-time licensed feed. Archived source
versions and locked dependencies are still needed to reproduce the execution environment.

`analysis_record` checks the manifest and every referenced object before serving any section.
Hashes detect corruption against a retained manifest ID; local administrators can delete
records or replace an entire chain and supply a new ID. Retain manifest IDs independently and
use external retention-controlled storage if stronger guarantees are required. No retention
purge, export, background task or external transfer is performed automatically.
