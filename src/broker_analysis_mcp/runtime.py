"""Bounded transport calls using the SDK dispatch and AnyIO's cancellable process pool."""

import asyncio
import json
import math
import os
import sys
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

import anyio
from anyio import to_process
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from .errors import DataError, error_payload
from .telemetry import measurements


def _setting(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        if not math.isfinite(value) or not .1 <= value <= maximum:
            raise ValueError()
    except ValueError:
        raise DataError(f"{name} must be finite and between 0.1 and {maximum}", code="CONFIGURATION_ERROR") from None
    return value


def failed(error: Exception) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": error_payload(error)}))], is_error=True)


def invoke(name: str, arguments: dict) -> tuple[CallToolResult, dict]:
    from .server import server

    stats = {"provider_seconds": 0., "provider_operations": 0}
    token = measurements.set(stats)
    started = perf_counter()
    try:
        try:
            result = asyncio.run(server.call_tool(name, arguments))
        except Exception as error:
            result = failed(error)
        return result, dict(stats, worker_seconds=perf_counter() - started)
    finally:
        measurements.reset(token)


def _age(result: CallToolResult) -> float | None:
    try:
        data = result.structured_content or json.loads(result.content[0].text)
        stamp = datetime.fromisoformat(data["retrieved_at"])
        if stamp.tzinfo is not None:
            return max(0., (datetime.now(UTC) - stamp).total_seconds())
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        pass
    return None


class BoundedServer(MCPServer):
    """Direct Python calls keep SDK semantics; transport calls receive isolated execution."""

    async def call_tool(self, name, arguments, context=None):
        if context is None:
            return await super().call_tool(name, arguments, context)
        if not hasattr(self, "_execution_limiter"):
            self._execution_limiter = anyio.CapacityLimiter(2)
        started = perf_counter()
        known = {tool.name for tool in await self.list_tools()}
        stats = {"request_id": uuid4().hex, "tool": name if name in known else "unknown", "provider_seconds": None, "worker_seconds": None}
        result = None
        try:
            # SDK performs handshake/security checks before dispatch. Reuse its public
            # call_tool in the worker for argument/output validation, not a second protocol.
            # AnyIO kills a cancelled worker: https://anyio.readthedocs.io/en/stable/subprocesses.html
            timeout = _setting("BROKER_ANALYSIS_TOOL_TIMEOUT", 120., 600.)
            stale_after = _setting("BROKER_ANALYSIS_STALE_AFTER", 86400., 31536000.)
            with anyio.fail_after(timeout):
                result, measured = await to_process.run_sync(invoke, name, arguments, cancellable=True,
                                                            limiter=self._execution_limiter)
                stats.update(measured)
        except TimeoutError:
            result = failed(DataError("tool execution budget exhausted; retry or use a saved snapshot",
                                      code="TOOL_TIMEOUT", retryable=True))
        except anyio.BrokenWorkerProcess:
            result = failed(DataError("analysis worker stopped; retry the request", code="WORKER_UNAVAILABLE", retryable=True))
        except DataError as error:
            result = failed(error)
        finally:
            stats["timestamp"] = datetime.now(UTC).isoformat()
            stats["mcp_seconds"] = perf_counter() - started
            stats["status"] = "cancelled" if result is None else "error" if result.is_error else "ok"
            if stats["worker_seconds"] is not None:
                stats["processing_seconds"] = max(0., stats["worker_seconds"] - stats["provider_seconds"])
                stats["queue_and_worker_overhead_seconds"] = max(0., stats["mcp_seconds"] - stats["worker_seconds"])
            stats["data_age_seconds"] = _age(result) if result is not None else None
            stats["freshness"] = ("unknown" if stats["data_age_seconds"] is None else
                                  "older_than_threshold" if stats["data_age_seconds"] > stale_after else "within_threshold")
            if result is not None and result.is_error:
                stats["error_code"] = json.loads(result.content[0].text)["error"]["code"]
            print(json.dumps({"event": "broker_tool", **stats}), file=sys.stderr, flush=True)
        result.meta = {**(result.meta or {}), "broker_analysis": stats}
        return result
