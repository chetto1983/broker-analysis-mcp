# Timestral-209M GGUF GPU pilot — 25 October 2026

The requested model ran successfully on the NVIDIA RTX A2000 Laptop GPU (4 GiB, driver 596.52)
through the conversion author's strict Vulkan runtime. This pilot did not establish an
improvement over the existing risk baselines. Timestral was subsequently removed at the user's
request: both CPU/GPU adapters and the local model/runtime downloads. This document is a
historical evaluation, not a supported installation guide.

## Reproduction and execution evidence

- Model: [EvoAwaken-Workshop/Timestral-209M-gguf](https://huggingface.co/EvoAwaken-Workshop/Timestral-209M-gguf),
  revision `96daea8a35f5b9c40afd91e5c5971e576367cc0d`.
- File: `Timestral-209M-F32.gguf`, 837,265,632 bytes, SHA-256
  `7628bcbfe43c8fec82bfbeace0ebbeb7dcd49facec6a785a51f430995d704e10`.
  This is F32, not a reduced-precision quantization.
- Runtime: [rust-model-inference](https://github.com/gouzil/rust-model-inference/tree/cc72a61fcd0c4314479291bd189555f5718ca85e),
  unmodified commit `cc72a61fcd0c4314479291bd189555f5718ca85e`, compiled with Rust 1.99.1,
  `cargo build --locked --profile release-fast --features vulkan --bin rust-model-inference`.
- Binary SHA-256: `e1d63617f2d4040e930823a7e7ba7002b9c34d2965c801caa46e990b679c1782`.
- 51 GPU forecasts: three numerical checks and 48 financial forecasts. Every GPU call logged
  the NVIDIA device and two Transformer executions of 252 GPU dispatches each (symmetric
  averaging). `--compute vulkan` was explicit. No CPU fallback counted as GPU success.
- The runtime also uses CPU work for preprocessing and output projections. GPU execution here
  means the Transformer uses Vulkan, not that every operation runs on GPU.

The former adapter and reproduction command are preserved at Git tag `v0.2.0`.
Results, per-origin losses, model hashes,
device names and per-call timings are in
[timestral_209m_gguf.json](../e2e/runs/timestral_209m_gguf.json). Requests, full forecasts, stderr
logs and price CSV snapshots from `.research/gguf-evaluation/` were removed during cleanup;
the report retains their relevant hashes. Runtime, compiler and model downloads were excluded
from Git and have also been removed locally.

## Numerical checks and runtime

Three 1,024-point inputs (trend plus seasonality, constant, small variance-like values), each
forecast 20 steps on both the Rust CPU and GPU paths. All 21 output quantiles were compared.
All three passed the model card's combined absolute/relative maximum-error and RMSE bounds.
The largest absolute difference was approximately `1e-5`; the constant case matched exactly.
This checks CPU/GPU agreement in the same runtime, not independent parity with PyTorch.

| Matched case | CPU seconds | GPU seconds |
| --- | ---: | ---: |
| Trend/seasonal | 1.210 | 25.683 |
| Constant | 1.421 | 21.351 |
| Variance-scale | 1.173 | 21.735 |

Matched-case median: CPU 1.21 seconds; GPU 21.74 seconds (about 18 times slower). Across all 51
GPU calls, median end-to-end latency was 19.23 seconds, total 1,007.95 seconds. These include
process startup, model loading, GPU initialization, preprocessing and output; there is no
persistent-runtime throughput measurement or repeated randomized CPU/GPU timing study.

One possible explanation for the poor discrete-GPU performance is visible in the pinned
runtime: Timestral uses `Qwen3Ops::new_with_size` with a mapped arena, and `UploadedBuffers`
uses `upload_static`, rather than the available device-local upload path. That is a hypothesis
from source inspection, not a measured causal diagnosis. No runtime optimization was applied
to this experiment.

## Financial pilot

Protocol recorded before inference: AAPL, ENI.MI and BTC-USD; eight origins per asset, 10-session
step over the latest 100 observations; 1,024 observations of context; price horizons of 5 and
20 sessions; a 20-session Garman–Klass variance target. Current UTC-date bars are excluded.
Historical adjusted Yahoo OHLC data is used. Quantile levels 0.1 through 0.9 and the existing
benchmark's median, pinball, coverage, QLIKE and loss-difference calculations are retained.
The volatility point forecast averages the nine selected quantiles across the 20 steps.

| 20-session metric | Timestral | Baseline |
| --- | ---: | ---: |
| Mean absolute log-price error, asset average | 0.078302 | Drift: 0.078453; random walk: 0.078439 |
| Mean pinball loss, asset average | 0.031396 | Historical simulation: 0.030501 |
| Coverage of nominal 80% interval | 62.5% | Historical simulation: 70.8% |
| Mean volatility QLIKE, asset average | 0.099283 | EWMA: 0.099306; 60-session mean: 0.075864 |

The averages are descriptive summaries across different calendars, not pooled statistical
tests. Per-asset volatility QLIKE loss-difference statistics against EWMA were +0.53 (AAPL),
-1.58 (ENI) and +0.13 (Bitcoin); negative favors Timestral. Bitcoin's 20-session price test
against drift was -2.84, but against random walk it was -0.05. With eight origins, overlapping
targets and many comparisons, isolated statistics do not establish a reliable advantage.
Training-data overlap is not established, and these are not post-release-only evaluation data.

Decision: remove Timestral execution support and retain the measured evidence. This finding
is limited to the pinned model, runtime, hardware, horizons and small sample tested here.
