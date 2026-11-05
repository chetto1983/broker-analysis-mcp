# Release 0.4.1

New acquisitions use yfinance's existing dated historical regular-session schedule when its
current session cannot describe the latest observed daily bar. This fixes a measured case
where November 4 was dropped because current metadata named November 5. The matched session
end is frozen with the OHLCV; an unrelated day's close is no longer attached to the selected
bar. Missing schedule evidence retains explicit uncertainty. Crypto still closes at UTC
midnight, and a future bar is never admitted.

Old snapshots remain immutable and preserve their original values and metadata. Acquire a
new snapshot to use the new evidence. Replay validates captured schedules and remains fully
provider-free. No calendar dependency, cache service or indicator was added.

Numerical custom analysis with chart=false accepts more than four lower groups. Both the
chart entry point and renderer retain the four-pane limit for charts. An oversized custom
lookback now warns with its source-history count. Identical warnings from an identity
comparison appear once, while different acquisition messages are retained. Missing or
unreadable audit objects no longer expose server paths; hash failures remain explicit.

These are functional fixes. Independent formula verification, provider failure/load testing
and operational continuity are separate acceptance dimensions; this release is still a
prototype using unofficial Yahoo Finance data.

## Verification

The live 0.4.0 VM reproduced every reported defect before changes. The public historical
schedule supplied the missing November 4 boundary in one cached 0.124-second sample.
Regressions were watched failing before their fixes; the final pre-release offline suite
has 336 passing tests with 26 live tests excluded, and Ruff/diff checks pass. Release CI,
installed-package and real-agent acceptance are recorded separately after they complete.
