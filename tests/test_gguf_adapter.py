"""The experimental GPU harness rejects fallback and malformed model outputs."""

import json
import subprocess

import numpy as np
import pytest

from e2e.timestral_gguf_eval import GGUF


@pytest.mark.parametrize("stderr,shape,expected_error", [
    ("compute backend=Cpu", (1, 20, 21), "GPU dispatch"),
    ("scope=timestral.transformer gpu_dispatches=251", (1, 20, 21), "GPU dispatch"),
    ("scope=timestral.transformer gpu_dispatches=252", (1, 20, 20), "shape"),
])
def test_rejects_false_gpu_success(monkeypatch, tmp_path, stderr, shape, expected_error):
    payload = {"quantiles": np.ones(shape).tolist(), "quantile_levels": [i / 20 for i in range(21)]}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(payload), stderr))
    adapter = GGUF(tmp_path / "runtime.exe", tmp_path / "model.gguf", tmp_path / "artifacts")
    with pytest.raises(RuntimeError, match=expected_error):
        adapter.invoke(np.ones(100), 20)
    assert not adapter.calls


def test_valid_quantiles_preserve_horizon_and_record_evidence(monkeypatch, tmp_path):
    payload = {"quantiles": np.tile(np.arange(21), (1, 20, 1)).tolist(),
               "quantile_levels": [.01, .05, .1, .15, .2, .25, .3, .35, .4, .45, .5, .55, .6, .65, .7, .75, .8, .85, .9, .95, .99]}

    def run(args, **kwargs):
        assert args[args.index("--compute") + 1] == "vulkan"
        assert kwargs["env"]["RMI_COMPUTE_TRACE"] == "1"
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "scope=timestral.transformer gpu_dispatches=252")

    monkeypatch.setattr(subprocess, "run", run)
    adapter = GGUF(tmp_path / "runtime.exe", tmp_path / "model.gguf", tmp_path / "artifacts")
    values = adapter.quantiles(np.ones((2, 100)), 20)
    assert values.shape == (2, 20, 9)
    assert values[0, 0, 4] == 10  # Median from the 21-quantile model, not an assumed array midpoint after selection.
    assert len(adapter.calls) == 2
    assert all(call["gpu_dispatches"] == [252] and len(call["output_sha256"]) == 64 for call in adapter.calls)
