"""Reproducible strict-Vulkan pilot for EvoAwaken's lossless Timestral-209M GGUF.

Run as `python -m e2e.timestral_gguf_eval --binary PATH --model PATH --out PATH`.
Uses the existing TSFM benchmark losses and baselines, with a declared smaller default pilot
window (100 sessions, step 10). This is exploratory evidence, not model approval.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from e2e import tsfm_eval as benchmark

MODEL_SHA256 = "7628bcbfe43c8fec82bfbeace0ebbeb7dcd49facec6a785a51f430995d704e10"
MODEL_REVISION = "96daea8a35f5b9c40afd91e5c5971e576367cc0d"
RUNTIME_REVISION = "cc72a61fcd0c4314479291bd189555f5718ca85e"


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class GGUF(benchmark.Univariate):
    def __init__(self, binary: Path, model: Path, artifacts: Path):
        self.binary, self.model, self.artifacts = binary.resolve(), model.resolve(), artifacts.resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict] = []

    def invoke(self, context: np.ndarray, horizon: int, compute: str = "vulkan") -> tuple[np.ndarray, list[float]]:
        request = {"context": context.tolist(), "prediction_length": horizon,
                   "symmetric_averaging": True, "clip_nonnegative": True}
        raw = json.dumps(request, allow_nan=False).encode()
        key = hashlib.sha256(raw + compute.encode()).hexdigest()
        input_path = self.artifacts / f"{key}.request.json"
        input_path.write_bytes(raw)
        started = time.perf_counter()
        result = subprocess.run(
            [str(self.binary), "--model", str(self.model), "--timestral-request", str(input_path),
             "--compute", compute, "--threads", "4"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            env=os.environ | {"RMI_COMPUTE_TRACE": "1"}, check=False,
        )
        seconds = time.perf_counter() - started
        (self.artifacts / f"{key}.stdout.json").write_text(result.stdout, encoding="utf-8")
        (self.artifacts / f"{key}.stderr.log").write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{compute} runtime exited {result.returncode}: {result.stderr[-3000:]}")
        dispatches = [int(n) for n in re.findall(r"scope=timestral.transformer gpu_dispatches=(\d+)", result.stderr)]
        devices = re.findall(r"\[GPU\] Vulkan device: (.+?) \(int8", result.stderr)
        if compute == "vulkan" and (not dispatches or min(dispatches) < 252):
            raise RuntimeError("No sufficient Transformer GPU dispatch evidence; refusing to count CPU fallback")
        output = json.loads(result.stdout)
        values = np.asarray(output["quantiles"], dtype=float)
        levels = output["quantile_levels"]
        if values.shape != (1, horizon, 21) or not np.isfinite(values).all():
            raise RuntimeError(f"Invalid forecast shape/values: {values.shape}")
        if (np.diff(values, axis=-1) < -1e-7).any():
            raise RuntimeError("Crossed forecast quantiles")
        self.calls.append({"key": key, "compute": compute, "seconds": seconds,
                           "devices": devices,
                           "gpu_dispatches": dispatches, "context": len(context), "horizon": horizon,
                           "output_sha256": file_hash(self.artifacts / f"{key}.stdout.json"),
                           "stderr_sha256": file_hash(self.artifacts / f"{key}.stderr.log")})
        print(f"{compute} call {len(self.calls)}: {seconds:.2f}s; dispatches={dispatches}", flush=True)
        return values[0], levels

    def quantiles(self, contexts: np.ndarray, horizon: int) -> np.ndarray:
        predictions = []
        for context in contexts:
            values, levels = self.invoke(context, horizon)
            predictions.append(values[:, [levels.index(q) for q in benchmark.LEVELS]])
        return np.stack(predictions)


def parity(model: GGUF) -> list[dict]:
    t = np.arange(1024)
    cases = {"trend_seasonal": 100 + .01 * t + np.sin(t / 12),
             "constant": np.full(1024, 100.), "variance_scale": .0001 * (1.1 + np.sin(t / 12))}
    results = []
    for name, context in cases.items():
        cpu, _ = model.invoke(context, 20, "cpu")
        gpu, _ = model.invoke(context, 20, "vulkan")
        max_error = float(np.max(np.abs(cpu - gpu)))
        rmse = float(np.sqrt(np.mean((cpu - gpu) ** 2)))
        maximum_bound = float(3e-5 + 3e-4 * np.max(np.abs(cpu)))
        rmse_bound = float(3e-5 + 5e-5 * np.sqrt(np.mean(cpu ** 2)))
        passed = max_error <= maximum_bound and rmse <= rmse_bound
        results.append({"case": name, "max_abs_error": max_error, "rmse": rmse,
                        "max_abs_bound": maximum_bound, "rmse_bound": rmse_bound, "passed": passed})
        if not passed:
            raise RuntimeError(f"CPU/GPU numerical parity failed: {results[-1]}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, default=Path(".research/gguf-evaluation"))
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "ENI.MI", "BTC-USD"])
    parser.add_argument("--span", type=int, default=100)
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    if args.span < 40:
        parser.error("span must be at least 40")
    if file_hash(args.model) != MODEL_SHA256:
        raise RuntimeError("Model checksum differs from the requested pinned conversion")
    model = GGUF(args.binary, args.model, args.artifacts)
    benchmark.SPAN = args.span
    report = {"created_at": datetime.now(UTC).isoformat(), "model_repository": "EvoAwaken-Workshop/Timestral-209M-gguf",
              "model_revision": MODEL_REVISION, "model_sha256": MODEL_SHA256,
              "documented_runtime_revision": RUNTIME_REVISION, "binary_sha256": file_hash(args.binary),
              "protocol": {"context": benchmark.CONTEXT, "span": args.span, "step": benchmark.STEP,
                           "horizons": benchmark.HORIZONS, "volatility_horizon": benchmark.VOL_H,
                           "tickers": args.tickers, "compute": "strict Vulkan", "threads": 4,
                           "promotion": "none; exploratory pilot; training-data overlap is not established"},
              "complete": False, "parity": [], "per_ticker": {}, "calls": model.calls}
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")

    save()  # Protocol persisted before inference or seeing benchmark results.
    try:
        report["parity"] = parity(model)
        save()
        if not args.smoke_only:
            for ticker in args.tickers:
                df = benchmark.load(ticker)
                cutoff = pd.Timestamp(datetime.now(UTC).date())
                dates = df.index.tz_localize(None)
                df = df.loc[dates < cutoff]
                if len(df) < benchmark.CONTEXT + args.span:
                    raise RuntimeError(f"Insufficient history for {ticker}")
                destination = model.artifacts / f"prices-{hashlib.sha256(ticker.encode()).hexdigest()[:12]}.csv"
                df.to_csv(destination)
                result = benchmark.evaluate(model, df)
                report["per_ticker"][ticker] = result | {"data_sha256": file_hash(destination)}
                save()
            report["mean"] = benchmark.summarise(report["per_ticker"])
            report["diebold_mariano"] = benchmark.all_tests(report["per_ticker"])
        report["complete"] = True
        report["verdict"] = "GPU execution verified; market results are exploratory, not production validation."
    except Exception as err:
        report["error"] = f"{type(err).__name__}: {err}"
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
