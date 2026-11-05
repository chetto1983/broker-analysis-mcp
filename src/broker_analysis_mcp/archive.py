"""Verify, back up and merge-restore immutable records. Never deletes live records."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from . import audit
from .errors import DataError, error_payload

NAME = re.compile(r"[a-f0-9]{64}\.json")
MAX_OBJECT = 128 * 1024 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024


def _decode(name: str, raw: bytes) -> dict:
    if not NAME.fullmatch(name) or hashlib.sha256(raw).hexdigest() != name[:-5]:
        raise DataError("archive object name/hash is invalid", code="RECORD_INTEGRITY")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or audit.canonical(value) != raw:
            raise ValueError("not canonical JSON")
    except (TypeError, ValueError):
        raise DataError("archive object must contain canonical JSON", code="RECORD_INTEGRITY") from None
    return value


def _links(value: dict) -> list[str]:
    if value.get("kind") != "portfolio_analysis":
        return []
    try:
        links = [value["snapshot"], value["output"], *value["reports"].values()]
        if not all(isinstance(link, str) and NAME.fullmatch(f"{link}.json") for link in links):
            raise ValueError()
        return links
    except (KeyError, AttributeError, TypeError, ValueError):
        raise DataError("archive portfolio manifest is invalid", code="RECORD_INTEGRITY") from None


def _inventory() -> dict[str, Path]:
    return {path.name: path for path in sorted(audit.root().glob("*.json"))}


def verify() -> dict:
    paths = _inventory()
    size = 0
    for name, path in paths.items():
        raw = path.read_bytes()
        value = _decode(name, raw)
        size += len(raw)
        for link in _links(value):
            if f"{link}.json" not in paths:
                raise DataError("archive manifest references a missing object", code="RECORD_NOT_FOUND")
    return {"status": "ok", "records": len(paths), "bytes": size,
            "pending_files": len(list(audit.root().glob(".pending-*")))}


def backup(destination: Path) -> dict:
    paths = _inventory()
    destination = Path(destination)
    if destination.exists():
        raise DataError("backup destination already exists", code="INVALID_INPUT", field="destination")
    sizes = [path.stat().st_size for path in paths.values()]
    if any(size > MAX_OBJECT for size in sizes) or sum(sizes) > MAX_ARCHIVE:
        raise DataError("archive size exceeds the restore limits", code="INVALID_ARCHIVE")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".broker-backup-", dir=destination.parent)
        with os.fdopen(fd, "w+b") as stream:
            with ZipFile(stream, "w") as zipped:
                for name, path in paths.items():
                    raw = path.read_bytes()
                    if len(raw) > MAX_OBJECT:
                        raise DataError("object size exceeds the restore limit", code="INVALID_ARCHIVE")
                    value = _decode(name, raw)
                    if any(f"{link}.json" not in paths for link in _links(value)):
                        raise DataError("backup inventory changed; retry the backup", code="ARCHIVE_BUSY", retryable=True)
                    zipped.writestr(name, raw)
            stream.flush()
            os.fsync(stream.fileno())
        # link is an atomic no-overwrite publication, including a racing backup writer.
        os.link(temporary, destination)
        audit.sync_directory(destination.parent)
        with destination.open("rb") as saved:
            digest = hashlib.file_digest(saved, "sha256").hexdigest()
        return {"status": "ok", "records": len(paths), "sha256": digest, "bytes": destination.stat().st_size}
    except OSError as error:
        raise audit.storage_failure(error) from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def restore(source: Path) -> dict:
    try:
        with ZipFile(source) as zipped:
            members = zipped.infolist()
            names = [member.filename for member in members]
            by_name = {member.filename: member for member in members}
            if (len(set(names)) != len(names) or any(not NAME.fullmatch(name) for name in names)
                    or any(member.file_size > MAX_OBJECT or member.flag_bits & 1 for member in members)
                    or sum(member.file_size for member in members) > MAX_ARCHIVE):
                raise DataError("archive contains unsafe, duplicate or oversized objects", code="INVALID_ARCHIVE")
            # All source data and existing targets are verified before the first new write.
            dependencies = {}
            for member in members:
                value = _decode(member.filename, zipped.read(member))
                links = _links(value)
                dependencies[member.filename] = {f"{link}.json" for link in links if f"{link}.json" in by_name}
                for link in links:
                    if f"{link}.json" not in by_name:
                        audit.read(link)
                if (audit.root() / member.filename).exists():
                    audit.read(member.filename[:-5])
            added = 0
            order = tuple(TopologicalSorter(dependencies).static_order())
            for name in order:
                member = by_name[name]
                value = _decode(member.filename, zipped.read(member))
                existed = (audit.root() / member.filename).exists()
                if audit.put(value) != member.filename[:-5]:
                    raise DataError("restored object identity differs", code="RECORD_INTEGRITY")
                added += not existed
            return {"status": "ok", "records": len(members), "added": added}
    except (BadZipFile, RuntimeError):
        raise DataError("cannot read archive ZIP", code="INVALID_ARCHIVE") from None
    except CycleError:
        raise DataError("archive manifests have cyclic references", code="INVALID_ARCHIVE") from None
    except OSError as error:
        raise audit.storage_failure(error) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    for name in ("backup", "restore"):
        commands.add_parser(name).add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify() if args.command == "verify" else backup(args.path) if args.command == "backup" else restore(args.path)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"error": error_payload(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
