# Release 0.5.0

Broker Analysis adds standalone operational controls. MCP tool calls execute in a
bounded AnyIO process pool, with a default 120-second hard budget including queueing.
Timeout and cancellation terminate the worker; later requests can recover without
restarting the MCP server. Discovery remains lazy.

Application errors carry code, field, safe message, retryability and constraint details.
Provider operations distinguish rate limits, timeouts, connection loss and invalid data.
They reuse native yfinance retries rather than layering another retry loop. Failed
acquisitions publish no partial market snapshot.

Risk, fundamental, ETF, technical and custom outputs distinguish absent data, short
history, inapplicable metrics and undefined results. Custom latest values refer to the
last observed bar, including an explicit null when that observation is non-finite;
an earlier finite value is not silently substituted. Nested source history and
indicator-specific warmup counts agree with warnings. Empty event studies explain
their null statistics. Zero reported dividend yield remains zero.

Per-request metadata and structured stderr events separate server call, provider,
processing and worker overhead timings. They expose retrieval age without changing
frozen report content. This process does not claim to measure an external MCP gateway.

The new `broker-analysis-archive` command verifies, backs up and restores immutable
records with prevalidated hashes, safe member names, size limits and dependency-first
publication. Restore never overwrites existing objects. Hook state updates use native
cross-process file locks and atomic unique temporary files. Distinct chart runs retain
their own artifacts, including runs within the same second.

Current skills, server descriptions, chart footer and plugin metadata use the product
name without the prototype label. Unofficial Yahoo source, data-use restrictions,
warnings and analytical limitations remain explicit. Old immutable records retain their
original source metadata.

The offline suite covers provider failures, process cancellation/crash/recovery, input
redaction, concurrent replay and hook writes, disk errors, archive integrity and restore.
CI also runs the standalone fixture load/restore/restart rehearsal. Local real-data and
Windows installed-artifact acceptance are recorded under `docs/superpowers/verification/`.
See [OPERATIONS.md](OPERATIONS.md) for commands, configuration and recovery procedures.

These controls support professional supervised analytical use. Measured local load is
not an availability SLA. Machine power-loss durability, hostile local writers and
exactly-once external delivery are not guarantees of this release. Direct synchronous
Python/cron entry points are outside the MCP hard-budget boundary. Mutable hook backups
require stopped runners; record backup is independently available while reports run.
