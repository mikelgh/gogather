import errno
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import backup


SECRET = "private-row-account-password-should-not-appear"
CLI = Path(__file__).resolve().parents[1] / "app" / "backup.py"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "source ? #.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
        CREATE TABLE plans (
            id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
            params TEXT, conversation TEXT, timeline TEXT, budget TEXT,
            action_items TEXT, room_allocation TEXT, members TEXT, warnings TEXT
        );
        CREATE TABLE plan_revisions (
            id INTEGER PRIMARY KEY, plan_id INTEGER REFERENCES plans(id), params TEXT
        );
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, meta TEXT);
    """)
    connection.execute("INSERT INTO users VALUES (1, ?, ?)", (SECRET, SECRET))
    for key, user in ((1, 1), (2, 999)):
        connection.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, user, json.dumps({"city": SECRET}), "[]", "[]", "[]", "[]", "[]", "[]", "[]"),
        )
    connection.execute("INSERT INTO plan_revisions VALUES (1, 1, '{}')")
    connection.execute("INSERT INTO conversations VALUES (1, '{}')")
    connection.commit()
    yield path, connection
    connection.close()


def _manifest(path):
    return json.loads((path / backup.MANIFEST_FILE).read_text())


def _snapshots(root):
    return sorted(p for p in root.iterdir() if backup._NAME.fullmatch(p.name))


def _count(path, table="users"):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-I", str(CLI), *map(str, args)],
        capture_output=True, text=True, timeout=20,
    )


def _guard_worker(path, value, entered, release, output):
    try:
        with backup.guarded_connection(path, "pre-update") as connection:
            entered.set()
            count = connection.execute("SELECT count(*) FROM users").fetchone()[0]
            connection.execute("INSERT INTO users VALUES (?, ?, ?)", (value, "worker", "test"))
            if release is not None and not release.wait(8):
                raise RuntimeError("worker timeout")
        output.put(("ok", count))
    except Exception as exc:
        output.put(("error", type(exc).__name__))


def _hold_lock(root, ready, release):
    with backup._locked(Path(root)):
        ready.set()
        release.wait(8)


def test_consistent_wal_snapshot_manifest_and_permissions(database):
    path, connection = database
    assert Path(str(path) + "-wal").stat().st_size > 0
    connection.execute("INSERT INTO users VALUES (2, 'committed-in-wal', 'test')")
    connection.commit()
    connection.execute("INSERT INTO users VALUES (3, 'not-committed', 'test')")
    snapshot = backup.create_backup(path)
    assert _count(snapshot / backup.SNAPSHOT_FILE) == 2
    manifest = _manifest(snapshot)
    assert manifest["table_counts"]["users"] == 2
    assert manifest["table_counts"]["plans"] == 2
    assert manifest["foreign_key_violations"] == 1
    assert manifest["sha256"] == hashlib.sha256((snapshot / backup.SNAPSHOT_FILE).read_bytes()).hexdigest()
    assert manifest["size_bytes"] == (snapshot / backup.SNAPSHOT_FILE).stat().st_size
    assert backup._parse_time(manifest["created_at"]).utcoffset() == timedelta(0)
    assert SECRET not in json.dumps(manifest)
    root = path.parent / "backups"
    for directory in (root, snapshot):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for file in (snapshot / backup.SNAPSHOT_FILE, snapshot / backup.MANIFEST_FILE, root / "last-backup.json", root / ".sqlite-backup.lock"):
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert not list(root.glob("*.partial"))
    assert SECRET not in (root / "last-backup.json").read_text()
    connection.rollback()


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt"])
def test_missing_empty_or_corrupt_database_fails(tmp_path, kind):
    path = tmp_path / "invalid.sqlite3"
    if kind != "missing":
        path.write_bytes(b"" if kind == "empty" else SECRET.encode() * 100)
    with pytest.raises(backup.BackupError) as error:
        backup.create_backup(path)
    assert SECRET not in str(error.value)
    assert path.exists() == (kind != "missing")
    root = tmp_path / "backups"
    assert not _snapshots(root)
    assert not list(root.glob("*.partial"))
    assert json.loads((root / "last-backup.json").read_text())["success"] is False


@pytest.mark.parametrize("failure", ["space", "sqlite-full", "inspect", "rename", "fsync"])
def test_failure_preserves_good_snapshot_and_cleans_partial(database, monkeypatch, failure):
    path, connection = database
    good = backup.create_backup(path, "manual")
    original = (good / backup.SNAPSHOT_FILE).read_bytes()
    root = good.parent
    if failure == "space":
        monkeypatch.setattr(backup.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
    elif failure == "sqlite-full":
        def no_space(*args):
            raise OSError(errno.ENOSPC, SECRET)
        monkeypatch.setattr(backup, "_inspect", no_space)
    elif failure == "inspect":
        monkeypatch.setattr(backup, "_inspect", lambda connection: (_ for _ in ()).throw(sqlite3.DatabaseError(SECRET)))
    elif failure == "rename":
        monkeypatch.setattr(backup.os, "rename", lambda *args: (_ for _ in ()).throw(OSError(SECRET)))
    else:
        original_fsync = backup.os.fsync
        calls = 0

        def fsync_once(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(SECRET)
            return original_fsync(fd)

        monkeypatch.setattr(backup.os, "fsync", fsync_once)
    with pytest.raises(backup.BackupError) as error:
        backup.create_backup(path)
    assert SECRET not in str(error.value)
    assert good.exists() and (good / backup.SNAPSHOT_FILE).read_bytes() == original
    assert len(_snapshots(root)) == 1
    assert not list(root.glob("*.partial"))
    state = json.loads((root / "last-backup.json").read_text())
    assert state["success"] is False and state["last_success_at"]
    assert SECRET not in json.dumps(state)


def _dated_snapshot(template, root, when, reason="hourly"):
    data = _manifest(template)
    name = "sqlite-backup-v1-" + when.strftime("%Y%m%dT%H%M%S%fZ") + "-" + reason + "-" + os.urandom(16).hex()
    destination = root / name
    destination.mkdir(mode=0o700)
    os.link(template / backup.SNAPSHOT_FILE, destination / backup.SNAPSHOT_FILE)
    data.update(snapshot=name, created_at=backup._timestamp(when), reason=reason)
    backup._atomic_json(destination / backup.MANIFEST_FILE, data)
    return destination


def test_hour_day_month_union_retention_and_unknown_files(database, monkeypatch):
    path, connection = database
    now = datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: now)
    template = backup.create_backup(path, "manual")
    root = template.parent
    entries = []
    for hour in range(65):
        for minute in (0, 15):
            when = now - timedelta(hours=hour, minutes=minute + 1)
            entries.append(_dated_snapshot(template, root, when))
    for day in range(3, 45):
        entries.append(_dated_snapshot(template, root, now - timedelta(days=day)))
    for month in range(15):
        year, index = divmod(2026 * 12 + 9 - month, 12)
        entries.append(_dated_snapshot(template, root, datetime(year, index + 1, 1, tzinfo=timezone.utc)))
    artificial = root / "recovery-20200101"
    artificial.mkdir()
    (artificial / "manual.db").write_text("leave alone")
    unknown = root / "unknown-file"
    unknown.write_text("leave alone")
    foreign_partial = root / ".someone-else.partial"
    foreign_partial.mkdir()
    invalid_owned = _dated_snapshot(template, root, now - timedelta(days=800))
    (invalid_owned / backup.MANIFEST_FILE).write_text("{}")
    dated = [(entry, _manifest(entry)["created_at"]) for entry in entries]
    new = backup.create_backup(path)
    dated.append((new, backup._timestamp(now)))
    expected = {template, invalid_owned}
    ordered = sorted(dated, key=lambda item: item[1], reverse=True)
    for width, limit in ((13, 48), (10, 30), (7, 12)):
        buckets = {}
        for entry, stamp in ordered:
            buckets.setdefault(stamp[:width], entry)
        expected.update(list(buckets.values())[:limit])
    assert set(_snapshots(root)) == expected
    actual = [_manifest(p) for p in _snapshots(root) if p not in (template, invalid_owned)]
    assert len(actual) >= 48
    assert len({data["created_at"][:7] for data in actual}) == 12
    assert (artificial / "manual.db").read_text() == "leave alone"
    assert unknown.read_text() == "leave alone"
    assert foreign_partial.is_dir() and invalid_owned.is_dir()


def test_event_retention_90_days_100_max_and_one_old(database, monkeypatch):
    path, connection = database
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: now)
    template = backup.create_backup(path, "manual")
    root = template.parent
    recent = [_dated_snapshot(template, root, now - timedelta(hours=i + 1), backup.REASONS[1 + i % 6]) for i in range(115)]
    stale = _dated_snapshot(template, root, now - timedelta(days=91), "pre-delete")
    backup.create_backup(path)
    assert set(p for p in _snapshots(root) if _manifest(p)["reason"] != "hourly") == {template, *recent[:99]}
    assert not stale.exists()
    monkeypatch.setattr(backup, "_now", lambda: now + timedelta(days=200))
    backup.create_backup(path)
    assert [p for p in _snapshots(root) if _manifest(p)["reason"] != "hourly"] == [template]


def test_guarded_transactions_serialize_and_back_up_prewrite_state(database):
    path, connection = database
    context = multiprocessing.get_context("spawn")
    first_entered, second_entered, release = context.Event(), context.Event(), context.Event()
    output = context.Queue()
    first = context.Process(target=_guard_worker, args=(str(path), 2, first_entered, release, output))
    second = context.Process(target=_guard_worker, args=(str(path), 3, second_entered, None, output))
    try:
        first.start()
        assert first_entered.wait(8)
        second.start()
        assert not second_entered.wait(0.2)
        snapshots = _snapshots(path.parent / "backups")
        assert len(snapshots) == 1
        assert _count(snapshots[0] / backup.SNAPSHOT_FILE) == 1
        release.set()
        first.join(10)
        second.join(10)
        assert first.exitcode == second.exitcode == 0
        assert sorted([output.get(timeout=2), output.get(timeout=2)]) == [("ok", 1), ("ok", 2)]
        assert sorted(_count(p / backup.SNAPSHOT_FILE) for p in _snapshots(path.parent / "backups")) == [1, 2]
        assert _count(path) == 3
    finally:
        release.set()
        for process in (first, second):
            if process.pid and process.is_alive():
                process.terminate()
                process.join()
        output.close()


def test_guarded_rollback_row_factory_close_and_backup_failure(database, monkeypatch):
    path, original = database
    with pytest.raises(RuntimeError):
        with backup.guarded_connection(path, "pre-delete") as connection:
            assert isinstance(connection.execute("SELECT * FROM users").fetchone(), sqlite3.Row)
            assert connection.in_transaction
            connection.execute("DELETE FROM users")
            raise RuntimeError("business failure")
    assert _count(path) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with backup.guarded_connection(path, "pre-update") as connection:
        connection.execute("INSERT INTO users VALUES (2, 'test', 'test')")
    assert _count(path) == 2
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
    with pytest.raises(backup.BackupError):
        with backup.guarded_connection(path, "pre-delete"):
            pytest.fail("must not yield before successful backup")
    original.execute("INSERT INTO users VALUES (3, 'lock-released', 'test')")
    original.commit()
    assert _count(path) == 3


def test_guarded_missing_database_and_writer_timeout(tmp_path, database, monkeypatch):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(backup.BackupError):
        with backup.guarded_connection(missing, "pre-update"):
            pytest.fail("missing database yielded")
    assert not missing.exists()
    path, writer = database
    writer.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(backup, "SQLITE_TIMEOUT", 0.05)
    with pytest.raises(backup.BackupError):
        with backup.guarded_connection(path, "pre-update"):
            pytest.fail("locked database yielded")
    writer.rollback()


def test_cross_process_lock_timeout(database, monkeypatch):
    path, connection = database
    root = path.parent / "backups"
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_lock, args=(str(root), ready, release))
    try:
        holder.start()
        assert ready.wait(8)
        monkeypatch.setattr(backup, "LOCK_TIMEOUT", 0.05)
        with pytest.raises(backup.BackupError, match="lock-timeout"):
            backup.create_backup(path)
        assert json.loads((root / "last-backup.json").read_text())["success"] is False
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join()


def test_sqlite_backup_busy_timeout(database, monkeypatch):
    path, connection = database
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(backup, "SQLITE_TIMEOUT", 0.05)
    monkeypatch.setattr(backup, "BACKUP_TIMEOUT", 0.05)
    with pytest.raises(backup.BackupError):
        backup.create_backup(path)
    connection.rollback()


@pytest.mark.parametrize("escape", [
    "COMMIT;", "END;", "ROLLBACK;", "BEGIN;", "SAVEPOINT x;", "RELEASE x;",
    "ROLLBACK TO x;", "PRAGMA user_version=123;", "PRAGMA writable_schema=ON;",
    "ATTACH DATABASE 'other.sqlite3' AS other;", "DETACH DATABASE main;",
    "VACUUM INTO 'other.sqlite3';", "CREATE TEMP TABLE escaped (id);",
    "SELECT load_extension('untrusted');", "EXPLAIN COMMIT;",
])
def test_run_sql_blocks_transaction_escape_and_other_databases(database, tmp_path, escape):
    path, connection = database
    sql = tmp_path / "trusted.sql"
    other = tmp_path / "other.sqlite3"
    escape = escape.replace("other.sqlite3", str(other))
    sql.write_text("INSERT INTO users VALUES (2, 'test', 'test');\n" + escape)
    with pytest.raises(backup.BackupError):
        backup.run_sql(path, sql)
    assert _count(path) == 1
    assert not other.exists()
    snapshots = _snapshots(path.parent / "backups")
    assert len(snapshots) == 1 and _manifest(snapshots[0])["reason"] == "pre-bulk"


@pytest.mark.parametrize("script", ["", "  -- empty\n /* comment */ ;", "INSERT INTO users VALUES (2, 'x', 'y'); THIS IS INVALID;", "INSERT INTO users VALUES (2, 'x', 'y'); SELECT '"])
def test_run_sql_empty_and_syntax_errors_roll_back(database, tmp_path, script):
    path, connection = database
    file = tmp_path / "trusted.sql"
    file.write_text(script)
    with pytest.raises(backup.BackupError):
        backup.run_sql(path, file)
    assert _count(path) == 1


def test_run_sql_statements_comments_strings_and_trigger_are_atomic(database, tmp_path):
    path, connection = database
    sql = tmp_path / "trusted.sql"
    sql.write_text("""
        -- a comment ; COMMIT;
        CREATE TABLE audit (value TEXT);
        CREATE TRIGGER audit_insert AFTER INSERT ON users BEGIN
            INSERT INTO audit VALUES ('trigger;value');
            INSERT INTO audit VALUES ('second');
        END;
        /* comment ; */ INSERT INTO users VALUES (2, 'quoted;value', 'test');
        INSERT INTO users VALUES (3, 'trailing', 'test')
    """)
    assert backup.run_sql(path, sql)["statements_executed"] == 4
    assert _count(path) == 3
    assert _count(path, "audit") == 4
    snapshot = _snapshots(path.parent / "backups")[0]
    assert _count(snapshot / backup.SNAPSHOT_FILE) == 1


def _production_fingerprint(path):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        file = Path(str(path) + suffix)
        if file.exists():
            info = file.stat()
            result[suffix] = (file.read_bytes(), info.st_mode, info.st_size, info.st_mtime_ns)
    return result


def test_drill_restores_every_snapshot_without_connecting_to_production(database, monkeypatch):
    path, connection = database
    backup.create_backup(path)
    backup.create_backup(path, "manual")
    before = _production_fingerprint(path)
    connect = backup._connect
    inspected = []

    def only_isolated(restored, *args, **kwargs):
        restored = Path(restored)
        assert restored.parent.name.startswith(".sqlite-drill-")
        assert stat.S_IMODE(restored.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(restored.stat().st_mode) == 0o600
        inspected.append(restored)
        return connect(restored, *args, **kwargs)

    monkeypatch.setattr(backup, "_connect", only_isolated)
    imports_before = set(sys.modules)
    assert backup.drill(path) == {"success": True, "snapshots_checked": 2}
    assert "app.main" not in set(sys.modules) - imports_before
    assert len(inspected) == 2 and not any(p.exists() for p in inspected)
    assert _production_fingerprint(path) == before
    record = path.parent / "backups" / "last-drill.json"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert SECRET not in record.read_text()


def test_drill_can_restore_when_source_does_not_exist(database):
    path, connection = database
    backup.create_backup(path)
    assert backup.drill(path.parent / "never-existed.sqlite3")["success"]
    assert not (path.parent / "never-existed.sqlite3").exists()


@pytest.mark.parametrize("tamper", ["data", "hash", "schema", "counts", "time", "reason", "negative", "extra", "duplicate", "fk", "fk-raised", "symlink"])
def test_drill_tampering_fails_without_changing_production(database, tamper, tmp_path):
    path, connection = database
    snapshot = backup.create_backup(path)
    before = _production_fingerprint(path)
    data = _manifest(snapshot)
    manifest = snapshot / backup.MANIFEST_FILE
    if tamper == "data":
        with (snapshot / backup.SNAPSHOT_FILE).open("r+b") as stream:
            stream.seek(500)
            stream.write(b"tampered")
    elif tamper == "symlink":
        (snapshot / backup.SNAPSHOT_FILE).unlink()
        (snapshot / backup.SNAPSHOT_FILE).symlink_to(path)
    elif tamper == "duplicate":
        manifest.write_text('{"version": 1,' + manifest.read_text()[1:])
    else:
        if tamper == "hash":
            data["sha256"] = "0" * 64
        elif tamper == "schema":
            data["schema_sha256"] = "0" * 64
        elif tamper == "counts":
            data["table_counts"]["users"] += 1
        elif tamper == "time":
            data["created_at"] = "not-a-time"
        elif tamper == "reason":
            data["reason"] = "manual"
        elif tamper == "negative":
            data["size_bytes"] = -1
        elif tamper == "extra":
            data["sensitive"] = SECRET
        elif tamper == "fk":
            data["foreign_key_violations"] = 0
        elif tamper == "fk-raised":
            data["foreign_key_violations"] = 10
        manifest.write_text(json.dumps(data))
    with pytest.raises(backup.BackupError) as error:
        backup.drill(path)
    assert SECRET not in str(error.value)
    assert _production_fingerprint(path) == before
    root = snapshot.parent
    assert not list(root.glob(".sqlite-drill-*"))
    state = json.loads((root / "last-drill.json").read_text())
    assert state["success"] is False
    assert SECRET not in json.dumps(state)
    assert snapshot.is_dir()


@pytest.mark.parametrize("table,column,value", [
    ("plans", "params", "not-json"), ("plans", "params", "[]"),
    ("plans", "timeline", "{}"), ("plans", "budget", "[NaN]"),
    ("plans", "action_items", ""), ("plans", "conversation", '"text"'),
    ("plans", "members", "null"), ("plans", "warnings", "false"),
    ("plan_revisions", "params", "broken"), ("conversations", "meta", "broken"),
])
def test_drill_validates_json_fields(database, table, column, value):
    path, connection = database
    connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE id=1', (value,))
    connection.commit()
    backup.create_backup(path)
    with pytest.raises(backup.BackupError, match="invalid-json-field"):
        backup.drill(path)


def test_status_initial_failure_success_and_staleness(database, monkeypatch, capsys):
    path, connection = database
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: now)
    assert not backup.status(path)["healthy"]
    assert backup.main(["status", "--db", str(path)]) == 1
    backup.create_backup(path)
    assert not backup.status(path)["healthy"]
    backup.drill(path)
    assert backup.status(path)["healthy"]
    assert backup.main(["status", "--db", str(path)]) == 0
    monkeypatch.setattr(backup, "_now", lambda: now + timedelta(hours=2, seconds=1))
    state = backup.status(path)
    assert not state["healthy"] and state["backup"]["stale"] and not state["drill"]["stale"]
    backup.create_backup(path)
    assert backup.status(path)["healthy"]
    with monkeypatch.context() as patch:
        patch.setattr(backup.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))
        with pytest.raises(backup.BackupError):
            backup.create_backup(path)
    assert not backup.status(path)["healthy"]
    assert not backup.status(path)["backup"]["stale"]
    monkeypatch.setattr(backup, "_now", lambda: now + timedelta(days=35, seconds=1))
    backup.create_backup(path)
    assert backup.status(path)["drill"]["stale"]
    assert backup.main(["status", "--db", str(path)]) == 1
    assert SECRET not in capsys.readouterr().out


def test_status_rejects_corrupt_and_future_records_without_leaking(database, monkeypatch):
    path, connection = database
    snapshot = backup.create_backup(path)
    backup.drill(path)
    record = snapshot.parent / "last-backup.json"
    record.write_text(SECRET)
    assert not backup.status(path)["healthy"]
    backup.create_backup(path)
    data = json.loads(record.read_text())
    data["last_success_at"] = backup._timestamp(backup._now() + timedelta(days=1))
    data["error"] = SECRET
    record.write_text(json.dumps(data))
    result = backup.status(path)
    assert not result["healthy"]
    assert SECRET not in json.dumps(result)


def test_cli_standalone_custom_directory_and_exit_codes(database, tmp_path):
    path, connection = database
    root = tmp_path / "custom-local-backups"
    initial = _cli("status", "--db", path, "--backup-dir", root)
    assert initial.returncode == 1
    created = _cli("create", "--db", path, "--backup-dir", root, "--reason", "pre-deploy")
    assert created.returncode == 0, created.stderr
    restored = _cli("drill", "--db", path, "--backup-dir", root)
    assert restored.returncode == 0, restored.stderr
    assert _cli("status", "--db", path, "--backup-dir", root).returncode == 0
    sql = tmp_path / "trusted.sql"
    sql.write_text("INSERT INTO users VALUES (2, 'test', 'test');")
    result = _cli("run-sql", "--db", path, "--backup-dir", root, "--file", sql)
    assert result.returncode == 0, result.stderr
    assert _count(path) == 2
    assert len(_snapshots(root)) == 2
    assert not (path.parent / "backups").exists()
    bad = _cli("create", "--db", tmp_path / "missing.sqlite3", "--backup-dir", root)
    assert bad.returncode == 1
    assert SECRET not in created.stdout + restored.stdout + bad.stderr
    assert _cli("create").returncode != 0
    assert _cli("create", "--db", path, "--reason", "private-value").returncode != 0
    imported = subprocess.run(
        [sys.executable, "-I", "-c", "import runpy,sys; runpy.run_path(sys.argv[1]); assert 'app.config' not in sys.modules; assert 'app.main' not in sys.modules; assert 'dotenv' not in sys.modules", str(CLI)],
        capture_output=True, text=True, timeout=10,
    )
    assert imported.returncode == 0, imported.stderr


def test_same_timestamp_keeps_newly_published_snapshot(database, monkeypatch):
    path, connection = database
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: now)
    for index in range(5):
        newest = backup.create_backup(path)
        assert newest.is_dir()
        assert _snapshots(newest.parent) == [newest]


def test_status_write_failure_happens_before_retention(database, monkeypatch):
    path, connection = database
    old = backup.create_backup(path)
    original = backup._atomic_json
    failed = False

    def fail_status_once(file, data):
        nonlocal failed
        if file.name == "last-backup.json" and not failed:
            failed = True
            raise OSError(errno.ENOSPC, SECRET)
        return original(file, data)

    monkeypatch.setattr(backup, "_atomic_json", fail_status_once)
    with pytest.raises(backup.BackupError, match="insufficient-space"):
        backup.create_backup(path)
    assert old.is_dir()
    assert len(_snapshots(old.parent)) == 2
    assert not backup.status(path)["backup"]["success"]
    assert not list(old.parent.glob("*.partial"))


def test_actual_backup_busy_loop_has_deadline(database, monkeypatch):
    path, connection = database
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode=DELETE")
    check_space = backup._check_space

    def lock_after_source_open(root, size):
        check_space(root, size)
        connection.execute("BEGIN EXCLUSIVE")

    monkeypatch.setattr(backup, "_check_space", lock_after_source_open)
    monkeypatch.setattr(backup, "SQLITE_TIMEOUT", 0.02)
    monkeypatch.setattr(backup, "BACKUP_TIMEOUT", 0.02)
    try:
        with pytest.raises(backup.BackupError, match="sqlite-backup-timeout"):
            backup.create_backup(path)
    finally:
        connection.rollback()
    assert not _snapshots(path.parent / "backups")
    assert not list((path.parent / "backups").glob("*.partial"))


def test_guarded_backup_also_works_in_rollback_journal_mode(database):
    path, connection = database
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode=DELETE")
    with backup.guarded_connection(path, "pre-delete") as guarded:
        guarded.execute("DELETE FROM users")
    assert _count(path) == 0
    assert _count(_snapshots(path.parent / "backups")[0] / backup.SNAPSHOT_FILE) == 1


def test_atomic_publication_contains_only_synced_private_files(database, monkeypatch):
    path, connection = database
    rename = backup.os.rename
    observed = []

    def check_publish(source, destination):
        assert source.name.endswith(".partial")
        assert not destination.exists()
        assert {p.name for p in source.iterdir()} == {backup.SNAPSHOT_FILE, backup.MANIFEST_FILE}
        assert stat.S_IMODE(source.stat().st_mode) == 0o700
        for file in source.iterdir():
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert _manifest(source)["sha256"] == backup._digest(source / backup.SNAPSHOT_FILE)
        observed.append(destination)
        rename(source, destination)

    monkeypatch.setattr(backup.os, "rename", check_publish)
    assert backup.create_backup(path) == observed[0]


def test_drill_detects_changed_schema_even_with_updated_data_hash(database):
    path, connection = database
    snapshot = backup.create_backup(path)
    restored = snapshot / backup.SNAPSHOT_FILE
    with closing(sqlite3.connect(restored)) as tampered:
        tampered.execute("CREATE INDEX injected ON users(username)")
        tampered.commit()
    data = _manifest(snapshot)
    data["sha256"] = backup._digest(restored)
    data["size_bytes"] = restored.stat().st_size
    (snapshot / backup.MANIFEST_FILE).write_text(json.dumps(data))
    with pytest.raises(backup.BackupError, match="schema-or-count"):
        backup.drill(path)


def test_cli_drill_and_sql_failures_are_nonzero_and_sanitized(database, tmp_path):
    path, connection = database
    snapshot = backup.create_backup(path)
    assert _cli("drill", "--db", path).returncode == 0
    previous = json.loads((snapshot.parent / "last-drill.json").read_text())["last_success_at"]
    (snapshot / backup.MANIFEST_FILE).write_text(SECRET)
    failure = _cli("drill", "--db", path)
    assert failure.returncode == 1 and SECRET not in failure.stderr
    state = json.loads((snapshot.parent / "last-drill.json").read_text())
    assert state["last_success_at"] == previous and state["success"] is False
    sql = tmp_path / "trusted.sql"
    sql.write_text("INSERT INTO users VALUES (2, 'test', 'test'); SELECT " + SECRET + ";")
    failure = _cli("run-sql", "--db", path, "--file", sql)
    assert failure.returncode == 1 and SECRET not in failure.stderr
    assert _count(path) == 1


def test_unsafe_root_symlink_and_missing_drill_leave_source_untouched(database, tmp_path):
    path, connection = database
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    root = tmp_path / "linked-backups"
    root.symlink_to(external, target_is_directory=True)
    with pytest.raises(backup.BackupError):
        backup.create_backup(path, backup_dir=root)
    assert not list(external.iterdir())
    assert stat.S_IMODE(external.stat().st_mode) == 0o755
    before = _production_fingerprint(path)
    with pytest.raises(backup.BackupError, match="no-snapshots"):
        backup.drill(path)
    assert _production_fingerprint(path) == before
    assert not backup.status(path)["drill"]["success"]


def test_existing_shared_directory_permissions_are_not_changed(database, tmp_path):
    path, connection = database
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(backup.BackupError, match="must-be-private"):
        backup.create_backup(path, backup_dir=shared)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert not list(shared.iterdir())
