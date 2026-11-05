# Operating Broker Analysis

The plugin runs independently of Aura. Install with Codex or Claude Code, or run
`uv run broker-analysis-mcp` for a stdio MCP client. Use the committed lockfile for
development and the immutable source commit in the shipped plugin launcher.

## Execution and failures

MCP transport calls run in AnyIO worker processes, two at a time. The default hard
budget is **120 seconds including queueing and worker startup**. A timeout or client
cancellation kills that worker; the next request starts/reuses a healthy one. Startup
and tools/list do not import the numerical stack. Direct Python report functions and
the cron monitor/hooks entry points are synchronous APIs; the MCP execution budget
applies to the MCP server, not those direct invocations.

`BROKER_ANALYSIS_TOOL_TIMEOUT` accepts finite seconds from 0.1 to 600. Yahoo history
requests use a 10-second HTTP timeout and yfinance's native single transient retry.
Other yfinance operations retain the dependency's request timeout; the MCP hard budget
encloses the whole operation. Rate limits are returned rather than hammered with a
second application retry loop. Failed acquisition never returns a partial snapshot.

Errors retain MCP `isError=true` and contain JSON `error` with `code`, `field`,
`message`, `retryable`, `details`. Constraint details omit rejected values. Codes
distinguish invalid input, rate limits, provider timeout/unavailability, missing or
corrupt records, worker loss, execution timeout and storage failures. A retryable
failure permits a later retry; it does not mean retry forever. Peer/hook batch results
retain human-readable error strings and add structured `error_details`.

Completed-only daily sessions remain the default. Unknown historical overnight session
association remains conservative and explicit. Acquisition time, daily-bar time and
trade time are different fields. Freeze a snapshot for cross-report comparisons.

## Measurements

Every transported result has `_meta.broker_analysis`, outside the frozen report content:

- `request_id`, tool, status and `mcp_seconds`: server-observed call duration.
- `provider_seconds`, `provider_operations`: complete provider operations, including their
  decoding/normalization; not HTTP-only wire time. These are zero for snapshot replay.
- `worker_seconds`, `processing_seconds`: worker dispatch and processing excluding measured
  provider operations. Processing includes computation, validation and storage.
- `queue_and_worker_overhead_seconds`: waiting, process initialization and transport overhead.
- `data_age_seconds`: time since the report's retrieval timestamp, not time since the
  exchange's last eligible session. `freshness` compares this with
  `BROKER_ANALYSIS_STALE_AFTER` (default 86400 seconds, range 0.1..31536000).

An unavailable measure is null, including provider work interrupted by a killed worker.
Structured stderr events use `event=broker_tool` and the same request ID. Tool arguments,
holdings and raw provider exception messages are not logged. Stdout is MCP only. The
SDK already provides OpenTelemetry server spans; configure its exporter in the host if
centralized tracing is required. A remote gateway/client needs its own measurements;
this process cannot measure a network hop outside itself.

## Records backup and restore

State root: `BROKER_ANALYSIS_HOME`, then `PLUGIN_DATA`, then `~/.broker-analysis`.
The archive CLI covers the immutable `records/` store, including market snapshots and
portfolio manifests/reports. It does not alter or delete live records.

```sh
uv run broker-analysis-archive verify
uv run broker-analysis-archive backup /separate-disk/broker-records.zip
BROKER_ANALYSIS_HOME=/fresh-home uv run broker-analysis-archive restore /separate-disk/broker-records.zip
BROKER_ANALYSIS_HOME=/fresh-home uv run broker-analysis-archive verify
```

Backup refuses an existing destination, verifies object hashes and publishes the ZIP
atomically after fsync. Keep its returned SHA-256 separately and copy the ZIP off the
machine. A backup on the same failed disk cannot restore availability. Each backup is
an inventory boundary; new records created afterward belong to the next backup.

Restore validates all ZIP members, hashes, canonical JSON, linked records and existing
targets before its first write. It rejects traversal/duplicate names, encrypted entries,
objects over 128 MiB and total uncompressed content over 2 GiB. Objects publish before
dependent manifests and existing records are never overwritten. An interrupted restore
can leave additional complete objects; retrying is safe. A backup is integrity-checked,
not authenticated against an attacker who can replace it and its separately held hash.

Files are fsynced before publication; POSIX directory entries are fsynced too. Windows
directory fsync is unavailable through this interface. Test power-loss recovery on the
actual filesystem before promising it. Interrupted workers may leave `.pending-*` files;
`verify` counts these under `records/`. Interrupted hook writes can leave them at the state
root. They are never treated as committed records. Remove only
operator-confirmed stale pending files with the server stopped. No automatic record
retention/deletion is enabled; monitor volume usage and provision space for archives.

Mutable state outside `records/`: stop hook runners, copy `hooks.json` and required
`alerts/` separately with the ordinary filesystem backup, restore them while stopped.
Generated charts can be regenerated. `.hooks.lock` is a synchronization artifact. Hook
mutations use a cross-process lock and atomic unique temporary files. Delivery remains
at-least-once: a crash after delivery and before bookmark publication can duplicate an
event. External webhook effects cannot be rolled back by restoring a local archive.

## Repeatable acceptance

```sh
uv run pytest
uv run python scripts/operational_acceptance.py --fixture --requests 100 --concurrency 4
uv run python scripts/operational_acceptance.py --requests 100 --concurrency 4 --output live-evidence.json
```

The fixture mode replaces only the provider network boundary; protocol, processes,
calculations, snapshots, concurrent replay, restart and restore are real. Live mode
acquires AAPL/SPY and runs read-only reports on one shared snapshot. Use `--command`
followed by an installed server launcher as the final arguments to test that artifact.
Fault/cancellation, disk-full, integrity and hook-concurrency cases are in the offline
suite and require no network. CI runs the repeatable fixture acceptance and prints
its evidence. A bounded measured run demonstrates that workload, not an uptime SLA.

## Updating the local Windows plugin

Refresh its marketplace, then add the plugin again. If Codex reports failure backing up
the cache with access denied, stop the clients using that plugin first (Codex app and
VS Code extension can each own MCP processes). Their processes keep the version directory
as their current directory, which Windows prevents the installer from renaming. This
was verified independently of ACLs; changing permissions or deleting the cache while
its processes are active is unnecessary. Retry installation after those processes exit.
