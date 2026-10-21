from account_identity.application.session_store import SQLiteSessionStore
import os
import stat


def test_session_payload_is_server_side_and_expires(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"), ttl_seconds=10)
    sid = store.new_id()
    store.save(sid, {"sub": "identity-1"}, now=100)
    assert store.load(sid, now=109) == {"sub": "identity-1"}
    assert store.load(sid, now=110) == {}
    assert store.load("attacker-chosen-id", now=101) == {}


def test_sliding_session_idle_expiry_cannot_exceed_absolute_lifetime(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"), ttl_seconds=10,
                               max_lifetime_seconds=20)
    sid = store.new_id()
    store.save(sid, {"sub": "identity-1"}, now=100)
    store.save(sid, {"sub": "identity-1"}, now=109)
    store.save(sid, {"sub": "identity-1"}, now=118)
    assert store.load(sid, now=119) == {"sub": "identity-1"}
    assert store.load(sid, now=120) == {}


def test_session_revocation_is_idempotent(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    sid = store.new_id()
    store.save(sid, {"sub": "identity-1"}, now=100)
    store.delete(sid)
    store.delete(sid)
    assert store.load(sid, now=101) == {}


def test_malformed_cookie_session_ids_fail_closed(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    assert store.load("🔥") == {}
    store.delete("🔥")


def test_session_database_permissions_are_private(tmp_path):
    database = tmp_path / "private" / "sessions.sqlite3"
    SQLiteSessionStore(str(database))
    assert stat.S_IMODE(os.stat(database).st_mode) == 0o600
