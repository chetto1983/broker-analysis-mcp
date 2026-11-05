# Standalone operational closure

The user approved closing the five operational gaps identified in the assessment and
removing the prototype label from the skills. The user then explicitly required local
plugin/MCP testing without Aura. Work stays in this repository and on its existing main.

## Measured starting point

0.4.1 has 339 passing offline tests, independent numerical comparisons, provider-free
snapshot replay, atomic content-addressed records and corruption/concurrent-write tests.
The source still has generic null reasons and text-only application errors. Provider
calls have no encompassing execution deadline. There is no archive restore command or
repeatable standalone load/fault acceptance. The connected local plugin reports 0.3.1;
refreshing its marketplace succeeds, replacing its active cache reports Windows access
denied. These are observations, not assumptions about Aura.

## Decisions

- Reuse MCP's public dispatch and validation, AnyIO's cancellable worker processes,
  native yfinance retry configuration, standard ZIP/hash/filesystem primitives, and
  filelock for cross-process hook updates. Do not implement a transport or worker pool.
- Transport tool calls have a configurable hard execution budget (default 120 seconds,
  includes waiting for capacity) and at most two worker processes. Cancellation kills
  the active worker rather than abandoning a thread that could keep writing. Discovery
  stays lazy. Pure direct Python calls retain their existing synchronous interfaces.
- Successful result content remains stable on snapshot replay. Runtime timing and data
  age belong in MCP result metadata and structured stderr logs, never stdout chatter.
  Distinguish end-to-end, provider operation, computation/storage and worker overhead;
  do not pretend to measure remote gateway latency from inside this server.
- Application errors use code, field, message, retryable and constraint details. Preserve
  MCP isError and suppress raw provider/internal exception text and rejected input values.
- Null metrics identify missing_input, insufficient_history, not_applicable or undefined
  with the actual field/domain/sample reason. Keep mathematical conventions explicit.
- Archive verify/backup/restore covers immutable records. Restore validates the entire
  archive before publishing, refuses unsafe names/corruption and never overwrites an
  existing record. Backups are point-in-time records inventories; mutable hooks, generated
  charts and external webhook effects are outside this command and documented separately.
- Hook mutations are serialized across processes and publish unique fsynced temporary
  files atomically. Delivery remains explicitly at-least-once after interruption.
- No automatic deletion of records, no new financial indicators, no source replacement,
  no unsubstantiated availability or certification claims. Retain unofficial-source and
  data-use caveats while removing the prototype label from current product descriptions.

## Acceptance

1. Real local stdio MCP: valid/invalid arguments, unavailable record, timeout then recovery,
   cancellation, concurrent replay and stable outputs across restarts.
2. Provider fault fixtures exercise rate limit, timeout, connection failure, absent and
   malformed data, with no partial snapshot manifest published.
3. Storage faults preserve existing objects, publish no incomplete manifest, and expose
   no server path. Backup to a separate destination and restore to a fresh home reproduces
   exact saved IDs and report content, including after process restart.
4. Repeatable bounded load test records request count, concurrency, errors, durations and
   process recovery. A measured local run is not an uptime guarantee.
5. Full offline suite, Ruff, wheel/sdist, installed plugin acceptance and release CI pass.
   Skills preserve source/window/unit/null/warning rules without the prototype label.
