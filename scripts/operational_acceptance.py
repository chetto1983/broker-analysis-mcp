"""Standalone MCP load/restart/recovery rehearsal. Use --fixture for network-free CI."""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def content(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return json.loads(result.content[0].text)


async def exercise(args, folder):
    env = dict(os.environ, BROKER_ANALYSIS_HOME=str(folder / "source"))
    command, server_args = (args.command[0], args.command[1:]) if args.command else (sys.executable, ["-m", "broker_analysis_mcp.server"])
    ticker, benchmark = ("A", "B") if args.fixture else ("AAPL", "SPY")
    if args.fixture:
        if args.command:
            raise ValueError("--fixture uses the current Python package; --command tests a real installed server")
        bootstrap = folder / "bootstrap.py"
        bootstrap.write_text("from runtime_fixture import install\ninstall()\nfrom broker_analysis_mcp.server import main\n"
                             "if __name__ == '__main__': main()\n")
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "tests"), *sys.path])
        server_args = [str(bootstrap)]
    params = StdioServerParameters(command=command, args=server_args, env=env)
    elapsed, observations = [], []
    async with stdio_client(params) as (r, w), ClientSession(r, w) as client:
        began = perf_counter()
        await client.initialize()
        tools = (await client.list_tools()).tools
        startup = perf_counter() - began
        acquired = await client.call_tool("market_snapshot", {"tickers": [ticker, benchmark], "benchmark": benchmark, "period": "1y"})
        snapshot = content(acquired)
        sid = snapshot["snapshot_id"]
        technical = content(await client.call_tool("technical_indicators", {"ticker": ticker, "snapshot_id": sid}))
        custom = content(await client.call_tool("custom_analysis", {
            "ticker": ticker, "snapshot_id": sid, "chart": False,
            "indicators": [{"id": "sma50", "fn": "sma", "params": {"n": 50}}]}))
        assert technical["metrics"]["sma50"]["value"] == custom["series_last_values"]["sma50"]
        risk = content(await client.call_tool("risk_metrics", {"ticker": benchmark, "benchmark": benchmark, "snapshot_id": sid}))
        assert risk["metrics"]["beta"]["value"] == 1 and risk["metrics"]["tracking_error"]["value"] == 0
        semaphore = asyncio.Semaphore(args.concurrency)
        async def replay():
            async with semaphore:
                began = perf_counter()
                result = await client.call_tool("technical_indicators", {"ticker": ticker, "snapshot_id": sid})
                elapsed.append(perf_counter() - began)
                assert content(result) == technical
                observation = result.meta["broker_analysis"]
                assert observation["provider_operations"] == 0
                observations.append(observation)
        await asyncio.gather(*(replay() for _ in range(args.requests)))
        bad = await client.call_tool("risk_metrics", {"ticker": ticker, "confidence": 2})
        assert bad.is_error and json.loads(bad.content[0].text)["error"]["code"] == "INVALID_INPUT"
    # Use separate CLI subprocesses so the operator's own process environment is unchanged.
    backup = folder / "records.zip"
    import subprocess

    for operation, home in (("backup", "source"), ("restore", "restored")):
        result = subprocess.run([sys.executable, "-m", "broker_analysis_mcp.archive", operation, str(backup)],
                                env=dict(env, BROKER_ANALYSIS_HOME=str(folder / home)), capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    restored = StdioServerParameters(command=command, args=server_args,
                                     env=dict(env, BROKER_ANALYSIS_HOME=str(folder / "restored")))
    async with stdio_client(restored) as (r, w), ClientSession(r, w) as client:
        await client.initialize()
        replayed = await client.call_tool("technical_indicators", {"ticker": ticker, "snapshot_id": sid})
        assert content(replayed) == technical
        assert replayed.meta["broker_analysis"]["provider_operations"] == 0
    ordered = sorted(elapsed)
    return {"status": "ok", "mode": "fixture" if args.fixture else "live", "tools": len(tools),
            "requests": args.requests, "concurrency": args.concurrency, "errors": 0,
            "startup_seconds": startup, "median_seconds": statistics.median(elapsed),
            "p95_seconds": ordered[max(0, int(.95 * len(ordered) + .999999) - 1)], "max_seconds": max(elapsed),
            "provider_operations_during_replay": sum(item["provider_operations"] for item in observations),
            "snapshot_id": sid, "report_sha256": hashlib.sha256(json.dumps(technical, sort_keys=True).encode()).hexdigest(),
            "backup_restore_restart": "passed", "archive_path": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workdir", type=Path, help="new directory to retain the rehearsal archive")
    parser.add_argument("--command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10000 or not 1 <= args.concurrency <= 32:
        parser.error("requests must be 1..10000 and concurrency 1..32")
    if args.workdir:
        args.workdir.mkdir(parents=True, exist_ok=False)
        result = asyncio.run(exercise(args, args.workdir))
    else:
        with tempfile.TemporaryDirectory(prefix="broker-acceptance-") as folder:
            result = asyncio.run(exercise(args, Path(folder)))
        result.pop("archive_path", None)
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
