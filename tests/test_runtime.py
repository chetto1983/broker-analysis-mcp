"""Real stdio: bounded work, sanitized errors, concurrent replay and recovery."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def parameters(tmp_path, timeout="120"):
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text("from runtime_fixture import install\ninstall()\n"
                         "from broker_analysis_mcp.server import main\n"
                         "if __name__ == '__main__': main()\n")
    env = dict(os.environ, BROKER_ANALYSIS_HOME=str(tmp_path / "home"),
               BROKER_ANALYSIS_TOOL_TIMEOUT=timeout, LATE_WRITE_MARKER=str(tmp_path / "late"),
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]))
    return StdioServerParameters(command=sys.executable, args=[str(bootstrap)], env=env)


def payload(result):
    return json.loads(result.content[0].text)


def test_wire_errors_are_structured_and_keep_constraints(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            bad = await client.call_tool("risk_metrics", {"ticker": "A", "confidence": 1.2})
            missing = await client.call_tool("technical_indicators", {"ticker": "A", "snapshot_id": "0" * 64})
            assert bad.is_error and missing.is_error
            error = payload(bad)["error"]
            assert error["code"] == "INVALID_INPUT" and error["field"] == "confidence"
            assert error["retryable"] is False and error["details"][0]["constraints"] == {"lt": 1.0}
            assert "1.2" not in bad.content[0].text
            assert payload(missing)["error"]["code"] == "RECORD_NOT_FOUND"
            assert str(tmp_path) not in missing.content[0].text
            unknown = await client.call_tool("not_a_tool", {})
            assert unknown.is_error and payload(unknown)["error"]["code"] == "INVALID_INPUT"
    asyncio.run(exercise())


def test_cancelled_request_stops_the_worker_and_session_remains_usable(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            call = asyncio.create_task(client.call_tool("technical_indicators", {"ticker": "SLOW"}))
            async def started():
                while not (tmp_path / "late.started").exists():
                    await asyncio.sleep(.05)
            await asyncio.wait_for(started(), 15)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            result = await client.call_tool("technical_indicators", {"ticker": "A"})
            assert not result.is_error, result.content
            await asyncio.sleep(12.1)
            assert not (tmp_path / "late").exists()
    asyncio.run(exercise())


def test_crashed_worker_is_replaced_without_restarting_mcp(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            crashed = await client.call_tool("technical_indicators", {"ticker": "CRASH"})
            assert crashed.is_error and payload(crashed)["error"]["code"] == "WORKER_UNAVAILABLE"
            assert not (await client.call_tool("technical_indicators", {"ticker": "A"})).is_error
    asyncio.run(exercise())


def test_invalid_runtime_configuration_is_a_structured_error(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path, "nan")) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            result = await client.call_tool("list_indicator_catalog", {})
            assert result.is_error and payload(result)["error"]["code"] == "CONFIGURATION_ERROR"
    asyncio.run(exercise())


def test_hook_configuration_survives_concurrent_worker_updates(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            created = await asyncio.gather(*(client.call_tool("create_hook", {
                "name": f"hook{i}", "ticker": "A", "condition": {"name": "positive", "when": "close > 0"},
                "action": {"type": "file", "target": "test_alerts"}}) for i in range(8)))
            assert all(not item.is_error for item in created), created
            loaded = await client.call_tool("list_hooks", {})
            assert not loaded.is_error
            assert len(payload(loaded)["hooks"]) == 8
    asyncio.run(exercise())


def test_hard_deadline_kills_work_and_next_request_recovers(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path, "8")) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            timed = await client.call_tool("technical_indicators", {"ticker": "SLOW"})
            assert timed.is_error
            assert payload(timed)["error"]["code"] == "TOOL_TIMEOUT"
            healthy = await client.call_tool("technical_indicators", {"ticker": "A"})
            assert not healthy.is_error, healthy.content
            await asyncio.sleep(4.1)
            assert not (tmp_path / "late").exists()
    asyncio.run(exercise())


def test_concurrent_snapshot_replay_is_stable_and_separates_runtime_metadata(tmp_path):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            snapshot = await client.call_tool("market_snapshot", {"tickers": ["A"], "period": "1y"})
            assert not snapshot.is_error, snapshot.content
            sid = payload(snapshot)["snapshot_id"]
            results = await asyncio.gather(*(client.call_tool("technical_indicators", {"ticker": "A", "snapshot_id": sid})
                                             for _ in range(8)))
            assert all(not result.is_error for result in results)
            assert all(payload(result) == payload(results[0]) for result in results)
            observations = [result.meta["broker_analysis"] for result in results]
            assert len({item["request_id"] for item in observations}) == 8
            assert all(item["provider_seconds"] == 0 for item in observations)
            assert all(item["mcp_seconds"] >= item["worker_seconds"] >= 0 for item in observations)
            assert all(item["data_age_seconds"] >= 0 for item in observations)
    asyncio.run(exercise())


@pytest.mark.parametrize(("ticker", "code"), [("RATE", "PROVIDER_RATE_LIMIT"), ("TIMEOUT", "PROVIDER_TIMEOUT"),
                                               ("DOWN", "PROVIDER_UNAVAILABLE"), ("EMPTY", "DATA_UNAVAILABLE")])
def test_provider_faults_have_safe_retry_information(tmp_path, ticker, code):
    async def exercise():
        async with stdio_client(parameters(tmp_path)) as (r, w), ClientSession(r, w) as client:
            await client.initialize()
            result = await client.call_tool("market_snapshot", {"tickers": ["A", ticker]})
            assert result.is_error
            error = payload(result)["error"]
            assert error["code"] == code
            assert error["retryable"] is (ticker != "EMPTY")
            assert "private" not in result.content[0].text
            assert not list((tmp_path / "home" / "records").glob("*.json"))
    asyncio.run(exercise())
