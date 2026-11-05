"""Append-only, content-addressed JSON records; hashes detect modification, not deletion.

Objects are published with an atomic hard link after fsync. Existing objects are verified,
never overwritten. This is application immutability, not an external WORM storage guarantee.
"""

import errno
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .errors import DataError


def root() -> Path:
    return Path(os.environ.get("BROKER_ANALYSIS_HOME") or os.environ.get("PLUGIN_DATA") or Path.home() / ".broker-analysis") / "records"


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def storage_failure(error: OSError) -> DataError:
    code = "STORAGE_FULL" if error.errno == errno.ENOSPC else "STORAGE_UNAVAILABLE"
    return DataError("archive storage is full" if code == "STORAGE_FULL" else "archive storage is unavailable",
                     code=code, retryable=code != "STORAGE_FULL")


def sync_directory(folder: Path) -> None:
    if os.name == "posix":
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_state(path: Path, value: dict) -> None:
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    except OSError as error:
        raise storage_failure(error) from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def read(object_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", object_id):
        raise DataError("record id must be a SHA-256 hex digest")
    try:
        raw = (root() / f"{object_id}.json").read_bytes()
    except FileNotFoundError:
        raise DataError("cannot read verified record: not found; check the snapshot or record id",
                        code="RECORD_NOT_FOUND", field="record_id") from None
    except OSError:
        raise DataError("cannot read verified record") from None
    if hashlib.sha256(raw).hexdigest() != object_id:
        raise DataError("record integrity check failed", code="RECORD_INTEGRITY")
    try:
        return json.loads(raw)
    except ValueError:
        raise DataError("cannot read verified record: invalid JSON") from None


def put(value: dict) -> str:
    raw = canonical(value)
    object_id = hashlib.sha256(raw).hexdigest()
    folder = root()
    temporary = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=folder)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, folder / f"{object_id}.json")
        except FileExistsError:
            read(object_id)
        sync_directory(folder)
    except OSError as error:
        raise storage_failure(error) from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return object_id


def save(snapshot: dict, output: dict, reports: dict) -> dict:
    """Publish the manifest last; a returned manifest always references complete objects."""
    manifest = {
        "schema_version": 1,
        "kind": "portfolio_analysis",
        "snapshot": put(snapshot),
        "output": put(output),
        "reports": {name: put(report) for name, report in reports.items()},
    }
    return {"record_id": put(manifest), "output_hash": manifest["output"],
            "snapshot_hash": manifest["snapshot"], "reports": manifest["reports"]}


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
