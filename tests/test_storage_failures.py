"""Disk errors and concurrent configuration writes preserve usable committed state."""

import errno
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_hooks import Market

from broker_analysis_mcp import audit, hooks
from broker_analysis_mcp.errors import DataError
from broker_analysis_mcp.models import ConditionSpec, HookAction


def test_disk_full_is_path_free_and_does_not_publish_a_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "private-path"))
    original = audit.put({"original": 1})
    raw = (audit.root() / f"{original}.json").read_bytes()
    def full(*args):
        raise OSError(errno.ENOSPC, f"disk full: {tmp_path}/private-path")
    monkeypatch.setattr(audit.os, "link", full)
    with pytest.raises(DataError) as error:
        audit.save({"new": 1}, {"result": 2}, {})
    assert error.value.code == "STORAGE_FULL"
    assert str(tmp_path) not in str(error.value)
    assert (audit.root() / f"{original}.json").read_bytes() == raw
    assert len(list(audit.root().glob("*.json"))) == 1
    assert not list(audit.root().glob(".pending-*"))


def test_fsync_failure_does_not_publish_an_object(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    def failed(*args):
        raise OSError(errno.EIO, "private device")
    monkeypatch.setattr(audit.os, "fsync", failed)
    with pytest.raises(DataError):
        audit.put({"test": 1})
    assert not list(audit.root().glob("*.json"))
    assert not list(audit.root().glob(".pending-*"))


def test_concurrent_hook_creations_preserve_every_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    def create(index):
        return hooks.create(f"hook{index}", "A", ConditionSpec(name="positive", when="close > 0"),
                            HookAction(type="file", target="alerts"), [], [], loader=Market([10, 11, 12]))
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(create, range(16)))
    assert set(hooks.load_hooks()) == {item["hook"]["id"] for item in created}


def test_process_death_before_hook_publication_preserves_state_and_allows_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path))
    hooks.create("original", "A", ConditionSpec(name="positive", when="close > 0"),
                 HookAction(type="file", target="alerts"), [], [], loader=Market([10, 11, 12]))
    original = (tmp_path / "hooks.json").read_bytes()
    code = ("import os; from broker_analysis_mcp import audit, hooks; "
            "os.fsync=lambda fd: os._exit(73); "
            "audit.write_state(hooks.home() / 'hooks.json', {'unfinished': True})")
    child = subprocess.run([sys.executable, "-c", code], env=dict(os.environ), capture_output=True, timeout=30)
    assert child.returncode == 73, child.stderr
    assert (tmp_path / "hooks.json").read_bytes() == original
    hooks.create("recovered", "A", ConditionSpec(name="positive", when="close > 0"),
                 HookAction(type="file", target="alerts"), [], [], loader=Market([10, 11, 12]))
    assert {value["name"] for value in hooks.load_hooks().values()} == {"original", "recovered"}
