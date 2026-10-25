"""Append-only, content-addressed JSON records; hashes detect modification, not deletion.

Objects are published with an atomic hard link after fsync. Existing objects are verified,
never overwritten. This is application immutability, not an external WORM storage guarantee.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .data import DataError


def root() -> Path:
    return Path(os.environ.get("BROKER_ANALYSIS_HOME", Path.home() / ".broker-analysis")) / "records"


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read(object_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", object_id):
        raise DataError("record id must be a SHA-256 hex digest")
    try:
        raw = (root() / f"{object_id}.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != object_id:
            raise DataError("record integrity check failed")
        return json.loads(raw)
    except (OSError, ValueError) as err:
        raise DataError(f"cannot read verified record: {err}") from err


def put(value: dict) -> str:
    raw = canonical(value)
    object_id = hashlib.sha256(raw).hexdigest()
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, folder / f"{object_id}.json")
        except FileExistsError:
            read(object_id)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return object_id


def save(snapshot: dict, output: dict, reports: dict) -> dict:
    """Publish the manifest last; a returned manifest always references complete objects."""
    try:
        manifest = {
            "schema_version": 1,
            "kind": "portfolio_analysis",
            "snapshot": put(snapshot),
            "output": put(output),
            "reports": {name: put(report) for name, report in reports.items()},
        }
        return {"record_id": put(manifest), "output_hash": manifest["output"],
                "snapshot_hash": manifest["snapshot"], "reports": manifest["reports"]}
    except OSError as err:
        raise DataError(f"analysis could not be recorded: {err}") from err


def record(record_id: str, section: str = "output") -> dict:
    manifest = read(record_id)
    if manifest.get("kind") != "portfolio_analysis":
        raise DataError("id is not a portfolio analysis manifest")
    # Check all linked objects, even when the caller requests only one report.
    objects = {key: read(manifest[key]) for key in ("snapshot", "output")}
    objects.update({key: read(value) for key, value in manifest["reports"].items()})
    if section == "manifest":
        return manifest
    if section not in objects:
        raise DataError("unknown analysis section")
    return objects[section]
