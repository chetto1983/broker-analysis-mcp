"""Backup/restore rejects damaged or unsafe input before publishing any objects."""

import importlib
import json
import os
import subprocess
import sys
from zipfile import ZipFile

import pytest

from broker_analysis_mcp import audit
from broker_analysis_mcp.errors import DataError


def test_archive_cli_verifies_without_creating_a_store(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "absent"))
    result = subprocess.run([sys.executable, "-m", "broker_analysis_mcp.archive", "verify"],
                            env=dict(os.environ), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["records"] == 0
    assert not (tmp_path / "absent").exists()


def test_archive_roundtrip_restores_exact_ids_and_manifest(monkeypatch, tmp_path):
    archive = importlib.import_module("broker_analysis_mcp.archive")
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "source"))
    saved = audit.save({"inputs": [1, 2]}, {"metric": 3}, {"validation": {"ok": True}})
    before = {path.name: path.read_bytes() for path in audit.root().glob("*.json")}
    backup = tmp_path / "backup.zip"
    assert archive.backup(backup)["records"] == len(before)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "restored"))
    assert archive.restore(backup)["records"] == len(before)
    assert {path.name: path.read_bytes() for path in audit.root().glob("*.json")} == before
    assert audit.record(saved["record_id"]) == {"metric": 3}
    assert archive.restore(backup)["added"] == 0


@pytest.mark.parametrize("bad", ["traversal", "duplicate", "hash", "missing_link"])
def test_invalid_archive_is_prevalidated_before_any_publication(monkeypatch, tmp_path, bad):
    archive = importlib.import_module("broker_analysis_mcp.archive")
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "source"))
    good = {"good": 1}
    key = audit.digest(good)
    backup = tmp_path / "bad.zip"
    with ZipFile(backup, "w") as zipped:
        zipped.writestr(f"{key}.json", audit.canonical(good))
        if bad == "traversal":
            zipped.writestr("../outside.json", b"{}")
        elif bad == "duplicate":
            with pytest.warns(UserWarning):
                zipped.writestr(f"{key}.json", audit.canonical(good))
        elif bad == "hash":
            zipped.writestr(f"{'0' * 64}.json", b"{}")
        else:
            manifest = {"kind": "portfolio_analysis", "snapshot": "0" * 64, "output": key, "reports": {}}
            zipped.writestr(f"{audit.digest(manifest)}.json", audit.canonical(manifest))
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "target"))
    with pytest.raises(DataError):
        archive.restore(backup)
    assert not list(audit.root().glob("*.json"))
    assert not (tmp_path / "outside.json").exists()


def test_corrupt_existing_target_prevents_all_restore_writes(monkeypatch, tmp_path):
    archive = importlib.import_module("broker_analysis_mcp.archive")
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "source"))
    existing = audit.put({"existing": 1})
    new = audit.put({"new": 2})
    backup = tmp_path / "backup.zip"
    archive.backup(backup)
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "target"))
    audit.root().mkdir(parents=True)
    target = audit.root() / f"{existing}.json"
    target.write_bytes(b"corrupt")
    with pytest.raises(DataError):
        archive.restore(backup)
    assert target.read_bytes() == b"corrupt" and not (audit.root() / f"{new}.json").exists()


def test_backup_refuses_objects_its_restore_limit_would_reject(monkeypatch, tmp_path):
    archive = importlib.import_module("broker_analysis_mcp.archive")
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "source"))
    audit.put({"oversized": "payload"})
    monkeypatch.setattr(archive, "MAX_OBJECT", 8)
    with pytest.raises(DataError, match="size"):
        archive.backup(tmp_path / "backup.zip")
    assert not (tmp_path / "backup.zip").exists()


def test_restore_publishes_dependencies_before_their_manifest(monkeypatch, tmp_path):
    archive = importlib.import_module("broker_analysis_mcp.archive")
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "source"))
    saved = audit.save({"input": 1}, {"output": 2}, {"validation": {"ok": True}})
    manifest = audit.root() / f"{saved['record_id']}.json"
    backup = tmp_path / "manifest-first.zip"
    with ZipFile(backup, "w") as zipped:
        for path in [manifest, *(path for path in audit.root().glob("*.json") if path != manifest)]:
            zipped.writestr(path.name, path.read_bytes())
    monkeypatch.setenv("BROKER_ANALYSIS_HOME", str(tmp_path / "restored"))
    publish = audit.put
    def observed(value):
        if value.get("kind") == "portfolio_analysis":
            assert audit.read(value["snapshot"]) == {"input": 1}
            assert audit.read(value["output"]) == {"output": 2}
            assert audit.read(value["reports"]["validation"]) == {"ok": True}
        return publish(value)
    monkeypatch.setattr(audit, "put", observed)
    archive.restore(backup)
    assert audit.record(saved["record_id"]) == {"output": 2}
