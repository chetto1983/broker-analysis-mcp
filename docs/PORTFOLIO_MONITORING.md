# Scheduled portfolio monitoring

`broker-analysis-monitor --config portfolio.json` performs one fresh analysis, saves its audit
record, prints a JSON summary and exits. It can be invoked by cron or Windows Task Scheduler.
It does not install a scheduled task or send notifications.

Start from [the illustrative configuration](../examples/portfolio-monitor.json). Replace its
holdings and NAV with current values before operational use. Signed market values are in each
security's quote currency; NAV is in `base_currency`. The program rejects unknown fields,
duplicate JSON keys, nonfinite numbers and invalid portfolio inputs. It does not update holdings
from a broker: repeating an old file continues to analyse those fixed exposures.

From a synchronized checkout:

```powershell
uv run broker-analysis-monitor --config D:/portfolios/current.json
```

From the published release, without a checkout:

```powershell
uvx --from git+https://github.com/chetto1983/broker-analysis-mcp@v0.3.0 broker-analysis-monitor --config D:/portfolios/current.json
```

For Windows Task Scheduler, select the full path to `uvx.exe` as the program, use the arguments
after `uvx` above, and choose a trigger after the relevant markets have closed. On Unix, use the
same command in cron with absolute executable/config paths. Use the same account and
`BROKER_ANALYSIS_HOME` for the monitor and MCP server so the returned records are accessible.
Capture stdout/stderr separately and inspect the process exit code. No real schedule is enabled
by the sample configuration.

| Exit code | Meaning |
| --- | --- |
| 0 | Analysis and record saved; inspect warnings and diagnostics |
| 2 | Input or analysis failure; JSON error on stderr for configuration/analysis errors |
| 3 | Analysis saved with the existing VaR coverage alarm; JSON summary on stdout |

Command-line usage errors also exit 2 and print argparse usage to stderr. A successful run's
`status: ok` describes execution, not model approval. Sparse-data diagnostics, stale observations,
insufficient validation history and EWMA fallback can still be present. The new independence
and ES severity diagnostics do not change the established coverage-alarm exit policy.

The summary includes `record_id`, `as_of`, champion, fallback/reason, calibration and warnings.
Use `analysis_record(record_id, "monitoring")` for the causal per-origin forecasts and realized
losses; `model_status(record_id)` reads the saved decision. Neither refreshes an old record.
An analysis failure never returns the previous record as if it were fresh.
