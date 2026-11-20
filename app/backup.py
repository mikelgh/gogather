import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone


class BackupError(Exception):
    pass


REASONS = (
    "hourly", "pre-delete", "pre-update", "pre-deploy", "pre-start",
    "pre-bulk", "manual",
)
LOCK_TIMEOUT = 30.0
SQLITE_TIMEOUT = 30.0
BACKUP_TIMEOUT = 120.0
SPACE_RESERVE = 64 * 1024 * 1024
SNAPSHOT_FILE = "snapshot.sqlite3"
MANIFEST_FILE = "manifest.json"
_NAME = re.compile(
    r"sqlite-backup-v1-(\d{8}T\d{12}Z)-("
    + "|".join(REASONS) + r")-([0-9a-f]{32})\Z"
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = {
    "version", "snapshot", "created_at", "reason", "sha256", "size_bytes",
    "table_counts", "foreign_key_violations", "schema_sha256",
}
_JSON_COLUMNS = {
    "plans": {
        "params": dict, "conversation": list, "timeline": list,
        "budget": list, "action_items": list, "room_allocation": list,
        "members": list, "warnings": list,
    },
    "plan_revisions": {
        "params": dict, "timeline": list, "budget": list,
        "action_items": list, "room_allocation": list,
    },
    "conversations": {"meta": dict},
}


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_time(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("invalid timestamp")
    return result


def _root(db_path, backup_dir):
    return Path(backup_dir).absolute() if backup_dir is not None else Path(db_path).absolute().parent / "backups"


def _prepare_root(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise BackupError("unsafe-backup-directory")
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise BackupError("backup-directory-must-be-private")


def _regular(path):
    return stat.S_ISREG(path.lstat().st_mode)


def _secure_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path, data):
    temporary = path.parent / (".sqlite-backup-" + uuid.uuid4().hex + ".partial")
    try:
        with _secure_file(temporary) as stream:
            stream.write(json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("invalid JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BackupError("invalid-metadata")
        text = stream.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise BackupError("invalid-metadata")
        return _strict_json(text)


def _error(exc):
    if isinstance(exc, BackupError):
        return exc
    if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return BackupError("insufficient-space")
    if isinstance(exc, sqlite3.Error):
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            return BackupError("insufficient-space")
        return BackupError("sqlite-operation-failed")
    return BackupError("backup-operation-failed")


def _record(root, kind, success, error=None):
    path = root / ("last-" + kind + ".json")
    previous = None
    try:
        old = _read_json(path)
        previous = _timestamp(_parse_time(old["last_success_at"]))
    except (OSError, ValueError, KeyError, TypeError, BackupError):
        pass
    now = _timestamp(_now())
    _atomic_json(path, {
        "version": 1, "success": success, "last_attempt_at": now,
        "last_success_at": now if success else previous,
        "error": error,
    })


def _record_failure(root, kind, exc):
    try:
        _prepare_root(root)
        _record(root, kind, False, str(_error(exc)))
    except (OSError, ValueError, BackupError):
        pass


@contextmanager
def _locked(root):
    _prepare_root(root)
    fd = os.open(root / ".sqlite-backup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BackupError("unsafe-lock-file")
        os.fchmod(fd, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BackupError("backup-lock-timeout")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def _connect(path, mode="ro", immutable=False):
    uri = Path(path).absolute().as_uri() + "?mode=" + mode
    if immutable:
        uri += "&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT, isolation_level=None)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        if mode == "ro":
            connection.execute("PRAGMA query_only=ON")
        return connection
    except BaseException:
        connection.close()
        raise


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _inspect(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("integrity-check-failed")
    schema = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name, tbl_name"
    ).fetchall()
    schema_hash = hashlib.sha256(json.dumps(schema, ensure_ascii=True).encode("utf-8")).hexdigest()
    counts = {}
    for kind, name, table, sql in schema:
        if kind == "table" and not name.startswith("sqlite_"):
            counts[name] = connection.execute("SELECT count(*) FROM " + _quote(name)).fetchone()[0]
    violations = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
    return counts, violations, schema_hash


def _digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BackupError("invalid-snapshot")
        digest = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
        return digest.hexdigest()


def _check_space(root, size):
    if shutil.disk_usage(root).free < size + SPACE_RESERVE:
        raise BackupError("insufficient-space")


def _snapshot(db_path, root, reason):
    if reason not in REASONS:
        raise BackupError("invalid-reason")
    now = _now()
    name = "sqlite-backup-v1-" + now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + reason + "-" + uuid.uuid4().hex
    temporary = root / ("." + name + ".partial")
    final = root / name
    try:
        with closing(_connect(db_path)) as source:
            page_count = source.execute("PRAGMA page_count").fetchone()[0]
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            if page_count == 0:
                raise BackupError("empty-database")
            _check_space(root, page_count * page_size)
            temporary.mkdir(mode=0o700)
            destination = temporary / SNAPSHOT_FILE
            with _secure_file(destination):
                pass
            with closing(sqlite3.connect(destination, isolation_level=None, timeout=SQLITE_TIMEOUT)) as target:
                target.execute("PRAGMA trusted_schema=OFF")
                deadline = time.monotonic() + BACKUP_TIMEOUT

                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise BackupError("sqlite-backup-timeout")

                source.backup(target, pages=256, progress=progress, sleep=0.05)
                target.execute("PRAGMA journal_mode=DELETE")
                counts, violations, schema_hash = _inspect(target)
            with destination.open("rb") as stream:
                os.fsync(stream.fileno())
            manifest = {
                "version": 1, "snapshot": name, "created_at": _timestamp(now),
                "reason": reason, "sha256": _digest(destination),
                "size_bytes": destination.stat().st_size, "table_counts": counts,
                "foreign_key_violations": violations, "schema_sha256": schema_hash,
            }
            _atomic_json(temporary / MANIFEST_FILE, manifest)
            _sync_dir(temporary)
            os.rename(temporary, final)
            _sync_dir(root)
        return final
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _manifest(path):
    try:
        match = _NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_dir():
            raise ValueError("invalid snapshot directory")
        if {p.name for p in path.iterdir()} != {SNAPSHOT_FILE, MANIFEST_FILE}:
            raise ValueError("invalid snapshot contents")
        data = _read_json(path / MANIFEST_FILE)
        if not isinstance(data, dict) or set(data) != _MANIFEST_KEYS:
            raise ValueError("invalid manifest fields")
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("invalid manifest version")
        if data["snapshot"] != path.name or data["reason"] != match[2]:
            raise ValueError("invalid manifest identity")
        created = _parse_time(data["created_at"])
        if created.strftime("%Y%m%dT%H%M%S%fZ") != match[1] or data["created_at"] != _timestamp(created):
            raise ValueError("invalid manifest timestamp")
        for key in ("sha256", "schema_sha256"):
            if not isinstance(data[key], str) or not _HASH.fullmatch(data[key]):
                raise ValueError("invalid manifest hash")
        for key in ("size_bytes", "foreign_key_violations"):
            if type(data[key]) is not int or data[key] < 0:
                raise ValueError("invalid manifest number")
        counts = data["table_counts"]
        if not isinstance(counts, dict) or any(
            not isinstance(k, str) or not k or type(v) is not int or v < 0
            for k, v in counts.items()
        ):
            raise ValueError("invalid table counts")
        database = path / SNAPSHOT_FILE
        if not _regular(database) or data["size_bytes"] == 0 or database.stat().st_size != data["size_bytes"]:
            raise ValueError("invalid snapshot length")
        return data
    except (OSError, ValueError, KeyError, TypeError, BackupError):
        raise BackupError("invalid-snapshot-manifest") from None


def _retained(entries, now, newest=None):
    ordered = sorted(entries, key=lambda item: (item[1]["created_at"], item[0] == newest, item[0].name), reverse=True)
    hourly = [(path, data) for path, data in ordered if data["reason"] == "hourly"]
    keep = set()
    for fmt, limit in (("%Y%m%d%H", 48), ("%Y%m%d", 30), ("%Y%m", 12)):
        buckets = set()
        for path, data in hourly:
            bucket = _parse_time(data["created_at"]).strftime(fmt)
            if bucket not in buckets:
                if len(buckets) == limit:
                    break
                buckets.add(bucket)
                keep.add(path)
    events = [(path, data) for path, data in ordered if data["reason"] != "hourly"]
    if events:
        keep.add(events[0][0])
        keep.update(path for path, data in events[:100] if _parse_time(data["created_at"]) >= now - timedelta(days=90))
    return keep


def _prune(root, newest=None):
    entries = []
    for path in root.iterdir():
        if _NAME.fullmatch(path.name):
            try:
                data = _manifest(path)
                if _digest(path / SNAPSHOT_FILE) == data["sha256"]:
                    entries.append((path, data))
            except (BackupError, OSError):
                continue
    keep = _retained(entries, _now(), newest)
    for path, data in entries:
        if path not in keep:
            shutil.rmtree(path)
    _sync_dir(root)


def create_backup(db_path, reason="hourly", backup_dir=None) -> Path:
    root = _root(db_path, backup_dir)
    recorded_failure = False
    try:
        with _locked(root):
            try:
                result = _snapshot(db_path, root, reason)
                _record(root, "backup", True)
                _prune(root, result)
                return result
            except Exception as exc:
                _record_failure(root, "backup", exc)
                recorded_failure = True
                raise _error(exc) from None
    except Exception as exc:
        if not recorded_failure:
            _record_failure(root, "backup", exc)
        raise _error(exc) from None


@contextmanager
def _guarded_connection(db_path, reason, backup_dir=None):
    connection = None
    try:
        try:
            if reason not in REASONS:
                raise BackupError("invalid-reason")
            connection = _connect(db_path, "rw")
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
        except Exception as exc:
            _record_failure(_root(db_path, backup_dir), "backup", exc)
            raise _error(exc) from None
        create_backup(db_path, reason, backup_dir)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        if connection is not None:
            connection.close()


def guarded_connection(db_path, reason):
    return _guarded_connection(db_path, reason)


def _validate_json(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    for table, wanted in _JSON_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row[1] for row in connection.execute("PRAGMA table_info(" + _quote(table) + ")")}
        for column, expected in wanted.items():
            if column not in columns:
                continue
            for (value,) in connection.execute("SELECT " + _quote(column) + " FROM " + _quote(table)):
                if value is None:
                    continue
                try:
                    decoded = _strict_json(value)
                    if not isinstance(decoded, expected):
                        raise ValueError("invalid JSON type")
                except (ValueError, TypeError, RecursionError):
                    raise BackupError("invalid-json-field") from None


def drill(db_path, backup_dir=None):
    root = _root(db_path, backup_dir)
    recorded_failure = False
    try:
        with _locked(root):
            try:
                snapshots = sorted(p for p in root.iterdir() if _NAME.fullmatch(p.name))
                if not snapshots:
                    raise BackupError("no-snapshots")
                for path in snapshots:
                    manifest = _manifest(path)
                    _check_space(root, manifest["size_bytes"])
                    with tempfile.TemporaryDirectory(prefix=".sqlite-drill-", dir=root) as temporary:
                        restored = Path(temporary) / SNAPSHOT_FILE
                        with _secure_file(restored) as target:
                            fd = os.open(path / SNAPSHOT_FILE, os.O_RDONLY | os.O_NOFOLLOW)
                            with os.fdopen(fd, "rb") as source:
                                shutil.copyfileobj(source, target)
                            target.flush()
                            os.fsync(target.fileno())
                        if restored.stat().st_size != manifest["size_bytes"] or _digest(restored) != manifest["sha256"]:
                            raise BackupError("snapshot-hash-mismatch")
                        with closing(_connect(restored, immutable=True)) as connection:
                            counts, violations, schema_hash = _inspect(connection)
                            if counts != manifest["table_counts"] or schema_hash != manifest["schema_sha256"]:
                                raise BackupError("snapshot-schema-or-count-mismatch")
                            if violations > manifest["foreign_key_violations"]:
                                raise BackupError("foreign-key-violations-increased")
                            if violations != manifest["foreign_key_violations"]:
                                raise BackupError("foreign-key-baseline-mismatch")
                            _validate_json(connection)
                _record(root, "drill", True)
                return {"success": True, "snapshots_checked": len(snapshots)}
            except Exception as exc:
                _record_failure(root, "drill", exc)
                recorded_failure = True
                raise _error(exc) from None
    except Exception as exc:
        if not recorded_failure:
            _record_failure(root, "drill", exc)
        raise _error(exc) from None


def status(db_path, backup_dir=None):
    root = _root(db_path, backup_dir)
    result = {}
    now = _now()
    for kind, maximum_age in (("backup", timedelta(hours=2)), ("drill", timedelta(days=35))):
        item = {"success": False, "last_attempt_at": None, "last_success_at": None, "error": "missing-or-invalid-status", "stale": True}
        try:
            data = _read_json(root / ("last-" + kind + ".json"))
            attempt = _parse_time(data["last_attempt_at"])
            success = _parse_time(data["last_success_at"]) if data["last_success_at"] else None
            if type(data["success"]) is not bool or type(data["version"]) is not int or data["version"] != 1:
                raise ValueError("invalid status")
            if attempt > now or (success and (success > attempt or success > now)):
                raise ValueError("invalid status time")
            item.update(
                success=data["success"], last_attempt_at=_timestamp(attempt),
                last_success_at=_timestamp(success) if success else None,
                error=None if data["success"] else "last-attempt-failed",
                stale=success is None or now - success > maximum_age,
            )
        except (OSError, ValueError, KeyError, TypeError, BackupError):
            pass
        result[kind] = item
    result["healthy"] = all(result[kind]["success"] and not result[kind]["stale"] for kind in ("backup", "drill"))
    return result


def _statements(script):
    empty = re.compile(r"(?:\s+|;|--[^\n]*(?:\n|$)|/\*.*?\*/)*\Z", re.DOTALL)
    start = 0
    for index, char in enumerate(script):
        if char == ";" and sqlite3.complete_statement(script[start:index + 1]):
            statement = script[start:index + 1]
            start = index + 1
            if not empty.fullmatch(statement):
                yield statement
    remainder = script[start:]
    if not empty.fullmatch(remainder):
        if not sqlite3.complete_statement(remainder + "\n;"):
            raise BackupError("incomplete-sql-script")
        yield remainder


def _authorize(action, arg1, arg2, database, trigger):
    if action in (
        sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
    ):
        return sqlite3.SQLITE_DENY
    if database not in (None, "main"):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in ("load_extension", "writefile", "readfile"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_sql(db_path, file, backup_dir=None):
    try:
        path = Path(file).absolute()
        if not _regular(path):
            raise BackupError("sql-file-must-be-regular")
        script = path.read_text(encoding="utf-8")
        manager = guarded_connection(db_path, "pre-bulk") if backup_dir is None else _guarded_connection(db_path, "pre-bulk", backup_dir)
        with manager as connection:
            connection.set_authorizer(_authorize)
            try:
                executed = 0
                for statement in _statements(script):
                    connection.execute(statement)
                    executed += 1
                if not executed:
                    raise BackupError("empty-sql-script")
                if not connection.in_transaction:
                    raise BackupError("sql-transaction-ended")
            finally:
                connection.set_authorizer(None)
        return {"success": True, "statements_executed": executed}
    except Exception as exc:
        raise _error(exc) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local SQLite backups and isolated recovery checks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "drill", "status", "run-sql"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--db", required=True)
        sub.add_argument("--backup-dir")
        if command == "create":
            sub.add_argument("--reason", choices=REASONS, default="hourly")
        if command == "run-sql":
            sub.add_argument("--file", required=True, help="Trusted local SQL file only")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            path = create_backup(args.db, args.reason, args.backup_dir)
            result = {"success": True, "snapshot": path.name}
        elif args.command == "drill":
            result = drill(args.db, args.backup_dir)
        elif args.command == "run-sql":
            result = run_sql(args.db, args.file, args.backup_dir)
        else:
            result = status(args.db, args.backup_dir)
        print(json.dumps(result, sort_keys=True))
        return 0 if args.command != "status" or result["healthy"] else 1
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(_error(exc))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
